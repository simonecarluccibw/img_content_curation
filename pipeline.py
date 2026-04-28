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


AI_COLUMNS = [
    "AI_Caption_Basic",
    "AI_Description_Basic",
    "AI_Caption_Experience",
    "AI_Description_Experience",
    "AI_Image_Tag",
    "AI_Alt_Text",
]
ALLOWED_TAGS = {"tag-mare", "tag-montagna", "tag-citta"}
AMENITY_COLUMNS = [
    "AI_Amenity_Category",
    "AI_Amenity_Codes",
    "AI_Amenity_Maxcategoria",
    "AI_Amenity_CustomTag1",
    "AI_Amenity_CustomTag2",
    "AI_Amenity_CustomTag3",
    "AI_Amenity_CustomTag4",
]
CLASSIFICATION_SCORE_THRESHOLD = 0.4
ALTRO = "Altro"


class RunLogger:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._path = log_dir / f"pipeline_{timestamp}.log"
        self._handle = self._path.open("w", encoding="utf-8")

    def log(self, record: Dict) -> None:
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


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
        "--model",
        default=None,
        help="Override the model from the prompts file.",
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
    parser.add_argument(
        "--hotel-name-file",
        help="Text file containing one hotel name per line.",
    )
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
    args = parser.parse_args()
    if not args.api_key and not args.dry_run:
        parser.error("Missing Gemini API key. Set --api-key or GEMINI_API_KEY.")
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
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    texts = [part.get("text", "") for part in parts if "text" in part]
    raw = "".join(texts).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()
    if not raw:
        raise RuntimeError("Gemini returned an empty text payload.")
    return raw


def extract_first_json_object(raw_text: str) -> str:
    start = raw_text.find("{")
    if start == -1:
        raise RuntimeError(f"Gemini returned no JSON object: {raw_text[:200]}")
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
    raise RuntimeError(f"Gemini returned an unterminated JSON object: {raw_text[:200]}")


def coerce_tag(value: str) -> str:
    cleaned = (value or "").strip().casefold()
    if cleaned in ALLOWED_TAGS:
        return cleaned
    if any(token in cleaned for token in ("mare", "sea", "beach", "coast", "ocean")):
        return "tag-mare"
    if any(token in cleaned for token in ("mont", "mount", "alps", "ski", "hill")):
        return "tag-montagna"
    return "tag-citta"


def build_prompt(row: Dict[str, str], config: Dict) -> str:
    tone = config.get("tone_of_voice", "").strip()
    style = config.get("style_rules", {})
    limits = config.get("length_limits", {})
    tags = ", ".join(config.get("tag_values", sorted(ALLOWED_TAGS)))
    context_lines = [
        f"Hotel name: {row.get('Listing_Name', '').strip()}",
        f"Brand: {row.get('Listing_Brand', '').strip()}",
        f"Existing caption: {row.get('Asset_Caption', '').strip()}",
        f"Media type: {row.get('Asset_MediaType', '').strip()}",
        f"Image index: {row.get('Asset_Index', '').strip()}",
    ]
    return (
        "You are generating hotel image metadata in English for hospitality distribution.\n"
        f"Tone of voice: {tone}\n"
        f"Basic caption style: {style.get('basic_caption', '')}\n"
        f"Basic description style: {style.get('basic_description', '')}\n"
        f"Experience caption style: {style.get('experience_caption', '')}\n"
        f"Experience description style: {style.get('experience_description', '')}\n"
        f"Alt text style: {style.get('alt_text', '')}\n"
        f"Caption max words: {limits.get('caption_max_words', 14)}\n"
        f"Description max words: {limits.get('description_max_words', 36)}\n"
        f"Alt text max words: {limits.get('alt_text_max_words', 18)}\n"
        f"Allowed tags: {tags}\n"
        "Rules:\n"
        "- Describe only what is visible in the image.\n"
        "- Avoid unverifiable claims, room types, amenities, or views unless clearly visible.\n"
        "- Keep the basic outputs factual and commercially neutral.\n"
        "- Keep the experience outputs more evocative, but still grounded in the visual content.\n"
        "- Alt text must be accessible, concise, and non-promotional.\n"
        "- Always return exactly one allowed tag, even for indoor images.\n"
        "- Return valid JSON only.\n"
        "Return an object with exactly these keys:\n"
        'AI_Caption_Basic, AI_Description_Basic, AI_Caption_Experience, AI_Description_Experience, AI_Image_Tag, AI_Alt_Text.\n'
        "Image context:\n"
        + "\n".join(context_lines)
    )


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
        f"- If no category fits with confidence >= {CLASSIFICATION_SCORE_THRESHOLD}, return Altro.\n"
        "- Return valid JSON only, no explanation, no markdown.\n"
        'Return exactly: {"category": "<category name or Altro>", "score": <float 0.0-1.0>}'
    )


