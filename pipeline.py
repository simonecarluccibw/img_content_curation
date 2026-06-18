import argparse
import base64
import csv
import json
import mimetypes
import os
import random
import re
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


AMENITY_COLUMNS = [
    "Amenity_Category",
    "Amenity_Codes",
    "Amenity_MaxCategory",
    "Amenity_CustomTag1",
    "Amenity_CustomTag2",
    "Amenity_CustomTag3",
    "Amenity_CustomTag4",
    "Amenity_CustomTags",
]
CUSTOM_TAG_COLUMNS = [
    "Amenity_CustomTag1",
    "Amenity_CustomTag2",
    "Amenity_CustomTag3",
    "Amenity_CustomTag4",
]
CONTENT_COLUMNS = [
    "Caption_Experience",
    "Description_Experience",
    "Alt_Text",
    "Check_Room",
]
CONTENT_RESPONSE_KEYS = CONTENT_COLUMNS
CLASSIFICATION_SCORE_THRESHOLD = 0.4
OTHER = "Other"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
CLASSIFICATION_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "category": {"type": "STRING"},
        "score": {"type": "NUMBER"},
    },
    "required": ["category", "score"],
}


class RunLogger:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._path = log_dir / f"pipeline_{timestamp}.log"
        self._handle = self._path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def log(self, record: Dict) -> None:
        with self._lock:
            self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    @property
    def path(self) -> Path:
        return self._path


def shorten_text(value: str, limit: int = 400) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "...<truncated>"


def log_debug_event(logger: RunLogger, debug_log: bool, record: Dict) -> None:
    if debug_log:
        logger.log(record)


def read_http_error_body(exc: urllib.error.HTTPError, limit: int = 1200) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    return shorten_text(body, limit=limit)


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def parse_args() -> argparse.Namespace:
    load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(
        description="Generate hotel image metadata per hotel from an ICEPortal CSV."
    )
    parser.add_argument("--input", required=True, help="Path to the source CSV file.")
    parser.add_argument(
        "--output-dir",
        default="output_hotels",
        help="Directory where one enriched CSV per hotel will be written.",
    )
    parser.add_argument(
        "--prompts",
        default="prompts.yaml",
        help="Path to the prompts configuration file (YAML or JSON).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GEMINI_API_KEY"),
        help="Gemini API key. Defaults to GEMINI_API_KEY.",
    )
    parser.add_argument(
        "--openrouter-api-key",
        default=os.environ.get("OPENROUTER_API_KEY"),
        help="OpenRouter API key. Defaults to OPENROUTER_API_KEY.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the Gemini model from the prompts file.",
    )
    parser.add_argument(
        "--propid",
        action="append",
        default=[],
        help="One or more Propid values. Comma-separated lists are supported.",
    )
    parser.add_argument(
        "--hotel-name",
        action="append",
        default=[],
        help="One or more exact hotel names. Repeat the flag for multiple names.",
    )
    parser.add_argument("--hotel-name-file", help="Text file containing one hotel name per line.")
    parser.add_argument(
        "--next-hotels",
        type=int,
        help="Process the next N hotels that do not already have an output file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate hotel CSVs even if they already exist.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="Optional delay in seconds between image requests.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Number of parallel threads for image processing within a hotel. Default 5, max recommended 20.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for image and API requests.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries for image download and API calls.",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory where the per-run JSONL log file will be written.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print a cost and call estimate without processing any image.",
    )
    parser.add_argument(
        "--debug-log",
        action="store_true",
        help="Write detailed diagnostic records for retries, parsing failures, and raw API snippets.",
    )
    args = parser.parse_args()
    if not args.propid and not args.hotel_name and not args.hotel_name_file and not args.next_hotels:
        parser.error("Select hotels with --propid, --hotel-name, --hotel-name-file, or --next-hotels.")
    if args.next_hotels is not None and args.next_hotels <= 0:
        parser.error("--next-hotels must be greater than zero.")
    return args


def load_prompts(path: Path) -> Dict:
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(raw)
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError(
                "YAML prompts require PyYAML. Install it with `pip install -r requirements.txt` or use JSON."
            )
        return yaml.safe_load(raw)
    raise RuntimeError(f"Unsupported prompts format: {path.suffix}")


