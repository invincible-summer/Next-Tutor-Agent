"""对话多模态上下文（multimodal_context）契约。

图片注入只改流式调用副本（最后一条 user 消息 parts），会话历史保持纯文本；
data URL 嗅探 PNG/JPEG；附件图加载失败静默降级。
"""
from __future__ import annotations

import unittest

from app.core import multimodal_context as mmc


class FakeStore:
    def __init__(self, files, upload_dir):
        self.files = files
        self.upload_dir = upload_dir


def _png_bytes() -> bytes:
    import fitz
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    pix.clear_with(90)
    return pix.tobytes("png")


class TestWithContextImages(unittest.TestCase):
    def test_no_images_returns_same_list(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertIs(mmc.with_context_images(msgs, []), msgs)

    def test_images_injected_into_last_user_only(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "答"},
            {"role": "user", "content": "看这张图"},
        ]
        url = "data:image/png;base64,AAAA"
        out = mmc.with_context_images(msgs, [url])
        # 原列表不被修改
        self.assertEqual(msgs[3]["content"], "看这张图")
        self.assertIsInstance(out[3]["content"], list)
        self.assertEqual(out[3]["content"][0], {"type": "text", "text": "看这张图"})
        self.assertEqual(out[3]["content"][1]["image_url"]["url"], url)
        self.assertEqual(out[0]["content"], "sys")

    def test_data_url_sniffs_png_and_jpeg_and_rejects_other(self):
        self.assertTrue(mmc._data_url(_png_bytes()).startswith("data:image/png;base64,"))
        self.assertTrue(mmc._data_url(b"\xff\xd8\xff\xe0xx").startswith("data:image/jpeg;base64,"))
        self.assertIsNone(mmc._data_url(b"GIF89a...."))
        self.assertIsNone(mmc._data_url(b"\x89PNG\r\n\x1a\n" + b"x" * (9 * 1024 * 1024)))


class TestAttachmentContextImages(unittest.TestCase):
    def test_loads_image_attachment_original(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            png = _png_bytes()
            (d / "img1.orig.png").write_bytes(png)
            store = FakeStore(
                [{"id": "img1", "filename": "photo.png", "orig_ext": ".png"}], d)
            session = type("S", (), {
                "knowledge": store,
                "messages": [{"role": "user", "content": "看图",
                              "attachments": [{"id": "img1"}]}],
            })()
            urls = mmc.attachment_context_images(session)
            self.assertEqual(len(urls), 1)
            self.assertTrue(urls[0].startswith("data:image/png;base64,"))

    def test_non_image_and_missing_attachments_skipped(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            store = FakeStore(
                [{"id": "pdf1", "filename": "notes.pdf", "orig_ext": ".pdf"}], Path(td))
            session = type("S", (), {
                "knowledge": store,
                "messages": [{"role": "user", "content": "看",
                              "attachments": [{"id": "pdf1"}, {"id": "gone"}]}],
            })()
            self.assertEqual(mmc.attachment_context_images(session), [])

    def test_no_store_returns_empty(self):
        session = type("S", (), {"messages": []})()
        self.assertEqual(mmc.attachment_context_images(session), [])


if __name__ == "__main__":
    unittest.main()
