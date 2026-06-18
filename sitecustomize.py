"""Runtime override for pipeline content generation skips.

Python imports sitecustomize automatically from the working directory. This keeps the
pipeline entrypoint unchanged while applying a small behavior patch at import time.
"""

from __future__ import annotations

import sys
from typing import Dict, Tuple


_PATCHED_MODULE_IDS = set()
_SKIPPED_GENERATION_BY_ASSET: Dict[Tuple[str, str], Dict[str, object]] = {}


def _patch_pipeline_module(globals_dict: Dict[str, object]) -> bool:
    module_id = id(globals_dict)
    if module_id in _PATCHED_MODULE_IDS:
        return True
    if globals_dict.get("OTHER") != "Other" or "enrich_row" not in globals_dict:
        return False

    _PATCHED_MODULE_IDS.add(module_id)
    original_log = globals_dict["RunLogger"].log

    def patched_log(self, record):
        if isinstance(record, dict) and record.get("step") == "generation":
            key = (str(record.get("propid", "")), str(record.get("asset_fileid", "")))
            skip_info = _SKIPPED_GENERATION_BY_ASSET.get(key)
            if skip_info:
                record = dict(record)
                record.update(skip_info)
        return original_log(self, record)

    globals_dict["RunLogger"].log = patched_log

    def enrich_row(
        row,
        gemini_api_key,
        openrouter_api_key,
        classification_model,
        content_generation,
        config,
        taxonomy,
        timeout,
        max_retries,
        logger,
        log_context,
        debug_log,
    ):
        image_url = (row.get("Asset_Link") or "").strip()
        if not image_url:
            raise RuntimeError("Missing Asset_Link.")

        image_bytes, mime_type = globals_dict["download_image_bytes"](
            image_url, timeout=timeout, max_retries=max_retries
        )
        if mime_type == "application/octet-stream":
            guessed, _ = globals_dict["mimetypes"].guess_type(image_url)
            mime_type = guessed or "image/jpeg"

        t0 = globals_dict["time"].perf_counter()
        classification_prompt = globals_dict["build_classification_prompt"](row, taxonomy)
        classification_fallback_prompt = globals_dict["build_classification_fallback_prompt"](row, taxonomy)
        classification_error = None
        try:
            classification_result = globals_dict["call_gemini_classification"](
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
            classification_result = {"category": globals_dict["OTHER"], "score": 0.0}
            globals_dict["log_debug_event"](
                logger,
                True,
                {
                    **log_context,
                    "step": "classification_fallback_other",
                    "attempt": None,
                    "duration_ms": None,
                    "error": classification_error,
                },
            )

        classification_ms = int((globals_dict["time"].perf_counter() - t0) * 1000)
        amenity_fields = globals_dict["resolve_amenity_fields"](
            classification_result["category"], classification_result["score"], taxonomy
        )

        if amenity_fields.get("Amenity_Category") == globals_dict["OTHER"]:
            skip_info = {"skipped": True, "skip_reason": "amenity_category_other"}
            key = (str(log_context.get("propid", "")), str(log_context.get("asset_fileid", "")))
            _SKIPPED_GENERATION_BY_ASSET[key] = skip_info
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
            t1 = globals_dict["time"].perf_counter()
            generation_prompt = globals_dict["build_prompt"](row, config)
            generation_fields = globals_dict["generate_content"](
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
            generation_ms = int((globals_dict["time"].perf_counter() - t1) * 1000)

        return {
            **amenity_fields,
            **generation_fields,
            "_classification_score": classification_result["score"],
            "_classification_ms": classification_ms,
            "_generation_ms": generation_ms,
            "_classification_error": classification_error,
        }

    globals_dict["enrich_row"] = enrich_row
    return True


def _trace_pipeline_import(frame, event, arg):
    if event == "line" and str(frame.f_code.co_filename).endswith("pipeline.py"):
        if _patch_pipeline_module(frame.f_globals):
            sys.settrace(None)
            return None
    return _trace_pipeline_import


sys.settrace(_trace_pipeline_import)