def read_rows(csv_path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        rows = list(reader)
        if not reader.fieldnames:
            raise RuntimeError("Input CSV has no header.")
        return rows, list(reader.fieldnames)


def normalize_name(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "hotel"


def parse_propid_values(raw_values: Sequence[str]) -> List[str]:
    items: List[str] = []
    for raw in raw_values:
        for item in raw.split(","):
            cleaned = item.strip()
            if cleaned:
                items.append(cleaned)
    return items


def load_names_from_file(path: Path) -> List[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def build_indexes(rows: Iterable[Dict[str, str]]) -> Tuple[Dict[str, List[Dict[str, str]]], Dict[str, str], Dict[str, Set[str]]]:
    groups: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    hotel_names: Dict[str, str] = {}
    name_to_propids: Dict[str, Set[str]] = defaultdict(set)
    for row in rows:
        propid = (row.get("Listing_MappedID") or "").strip()
        hotel_name = (row.get("Listing_Name") or "").strip()
        if not propid:
            raise RuntimeError("Found a row without Listing_MappedID.")
        groups[propid].append(row)
        hotel_names.setdefault(propid, hotel_name)
        name_to_propids[normalize_name(hotel_name)].add(propid)
    return groups, hotel_names, name_to_propids


def expected_output_path(output_dir: Path, propid: str, hotel_name: str) -> Path:
    return output_dir / f"{propid}_{slugify(hotel_name)}.csv"


def cumulative_output_path(output_dir: Path) -> Path:
    return output_dir / "all_hotels_cumulative.csv"


def resolve_hotels(
    args: argparse.Namespace,
    groups: Dict[str, List[Dict[str, str]]],
    hotel_names: Dict[str, str],
    name_to_propids: Dict[str, Set[str]],
    output_dir: Path,
) -> List[str]:
    selected: Set[str] = set()
    explicit_propids = parse_propid_values(args.propid)
    for propid in explicit_propids:
        if propid not in groups:
            raise RuntimeError(f"Propid not found in CSV: {propid}")
        selected.add(propid)

    requested_names = [name.strip() for name in args.hotel_name if name and name.strip()]
    if args.hotel_name_file:
        requested_names.extend(load_names_from_file(Path(args.hotel_name_file)))

    for hotel_name in requested_names:
        propids = name_to_propids.get(normalize_name(hotel_name), set())
        if not propids:
            raise RuntimeError(f"Hotel name not found in CSV: {hotel_name}")
        if len(propids) > 1:
            options = ", ".join(sorted(propids))
            raise RuntimeError(
                f"Hotel name is ambiguous: {hotel_name}. Use one of these Propid values instead: {options}"
            )
        selected.update(propids)

    if args.next_hotels:
        for propid in sorted(groups.keys(), key=lambda value: int(value) if value.isdigit() else value):
            output_path = expected_output_path(output_dir, propid, hotel_names[propid])
            if not output_path.exists() or args.force:
                selected.add(propid)
            if len(selected) >= args.next_hotels and not explicit_propids and not requested_names:
                break

    resolved = sorted(selected, key=lambda value: int(value) if value.isdigit() else value)
    if not resolved:
        raise RuntimeError("No hotels matched the requested selection.")
    return resolved


def download_image_bytes(url: str, timeout: int, max_retries: int) -> Tuple[bytes, str]:
    last_error = None
    headers = {"User-Agent": "img-caption-pipeline/1.0"}
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get_content_type() or "application/octet-stream"
                return response.read(), content_type
        except urllib.error.HTTPError as exc:
            last_error = exc
            wait = min(2.0 ** attempt, 60) + random.uniform(0, 1)
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else wait
            if attempt == max_retries:
                break
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            time.sleep(min(2.0 ** attempt, 60) + random.uniform(0, 1))
    raise RuntimeError(f"Failed to download image: {url} ({last_error})")


def extract_json_text(api_response: Dict) -> str:
    candidates = api_response.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini response has no candidates.")
    texts: List[str] = []
    for candidate in candidates:
        parts = (((candidate or {}).get("content") or {}).get("parts") or [])
        texts.extend(part.get("text", "") for part in parts if "text" in part)
    raw = "".join(texts).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()
    if not raw:
        raise RuntimeError("Gemini returned an empty text payload.")
    return raw


def extract_openrouter_text(api_response: Dict) -> str:
    choices = api_response.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter response has no choices.")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict):
                texts.append(str(part.get("text", "")))
        content = "".join(texts)
    raw = str(content).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()
    if not raw:
        raise RuntimeError("OpenRouter returned an empty text payload.")
    return raw


def summarize_api_response(api_response: Dict, limit: int = 1200) -> str:
    try:
        serialized = json.dumps(api_response, ensure_ascii=False)
    except Exception:
        serialized = str(api_response)
    return shorten_text(serialized, limit=limit)


def build_thinking_config(section: Dict) -> Dict:
    budget = section.get("thinking_budget", 0)
    return {"thinkingBudget": budget}


def extract_finish_reason(api_response: Dict) -> str:
    candidates = api_response.get("candidates") or []
    if not candidates:
        return ""
    return str((candidates[0] or {}).get("finishReason", ""))


def extract_first_json_object(raw_text: str) -> str:
    start = raw_text.find("{")
    if start == -1:
        raise RuntimeError(f"AI returned no JSON object: {raw_text[:200]}")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(raw_text)):
        char = raw_text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw_text[start : index + 1]
    raise RuntimeError(f"AI returned an unterminated JSON object: {raw_text[:200]}")


def parse_content_generation_text(raw_text: str) -> Dict[str, str]:
    parsed = json.loads(extract_first_json_object(raw_text))
    return {
        "Caption_Experience": str(parsed.get("Caption_Experience", "")).strip(),
        "Description_Experience": str(parsed.get("Description_Experience", "")).strip(),
        "Alt_Text": str(parsed.get("Alt_Text", "")).strip(),
        "Check_Room": coerce_check_room(parsed.get("Check_Room", "0")),
    }


def coerce_check_room(value: object) -> str:
    cleaned = str(value or "").strip().casefold()
    if cleaned in {"1", "true", "yes", "y"}:
        return "1"
    if cleaned in {"0", "false", "no", "n"}:
        return "0"
    try:
        return "1" if int(float(cleaned)) == 1 else "0"
    except ValueError:
        return "0"


def join_custom_tags(values: Sequence[str]) -> str:
    return ", ".join(value.strip() for value in values if value and value.strip())


def is_numbered_tag_template(value: str) -> bool:
    return bool(value and value.strip().endswith("-N"))


def materialize_numbered_tag_template(value: str, index: int) -> str:
    stripped = value.strip()
    return f"{stripped[:-2]}-{index}"


def number_custom_tag_placeholders_for_hotel(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    next_index_by_template: Dict[str, int] = {}
    numbered_rows: List[Dict[str, str]] = []
    for row in rows:
        numbered = dict(row)
        templates = []
        for column in CUSTOM_TAG_COLUMNS:
            value = (numbered.get(column) or "").strip()
            if is_numbered_tag_template(value) and value not in templates:
                templates.append(value)
        if templates:
            row_index = max(next_index_by_template.get(template, 1) for template in templates)
            for column in CUSTOM_TAG_COLUMNS:
                value = (numbered.get(column) or "").strip()
                if value in templates:
                    numbered[column] = materialize_numbered_tag_template(value, row_index)
            for template in templates:
                next_index_by_template[template] = row_index + 1
        numbered["Amenity_CustomTags"] = join_custom_tags([numbered.get(column, "") for column in CUSTOM_TAG_COLUMNS])
        numbered_rows.append(numbered)
    return numbered_rows


def harmonize_row_schema(row: Dict[str, str]) -> Dict[str, str]:
    normalized = dict(row)
    normalized["Amenity_Category"] = normalized.get("Amenity_Category") or normalized.get("AI_Amenity_Category", "")
    normalized["Amenity_Codes"] = normalized.get("Amenity_Codes") or normalized.get("AI_Amenity_Codes", "")
    normalized["Amenity_MaxCategory"] = normalized.get("Amenity_MaxCategory") or normalized.get(
        "AI_Amenity_Maxcategoria", ""
    )
    normalized["Amenity_CustomTag1"] = normalized.get("Amenity_CustomTag1") or normalized.get(
        "AI_Amenity_CustomTag1", ""
    )
    normalized["Amenity_CustomTag2"] = normalized.get("Amenity_CustomTag2") or normalized.get(
        "AI_Amenity_CustomTag2", ""
    )
    normalized["Amenity_CustomTag3"] = normalized.get("Amenity_CustomTag3") or normalized.get(
        "AI_Amenity_CustomTag3", ""
    )
    normalized["Amenity_CustomTag4"] = normalized.get("Amenity_CustomTag4") or normalized.get(
        "AI_Amenity_CustomTag4", ""
    )
    normalized["Caption_Experience"] = normalized.get("Caption_Experience") or normalized.get(
        "AI_Caption_Experience", ""
    )
    normalized["Description_Experience"] = normalized.get("Description_Experience") or normalized.get(
        "AI_Description_Experience", ""
    )
    normalized["Alt_Text"] = normalized.get("Alt_Text") or normalized.get("AI_Alt_Text", "")
    normalized["Check_Room"] = coerce_check_room(
        normalized.get("Check_Room", normalized.get("AI_Check_Room", "0"))
    )
    normalized["Amenity_CustomTags"] = join_custom_tags([normalized.get(column, "") for column in CUSTOM_TAG_COLUMNS])
    return normalized


def get_content_generation_config(config: Dict, gemini_model: str) -> Dict:
    legacy = config.get("generation", {}) or {}
    section = config.get("content_generation", {}) or {}
    provider = str(section.get("provider", "gemini")).strip().casefold()
    if provider not in {"gemini", "openrouter"}:
        raise RuntimeError(
            f"Unsupported content_generation.provider: {provider}. Use 'gemini' or 'openrouter'."
        )
    default_model = gemini_model if provider == "gemini" else "openai/gpt-4o"
    return {
        "provider": provider,
        "model": str(section.get("model") or default_model),
        "temperature": section.get("temperature", legacy.get("temperature", 0.8)),
        "max_tokens": section.get("max_tokens", section.get("max_output_tokens", legacy.get("max_output_tokens", 500))),
        "thinking_budget": section.get("thinking_budget", legacy.get("thinking_budget", 0)),
        "instructions": str(section.get("instructions", "")).strip(),
    }


def validate_runtime_keys(args: argparse.Namespace, content_generation: Dict) -> None:
    if args.dry_run:
        return
    if not args.api_key:
        raise RuntimeError("Missing Gemini API key. Set --api-key or GEMINI_API_KEY.")
    if content_generation["provider"] == "openrouter" and not args.openrouter_api_key:
        raise RuntimeError(
            "Missing OpenRouter API key. Set --openrouter-api-key or OPENROUTER_API_KEY because content_generation.provider is openrouter."
        )


def build_prompt(row: Dict[str, str], config: Dict) -> str:
    tone = config.get("tone_of_voice", "").strip()
    style = config.get("style_rules", {}) or {}
    limits = config.get("length_limits", {}) or {}
    content_generation = config.get("content_generation", {}) or {}
    extra_instructions = str(content_generation.get("instructions", "")).strip()
    context_lines = [
        f"Hotel name: {row.get('Listing_Name', '').strip()}",
        f"Brand: {row.get('Listing_Brand', '').strip()}",
        f"Existing caption: {row.get('Asset_Caption', '').strip()}",
        f"Media type: {row.get('Asset_MediaType', '').strip()}",
        f"Image index: {row.get('Asset_Index', '').strip()}",
    ]
    prompt = (
        "You are generating hotel image metadata in English for hospitality distribution.\n"
        f"Tone of voice: {tone}\n"
        f"Experience caption style: {style.get('experience_caption', '')}\n"
        f"Experience description style: {style.get('experience_description', '')}\n"
        f"Alt text style: {style.get('alt_text', '')}\n"
        f"Caption max words: {limits.get('caption_max_words', 14)}\n"
        f"Description max words: {limits.get('description_max_words', 36)}\n"
        f"Alt text max words: {limits.get('alt_text_max_words', 18)}\n"
        "Rules:\n"
        "- Caption_Experience and Description_Experience must be inspired by the image, evocative, and not too literal.\n"
        "- Push imagination, freshness, and editorial variety while staying grounded in the visual content.\n"
        "- Structures like 'Find your...', 'A quiet...', or 'A space...' are possible, but do not lean on them as repeated patterns.\n"
        "- Avoid making captions and descriptions interchangeable across similar images.\n"
        "- Do not invent amenities, services, locations, awards, views, room types, or details not visible or provided.\n"
        "- Alt text must be accessible, concise, factual, and non-promotional.\n"
        "- Set Check_Room to 1 only if the image clearly shows a hotel guest room or its private bathroom.\n"
        "- Set Check_Room to 0 for all other hotel scenes.\n"
        "- Return valid JSON only.\n"
        "Return an object with exactly these keys:\n"
        "Caption_Experience, Description_Experience, Alt_Text, Check_Room.\n"
    )
    if extra_instructions:
        prompt += f"Additional content generation instructions:\n{extra_instructions}\n"
    return prompt + "Image context:\n" + "\n".join(context_lines)


def build_classification_prompt(row: Dict[str, str], taxonomy: List[Dict]) -> str:
    category_lines = "\n".join(
        f"- {entry['category']}: {entry['keywords']}"
        for entry in taxonomy
    )
    return (
        "You are classifying a hotel image into exactly one amenity category.\n"
        "Analyze the image carefully, then consider the existing caption as supporting context.\n\n"
        "Available categories (name: description):\n"
        f"{category_lines}\n\n"
        f"Existing caption: {row.get('Asset_Caption', '').strip()}\n"
        f"Hotel name: {row.get('Listing_Name', '').strip()}\n\n"
        "Rules:\n"
        "- Assign the single most appropriate category based on what is visually dominant in the image.\n"
        "- The caption is a hint, not ground truth - trust the image first.\n"
        "- A guest room or a private bathroom is not an amenity category unless another listed amenity is visually dominant.\n"
        f"- If no category fits with confidence >= {CLASSIFICATION_SCORE_THRESHOLD}, return Other.\n"
        "- Return valid JSON only, no explanation, no markdown.\n"
        '{"category": "<category name or Other>", "score": <float 0.0-1.0>}'
    )


def build_classification_fallback_prompt(row: Dict[str, str], taxonomy: List[Dict]) -> str:
    categories = ", ".join([entry["category"] for entry in taxonomy] + [OTHER])
    return (
        "Classify this hotel image into exactly one category.\n"
        f"Allowed categories: {categories}\n"
        f"Existing caption: {row.get('Asset_Caption', '').strip()}\n"
        f"Hotel name: {row.get('Listing_Name', '').strip()}\n"
        "Output exactly one line in this format only:\n"
        "<category>|||<score>\n"
        "Rules:\n"
        "- Score must be a decimal between 0.0 and 1.0.\n"
        "- A guest room or a private bathroom alone should map to Other.\n"
        f"- If uncertain below {CLASSIFICATION_SCORE_THRESHOLD}, output Other.\n"
        "- Do not add any extra words."
    )


def parse_classification_text(raw_text: str, taxonomy: List[Dict]) -> Dict[str, float]:
    text = raw_text.strip()
    if not text:
        raise RuntimeError("Gemini returned an empty classification payload.")
    try:
        parsed = json.loads(extract_first_json_object(text))
        category = str(parsed.get("category", OTHER)).strip()
        score = float(parsed.get("score", 0.0))
        return {"category": category or OTHER, "score": score}
    except (ValueError, json.JSONDecodeError, RuntimeError):
        pass
    if "|||" in text:
        category_part, score_part = text.split("|||", 1)
        category = category_part.strip() or OTHER
        try:
            score = float(score_part.strip())
        except ValueError:
            score = 0.0
        return {"category": category, "score": score}
    lowered = text.casefold()
    matched_category = None
    for entry in taxonomy:
        if entry["category"].casefold() in lowered:
            matched_category = entry["category"]
            break
    if matched_category is None and OTHER.casefold() in lowered:
        matched_category = OTHER
    score_match = re.search(r"([01](?:\.\d+)?)", text)
    score = float(score_match.group(1)) if score_match else 0.0
    if matched_category:
        return {"category": matched_category, "score": score}
    raise RuntimeError(f"Gemini returned no recognizable classification payload: {text[:200]}")


def call_gemini_classification(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    fallback_prompt: str,
    api_key: str,
    model: str,
    config: Dict,
    taxonomy: List[Dict],
    timeout: int,
    max_retries: int,
    logger: RunLogger,
    log_context: Dict,
    debug_log: bool,
) -> Dict:
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}}]}],
        "generationConfig": {
            "temperature": config.get("classification", {}).get("temperature", 0.1),
            "maxOutputTokens": config.get("classification", {}).get("max_output_tokens", 100),
            "responseMimeType": "application/json",
            "responseSchema": CLASSIFICATION_RESPONSE_SCHEMA,
            "thinkingConfig": build_thinking_config(config.get("classification", {})),
        },
    }
    last_error = None
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                return parse_classification_text(extract_json_text(response_data), taxonomy)
        except urllib.error.HTTPError as exc:
            last_error = exc
            wait = float(exc.headers.get("Retry-After")) if exc.code == 429 and exc.headers.get("Retry-After") else min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "classification_retry", "attempt": attempt, "http_status": exc.code, "http_error_body": read_http_error_body(exc), "wait_seconds": wait, "error": str(exc)})
            if attempt == max_retries:
                break
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "classification_retry", "attempt": attempt, "http_status": None, "wait_seconds": wait, "error": str(exc)})
            time.sleep(wait)

    fallback_payload = {
        "contents": [{"parts": [{"text": fallback_prompt}, {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 40, "responseMimeType": "text/plain"},
    }
    fallback_error = None
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(endpoint, data=json.dumps(fallback_payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                return parse_classification_text(extract_json_text(response_data), taxonomy)
        except Exception as exc:
            fallback_error = exc
            if attempt == max_retries:
                break
            wait = min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "classification_fallback_retry", "attempt": attempt, "wait_seconds": wait, "error": str(exc)})
            time.sleep(wait)
    raise RuntimeError(
        f"Classification failed after {max_retries} attempts. JSON error: {last_error}. Fallback error: {fallback_error}"
    )


def call_gemini_generation(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    api_key: str,
    content_generation: Dict,
    timeout: int,
    max_retries: int,
    logger: RunLogger,
    log_context: Dict,
    debug_log: bool,
) -> Dict[str, str]:
    model = content_generation["model"]
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}}]}],
        "generationConfig": {
            "temperature": content_generation["temperature"],
            "maxOutputTokens": content_generation["max_tokens"],
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": content_generation.get("thinking_budget", 0)},
        },
    }
    last_error = None
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                raw_text = extract_json_text(response_data)
                result = parse_content_generation_text(raw_text)
                result["_generation_provider"] = "gemini"
                result["_generation_model"] = model
                return result
        except urllib.error.HTTPError as exc:
            last_error = exc
            wait = float(exc.headers.get("Retry-After")) if exc.code == 429 and exc.headers.get("Retry-After") else min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "generation_retry", "generation_provider": "gemini", "generation_model": model, "attempt": attempt, "http_status": exc.code, "http_error_body": read_http_error_body(exc), "wait_seconds": wait, "error": str(exc)})
            if attempt == max_retries:
                break
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "generation_retry", "generation_provider": "gemini", "generation_model": model, "attempt": attempt, "http_status": None, "wait_seconds": wait, "error": str(exc)})
            time.sleep(wait)
    raise RuntimeError(f"Generation failed after {max_retries} attempts: {last_error}")


