"""扫描版 PDF OCR 回退测试：pdf_ocr 判定/逐页 OCR + 教材库后台 OCR 管线 + 同步回退。

验收（plan §验证）：
- is_scanned_pdf：文本层满→False；空→True；混合→True。
- ocr_pdf_pages / ocr_pdf_pages_sync：逐页渲染+OCR 顺序拼接；max_pages 截断；on_progress 回调。
- 教材库端到端：扫描 PDF 上传→building/ocr→后台 OCR 写回 .txt→图谱 ready。
- 同步回退：扫描小 PDF（对话/资料库）走 tesseract（mock）回退；mode=off 不触发。
"""
import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _make_pdf(pages_text: list[str], *, scanned: bool = False) -> bytes:
    """Build a PDF with fitz. scanned=True → blank pages (get_text() returns "",
    renderable pixmap; mocks OCR on the rendered image)."""
    import fitz
    doc = fitz.open()
    for i in range(len(pages_text)):
        page = doc.new_page()
        if not scanned:
            page.insert_text((72, 72), pages_text[i] or f"page {i+1}")
        # scanned: 空白页，get_text() 返回 ""（is_scanned_pdf 判定成立）
    data = doc.tobytes()
    doc.close()
    return data


class TestIsScannedPdf(unittest.TestCase):
    def test_text_layer_full_not_scanned(self):
        from app.core.pdf_ocr import is_scanned_pdf
        raw = _make_pdf(["这是一页有足够文本内容的页面" * 5,
                         "第二页也有很多文字内容" * 5])
        self.assertFalse(is_scanned_pdf(raw))

    def test_blank_pages_scanned(self):
        from app.core.pdf_ocr import is_scanned_pdf
        raw = _make_pdf(["", "", ""], scanned=True)
        self.assertTrue(is_scanned_pdf(raw))

    def test_non_pdf_returns_false(self):
        from app.core.pdf_ocr import is_scanned_pdf
        self.assertFalse(is_scanned_pdf(b"not a pdf"))

    def test_mixed_sparse_treated_as_scanned(self):
        from app.core.pdf_ocr import is_scanned_pdf
        # 5 页但总共几乎没字符 → 页均 < 阈值 → 需 OCR
        raw = _make_pdf(["x", "", "", "", ""])
        self.assertTrue(is_scanned_pdf(raw))


class TestOcrPdfPages(unittest.TestCase):
    def test_async_ocr_pages_order_and_join(self):
        from app.core.pdf_ocr import ocr_pdf_pages
        raw = _make_pdf(["", ""], scanned=True)

        async def fake_ocr(png: bytes) -> str:
            return "OCR页文本"

        pages = asyncio.run(ocr_pdf_pages(raw, fake_ocr, max_pages=2))
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(p == "OCR页文本" for p in pages))
        self.assertEqual("\f".join(pages).count("\f"), 1)  # 页边界保留

    def test_async_max_pages_truncation(self):
        from app.core.pdf_ocr import ocr_pdf_pages
        raw = _make_pdf(["", "", "", ""], scanned=True)

        async def fake_ocr(png: bytes) -> str:
            return "p"

        pages = asyncio.run(ocr_pdf_pages(raw, fake_ocr, max_pages=2))
        self.assertEqual(len(pages), 2)

    def test_async_progress_callback(self):
        from app.core.pdf_ocr import ocr_pdf_pages
        raw = _make_pdf(["", "", ""], scanned=True)
        seen = []

        async def fake_ocr(png: bytes) -> str:
            return "x"

        asyncio.run(ocr_pdf_pages(raw, fake_ocr, max_pages=3,
                                   on_progress=lambda d, t: seen.append((d, t))))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_async_page_failure_keeps_going(self):
        from app.core.pdf_ocr import ocr_pdf_pages
        raw = _make_pdf(["", "", ""], scanned=True)
        calls = [0]

        async def flaky(png: bytes) -> str:
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("page 2 OCR down")
            return "ok"

        pages = asyncio.run(ocr_pdf_pages(raw, flaky, max_pages=3))
        self.assertEqual(pages, ["ok", "", "ok"])  # 失败页为空，不中断

    def test_sync_ocr_pages(self):
        from app.core.pdf_ocr import ocr_pdf_pages_sync
        raw = _make_pdf(["", ""], scanned=True)
        pages = ocr_pdf_pages_sync(raw, lambda png: "同步页", max_pages=2)
        self.assertEqual(pages, ["同步页", "同步页"])