def call_gemini_classification(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    api_key: str,
    model: str,
    config: Dict,
    timeout: int,
    max_retries: int,
) -> Dict:
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": config.get("classification", {}).get("temperature", 0.1),
            "maxOutputTokens": config.get("classification", {}).get("max_output_tokens", 100),
            "responseMimeType": "application/json",
        },
    }
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                parsed = json.loads(extract_first_json_object(extract_json_text(response_data)))
                category = str(parsed.get("category", ALTRO)).strip()
                score = float(parsed.get("score", 0.0))
                return {"category": category, "score": score}
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2.0 ** attempt, 60) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            if attempt == max_retries:
                break
            time.sleep(min(2.0 ** attempt, 60) + random.uniform(0, 1))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            time.sleep(min(2.0 ** attempt, 60) + random.uniform(0, 1))
    raise RuntimeError(f"Classification failed after {max_retries} attempts: {last_error}")


def call_gemini(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    api_key: str,
    model: str,
    config: Dict,
    timeout: int,
    max_retries: int,
) -> Dict[str, str]:
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": config.get("generation", {}).get("temperature", 0.3),
            "maxOutputTokens": config.get("generation", {}).get("max_output_tokens", 500),
            "responseMimeType": "application/json",
        },
    }
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                parsed = json.loads(extract_first_json_object(extract_json_text(response_data)))
                return {
                    "AI_Caption_Basic": str(parsed.get("AI_Caption_Basic", "")).strip(),
                    "AI_Description_Basic": str(parsed.get("AI_Description_Basic", "")).strip(),
                    "AI_Caption_Experience": str(parsed.get("AI_Caption_Experience", "")).strip(),
                    "AI_Description_Experience": str(parsed.get("AI_Description_Experience", "")).strip(),
                    "AI_Image_Tag": coerce_tag(str(parsed.get("AI_Image_Tag", ""))),
                    "AI_Alt_Text": str(parsed.get("AI_Alt_Text", "")).strip(),
                }
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2.0 ** attempt, 60) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            if attempt == max_retries:
                break
            time.sleep(min(2.0 ** attempt, 60) + random.uniform(0, 1))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            time.sleep(min(2.0 ** attempt, 60) + random.uniform(0, 1))
    raise RuntimeError(f"Generation failed after {max_retries} attempts: {last_error}")


def resolve_amenity_fields(
    category: str,
    score: float,
    taxonomy: List[Dict],
) -> Dict[str, str]:
    empty = {col: "" for col in AMENITY_COLUMNS}
    if score < CLASSIFICATION_SCORE_THRESHOLD or category == ALTRO:
        empty["AI_Amenity_Category"] = ALTRO
        return empty
    for entry in taxonomy:
        if entry["category"] == category:
            return {
                "AI_Amenity_Category": entry["category"],
                "AI_Amenity_Codes": entry["codes"],
                "AI_Amenity_Maxcategoria": entry["maxcategoria"],
                "AI_Amenity_CustomTag1": entry["custom_tag_1"],
                "AI_Amenity_CustomTag2": entry["custom_tag_2"],
                "AI_Amenity_CustomTag3": entry["custom_tag_3"],
                "AI_Amenity_CustomTag4": entry["custom_tag_4"],
            }
    empty["AI_Amenity_Category"] = ALTRO
    return empty