def call_openrouter_generation(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    api_key: str,
    content_generation: Dict,
    timeout: int,
    max_retries: int,
    logger: RunLogger,
    log_context: Dict,
    debug_log: bool,
) -> Dict[str, str]:
    model = content_generation["model"]
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": content_generation["temperature"],
        "max_tokens": content_generation["max_tokens"],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/simonecarluccibw/img_content_curation",
        "X-Title": "img_content_curation",
    }
    last_error = None
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(
            OPENROUTER_CHAT_COMPLETIONS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                raw_text = extract_openrouter_text(response_data)
                result = parse_content_generation_text(raw_text)
                result["_generation_provider"] = "openrouter"
                result["_generation_model"] = model
                log_debug_event(logger, debug_log, {**log_context, "step": "openrouter_generation_response", "generation_provider": "openrouter", "generation_model": model, "raw_response_excerpt": shorten_text(raw_text), "api_response_excerpt": summarize_api_response(response_data), "error": None})
                return result
        except urllib.error.HTTPError as exc:
            last_error = exc
            wait = float(exc.headers.get("Retry-After")) if exc.code == 429 and exc.headers.get("Retry-After") else min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "generation_retry", "generation_provider": "openrouter", "generation_model": model, "attempt": attempt, "http_status": exc.code, "http_error_body": read_http_error_body(exc), "wait_seconds": wait, "error": str(exc)})
            if attempt == max_retries:
                break
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = min(2.0 ** attempt, 60) + random.uniform(0, 1)
            log_debug_event(logger, debug_log, {**log_context, "step": "generation_retry", "generation_provider": "openrouter", "generation_model": model, "attempt": attempt, "http_status": None, "wait_seconds": wait, "error": str(exc)})
            time.sleep(wait)
    raise RuntimeError(f"OpenRouter generation failed after {max_retries} attempts: {last_error}")