class TestOcrPageImageChannel(unittest.TestCase):
    def test_tesseract_path_when_no_multimodal(self):
        # 未配 MULTIMODAL_API_KEY → 走 tesseract（mock 返回固定串）。
        from app.core import ocr
        with patch.object(ocr.settings, "multimodal_api_key", ""), \
             patch.object(ocr, "_tesseract_ocr", return_value="tess结果") as m:
            out = asyncio.run(ocr.ocr_page_image(b"png"))
        self.assertEqual(out, "tess结果")
        m.assert_called_once()
        # psm=3（自动分页），区别于题目专用的 psm=6
        self.assertEqual(m.call_args.kwargs.get("psm"), 3)

    def test_multimodal_path_uses_page_prompt(self):
        from app.core import ocr
        captured = {}

        async def fake_mm(image_bytes, *, prompt="", fallback_psm=6):
            captured["prompt"] = prompt
            captured["fallback_psm"] = fallback_psm
            return "视觉OCR结果"

        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch.object(ocr, "_multimodal_understand", side_effect=fake_mm):
            out = asyncio.run(ocr.ocr_page_image(b"png"))
        self.assertEqual(out, "视觉OCR结果")
        self.assertIs(captured["prompt"], ocr._PAGE_OCR_PROMPT)
        # 整页文档 tesseract 兜底 psm=3（区别于题目专用 psm=6）
        self.assertEqual(captured["fallback_psm"], 3)


class TestMultimodalThinkingOff(unittest.TestCase):
    """VLM OCR 通道的思考关闭/最低强度（P5a 追加）：默认下发，400 自动去参重试。"""

    def _png(self) -> bytes:
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="PNG")
        return buf.getvalue()

    def _fake_client(self, captured: dict, calls: dict, fail_first_400: bool = False):
        class FakeCompletions:
            async def create(self, **kwargs):
                captured.clear()
                captured.update(kwargs)
                calls["n"] += 1
                if fail_first_400 and calls["n"] == 1:
                    e = Exception("bad request: unknown field extra_body")
                    e.status_code = 400
                    raise e
                msg = type("M", (), {"content": "转录文本", "reasoning_content": ""})()
                return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()
        fake = type("FakeClient", (), {})()
        fake.chat = type("Chat", (), {})()
        fake.chat.completions = FakeCompletions()
        return fake

    def test_default_sends_thinking_off(self):
        from app.core import ocr
        captured, calls = {}, {"n": 0}
        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch("openai.AsyncOpenAI", return_value=self._fake_client(captured, calls)):
            out = asyncio.run(ocr._multimodal_understand(self._png()))
        self.assertEqual(out, "转录文本")
        self.assertEqual(captured.get("extra_body"),
                         {"thinking": {"type": "disabled"}, "reasoning_effort": "low"})

    def test_env_off_sends_no_extra_body(self):
        from app.core import ocr
        captured, calls = {}, {"n": 0}
        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch.object(ocr.settings, "multimodal_disable_thinking", False), \
             patch("openai.AsyncOpenAI", return_value=self._fake_client(captured, calls)):
            out = asyncio.run(ocr._multimodal_understand(self._png()))
        self.assertEqual(out, "转录文本")
        self.assertNotIn("extra_body", captured)

    def test_400_retries_without_extra_body(self):
        from app.core import ocr
        captured, calls = {}, {"n": 0}
        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch("openai.AsyncOpenAI",
                   return_value=self._fake_client(captured, calls, fail_first_400=True)):
            out = asyncio.run(ocr._multimodal_understand(self._png()))
        self.assertEqual(out, "转录文本")
        self.assertEqual(calls["n"], 2)              # 去参重试了一次
        self.assertNotIn("extra_body", captured)     # 第二次调用无推理控制字段