def enrich_row(
    row: Dict[str, str],
    api_key: str,
    model: str,
    config: Dict,
    taxonomy: List[Dict],
    timeout: int,
    max_retries: int,
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
    classification_result = call_gemini_classification(
        image_bytes=image_bytes,
        mime_type=mime_type,
        prompt=classification_prompt,
        api_key=api_key,
        model=model,
        config=config,
        timeout=timeout,
        max_retries=max_retries,
    )
    classification_ms = int((time.perf_counter() - t0) * 1000)
    amenity_fields = resolve_amenity_fields(
        classification_result["category"],
        classification_result["score"],
        taxonomy,
    )

    t1 = time.perf_counter()
    generation_prompt = build_prompt(row, config)
    generation_fields = call_gemini(
        image_bytes=image_bytes,
        mime_type=mime_type,
        prompt=generation_prompt,
        api_key=api_key,
        model=model,
        config=config,
        timeout=timeout,
        max_retries=max_retries,
    )
    generation_ms = int((time.perf_counter() - t1) * 1000)

    return {
        **amenity_fields,
        **generation_fields,
        "_classification_score": classification_result["score"],
        "_classification_ms": classification_ms,
        "_generation_ms": generation_ms,
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
                checkpoint[record["asset_fileid"]] = record["enriched_row"]
    return checkpoint


def append_checkpoint(sidecar: Path, asset_fileid: str, enriched_row: Dict) -> None:
    with sidecar.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"asset_fileid": asset_fileid, "enriched_row": enriched_row}, ensure_ascii=False) + "\n"
        )
        handle.flush()


def write_hotel_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=path.parent,
        suffix=".tmp",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def process_hotel(
    propid: str,
    rows: List[Dict[str, str]],
    hotel_name: str,
    output_path: Path,
    fieldnames: List[str],
    args: argparse.Namespace,
    config: Dict,
    model: str,
    taxonomy: List[Dict],
    logger: RunLogger,
) -> None:
    print(f"[START] {propid} | {hotel_name} | {len(rows)} images")
    sidecar = sidecar_path(output_path)
    checkpoint = load_checkpoint(sidecar)

    try:
        from tqdm import tqdm
        iterator = tqdm(rows, desc=propid, unit="img")
    except ImportError:
        iterator = rows

    enriched_rows: List[Dict[str, str]] = []

    for index, row in enumerate(iterator, start=1):
        asset_fileid = row.get("Asset_FileID", "")
        enriched = dict(row)

        if asset_fileid and asset_fileid in checkpoint:
            enriched_rows.append(checkpoint[asset_fileid])
            continue

        try:
            ai_values = enrich_row(
                row=row,
                api_key=args.api_key,
                model=model,
                config=config,
                taxonomy=taxonomy,
                timeout=args.timeout,
                max_retries=args.max_retries,
            )
            score = ai_values.pop("_classification_score", None)
            classification_ms = ai_values.pop("_classification_ms", 0)
            generation_ms = ai_values.pop("_generation_ms", 0)
            enriched.update(ai_values)

            logger.log({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "propid": propid,
                "hotel_name": hotel_name,
                "asset_fileid": asset_fileid,
                "asset_index": row.get("Asset_Index", ""),
                "asset_caption": row.get("Asset_Caption", ""),
                "asset_link": row.get("Asset_Link", ""),
                "step": "classification",
                "ai_amenity_category": enriched.get("AI_Amenity_Category"),
                "ai_amenity_score": score,
                "duration_ms": classification_ms,
                "error": None,
            })
            logger.log({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "propid": propid,
                "hotel_name": hotel_name,
                "asset_fileid": asset_fileid,
                "asset_index": row.get("Asset_Index", ""),
                "asset_caption": row.get("Asset_Caption", ""),
                "asset_link": row.get("Asset_Link", ""),
                "step": "generation",
                "AI_Caption_Basic": enriched.get("AI_Caption_Basic"),
                "AI_Description_Basic": enriched.get("AI_Description_Basic"),
                "AI_Caption_Experience": enriched.get("AI_Caption_Experience"),
                "AI_Description_Experience": enriched.get("AI_Description_Experience"),
                "AI_Image_Tag": enriched.get("AI_Image_Tag"),
                "AI_Alt_Text": enriched.get("AI_Alt_Text"),
                "duration_ms": generation_ms,
                "error": None,
            })
        except Exception as exc:
            print(
                f"[ROW ERROR] {propid} image {index}/{len(rows)} | Asset_FileID={asset_fileid} | {exc}",
                file=sys.stderr,
            )
            logger.log({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "propid": propid,
                "hotel_name": hotel_name,
                "asset_fileid": asset_fileid,
                "asset_index": row.get("Asset_Index", ""),
                "asset_caption": row.get("Asset_Caption", ""),
                "asset_link": row.get("Asset_Link", ""),
                "step": "error",
                "ai_amenity_category": None,
                "ai_amenity_score": None,
                "duration_ms": None,
                "error": str(exc),
            })
            for col in AMENITY_COLUMNS:
                enriched[col] = ""
            enriched["AI_Amenity_Category"] = ALTRO
            for col in AI_COLUMNS:
                enriched[col] = ""

        enriched_rows.append(enriched)
        if asset_fileid:
            append_checkpoint(sidecar, asset_fileid, enriched)

        if args.request_delay:
            time.sleep(args.request_delay)

    write_hotel_csv(output_path, fieldnames, enriched_rows)
    sidecar.unlink(missing_ok=True)
    print(f"[DONE] {propid} | wrote {output_path}")