def generate_content(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    gemini_api_key: str,
    openrouter_api_key: str,
    content_generation: Dict,
    timeout: int,
    max_retries: int,
    logger: RunLogger,
    log_context: Dict,
    debug_log: bool,
) -> Dict[str, str]:
    if content_generation["provider"] == "gemini":
        return call_gemini_generation(
            image_bytes, mime_type, prompt, gemini_api_key, content_generation, timeout, max_retries, logger, log_context, debug_log
        )
    if content_generation["provider"] == "openrouter":
        return call_openrouter_generation(
            image_bytes, mime_type, prompt, openrouter_api_key, content_generation, timeout, max_retries, logger, log_context, debug_log
        )
    raise RuntimeError(f"Unsupported content generation provider: {content_generation['provider']}")


def resolve_amenity_fields(category: str, score: float, taxonomy: List[Dict]) -> Dict[str, str]:
    empty = {col: "" for col in AMENITY_COLUMNS}
    if score < CLASSIFICATION_SCORE_THRESHOLD or category == OTHER:
        empty["Amenity_Category"] = OTHER
        return empty
    for entry in taxonomy:
        if entry["category"] == category:
            return {
                "Amenity_Category": entry["category"],
                "Amenity_Codes": entry["codes"],
                "Amenity_MaxCategory": entry["maxcategoria"],
                "Amenity_CustomTag1": entry["custom_tag_1"],
                "Amenity_CustomTag2": entry["custom_tag_2"],
                "Amenity_CustomTag3": entry["custom_tag_3"],
                "Amenity_CustomTag4": entry["custom_tag_4"],
                "Amenity_CustomTags": join_custom_tags([
                    entry["custom_tag_1"],
                    entry["custom_tag_2"],
                    entry["custom_tag_3"],
                    entry["custom_tag_4"],
                ]),
            }
    empty["Amenity_Category"] = OTHER
    return empty


