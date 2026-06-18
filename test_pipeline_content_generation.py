import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pipeline


class DummyLogger:
    def log(self, record):
        pass


class CaptureLogger:
    def __init__(self):
        self.records = []

    def log(self, record):
        self.records.append(dict(record))


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


VALID_TAXONOMY = [{
    "category": "Pool",
    "keywords": "Use for visible hotel pools.",
    "codes": "3",
    "maxcategoria": "Pool",
    "custom_tag_1": "overview-pool",
    "custom_tag_2": "amenity-pool",
    "custom_tag_3": "experience-pool-N",
    "custom_tag_4": "",
}]

CONTENT_GENERATION = {
    "provider": "openrouter",
    "model": "openai/gpt-4o-mini",
    "temperature": 1.0,
    "max_tokens": 500,
}

GENERATED_CONTENT = {
    "Caption_Experience": "By the water.",
    "Description_Experience": "A bright pool moment with room to unwind.",
    "Alt_Text": "Outdoor hotel pool",
    "Check_Room": "0",
    "_generation_provider": "openrouter",
    "_generation_model": "openai/gpt-4o-mini",
}


class PipelineContentGenerationTests(unittest.TestCase):
    def call_enrich_with_classification(self, classification_result):
        with mock.patch("pipeline.download_image_bytes", return_value=(b"fake-image", "image/jpeg")), \
             mock.patch("pipeline.call_gemini_classification", return_value=classification_result), \
             mock.patch("pipeline.generate_content", return_value=dict(GENERATED_CONTENT)) as generate_content:
            result = pipeline.enrich_row(
                row={"Asset_Link": "https://example.com/image.jpg", "Listing_Name": "Test Hotel"},
                gemini_api_key="gemini-key",
                openrouter_api_key="openrouter-key",
                classification_model="gemini-3.1-flash-lite",
                content_generation=dict(CONTENT_GENERATION),
                config={},
                taxonomy=VALID_TAXONOMY,
                timeout=10,
                max_retries=1,
                logger=DummyLogger(),
                log_context={"propid": "77519", "asset_fileid": "asset-1"},
                debug_log=False,
            )
        return result, generate_content

    def test_content_generation_config_uses_openrouter_model_from_prompts(self):
        config = {
            "content_generation": {
                "provider": "openrouter",
                "model": "openai/gpt-4o",
                "temperature": 1.0,
                "max_tokens": 500,
            }
        }

        result = pipeline.get_content_generation_config(config, "gemini-3.1-flash-lite")

        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(result["model"], "openai/gpt-4o")
        self.assertEqual(result["temperature"], 1.0)
        self.assertEqual(result["max_tokens"], 500)

    def test_content_generation_config_supports_gemini_provider(self):
        config = {
            "content_generation": {
                "provider": "gemini",
                "model": "gemini-3.1-flash-lite",
            },
            "generation": {"temperature": 0.7, "max_output_tokens": 300},
        }

        result = pipeline.get_content_generation_config(config, "gemini-default")

        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["model"], "gemini-3.1-flash-lite")
        self.assertEqual(result["temperature"], 0.7)
        self.assertEqual(result["max_tokens"], 300)

    def test_missing_openrouter_key_errors_only_for_openrouter_provider(self):
        args = argparse.Namespace(dry_run=False, api_key="gemini-key", openrouter_api_key=None)

        with self.assertRaisesRegex(RuntimeError, "Missing OpenRouter API key"):
            pipeline.validate_runtime_keys(args, {"provider": "openrouter"})

        pipeline.validate_runtime_keys(args, {"provider": "gemini"})

    def test_openrouter_payload_contains_base64_image_and_parses_json(self):
        captured = {}
        response_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "Caption_Experience": "Evening light, gently held.",
                            "Description_Experience": "A calm poolside mood shaped by soft light and open air.",
                            "Alt_Text": "Outdoor pool at dusk",
                            "Check_Room": 0,
                        })
                    }
                }
            ]
        }

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["authorization"] = request.headers.get("Authorization")
            return DummyResponse(response_payload)

        content_generation = {
            "provider": "openrouter",
            "model": "openai/gpt-4o",
            "temperature": 1.0,
            "max_tokens": 500,
        }

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = pipeline.call_openrouter_generation(
                image_bytes=b"fake-image",
                mime_type="image/jpeg",
                prompt="Return JSON.",
                api_key="openrouter-key",
                content_generation=content_generation,
                timeout=12,
                max_retries=1,
                logger=DummyLogger(),
                log_context={"propid": "77519"},
                debug_log=True,
            )

        self.assertEqual(result["Caption_Experience"], "Evening light, gently held.")
        self.assertEqual(result["Description_Experience"], "A calm poolside mood shaped by soft light and open air.")
        self.assertEqual(result["Alt_Text"], "Outdoor pool at dusk")
        self.assertEqual(result["Check_Room"], "0")
        self.assertEqual(captured["authorization"], "Bearer openrouter-key")
        self.assertEqual(captured["payload"]["model"], "openai/gpt-4o")
        image_part = captured["payload"]["messages"][0]["content"][1]
        self.assertEqual(image_part["type"], "image_url")
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_enrich_row_skips_content_generation_for_other_category(self):
        result, generate_content = self.call_enrich_with_classification({"category": pipeline.OTHER, "score": 0.95})

        generate_content.assert_not_called()
        self.assertEqual(result["Amenity_Category"], pipeline.OTHER)
        self.assertEqual(result["Caption_Experience"], "")
        self.assertEqual(result["Description_Experience"], "")
        self.assertEqual(result["Alt_Text"], "")
        self.assertEqual(result["Check_Room"], "0")
        self.assertEqual(result["_generation_ms"], 0)
        self.assertTrue(result["_generation_skipped"])
        self.assertEqual(result["_generation_skip_reason"], "amenity_category_other")

    def test_enrich_row_skips_content_generation_for_low_score_category(self):
        result, generate_content = self.call_enrich_with_classification({"category": "Pool", "score": 0.1})

        generate_content.assert_not_called()
        self.assertEqual(result["Amenity_Category"], pipeline.OTHER)
        self.assertEqual(result["Caption_Experience"], "")
        self.assertEqual(result["Description_Experience"], "")
        self.assertEqual(result["Alt_Text"], "")
        self.assertEqual(result["Check_Room"], "0")

    def test_enrich_row_keeps_content_generation_for_valid_category(self):
        result, generate_content = self.call_enrich_with_classification({"category": "Pool", "score": 0.9})

        generate_content.assert_called_once()
        self.assertEqual(result["Amenity_Category"], "Pool")
        self.assertEqual(result["Caption_Experience"], GENERATED_CONTENT["Caption_Experience"])
        self.assertEqual(result["Description_Experience"], GENERATED_CONTENT["Description_Experience"])
        self.assertEqual(result["Alt_Text"], GENERATED_CONTENT["Alt_Text"])

    def test_process_single_image_logs_skipped_generation_for_other(self):
        args = argparse.Namespace(
            api_key="gemini-key",
            openrouter_api_key="openrouter-key",
            timeout=10,
            max_retries=1,
            debug_log=False,
            request_delay=0,
        )

        with tempfile.TemporaryDirectory() as log_dir:
            logger = pipeline.RunLogger(Path(log_dir))
            with mock.patch("pipeline.download_image_bytes", return_value=(b"fake-image", "image/jpeg")), \
                 mock.patch("pipeline.call_gemini_classification", return_value={"category": pipeline.OTHER, "score": 0.95}), \
                 mock.patch("pipeline.generate_content") as generate_content, \
                 mock.patch("pipeline.append_checkpoint"):
                result = pipeline._process_single_image(
                    index=1,
                    total=1,
                    row={"Asset_FileID": "asset-1", "Asset_Link": "https://example.com/image.jpg"},
                    propid="77519",
                    hotel_name="Test Hotel",
                    args=args,
                    config={},
                    classification_model="gemini-3.1-flash-lite",
                    content_generation=dict(CONTENT_GENERATION),
                    taxonomy=VALID_TAXONOMY,
                    logger=logger,
                    sidecar=Path("unused.progress.jsonl"),
                    checkpoint_lock=mock.Mock(),
                )
            log_path = logger.path
            logger.close()
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        generate_content.assert_not_called()
        self.assertEqual(result["Amenity_Category"], pipeline.OTHER)
        generation_logs = [record for record in records if record.get("step") == "generation"]
        self.assertEqual(len(generation_logs), 1)
        self.assertTrue(generation_logs[0]["skipped"])
        self.assertEqual(generation_logs[0]["skip_reason"], "amenity_category_other")

    def test_numbered_custom_tags_increment_for_same_template(self):
        rows = [
            {"Amenity_CustomTag1": "spa-attribute-N"},
            {"Amenity_CustomTag1": "spa-attribute-N"},
        ]

        numbered = pipeline.number_custom_tag_placeholders_for_hotel(rows)

        self.assertEqual(numbered[0]["Amenity_CustomTag1"], "spa-attribute-1")
        self.assertEqual(numbered[1]["Amenity_CustomTag1"], "spa-attribute-2")

    def test_numbered_custom_tags_reset_for_each_hotel_call(self):
        first_hotel = pipeline.number_custom_tag_placeholders_for_hotel([
            {"Amenity_CustomTag1": "spa-attribute-N"},
        ])
        second_hotel = pipeline.number_custom_tag_placeholders_for_hotel([
            {"Amenity_CustomTag1": "spa-attribute-N"},
        ])

        self.assertEqual(first_hotel[0]["Amenity_CustomTag1"], "spa-attribute-1")
        self.assertEqual(second_hotel[0]["Amenity_CustomTag1"], "spa-attribute-1")

    def test_numbered_custom_tags_keep_separate_template_counters(self):
        rows = [
            {"Amenity_CustomTag1": "spa-attribute-N"},
            {"Amenity_CustomTag1": "experience-spa-N"},
            {"Amenity_CustomTag1": "spa-attribute-N"},
            {"Amenity_CustomTag1": "experience-spa-N"},
        ]

        numbered = pipeline.number_custom_tag_placeholders_for_hotel(rows)

        self.assertEqual(numbered[0]["Amenity_CustomTag1"], "spa-attribute-1")
        self.assertEqual(numbered[1]["Amenity_CustomTag1"], "experience-spa-1")
        self.assertEqual(numbered[2]["Amenity_CustomTag1"], "spa-attribute-2")
        self.assertEqual(numbered[3]["Amenity_CustomTag1"], "experience-spa-2")

    def test_numbered_custom_tags_use_same_index_for_multiple_templates_in_row(self):
        rows = [
            {"Amenity_CustomTag1": "experience-spa-N"},
            {"Amenity_CustomTag1": "experience-spa-N"},
            {"Amenity_CustomTag1": "experience-spa-N", "Amenity_CustomTag4": "spa-attribute-N"},
            {"Amenity_CustomTag1": "spa-attribute-N"},
        ]

        numbered = pipeline.number_custom_tag_placeholders_for_hotel(rows)

        self.assertEqual(numbered[0]["Amenity_CustomTag1"], "experience-spa-1")
        self.assertEqual(numbered[1]["Amenity_CustomTag1"], "experience-spa-2")
        self.assertEqual(numbered[2]["Amenity_CustomTag1"], "experience-spa-3")
        self.assertEqual(numbered[2]["Amenity_CustomTag4"], "spa-attribute-3")
        self.assertEqual(numbered[3]["Amenity_CustomTag1"], "spa-attribute-4")

    def test_numbered_custom_tags_regenerate_custom_tags_union(self):
        rows = [{
            "Amenity_CustomTag1": "overview-spa",
            "Amenity_CustomTag2": "amenity-spa",
            "Amenity_CustomTag3": "experience-spa-N",
            "Amenity_CustomTag4": "spa-attribute-N",
            "Amenity_CustomTags": "stale-value",
        }]

        numbered = pipeline.number_custom_tag_placeholders_for_hotel(rows)

        self.assertEqual(
            numbered[0]["Amenity_CustomTags"],
            "overview-spa, amenity-spa, experience-spa-1, spa-attribute-1",
        )

    def test_numbered_custom_tags_leave_non_placeholder_tags_unchanged(self):
        rows = [{
            "Amenity_CustomTag1": "amenity-spa",
            "Amenity_CustomTag2": "spa-attribute-7",
            "Amenity_CustomTag3": "spa-attribute-N-extra",
        }]

        numbered = pipeline.number_custom_tag_placeholders_for_hotel(rows)

        self.assertEqual(numbered[0]["Amenity_CustomTag1"], "amenity-spa")
        self.assertEqual(numbered[0]["Amenity_CustomTag2"], "spa-attribute-7")
        self.assertEqual(numbered[0]["Amenity_CustomTag3"], "spa-attribute-N-extra")
        self.assertEqual(numbered[0]["Amenity_CustomTags"], "amenity-spa, spa-attribute-7, spa-attribute-N-extra")


if __name__ == "__main__":
    unittest.main()
