"""Async chat/workspace extraction: text short-circuit + selective OCR."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core import multimodal_parser as mp  # noqa: E402


class TestAsyncMultimodalParser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def inline(func, /, *args, **kwargs):
            return func(*args, **kwargs)
        self._thread_patch = patch.object(mp.asyncio, "to_thread", new=inline)
        self._thread_patch.start()

    async def asyncTearDown(self):
        self._thread_patch.stop()

    async def test_plain_text_never_calls_ocr(self):
        with patch.object(mp, "understand_image", new=AsyncMock()) as ocr:
            result = await mp.extract_text_async("note.txt", b"plain text")
        self.assertEqual(result.text, "plain text")
        self.assertFalse(result.used_ocr)
        ocr.assert_not_awaited()

    async def test_image_is_ocr_material(self):
        with patch.object(mp, "understand_image", new=AsyncMock(return_value="图中公式 E=mc^2")) as ocr:
            result = await mp.extract_text_async("problem.png", b"fake-image")
        self.assertTrue(result.used_ocr)
        self.assertIn("E=mc^2", result.text)
        ocr.assert_awaited_once()

    async def test_dense_pdf_skips_page_ocr(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "dense textbook text " * 20)
        raw = doc.tobytes()
        doc.close()
        with patch.object(mp, "ocr_page_image", new=AsyncMock(return_value="should not run")) as ocr:
            result = await mp.extract_text_async("dense.pdf", raw)
        self.assertFalse(result.used_ocr)
        self.assertIn("dense textbook", result.text)
        ocr.assert_not_awaited()

    async def test_mixed_pdf_ocrs_only_sparse_page(self):
        import fitz
        doc = fitz.open()
        dense = doc.new_page()
        dense.insert_text((72, 72), "dense page content " * 20)
        doc.new_page()
        raw = doc.tobytes()
        doc.close()
        with patch.object(mp, "ocr_page_image", new=AsyncMock(return_value="扫描页识别内容")) as ocr:
            result = await mp.extract_text_async("mixed.pdf", raw)
        self.assertTrue(result.used_ocr)
        self.assertIn("dense page content", result.text)
        self.assertIn("扫描页识别内容", result.text)
        self.assertEqual(result.ocr_pages, [2])
        ocr.assert_awaited_once()

    async def test_office_embedded_image_is_appended(self):
        # P7 起内嵌图走分型描述（describe_embedded_image），产出 [图|...] 标记块
        with patch.object(mp, "extract_text", return_value="正文内容"), \
                patch.object(mp, "_office_images", return_value=[b"img"]), \
                patch.object(mp, "describe_embedded_image",
                             new=AsyncMock(return_value="图述\n图片中的坐标图")):
            result = await mp.extract_text_async("lesson.docx", b"fake")
        self.assertTrue(result.used_ocr)
        self.assertIn("正文内容", result.text)
        self.assertIn("图片中的坐标图", result.text)
        self.assertIn("[图|文档图片 1]", result.text)

    async def test_office_without_media_short_circuits(self):
        with patch.object(mp, "extract_text", return_value="纯文字课件"), \
                patch.object(mp, "_office_images", return_value=[]), \
                patch.object(mp, "describe_embedded_image", new=AsyncMock()) as ocr:
            result = await mp.extract_text_async("lesson.pptx", b"fake")
        self.assertFalse(result.used_ocr)
        self.assertEqual(result.text, "纯文字课件")
        ocr.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
