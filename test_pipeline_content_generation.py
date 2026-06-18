import argparse
import json
import unittest
from unittest import mock

import pipeline


class DummyLogger:
    def log(self, record):
        pass


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class PipelineContentGenerationTests(unittest.TestCase):
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