class TestFileParserSyncFallback(unittest.TestCase):
    """file_parser._extract_pdf 扫描回退（mock tesseract，PDF_OCR_MODE 控制）。"""

    def test_off_mode_no_ocr(self):
        from app.core import file_parser, pdf_ocr, config
        raw = _make_pdf(["", ""], scanned=True)
        with patch.object(config.settings, "pdf_ocr_mode", "off"), \
             patch.object(pdf_ocr, "ocr_pdf_pages_sync") as m, \
             patch.object(pdf_ocr, "ocr_pdf_pages_mixed_sync") as m2:
            text = file_parser.extract_text("s.pdf", raw)
        self.assertEqual(text, "\f")  # 两页空文本层，\f 连接
        m.assert_not_called()
        m2.assert_not_called()

    def test_auto_scanned_triggers_tesseract(self):
        from app.core import file_parser, pdf_ocr, ocr, config
        raw = _make_pdf(["", ""], scanned=True)
        with patch.object(config.settings, "pdf_ocr_mode", "auto"), \
             patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.object(ocr, "_tesseract_ocr", return_value="OCR result text") as tess:
            text = file_parser.extract_text("s.pdf", raw)
        self.assertIn("OCR result text", text)
        tess.assert_called()
        # psm=3（自动分页），区别于题目专用 psm=6
        self.assertTrue(all(c.kwargs.get("psm") == 3 for c in tess.call_args_list))

    def test_normal_pdf_no_ocr(self):
        from app.core import file_parser, pdf_ocr, config
        # 用 ASCII 文本（fitz 默认字体能嵌入；CJK 需 CJK 字体）
        raw = _make_pdf(["enough ascii text content for page one " * 8,
                         "second page also has plenty of ascii text " * 8])
        with patch.object(config.settings, "pdf_ocr_mode", "auto"), \
             patch.object(pdf_ocr, "ocr_pdf_pages_sync") as m, \
             patch.object(pdf_ocr, "ocr_pdf_pages_mixed_sync") as m2:
            text = file_parser.extract_text("n.pdf", raw)
        self.assertIn("ascii text", text)
        m.assert_not_called()
        m2.assert_not_called()

    def test_ocr_fallback_false_skips_ocr(self):
        # 教材库路径：ocr_fallback=False → 不触发同步 OCR（builder 负责）
        from app.core import file_parser, pdf_ocr, config
        raw = _make_pdf(["", ""], scanned=True)
        with patch.object(config.settings, "pdf_ocr_mode", "auto"), \
             patch.object(pdf_ocr, "ocr_pdf_pages_sync") as m:
            text = file_parser.extract_text("s.pdf", raw, ocr_fallback=False)
        self.assertEqual(text.count("\f"), 1)  # 纯文本层
        m.assert_not_called()


