"""Textbook-only multimodal OCR transport: exact attempts and no local fallback."""
from __future__ import annotations
import asyncio
import io
import unittest
from unittest.mock import patch
from PIL import Image

from app.core import ocr


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


class TestTextbookOCRAPI(unittest.TestCase):
    def setUp(self):
        ocr._textbook_client_cache.clear()

    def test_missing_config_is_blocked_without_tesseract(self):
        with patch.object(ocr.settings, "multimodal_api_key", ""), \
             patch.object(ocr, "_tesseract_ocr") as tess:
            result = asyncio.run(ocr.textbook_ocr_page_api(_png(), attempt=2))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "multimodal_not_configured")
        self.assertFalse(result.retryable)
        self.assertEqual(result.attempt, 2)
        tess.assert_not_called()

    def test_success_returns_text(self):
        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch.object(ocr, "_get_textbook_client", return_value=object()), \
             patch.object(ocr, "_vision_once", return_value="教材正文"):
            result = asyncio.run(ocr.textbook_ocr_page_api(_png()))
        self.assertTrue(result.success)
        self.assertEqual(result.text, "教材正文")

    def test_empty_is_retryable_and_no_tesseract(self):
        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch.object(ocr, "_get_textbook_client", return_value=object()), \
             patch.object(ocr, "_vision_once", return_value=""), \
             patch.object(ocr, "_tesseract_ocr") as tess:
            result = asyncio.run(ocr.textbook_ocr_page_api(_png()))
        self.assertEqual(result.error_code, "empty_content")
        self.assertTrue(result.retryable)
        tess.assert_not_called()

    def test_429_is_retryable_401_is_blocked(self):
        class APIError(RuntimeError):
            def __init__(self, status):
                super().__init__(f"status {status}")
                self.status_code = status
        for status, retryable in ((429, True), (503, True), (401, False)):
            with self.subTest(status=status), \
                 patch.object(ocr.settings, "multimodal_api_key", "k"), \
                 patch.object(ocr, "_get_textbook_client", return_value=object()), \
                 patch.object(ocr, "_vision_once", side_effect=APIError(status)), \
                 patch.object(ocr, "_tesseract_ocr") as tess:
                result = asyncio.run(ocr.textbook_ocr_page_api(_png()))
                self.assertEqual(result.retryable, retryable)
                self.assertEqual(result.http_status, status)
                tess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
