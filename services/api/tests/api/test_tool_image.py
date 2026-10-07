"""Provider request contracts for the V4 image gateway.

These tests are deliberately offline: they lock the provider-specific wire
shapes without requiring API keys or making paid model calls.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.tool_image import (  # noqa: E402
    GenerateImageRequest,
    _image_from_payload,
    _openai_generation_payload,
    _openai_edit_payload,
    _qwen_payload,
    _seed_payload,
    active_image_config,
)
from app.core.config import settings  # noqa: E402


class ToolImageProviderContractTests(unittest.TestCase):
    def _body(self, provider: str, model: str, **kwargs):
        return GenerateImageRequest(provider=provider, model=model, prompt="画一张教学示意图", **kwargs)

    def test_openai_generation_uses_images_generation_json(self):
        body = self._body("gpt-image", "gpt-image-1", aspect_ratio="16:9")
        payload = _openai_generation_payload(body, body.prompt)
        self.assertEqual(payload["size"], "1536x1024")
        self.assertEqual(payload["n"], 1)
        self.assertEqual(payload["output_format"], "png")

    def test_openai_reference_uses_json_image_url_array(self):
        body = self._body("gpt-image", "gpt-image-1", reference_images=["data:image/png;base64,YQ=="])
        payload = _openai_edit_payload(body, body.prompt)
        self.assertEqual(payload["input_fidelity"], "high")
        self.assertEqual(payload["images"], [{"image_url": body.reference_images[0]}])

    def test_qwen_async_message_payload_uses_star_size(self):
        body = self._body("qwen-image", "qwen-image-3.0-pro", reference_images=["data:image/png;base64,YQ=="])
        payload = _qwen_payload(body, body.prompt)
        content = payload["input"]["messages"][0]["content"]
        self.assertEqual(payload["parameters"]["size"], "1536*864")
        self.assertEqual(content[0]["image"], body.reference_images[0])
        self.assertEqual(content[-1]["text"], body.prompt)

    def test_seedream_uses_image_field_and_no_n(self):
        body = self._body("seed", "doubao-seedream-4-0-250828", reference_images=["data:image/png;base64,YQ==", "data:image/png;base64,Yg=="])
        payload = _seed_payload(body, body.prompt)
        self.assertEqual(payload["image"], body.reference_images)
        self.assertNotIn("n", payload)
        self.assertEqual(payload["sequential_image_generation"], "disabled")

    def test_seedream_5_pro_omits_unsupported_sequence_flag(self):
        body = self._body("seed", "doubao-seedream-5-0-pro-260628")
        payload = _seed_payload(body, body.prompt)
        self.assertNotIn("sequential_image_generation", payload)

    def test_qwen_choices_response_is_normalized(self):
        value, _ = _image_from_payload("qwen-image", {"output": {"choices": [{"message": {"content": [{"image": "https://example.test/result.png"}]}}]}})
        self.assertEqual(value, "https://example.test/result.png")

    def test_third_party_nested_image_url_response_is_normalized(self):
        value, _ = _image_from_payload("custom", {"images": [{"image_url": {"url": "data:image/png;base64,YQ=="}}]})
        self.assertEqual(value, "data:image/png;base64,YQ==")

    def test_arbitrary_server_model_is_forwarded_without_catalog(self):
        body = self._body("custom", "vendor/new-image-model-2026", aspect_ratio="1:1")
        payload = _openai_generation_payload(body, body.prompt)
        self.assertEqual(payload["model"], "vendor/new-image-model-2026")

    def test_unified_config_exposes_server_selected_model(self):
        with patch.object(settings, "image_api_enabled", True), \
             patch.object(settings, "image_api_provider", "acme"), \
             patch.object(settings, "image_api_protocol", "openai_compatible"), \
             patch.object(settings, "image_api_base_url", "https://img.example/v1"), \
             patch.object(settings, "image_api_key", "secret"), \
             patch.object(settings, "image_api_model", "acme-image-9"):
            config = active_image_config()
        self.assertTrue(config.configured)
        self.assertEqual(config.model, "acme-image-9")


if __name__ == "__main__":
    unittest.main()