def enrich_row(
    row: Dict[str, str],
    gemini_api_key: str,
    openrouter_api_key: str,
    classification_model: str,
    content_generation: Dict,
    config: Dict,
    taxonomy: List[Dict],
    timeout: int,
    max_retries: int,
    logger: RunLogger,
    log_context: Dict,
    debug_log: bool,
) -> Dict:
    image_url = (row.get("Asset_Link") or "").strip()
    if not image_url:
        raise RuntimeError("Missing Asset_Link.")

    image_bytes, mime_type = download_image_bytes(image_url, timeout=timeout, max_retries=max_retries)
    if mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(image_url)
        mime_type = guessed or "image/jpeg"

    t0 = time.perf_counter()
    classification_prompt = build_classification_prompt(row, taxonomy)
    classification_fallback_prompt = build_classification_fallback_prompt(row, taxonomy)
    classification_error = None
    try:
        classification_result = call_gemini_classification(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=classification_prompt,
            fallback_prompt=classification_fallback_prompt,
            api_key=gemini_api_key,
            model=classification_model,
            config=config,
            taxonomy=taxonomy,
            timeout=timeout,
            max_retries=max_retries,
            logger=logger,
            log_context=log_context,
            debug_log=debug_log,
        )
    except Exception as exc:
        classification_error = str(exc)
        classification_result = {"category": OTHER, "score": 0.0}
        log_debug_event(logger, True, {**log_context, "step": "classification_fallback_other", "attempt": None, "duration_ms": None, "error": classification_error})
    classification_ms = int((time.perf_counter() - t0) * 1000)
    amenity_fields = resolve_amenity_fields(classification_result["category"], classification_result["score"], taxonomy)

    if amenity_fields.get("Amenity_Category") == OTHER:
        generation_fields = {
            "Caption_Experience": "",
            "Description_Experience": "",
            "Alt_Text": "",
            "Check_Room": "0",
            "_generation_provider": content_generation["provider"],
            "_generation_model": content_generation["model"],
            "_generation_skipped": True,
            "_generation_skip_reason": "amenity_category_other",
        }
        generation_ms = 0
    else:
        t1 = time.perf_counter()
        generation_prompt = build_prompt(row, config)
        generation_fields = generate_content(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=generation_prompt,
            gemini_api_key=gemini_api_key,
            openrouter_api_key=openrouter_api_key,
            content_generation=content_generation,
            timeout=timeout,
            max_retries=max_retries,
            logger=logger,
            log_context=log_context,
            debug_log=debug_log,
        )
        generation_ms = int((time.perf_counter() - t1) * 1000)

    return {
        **amenity_fields,
        **generation_fields,
        "_classification_score": classification_result["score"],
        "_classification_ms": classification_ms,
        "_generation_ms": generation_ms,
        "_classification_error": classification_error,
    }