class TestTextbookBuilderOcrPipeline(unittest.TestCase):
    """教材库扫描 PDF → 后台 OCR 写回 .txt → 图谱构建（mock OCR + LLM）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_name)
        import app.core.textbook as tb
        import app.core.library as lib
        import app.agents.knowledge.store as kgs
        self._tb, self._lib, self._kgs = tb, lib, kgs
        self._orig = {
            "tb._LIBRARY_DIR": tb._LIBRARY_DIR,
            "lib._LIBRARY_DIR": lib._LIBRARY_DIR,
            "kgs._KG_DIR": kgs._KG_DIR,
            "kgs._CUSTOM_DIR": kgs._CUSTOM_DIR,
        }
        tb._LIBRARY_DIR = self.root / "library"
        lib._LIBRARY_DIR = self.root / "library"
        kgs._KG_DIR = self.root / "knowledge"
        kgs._CUSTOM_DIR = self.root / "knowledge" / "custom"

    @property
    def tmp_name(self):
        return self._tmp.name

    def tearDown(self):
        self._tb._LIBRARY_DIR = self._orig["tb._LIBRARY_DIR"]
        self._lib._LIBRARY_DIR = self._orig["lib._LIBRARY_DIR"]
        self._kgs._KG_DIR = self._orig["kgs._KG_DIR"]
        self._kgs._CUSTOM_DIR = self._orig["kgs._CUSTOM_DIR"]
        self._tmp.cleanup()

    def _seed_scanned_textbook(self, student_id="stu1", file_id="f1"):
        """Write a scanned PDF as library original + register textbook (empty .txt)."""
        from app.core.library import Library, save_library, library_data_dir
        raw = _make_pdf(["", "", ""], scanned=True)
        lib = Library(student_id=student_id)
        data = library_data_dir(student_id)
        (data / f"{file_id}.txt").write_text("", encoding="utf-8")  # 空 .txt（待 OCR）
        (data / f"{file_id}.orig.pdf").write_bytes(raw)
        lib.files.append({"id": file_id, "filename": "scan.pdf", "folder_id": "",
                          "char_count": 0, "chunk_count": 0,
                          "orig_ext": ".pdf", "kind": "textbook"})
        save_library(lib)
        rec = self._tb.create_textbook(student_id, file_id=file_id, title="扫描教材")
        return rec, raw

    def test_scanned_pdf_ocr_then_build_ready(self):
        from app.agents.knowledge import textbook_builder
        from app.core import ocr, textbook_ocr
        rec, raw = self._seed_scanned_textbook()

        # mock：OCR 每页返回短文本（足够走快速路径）；LLM 返回有效 spec
        async def fake_ocr_page(*args, **kwargs):
            return textbook_ocr.ocr.TextbookOCRResult(
                True, text="极限的定义与性质 连续性", attempt=kwargs.get("attempt", 1))

        class LLM:
            async def complete(self, messages, **kw):
                p = messages[0]["content"]
                if "subject" in p and "level" in p and "概念清单" not in p:
                    return ('{"subject":"数学","level":"本科"}', {})
                return ('{"subject":"数学","level":"本科","chapters":'
                        '[{"name":"第一章","concepts":[{"name":"极限","difficulty":2}]}]}', {})

        with patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", side_effect=fake_ocr_page), \
             patch.object(textbook_builder.settings, "pdf_ocr_mode", "auto"):
            asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], LLM()))
        out = self._tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "ready")
        self.assertGreaterEqual(out["concept_count"], 1)
        # .txt 被 OCR 写回（非空）
        from app.core.library import library_data_dir
        txt = (library_data_dir("stu1") / "f1.txt").read_text(encoding="utf-8")
        self.assertIn("极限", txt)

    def test_ocr_all_empty_marks_failed(self):
        from app.agents.knowledge import textbook_builder
        from app.core import textbook_ocr, ocr_policy
        rec, raw = self._seed_scanned_textbook()

        async def empty_ocr(*args, **kwargs):
            return textbook_ocr.ocr.TextbookOCRResult(
                False, error_code="provider_retryable", error_summary="OCR 全失败",
                retryable=True, attempt=kwargs.get("attempt", 1))

        with patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", side_effect=empty_ocr), \
             patch.object(ocr_policy, "get_retry_policy", return_value={
                 "failure_mode": "bounded_api_only", "max_attempts": 1,
                 "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                 "policy_version": 2}), \
             patch.object(textbook_builder.settings, "pdf_ocr_mode", "auto"):
            asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], None))
        out = self._tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "ocr_paused")
        self.assertTrue(out["ocr_state"]["volumes"]["f1"]["paused_pages"])

    def test_off_mode_no_ocr_textbook(self):
        # PDF_OCR_MODE=off：扫描 PDF 文本为空 + OCR 关闭 → failed（旧行为，不 OCR）
        from app.agents.knowledge import textbook_builder
        rec, raw = self._seed_scanned_textbook()
        with patch.object(textbook_builder.settings, "pdf_ocr_mode", "off"):
            asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], None))
        out = self._tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "failed")


if __name__ == "__main__":
    unittest.main()


class TestOcrPdfPagesParallel(unittest.TestCase):
    """批次并发（PDF_OCR_CONCURRENCY>1）：页序对齐、并发上限、失败隔离、
    进度语义与串行一致、单页异常不扩散。"""

    def test_mixed_parallel_preserves_order_and_caps_concurrency(self):
        from app.core.pdf_ocr import ocr_pdf_pages_mixed
        raw = _make_pdf(["", "", "", "", "", ""], scanned=True)  # 6 稀疏页
        in_flight = {"cur": 0, "peak": 0}

        async def fake_ocr(png):
            in_flight["cur"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["cur"])
            await asyncio.sleep(0.02)
            in_flight["cur"] -= 1
            return "识别文本"

        progress = []
        pages, stats = asyncio.run(ocr_pdf_pages_mixed(
            raw, fake_ocr, on_progress=lambda d, t: progress.append((d, t)),
            concurrency=3))
        self.assertEqual(len(pages), 6)                 # 页序/页数对齐
        self.assertTrue(all(p == "识别文本" for p in pages))
        self.assertEqual(stats["ocr_done"], 6)
        self.assertEqual(stats["ocr_failed"], 0)
        self.assertLessEqual(in_flight["peak"], 3)      # 并发不超批次
        self.assertGreater(in_flight["peak"], 1)        # 确实并发了
        self.assertEqual(len(progress), 6)              # 逐页进度语义不变
        self.assertEqual(progress[-1], (6, 6))

    def test_mixed_parallel_failure_isolated(self):
        from app.core.pdf_ocr import ocr_pdf_pages_mixed
        raw = _make_pdf(["", "", "", ""], scanned=True)
        calls = {"n": 0}

        async def fake_ocr(png):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("vision API boom")
            return "ok"

        pages, stats = asyncio.run(ocr_pdf_pages_mixed(raw, fake_ocr, concurrency=4))
        self.assertEqual(stats["ocr_failed"], 1)        # 失败页占位，不扩散
        self.assertEqual(sum(1 for p in pages if p == "ok"), 3)

    def test_full_ocr_parallel(self):
        from app.core.pdf_ocr import ocr_pdf_pages
        raw = _make_pdf(["", "", "", "", ""], scanned=True)

        async def fake_ocr(png):
            await asyncio.sleep(0.01)
            return "t"

        progress = []
        pages = asyncio.run(ocr_pdf_pages(
            raw, fake_ocr, on_progress=lambda d, t: progress.append(d),
            concurrency=2))
        self.assertEqual(pages, ["t"] * 5)
        self.assertEqual(progress[-1], 5)

    def test_serial_default_unchanged(self):
        # concurrency=1（默认）：串行旧行为。
        from app.core.pdf_ocr import ocr_pdf_pages_mixed
        raw = _make_pdf(["", ""], scanned=True)

        async def fake_ocr(png):
            return "x"

        pages, stats = asyncio.run(ocr_pdf_pages_mixed(raw, fake_ocr))
        self.assertEqual(pages, ["x", "x"])
        self.assertEqual(stats["ocr_done"], 2)


class TestMultimodalRetry(unittest.TestCase):
    """vision 调用重试（MULTIMODAL_OCR_RETRIES）：API 异常与空 content 都
    按退避重试；耗尽才回退 tesseract（整页 psm=3）。"""

    def _png(self) -> bytes:
        import io as _io
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="PNG")
        return buf.getvalue()

    def _flaky_client(self, calls: dict, fail_times: int = 0, empty_times: int = 0):
        class FakeCompletions:
            async def create(self, **kwargs):
                calls["n"] += 1
                n = calls["n"]
                if n <= fail_times:
                    raise RuntimeError("429 rate limit")
                if n <= fail_times + empty_times:
                    msg = type("M", (), {"content": "", "reasoning_content": ""})()
                else:
                    msg = type("M", (), {"content": "转录文本", "reasoning_content": ""})()
                return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()
        fake = type("FakeClient", (), {})()
        fake.chat = type("Chat", (), {})()
        fake.chat.completions = FakeCompletions()
        return fake

    def _run(self, calls, *, fail=0, empty=0, retries=3):
        from app.core import ocr
        async def _no_sleep(_s):
            return None
        with patch.object(ocr.settings, "multimodal_api_key", "k"), \
             patch.object(ocr.settings, "multimodal_ocr_retries", retries), \
             patch("openai.AsyncOpenAI",
                   return_value=self._flaky_client(calls, fail, empty)), \
             patch("asyncio.sleep", side_effect=_no_sleep):
            return asyncio.run(ocr._multimodal_understand(self._png()))

    def test_errors_retried_until_success(self):
        calls = {"n": 0}
        out = self._run(calls, fail=2)
        self.assertEqual(out, "转录文本")
        self.assertEqual(calls["n"], 3)                 # 前 2 次失败第 3 次成功

    def test_empty_content_retried(self):
        calls = {"n": 0}
        out = self._run(calls, empty=1)
        self.assertEqual(out, "转录文本")
        self.assertEqual(calls["n"], 2)                 # 空 content 也重试

    def test_exhausted_falls_back_to_tesseract(self):
        from app.core import ocr
        calls = {"n": 0}
        with patch.object(ocr, "_tesseract_ocr", return_value="tess兜底") as tess:
            out = self._run(calls, fail=99, retries=2)
        self.assertEqual(out, "tess兜底")
        self.assertEqual(calls["n"], 2)                 # 到达重试上限即止
        tess.assert_called_once()

    def test_page_ocr_tesseract_fallback_uses_psm3(self):
        from app.core import ocr
        calls = {"n": 0}
        with patch.object(ocr, "_tesseract_ocr", return_value="tess") as tess:
            async def _no_sleep(_s):
                return None
            with patch.object(ocr.settings, "multimodal_api_key", "k"), \
                 patch.object(ocr.settings, "multimodal_ocr_retries", 1), \
                 patch("openai.AsyncOpenAI",
                       return_value=self._flaky_client(calls, fail_times=99)), \
                 patch("asyncio.sleep", side_effect=_no_sleep):
                out = asyncio.run(ocr.ocr_page_image(self._png()))
        self.assertEqual(out, "tess")
        self.assertEqual(tess.call_args.kwargs.get("psm"), 3)


class TestFitzGlobalLock(unittest.TestCase):
    """PyMuPDF 非线程安全（MuPDF 全局上下文）：并发 50 页渲染曾实测段错误整个
    进程（2026-08-16，uvicorn SIGSEGV）。所有文档操作必须经 FITZ_LOCK 串行。"""

    def test_render_blocks_while_lock_held_elsewhere(self):
        import threading
        import time
        from app.core import pdf_ocr

        raw = _make_pdf(["page one", "page two"])
        done = threading.Event()
        out: list[bytes | None] = []

        def target():
            out.append(pdf_ocr.render_page_pixmap(raw, 0))
            done.set()

        with pdf_ocr.FITZ_LOCK:  # 测试线程占住全局锁：渲染必须阻塞等锁
            t = threading.Thread(target=target)
            t.start()
            time.sleep(0.05)
            self.assertFalse(done.is_set())  # 持锁期间未进入 fitz
        t.join(timeout=3)
        self.assertTrue(done.is_set())
        self.assertTrue(out and out[0] and out[0][:8] == b"\x89PNG\r\n\x1a\n")

    def test_concurrent_renders_all_valid(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.core import pdf_ocr

        raw = _make_pdf([f"page {i + 1} text" for i in range(8)])
        with ThreadPoolExecutor(max_workers=16) as pool:
            pngs = list(pool.map(
                lambda i: pdf_ocr.render_page_pixmap(raw, i % 8), range(48)))
        self.assertTrue(all(p and p[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs))

    def test_locked_helpers_cover_page_count_and_texts(self):
        from app.core import pdf_ocr
        raw = _make_pdf(["page one", "page two", ""])
        # _make_pdf 对空字符串页写入默认 "page N" 占位；get_text 保留行尾换行
        self.assertEqual(pdf_ocr.pdf_page_count(raw), 3)
        self.assertEqual(pdf_ocr.pdf_page_texts(raw),
                         ["page one\n", "page two\n", "page 3\n"])
        self.assertEqual(pdf_ocr.pdf_page_count(b"not a pdf"), 0)
        self.assertEqual(pdf_ocr.pdf_page_texts(b"not a pdf"), [])