def run_dry(
    selected_propids: List[str],
    groups: Dict[str, List[Dict]],
    hotel_names: Dict[str, str],
    prompts_config: Dict,
) -> int:
    cost_per_call = prompts_config.get("cost_per_call_usd", 0.000125)
    total_images = 0
    print("\n[DRY RUN] Estimate summary:")
    print(f"{'Propid':<12} {'Hotel':<50} {'Images':>8} {'API calls':>10} {'Est. cost $':>12}")
    print("-" * 96)
    for propid in selected_propids:
        n = len(groups[propid])
        calls = n * 2
        cost = calls * cost_per_call
        total_images += n
        print(f"{propid:<12} {hotel_names[propid][:50]:<50} {n:>8} {calls:>10} {cost:>12.4f}")
    total_calls = total_images * 2
    total_cost = total_calls * cost_per_call
    print("-" * 96)
    print(f"{'TOTAL':<12} {'':<50} {total_images:>8} {total_calls:>10} {total_cost:>12.4f}\n")
    return 0


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    prompts_path = Path(args.prompts)
    output_dir = Path(args.output_dir)
    prompts_config = load_prompts(prompts_path)
    model = args.model or prompts_config.get("model", "gemini-2.5-flash")
    taxonomy = prompts_config.get("amenity_taxonomy", [])

    rows, original_fieldnames = read_rows(input_path)
    groups, hotel_names, name_to_propids = build_indexes(rows)
    selected_propids = resolve_hotels(args, groups, hotel_names, name_to_propids, output_dir)
    output_fieldnames = original_fieldnames + [
        col for col in AMENITY_COLUMNS + AI_COLUMNS
        if col not in original_fieldnames
    ]
    if args.dry_run:
        return run_dry(selected_propids, groups, hotel_names, prompts_config)

    logger = RunLogger(Path(args.log_dir))
    try:
        for propid in selected_propids:
            hotel_name = hotel_names[propid]
            output_path = expected_output_path(output_dir, propid, hotel_name)
            if output_path.exists() and not args.force:
                print(f"[SKIP] {propid} | {hotel_name} | output exists: {output_path}")
                continue
            process_hotel(
                propid=propid,
                rows=groups[propid],
                hotel_name=hotel_name,
                output_path=output_path,
                fieldnames=output_fieldnames,
                args=args,
                config=prompts_config,
                model=model,
                taxonomy=taxonomy,
                logger=logger,
            )
    finally:
        logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
