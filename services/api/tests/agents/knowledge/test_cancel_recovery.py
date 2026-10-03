"""Textbook OCR scheduler: cancellation and forced full propagation."""
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
    import fitz
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    raw = doc.tobytes()
    doc.close()
    return raw
def _persistent_policy(**over):
    policy = {"failure_mode": "persistent_api", "max_attempts": 3,
              "retry_interval_seconds": 60, "request_timeout_seconds": 60,
              "policy_version": 2}
    policy.update(over)
    return policy
if __name__ == "__main__":
    unittest.main()
class TestForceFullPropagation(unittest.TestCase):
    """force_full 意图必须跨「等待→恢复轮」传播：否则恢复轮只补稀疏页，
    已稠密页保留旧 prompt 文本，新 OCR prompt 永远不生效（语文选必下/中册
    实测缺陷的类级回归）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
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

    def _seed(self, *, pages: int = 2, text: str = ""):
        raw = _pdf(pages)
        lib = Library("stu")
        data = library_data_dir("stu")
        (data / "f1.txt").write_text(text, encoding="utf-8")
        (data / "f1.orig.pdf").write_bytes(raw)
        lib.files.append({"id": "f1", "filename": "scan.pdf", "folder_id": "",
                          "char_count": len(text), "chunk_count": 0,
                          "orig_ext": ".pdf", "kind": "textbook"})
        save_library(lib)
        tb = tb_store.create_textbook("stu", file_id="f1", title="扫描教材")
        return tb, raw

    _POLICY = {"failure_mode": "persistent_api", "max_attempts": 3,
               "retry_interval_seconds": 60, "request_timeout_seconds": 60,
               "policy_version": 2}

    def test_resume_round_inherits_force_full_intent(self):
        """state=waiting+force_full 时，即使调用方丢了标志（force_full=False）
        且当前文本已稠密，轮次仍按全量继续重试 pending 页。"""
        tb, raw = self._seed(pages=1)
        calls = {"n": 0}

        async def api_page(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return textbook_ocr.ocr.TextbookOCRResult(
                    False, error_code="provider_retryable", error_summary="429",
                    retryable=True, http_status=429, attempt=kwargs.get("attempt", 1))
            return textbook_ocr.ocr.TextbookOCRResult(
                True, text="新 prompt 带页码标记的文本[页码=1]",
                attempt=kwargs.get("attempt", 1))

        with patch.object(ocr_policy, "get_retry_policy", return_value=self._POLICY), \
             patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", side_effect=api_page), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            first = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, "", force_full=True))
            self.assertEqual(first.status, "waiting")
            state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
            self.assertTrue(state["force_full"])
            # 模拟恢复调用方丢标志：force_full=False + 文本已稠密
            second = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw,
                "已经稠密的旧 OCR 文本，无需逐页重判。",
                force_full=False))
        self.assertEqual(second.status, "complete")
        self.assertIn("页码标记", second.text)
        self.assertEqual(calls["n"], 2)  # pending 页确实重新 OCR，而非跳过

    def test_sparse_inflight_round_never_escalates_to_full(self):
        """反向钳制：组级传播的 force_full=True 不得把在途稀疏轮翻成全量
        重 OCR——successful/attempts 保留，只重试 pending 页。语文必修
        api_success 150→0、大学物理学 428→515 实测缺陷的类级回归。"""
        tb, raw = self._seed(pages=3)
        tb_store.update_textbook("stu", tb["id"], status="ocr_waiting", ocr_state={
            "version": 1, "volumes": {"f1": {
                "status": "waiting", "force_full": False,
                "source_text_sha256": textbook_ocr._text_hash(""),
                "total_pages": 3, "target_pages": [1, 2],
                "successful_pages": [1], "pending_pages": [2],
                "paused_pages": [], "empty_pages": [],
                "attempts": {"2": 1}}}})
        calls = {"n": 0}

        async def api_page(*args, **kwargs):
            calls["n"] += 1
            return textbook_ocr.ocr.TextbookOCRResult(
                True, text="第2页重试成功", attempt=kwargs.get("attempt", 1))

        with patch.object(ocr_policy, "get_retry_policy", return_value=self._POLICY), \
             patch.object(textbook_ocr.ocr, "textbook_ocr_page_api",
                          side_effect=api_page):
            result = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, "", force_full=True))
        self.assertEqual(result.status, "complete")
        self.assertEqual(calls["n"], 1)  # 未翻全量：只跑了 pending 的第 2 页
        state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
        self.assertFalse(state["force_full"])
        self.assertEqual(state["successful_pages"], [1, 2])  # 既有成果未被清零
        self.assertNotIn("第1页", result.text)  # 未重 OCR 已成功页

    def test_resume_enqueue_passes_force_full_ocr(self):
        """恢复入队：任一卷处于未完成全量轮 → 入队项带 force_full_ocr=True
        （弱提示：在途稀疏轮由轮次入口按卷钳制，不会被升级）。"""
        tb, _raw = self._seed(pages=1)
        tb_store.update_textbook(
            "stu", tb["id"], status="ocr_waiting",
            ocr_state={"volumes": {"f1": {
                "status": "waiting", "force_full": True,
                "next_retry_at": 0.0}}})

        from app.agents.knowledge import textbook_builder

        async def go():
            with patch.object(textbook_builder, "enqueue_textbook_build") as enqueue:
                textbook_ocr.schedule_textbook_resume("stu", tb["id"], 0.0)
            enqueue.assert_called_once_with(
                "stu", tb["id"], ocr_parallel=True, force_reextract=False,
                force_full_ocr=True, auto_retry=True)

        asyncio.run(go())
class TestParseCancel(unittest.TestCase):
    """合作式终止：标记置位后轮次短路返回 cancelled，端点结算保留文本。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _seed(self):
        lib = Library("stu")
        data = library_data_dir("stu")
        (data / "f1.txt").write_text("已有的旧文本内容，足够长以判定可用。", encoding="utf-8")
        lib.files.append({"id": "f1", "filename": "scan.pdf", "folder_id": "",
                          "char_count": 20, "chunk_count": 1, "orig_ext": ".pdf",
                          "kind": "textbook"})
        save_library(lib)
        return tb_store.create_textbook("stu", file_id="f1", title="扫描教材")

    def test_cancelled_round_short_circuits_without_ocr(self):
        tb = self._seed()
        tb_store.update_textbook("stu", tb["id"], status="building",
                                 parse_cancel_requested=True)

        async def no_call(*a, **k):  # 任何 OCR 调用都是违规
            raise AssertionError("cancelled round must not call OCR")

        with patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", no_call):
            result = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", _pdf(1), ""))
        self.assertEqual(result.status, "cancelled")
        state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
        self.assertEqual(state["status"], "cancelled")

    def test_settle_keeps_text_and_returns_ready(self):
        tb = self._seed()
        tb_store.update_textbook("stu", tb["id"], status="building",
                                 parse_cancel_requested=True)
        final = tb_store.settle_cancelled_parse("stu", tb["id"])
        self.assertEqual(final, "ready")
        rec = tb_store.find_textbook("stu", tb["id"])
        self.assertEqual(rec["status"], "ready")
        self.assertEqual(rec["error"], "")
        # 标记保留（仍在跑的构建检查点需要观测）；新构建开始时才清
        self.assertTrue(rec.get("parse_cancel_requested"))

    def test_settle_without_text_marks_failed(self):
        lib = Library("stu2")
        (library_data_dir("stu2")).mkdir(parents=True, exist_ok=True)
        (library_data_dir("stu2") / "f9.txt").write_text("", encoding="utf-8")
        lib.files.append({"id": "f9", "filename": "scan.pdf", "folder_id": "",
                          "char_count": 0, "chunk_count": 0, "orig_ext": ".pdf",
                          "kind": "textbook"})
        save_library(lib)
        tb = tb_store.create_textbook("stu2", file_id="f9", title="无文本教材")
        tb_store.update_textbook("stu2", tb["id"], status="building")
        final = tb_store.settle_cancelled_parse("stu2", tb["id"])
        self.assertEqual(final, "failed")
        self.assertIn("终止", tb_store.find_textbook("stu2", tb["id"])["error"])