def sidecar_path(output_path: Path) -> Path:
    return output_path.with_suffix(".progress.jsonl")


def load_checkpoint(sidecar: Path) -> Dict[str, Dict]:
    if not sidecar.exists():
        return {}
    checkpoint = {}
    with sidecar.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                record = json.loads(line)
                checkpoint[record["asset_fileid"]] = harmonize_row_schema(record["enriched_row"])
    return checkpoint


def append_checkpoint(sidecar: Path, asset_fileid: str, enriched_row: Dict, lock: "threading.Lock | None" = None) -> None:
    def _write() -> None:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with sidecar.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"asset_fileid": asset_fileid, "enriched_row": enriched_row}, ensure_ascii=False) + "\n")
            handle.flush()

    if lock is not None:
        with lock:
            _write()
    else:
        _write()


def write_hotel_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=path.parent, suffix=".tmp") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def read_output_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        return [harmonize_row_schema(row) for row in reader]


def update_cumulative_csv(cumulative_path: Path, fieldnames: List[str], propid: str, hotel_rows: List[Dict[str, str]]) -> None:
    existing_rows: List[Dict[str, str]] = []
    if cumulative_path.exists():
        existing_rows = read_output_csv(cumulative_path)
    remaining_rows = [row for row in existing_rows if (row.get("Listing_MappedID") or "").strip() != propid]
    write_hotel_csv(cumulative_path, fieldnames, remaining_rows + [harmonize_row_schema(row) for row in hotel_rows])


