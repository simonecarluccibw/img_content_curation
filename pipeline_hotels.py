#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import OrderedDict, defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable

AI_FIELDS = [
    "AI_Caption_Basic",
    "AI_Description_Basic",
    "AI_Caption_Experience",
    "AI_Description_Experience",
    "AI_Image_Tag",
    "AI_Alt_Text",
]
VALID_TAGS = {"tag-mare", "tag-montagna", "tag-citta"}
DEFAULT_MODEL = "gemini-2.5-flash"


class PipelineError(Exception):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-") or "hotel"


def parse_csv_list_args(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for value in values:
        parts = [p.strip() for p in value.split(",")]
        out.extend([p for p in parts if p])
    return out


def load_prompts(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise PipelineError("PyYAML is required. Install with: pip install pyyaml") from exc

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise PipelineError("prompts file must be a YAML object")
    for key in ["system", "user_template"]:
        if key not in data or not isinstance(data[key], str):
            raise PipelineError(f"prompts file missing string key: {key}")
    return data


def load_csv(input_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        if reader.fieldnames is None:
            raise PipelineError("CSV missing header")
        rows = [dict(row) for row in reader]
        fieldnames = list(reader.fieldnames)

    required = {"Listing_MappedID", "Listing_Name", "Asset_Link"}
    missing = required.difference(fieldnames)
    if missing:
        raise PipelineError(f"CSV missing required columns: {', '.join(sorted(missing))}")
    return rows, fieldnames


def build_hotel_index(rows: list[dict[str, str]]) -> OrderedDict[str, dict[str, Any]]:
    hotels: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        propid = (row.get("Listing_MappedID") or "").strip()
        name = (row.get("Listing_Name") or "").strip()
        if not propid:
            raise PipelineError("Found row with empty Listing_MappedID")

        if propid not in hotels:
            hotels[propid] = {"propid": propid, "name": name, "rows": []}
        hotels[propid]["rows"].append(row)
        if not hotels[propid]["name"] and name:
            hotels[propid]["name"] = name
    return hotels


def build_name_index(hotels: OrderedDict[str, dict[str, Any]]) -> dict[str, set[str]]:
    name_index: dict[str, set[str]] = defaultdict(set)
    for propid, hotel in hotels.items():
        normalized = hotel["name"].strip().casefold()
        if normalized:
            name_index[normalized].add(propid)
    return name_index


def output_path_for_hotel(output_dir: Path, propid: str, hotel_name: str) -> Path:
    return output_dir / f"{propid}_{slugify(hotel_name)}.csv"


def resolve_targets(
    hotels: OrderedDict[str, dict[str, Any]],
    output_dir: Path,
    propids: list[str],
    hotel_names: list[str],
    next_hotels: int | None,
    force: bool,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    name_index = build_name_index(hotels)

    def add_target(pid: str) -> None:
        if pid not in hotels:
            raise PipelineError(f"Propid '{pid}' not found in CSV")
        if pid in seen:
            return
        seen.add(pid)
        selected.append(pid)

    for pid in propids:
        add_target(pid)

    for hotel_name in hotel_names:
        norm = hotel_name.strip().casefold()
        matches = sorted(name_index.get(norm, set()))
        if not matches:
            raise PipelineError(f"Hotel name '{hotel_name}' not found")
        if len(matches) > 1:
            raise PipelineError(
                f"Hotel name '{hotel_name}' is ambiguous. Matching Propid: {', '.join(matches)}"
            )
        add_target(matches[0])

    if next_hotels:
        added_from_next = 0
        for pid, hotel in hotels.items():
            if pid in seen:
                continue
            out = output_path_for_hotel(output_dir, pid, hotel["name"])
            if out.exists() and not force:
                continue
            add_target(pid)
            added_from_next += 1
            if added_from_next >= next_hotels:
                break

    return selected


def build_gemini_payload(asset_link: str, prompts: dict[str, Any]) -> dict[str, Any]:
    user_prompt = prompts["user_template"].format(asset_link=asset_link)
    return {
        "systemInstruction": {"parts": [{"text": prompts["system"]}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.3,
        },
    }


def call_gemini(
    api_key: str,
    model: str,
    payload: dict[str, Any],
    timeout_sec: int = 60,
) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise PipelineError("requests is required. Install with: pip install requests") from exc

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(url, params={"key": api_key}, json=payload, timeout=timeout_sec)
    resp.raise_for_status()
    return resp.json()


def parse_gemini_json(data: dict[str, Any]) -> dict[str, str]:
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PipelineError("Gemini response missing text candidate") from exc

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Invalid JSON returned by Gemini: {text[:200]}") from exc

    missing = [f for f in AI_FIELDS if f not in obj]
    if missing:
        raise PipelineError(f"Gemini JSON missing fields: {', '.join(missing)}")

    out = {field: str(obj[field]).strip() for field in AI_FIELDS}
    if out["AI_Image_Tag"] not in VALID_TAGS:
        raise PipelineError(
            f"Invalid AI_Image_Tag '{out['AI_Image_Tag']}', expected one of: {', '.join(sorted(VALID_TAGS))}"
        )
    return out


def generate_ai_fields(
    asset_link: str,
    prompts: dict[str, Any],
    api_key: str,
    model: str,
    retries: int = 3,
    backoff: float = 1.5,
) -> dict[str, str]:
    payload = build_gemini_payload(asset_link, prompts)

    for attempt in range(1, retries + 1):
        try:
            response = call_gemini(api_key, model, payload)
            return parse_gemini_json(response)
        except Exception as exc:  # noqa: BLE001
            if attempt == retries:
                raise PipelineError(f"Gemini call failed after {retries} attempts: {exc}") from exc
            sleep_for = backoff ** attempt
            time.sleep(sleep_for)
    raise PipelineError("Unreachable retry state")


def atomic_write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=path.parent) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(tmp.name)
    temp_path.replace(path)


def process_hotel(
    hotel: dict[str, Any],
    output_path: Path,
    fieldnames: list[str],
    ai_func: Callable[[str], dict[str, str]],
    force: bool,
) -> tuple[str, int]:
    if output_path.exists() and not force:
        log(f"[SKIP] {hotel['propid']} - {hotel['name']} (already exists)")
        return "skipped", 0

    log(f"[START] {hotel['propid']} - {hotel['name']} ({len(hotel['rows'])} rows)")
    processed_rows: list[dict[str, str]] = []
    errors = 0

    for idx, row in enumerate(hotel["rows"], start=1):
        row_out = dict(row)
        asset_link = (row.get("Asset_Link") or "").strip()
        try:
            ai_values = ai_func(asset_link)
            row_out.update(ai_values)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log(f"[ROW-ERROR] {hotel['propid']} row {idx}: {exc}")
            for field in AI_FIELDS:
                row_out[field] = ""
        processed_rows.append(row_out)

    out_fields = fieldnames + [f for f in AI_FIELDS if f not in fieldnames]
    atomic_write_csv(output_path, processed_rows, out_fields)
    log(f"[DONE] {hotel['propid']} - {hotel['name']} (errors: {errors}) -> {output_path}")
    return "done", errors


def read_hotel_names_file(path: Path) -> list[str]:
    names: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                names.append(stripped)
    return names


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch pipeline per hotel from source CSV images using Gemini 2.5 Flash."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input CSV file (; separated)")
    parser.add_argument("--output-dir", type=Path, default=Path("output_hotels"))
    parser.add_argument("--prompts", type=Path, default=Path("prompts.yaml"))
    parser.add_argument("--propid", action="append", help="Single propid or comma-separated list")
    parser.add_argument("--hotel-name", action="append", help="Single hotel name or comma-separated list")
    parser.add_argument("--hotel-name-file", type=Path, help="File containing hotel names (one per line)")
    parser.add_argument("--next-hotels", type=int, help="Process next N not-yet-exported hotels")
    parser.add_argument("--force", action="store_true", help="Regenerate hotel files even if they exist")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if not args.propid and not args.hotel_name and not args.hotel_name_file and not args.next_hotels:
        raise PipelineError(
            "Select target hotels with --propid, --hotel-name, --hotel-name-file, and/or --next-hotels"
        )

    rows, fieldnames = load_csv(args.input)
    hotels = build_hotel_index(rows)
    prompts = load_prompts(args.prompts)

    propids = parse_csv_list_args(args.propid)
    hotel_names = args.hotel_name or []
    if args.hotel_name_file:
        hotel_names.extend(read_hotel_names_file(args.hotel_name_file))

    targets = resolve_targets(
        hotels=hotels,
        output_dir=args.output_dir,
        propids=propids,
        hotel_names=hotel_names,
        next_hotels=args.next_hotels,
        force=args.force,
    )

    if not targets:
        log("No hotels selected after filtering.")
        return 0

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise PipelineError("Environment variable GEMINI_API_KEY is required")

    total_errors = 0
    for pid in targets:
        hotel = hotels[pid]
        output = output_path_for_hotel(args.output_dir, pid, hotel["name"])
        _, errors = process_hotel(
            hotel,
            output,
            fieldnames,
            ai_func=lambda link: generate_ai_fields(link, prompts, api_key=api_key, model=args.model),
            force=args.force,
        )
        total_errors += errors

    log(f"Pipeline completed. Hotels selected: {len(targets)}. Row errors: {total_errors}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        log(f"ERROR: {exc}")
        raise SystemExit(1)
