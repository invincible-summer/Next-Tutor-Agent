"""Textbook OCR scheduler: round durability and resume."""
from __future__ import annotations
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import library as library_mod
from app.core import textbook as tb_store
from app.core import textbook_ocr
from app.core import textbook_pipeline
from app.core import ocr_policy
from app.core.library import Library, library_data_dir, save_library
"""Durable textbook OCR retry rounds; chat OCR is tested separately."""
def _pipeline_policy(mode: str, *, build: int = 2, volume: int = 2, llm: int = 4) -> dict:
    return {"mode": mode, "build_concurrency": build, "volume_concurrency": volume,
            "llm_concurrency": llm, "updated_at": 0.0, "version": 1}
def _pdf(pages: int = 1) -> bytes:
    from tests.support import pdf_fixtures
    return pdf_fixtures.blank_pdf(pages)
def _persistent_policy(**over):
    policy = {"failure_mode": "persistent_api", "max_attempts": 3,
              "retry_interval_seconds": 60, "request_timeout_seconds": 60,
              "policy_version": 2}
    policy.update(over)
    return policy
if __name__ == "__main__":
    unittest.main()
class TestRoundDurability(unittest.TestCase):
    """永久性页面错误终态 / 逐页增量落盘 / 零进展跳过 / 删除守卫。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        from app.agents.knowledge import store as kgs_mod
        self.kgs_mod = kgs_mod
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(kgs_mod, "_KG_DIR", root / "knowledge"),
            patch.object(kgs_mod, "_CUSTOM_DIR", root / "knowledge" / "custom"),
        ]
        for p in self.patches:
            p.start()
        self.old_runtime = ocr_policy._RUNTIME
        ocr_policy._RUNTIME = ocr_policy._Runtime()
        textbook_ocr._TASKS.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        ocr_policy._RUNTIME = self.old_runtime
        textbook_ocr._TASKS.clear()
        self.tmp.cleanup()

    def _seed(self, *, owner: str = "stu", pages: int = 1):
        raw = _pdf(pages)
        lib = Library(owner)
        data = library_data_dir(owner)
        (data / "f1.txt").write_text("", encoding="utf-8")
        (data / "f1.orig.pdf").write_bytes(raw)
        lib.files.append({"id": "f1", "filename": "scan.pdf", "folder_id": "",
                          "char_count": 0, "chunk_count": 0, "orig_ext": ".pdf",
                          "kind": "textbook"})
        save_library(lib)
        tb = tb_store.create_textbook(owner, file_id="f1", title="扫描教材")
        return tb, raw

    def _round(self, raw, current_text="", **kwargs):
        tb_id = kwargs.pop("tb_id")
        return asyncio.run(textbook_ocr.process_textbook_ocr_round(
            "stu", tb_id, "f1", raw, current_text, **kwargs))

    def test_persistent_empty_content_completes_as_blank_after_attempts(self):
        """persistent_api 下空白页（模型正常响应但无文字）达到 max_attempts 后按
        空白页收尾，不再无限 waiting 重试——死循环回归。"""
        tb, raw = self._seed()
        calls = {"n": 0}

        def api_page(*args, **kwargs):
            calls["n"] += 1
            return textbook_ocr.ocr.TextbookOCRResult(
                False, error_code="empty_content", error_summary="多模态 OCR 返回空内容",
                retryable=True, attempt=calls["n"])

        text = ""
        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy(max_attempts=3)), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=(
                 lambda *_a, **_k: (api_page(), ""))), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            for expected in ("waiting", "waiting", "complete"):
                result = self._round(raw, text, tb_id=tb["id"])
                self.assertEqual(result.status, expected)
                text = result.text
        self.assertEqual(calls["n"], 3)  # 恰好 max_attempts 次，随后收敛
        state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["empty_pages"], [1])
        self.assertEqual(state["pending_pages"], [])
        self.assertIn("空白页", state["last_error_summary"])
        # 教材记录离开 ocr_waiting，可继续图谱构建
        self.assertEqual(tb_store.find_textbook("stu", tb["id"])["status"], "building")

    def test_bounded_then_local_empty_content_falls_back_to_tesseract(self):
        tb, raw = self._seed()
        api = lambda **kw: textbook_ocr.ocr.TextbookOCRResult(
            False, error_code="empty_content", error_summary="空", retryable=True)
        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy(
                              failure_mode="bounded_then_local", max_attempts=1)), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=(
                 lambda *a, **k: (api(), "本地兜底" if k.get("local_fallback") else ""))), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            result = self._round(raw, tb_id=tb["id"])
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.text, "本地兜底")
        state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
        self.assertEqual(state["empty_pages"], [])

    def test_bounded_then_local_empty_content_and_blank_local_completes_empty(self):
        tb, raw = self._seed()

        def attempt(_raw, _idx, _attempt, _timeout, *, local_fallback=False):
            code = textbook_ocr.ocr.TextbookOCRResult(
                False, error_code="empty_content", error_summary="空", retryable=True)
            return (code, "")

        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy(
                              failure_mode="bounded_then_local", max_attempts=1)), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=attempt), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            result = self._round(raw, tb_id=tb["id"])
        self.assertEqual(result.status, "complete")
        state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
        self.assertEqual(state["empty_pages"], [1])
        self.assertEqual(state["pending_pages"], [])

    def test_state_reset_inherits_empty_pages(self):
        """状态重建（hash 变化）不重试已知空白页。"""
        tb, raw = self._seed(pages=2)
        tb_store.update_textbook("stu", tb["id"], ocr_state={
            "version": 1, "volumes": {"f1": {
                "status": "complete", "force_full": False,
                "source_text_sha256": "stale", "total_pages": 2,
                "target_pages": [1], "successful_pages": [1], "empty_pages": [1],
                "pending_pages": [], "paused_pages": [], "attempts": {}}}})
        calls: list[int] = []

        def attempt(_raw, page_idx, _attempt, _timeout, *, local_fallback=False):
            calls.append(page_idx)
            return (textbook_ocr.ocr.TextbookOCRResult(
                True, text="第二页正文" * 10, attempt=1), "")

        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy()), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=attempt), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            result = self._round(raw, "", tb_id=tb["id"], force_full=False)
        self.assertEqual(result.status, "complete")
        self.assertEqual(calls, [1])  # 只重试第 2 页，空白页 1 未再尝试
        state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
        self.assertEqual(state["empty_pages"], [1])
        self.assertEqual(state["successful_pages"], [1, 2])

    def test_pages_persist_incrementally_during_round(self):
        """每页完成立即写 .txt（慢模型下进程被杀不丢已完成页）。"""
        tb, raw = self._seed(pages=2)
        data = library_data_dir("stu")
        writes: list[str] = []
        real_write = textbook_ocr.atomic_write_text

        def spy_write(path, text, *a, **k):
            writes.append(text)
            return real_write(path, text, *a, **k)

        async def attempt(_raw, page_idx, _attempt, _timeout, *, local_fallback=False):
            if page_idx == 0:
                return (textbook_ocr.ocr.TextbookOCRResult(
                    True, text="第一页正文" * 10, attempt=1), "")
            for _ in range(300):  # 等第 1 页先落盘再返回
                if (data / "f1.txt").exists() and \
                        (data / "f1.txt").read_text(encoding="utf-8").endswith("第一页正文" * 10):
                    break
                await asyncio.sleep(0.01)
            return (textbook_ocr.ocr.TextbookOCRResult(
                True, text="第二页正文" * 10, attempt=1), "")

        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy()), \
             patch.object(textbook_ocr, "_attempt_page", new=attempt), \
             patch.object(textbook_ocr, "schedule_textbook_resume"), \
             patch.object(textbook_ocr, "atomic_write_text", side_effect=spy_write):
            result = self._round(raw, tb_id=tb["id"])
        self.assertEqual(result.status, "complete")
        first_partial = "第一页正文" * 10 + "\f"
        self.assertIn(first_partial, writes)  # 轮中检查点：第 2 页完成前已写出
        self.assertEqual((data / "f1.txt").read_text(encoding="utf-8"),
                         first_partial + "第二页正文" * 10)

    def test_zero_progress_round_skips_rechunk(self):
        """等待轮（页面全部暂时失败、文本未变）不再全书重切块。"""
        from app.core.library import load_library
        tb, raw = self._seed(pages=2)

        def ok_attempt(_raw, page_idx, _attempt, _timeout, *, local_fallback=False):
            return (textbook_ocr.ocr.TextbookOCRResult(
                True, text=f"第{page_idx + 1}页正文" * 10, attempt=1), "")

        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy()), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=ok_attempt), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            first = self._round(raw, tb_id=tb["id"])
        self.assertEqual(first.status, "complete")
        meta = load_library("stu").find_file("f1")
        stamp = dict(meta.get("rag_index") or {}).get("updated_at")

        root = {"version": 1, "volumes": {"f1": dict(
            tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"],
            status="waiting", pending_pages=[1])}}
        tb_store.update_textbook("stu", tb["id"], ocr_state=root)

        def fail_attempt(_raw, _page_idx, _attempt, _timeout, *, local_fallback=False):
            return (textbook_ocr.ocr.TextbookOCRResult(
                False, error_code="provider_retryable", error_summary="429",
                retryable=True, http_status=429), "")

        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy()), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=fail_attempt), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            second = self._round(raw, first.text, tb_id=tb["id"])
        self.assertEqual(second.status, "waiting")
        meta = load_library("stu").find_file("f1")
        self.assertEqual(dict(meta.get("rag_index") or {}).get("updated_at"), stamp)

    def test_deleted_record_and_file_round_writes_nothing(self):
        """轮中教材被归档删除：不复活 .txt / 不更新库元数据。"""
        tb, raw = self._seed()
        from app.core.library import load_library, save_library
        lib = load_library("stu")
        lib.remove_file("f1")
        save_library(lib)
        tb_store.remove_textbook("stu", tb["id"])

        def ok_attempt(*_a, **_k):
            return (textbook_ocr.ocr.TextbookOCRResult(
                True, text="视觉模型识别正文", attempt=1), "")

        with patch.object(ocr_policy, "get_retry_policy",
                          return_value=_persistent_policy()), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=ok_attempt), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            result = self._round(raw, tb_id=tb["id"])
        self.assertEqual(result.status, "complete")  # 轮本身不炸
        self.assertFalse((library_data_dir("stu") / "f1.txt").exists())
        self.assertIsNone(load_library("stu").find_file("f1"))