def _process_single_image(
    index: int,
    total: int,
    row: Dict[str, str],
    propid: str,
    hotel_name: str,
    args: argparse.Namespace,
    config: Dict,
    classification_model: str,
    content_generation: Dict,
    taxonomy: List[Dict],
    logger: RunLogger,
    sidecar: Path,
    checkpoint_lock: "threading.Lock",
) -> Dict[str, str]:
    asset_fileid = row.get("Asset_FileID", "")
    enriched = harmonize_row_schema(row)
    log_context = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "propid": propid,
        "hotel_name": hotel_name,
        "asset_fileid": asset_fileid,
        "asset_index": row.get("Asset_Index", ""),
        "asset_caption": row.get("Asset_Caption", ""),
        "asset_link": row.get("Asset_Link", ""),
    }

    try:
        log_debug_event(logger, args.debug_log, {**log_context, "step": "row_start", "error": None})
        ai_values = enrich_row(
            row=row,
            gemini_api_key=args.api_key,
            openrouter_api_key=args.openrouter_api_key,
            classification_model=classification_model,
            content_generation=content_generation,
            config=config,
            taxonomy=taxonomy,
            timeout=args.timeout,
            max_retries=args.max_retries,
            logger=logger,
            log_context=log_context,
            debug_log=args.debug_log,
        )
        score = ai_values.pop("_classification_score", None)
        classification_ms = ai_values.pop("_classification_ms", 0)
        generation_ms = ai_values.pop("_generation_ms", 0)
        classification_error = ai_values.pop("_classification_error", None)
        generation_provider = ai_values.pop("_generation_provider", content_generation["provider"])
        generation_model = ai_values.pop("_generation_model", content_generation["model"])
        generation_skipped = ai_values.pop("_generation_skipped", False)
        generation_skip_reason = ai_values.pop("_generation_skip_reason", None)
        enriched.update(ai_values)

        logger.log({**log_context, "step": "classification", "ai_amenity_category": enriched.get("Amenity_Category"), "ai_amenity_score": score, "duration_ms": classification_ms, "error": classification_error})
        logger.log({
            **log_context,
            "step": "generation",
            "generation_provider": generation_provider,
            "generation_model": generation_model,
            "Caption_Experience": enriched.get("Caption_Experience"),
            "Description_Experience": enriched.get("Description_Experience"),
            "Alt_Text": enriched.get("Alt_Text"),
            "Check_Room": enriched.get("Check_Room"),
            "duration_ms": generation_ms,
            "error": None,
            "skipped": generation_skipped,
            "skip_reason": generation_skip_reason,
        })
        log_debug_event(logger, args.debug_log, {**log_context, "step": "row_done", "classification_duration_ms": classification_ms, "generation_duration_ms": generation_ms, "generation_provider": generation_provider, "generation_model": generation_model, "generation_skipped": generation_skipped, "generation_skip_reason": generation_skip_reason, "error": None})
    except Exception as exc:
        print(f"[ROW ERROR] {propid} image {index}/{total} | Asset_FileID={asset_fileid} | {exc}", file=sys.stderr)
        logger.log({**log_context, "step": "error", "ai_amenity_category": None, "ai_amenity_score": None, "duration_ms": None, "error": str(exc)})
        for col in AMENITY_COLUMNS:
            enriched[col] = ""
        enriched["Amenity_Category"] = OTHER
        for col in CONTENT_COLUMNS:
            enriched[col] = ""
        enriched["Check_Room"] = "0"

    enriched = harmonize_row_schema(enriched)
    if asset_fileid:
        append_checkpoint(sidecar, asset_fileid, enriched, lock=checkpoint_lock)
    if args.request_delay:
        time.sleep(args.request_delay)
    return enriched


def process_hotel(
    propid: str,
    rows: List[Dict[str, str]],
    hotel_name: str,
    output_path: Path,
    fieldnames: List[str],
    args: argparse.Namespace,
    config: Dict,
    classification_model: str,
    content_generation: Dict,
    taxonomy: List[Dict],
    logger: RunLogger,
) -> List[Dict[str, str]]:
    from concurrent.futures import ThreadPoolExecutor

    print(f"[START] {propid} | {hotel_name} | {len(rows)} images | workers={args.workers}")
    sidecar = sidecar_path(output_path)
    checkpoint = load_checkpoint(sidecar)
    checkpoint_lock = threading.Lock()

    to_process: List[Tuple[int, Dict[str, str]]] = []
    cached: Dict[str, Dict] = {}
    for index, row in enumerate(rows, start=1):
        asset_fileid = row.get("Asset_FileID", "")
        if asset_fileid and asset_fileid in checkpoint:
            cached[asset_fileid] = harmonize_row_schema(checkpoint[asset_fileid])
        else:
            to_process.append((index, row))

    total = len(rows)
    try:
        from tqdm import tqdm
        progress = tqdm(total=total, desc=propid, unit="img")
        progress.update(len(cached))
    except ImportError:
        progress = None

    for row in rows:
        asset_fileid = row.get("Asset_FileID", "")
        if asset_fileid and asset_fileid in cached:
            log_debug_event(logger, args.debug_log, {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "propid": propid,
                "hotel_name": hotel_name,
                "asset_fileid": asset_fileid,
                "asset_index": row.get("Asset_Index", ""),
                "asset_caption": row.get("Asset_Caption", ""),
                "asset_link": row.get("Asset_Link", ""),
                "step": "checkpoint_hit",
                "error": None,
            })

    def process_one(item: Tuple[int, Dict[str, str]]) -> Dict[str, str]:
        idx, row = item
        result = _process_single_image(
            index=idx,
            total=total,
            row=row,
            propid=propid,
            hotel_name=hotel_name,
            args=args,
            config=config,
            classification_model=classification_model,
            content_generation=content_generation,
            taxonomy=taxonomy,
            logger=logger,
            sidecar=sidecar,
            checkpoint_lock=checkpoint_lock,
        )
        if progress is not None:
            progress.update(1)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        processed_results = list(executor.map(process_one, to_process))

    if progress is not None:
        progress.close()

    processed_iter = iter(processed_results)
    enriched_rows: List[Dict[str, str]] = []
    for row in rows:
        asset_fileid = row.get("Asset_FileID", "")
        if asset_fileid and asset_fileid in cached:
            enriched_rows.append(cached[asset_fileid])
        else:
            enriched_rows.append(harmonize_row_schema(next(processed_iter)))

    enriched_rows = number_custom_tag_placeholders_for_hotel(enriched_rows)
    write_hotel_csv(output_path, fieldnames, enriched_rows)
    sidecar.unlink(missing_ok=True)
    print(f"[DONE] {propid} | wrote {output_path}")
    return enriched_rows


def run_dry(selected_propids: List[str], groups: Dict[str, List[Dict]], hotel_names: Dict[str, str], prompts_config: Dict, workers: int = 5) -> int:
    cost_section = prompts_config.get("cost", {})
    class_cost = cost_section.get("classification_per_call_usd", 0.00056)
    gen_cost = cost_section.get("generation_per_call_usd", 0.00076)
    per_image = class_cost + gen_cost
    total_images = 0
    print("\n[DRY RUN] Estimate summary:")
    print(f"Classification model: {prompts_config.get('model', 'unknown')}")
    content_section = prompts_config.get("content_generation", {}) or {}
    print(f"Content provider: {content_section.get('provider', 'gemini')} | model: {content_section.get('model', prompts_config.get('model', 'unknown'))}")
    print(f"Per-image cost: ${per_image:.6f} (class ${class_cost:.6f} + gen ${gen_cost:.6f})")
    print(f"{'Propid':<12} {'Hotel':<50} {'Images':>8} {'API calls':>10} {'Est. cost $':>12}")
    print("-" * 96)
    for propid in selected_propids:
        n = len(groups[propid])
        cost = n * per_image
        total_images += n
        print(f"{propid:<12} {hotel_names[propid][:50]:<50} {n:>8} {n*2:>10} {cost:>12.4f}")
    total_cost = total_images * per_image
    seq_hours = (total_images * 12) / 3600
    par_hours = (total_images * 12) / (3600 * workers)
    print("-" * 96)
    print(f"{'TOTAL':<12} {'':<50} {total_images:>8} {total_images*2:>10} {total_cost:>12.4f}")
    print(f"\nEstimated time (sequential):        {seq_hours:.1f} hours")
    print(f"Estimated time ({workers} workers): {par_hours:.1f} hours\n")
    return 0


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    prompts_path = Path(args.prompts)
    output_dir = Path(args.output_dir)
    prompts_config = load_prompts(prompts_path)
    classification_model = args.model or prompts_config.get("model", "gemini-3.5-flash")
    content_generation = get_content_generation_config(prompts_config, classification_model)
    validate_runtime_keys(args, content_generation)
    taxonomy = prompts_config.get("amenity_taxonomy", [])

    rows, original_fieldnames = read_rows(input_path)
    groups, hotel_names, name_to_propids = build_indexes(rows)
    selected_propids = resolve_hotels(args, groups, hotel_names, name_to_propids, output_dir)
    output_fieldnames = original_fieldnames + [col for col in AMENITY_COLUMNS + CONTENT_COLUMNS if col not in original_fieldnames]
    if args.dry_run:
        return run_dry(selected_propids, groups, hotel_names, prompts_config, workers=args.workers)

    logger = RunLogger(Path(args.log_dir))
    print(f"[LOG] Detailed run log: {logger.path}")
    print(f"[CONTENT] provider={content_generation['provider']} | model={content_generation['model']}")
    try:
        for propid in selected_propids:
            hotel_name = hotel_names[propid]
            output_path = expected_output_path(output_dir, propid, hotel_name)
            if output_path.exists() and not args.force:
                print(f"[SKIP] {propid} | {hotel_name} | output exists: {output_path}")
                hotel_rows = number_custom_tag_placeholders_for_hotel(read_output_csv(output_path))
                write_hotel_csv(output_path, output_fieldnames, hotel_rows)
                update_cumulative_csv(cumulative_output_path(output_dir), output_fieldnames, propid, hotel_rows)
                continue
            enriched_rows = process_hotel(
                propid=propid,
                rows=groups[propid],
                hotel_name=hotel_name,
                output_path=output_path,
                fieldnames=output_fieldnames,
                args=args,
                config=prompts_config,
                classification_model=classification_model,
                content_generation=content_generation,
                taxonomy=taxonomy,
                logger=logger,
            )
            update_cumulative_csv(cumulative_output_path(output_dir), output_fieldnames, propid, enriched_rows)
    finally:
        logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
