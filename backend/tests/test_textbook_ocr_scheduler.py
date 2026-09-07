"""Durable textbook OCR retry rounds; chat OCR is tested separately."""
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


class TestTextbookOCRScheduler(unittest.TestCase):
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

    def test_persistent_failure_waits_without_tesseract_then_retries_failed_page(self):
        tb, raw = self._seed()
        calls = {"n": 0}

        async def api_page(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return textbook_ocr.ocr.TextbookOCRResult(
                    False, error_code="provider_retryable", error_summary="429",
                    retryable=True, http_status=429, attempt=kwargs.get("attempt", 1))
            return textbook_ocr.ocr.TextbookOCRResult(
                True, text="视觉模型识别正文", attempt=kwargs.get("attempt", 1))

        def no_schedule(*args, **kwargs):
            return None

        with patch.object(ocr_policy, "get_retry_policy", return_value={
                "failure_mode": "persistent_api", "max_attempts": 3,
                "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                "policy_version": 2}), \
             patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", side_effect=api_page), \
             patch.object(textbook_ocr.ocr, "_tesseract_ocr", side_effect=AssertionError("no fallback")), \
             patch.object(textbook_ocr, "schedule_textbook_resume", side_effect=no_schedule):
            first = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, ""))
            self.assertEqual(first.status, "waiting")
            record = tb_store.find_textbook("stu", tb["id"])
            self.assertEqual(record["status"], "ocr_waiting")
            self.assertEqual(record["ocr_state"]["volumes"]["f1"]["pending_pages"], [1])
            self.assertEqual(ocr_policy.get_policy()["active_ocr_pages"], 0)
            second = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, ""))
        self.assertEqual(second.status, "complete")
        self.assertEqual(second.text, "视觉模型识别正文")
        self.assertEqual(calls["n"], 2)

    def test_partial_success_retries_only_failed_page_and_preserves_page_order(self):
        tb, raw = self._seed(pages=3)
        calls: list[tuple[int, int]] = []

        async def attempt_page(_raw, page_idx, attempt, _timeout, *, local_fallback=False):
            self.assertFalse(local_fallback)
            calls.append((page_idx, attempt))
            if page_idx == 1 and attempt == 1:
                return (textbook_ocr.ocr.TextbookOCRResult(
                    False, error_code="provider_retryable", error_summary="503",
                    retryable=True, http_status=503, attempt=1), "")
            return (textbook_ocr.ocr.TextbookOCRResult(
                True, text=("第一页", "第二页", "第三页")[page_idx],
                attempt=attempt), "")

        with patch.object(ocr_policy, "get_retry_policy", return_value={
                "failure_mode": "persistent_api", "max_attempts": 3,
                "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                "policy_version": 2}), \
             patch.object(textbook_ocr, "_attempt_page", side_effect=attempt_page), \
             patch.object(textbook_ocr.ocr, "_tesseract_ocr",
                          side_effect=AssertionError("no fallback")), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            first = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, ""))
            self.assertEqual(first.status, "waiting")
            self.assertEqual(first.text, "第一页\f\f第三页")
            first_state = tb_store.find_textbook("stu", tb["id"])["ocr_state"]["volumes"]["f1"]
            self.assertEqual(first_state["successful_pages"], [1, 3])
            self.assertEqual(first_state["pending_pages"], [2])
            second = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, first.text))

        self.assertEqual(second.status, "complete")
        self.assertEqual(second.text, "第一页\f第二页\f第三页")
        self.assertEqual(calls, [(0, 1), (1, 1), (2, 1), (1, 2)])
        self.assertEqual(
            (library_data_dir("stu") / "f1.txt").read_text(encoding="utf-8"),
            "第一页\f第二页\f第三页")


    def test_waiting_round_releases_textbook_build_lock(self):
        from app.agents.knowledge import textbook_builder

        owner = "lockstu"
        tb, _ = self._seed(owner=owner)
        textbook_builder._BUILD_LOCKS.pop((owner, tb["id"]), None)

        async def drive():
            with patch.object(ocr_policy, "get_retry_policy", return_value={
                    "failure_mode": "persistent_api", "max_attempts": 3,
                    "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                    "policy_version": 2}), \
                 patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", return_value=
                     textbook_ocr.ocr.TextbookOCRResult(
                         False, error_code="provider_retryable", error_summary="429",
                         retryable=True, http_status=429)), \
                 patch.object(textbook_ocr, "schedule_textbook_resume"):
                await textbook_builder.build_textbook_graph(owner, tb["id"], llm=None)
            lock = await textbook_builder._lock_for(owner, tb["id"])
            self.assertFalse(lock.locked())
            snapshot = ocr_policy.get_policy()
            self.assertEqual(snapshot["active_ocr_jobs"], 0)
            self.assertEqual(snapshot["active_ocr_pages"], 0)
            self.assertEqual(tb_store.find_textbook(owner, tb["id"])["status"],
                             "ocr_waiting")

        try:
            asyncio.run(drive())
        finally:
            textbook_builder._BUILD_LOCKS.pop((owner, tb["id"]), None)

    def test_bounded_then_local_only_falls_back_after_limit(self):
        tb, raw = self._seed()
        with patch.object(ocr_policy, "get_retry_policy", return_value={
                "failure_mode": "bounded_then_local", "max_attempts": 1,
                "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                "policy_version": 2}), \
             patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", return_value=
                 textbook_ocr.ocr.TextbookOCRResult(False, error_code="provider_retryable",
                                                    error_summary="down", retryable=True)), \
             patch.object(textbook_ocr.ocr, "_tesseract_ocr", return_value="本地兜底") as tess:
            result = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, ""))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.text, "本地兜底")
        tess.assert_called_once()

    def test_bounded_api_only_pauses_without_local_fallback(self):
        tb, raw = self._seed()
        with patch.object(ocr_policy, "get_retry_policy", return_value={
                "failure_mode": "bounded_api_only", "max_attempts": 1,
                "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                "policy_version": 2, "policy_generation": 3}), \
             patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", return_value=
                 textbook_ocr.ocr.TextbookOCRResult(False, error_code="provider_retryable",
                                                    error_summary="down", retryable=True)), \
             patch.object(textbook_ocr.ocr, "_tesseract_ocr",
                          side_effect=AssertionError("no local fallback")):
            result = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, ""))
        self.assertEqual(result.status, "paused")
        self.assertEqual(tb_store.find_textbook("stu", tb["id"])["status"], "ocr_paused")

    def test_persistent_configuration_error_waits_and_releases_slots(self):
        tb, raw = self._seed()
        with patch.object(ocr_policy, "get_retry_policy", return_value={
                "failure_mode": "persistent_api", "max_attempts": 1,
                "retry_interval_seconds": 60, "request_timeout_seconds": 60,
                "policy_version": 2, "policy_generation": 4}), \
             patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", return_value=
                 textbook_ocr.ocr.TextbookOCRResult(False, error_code="auth_error",
                                                    error_summary="401", retryable=False)), \
             patch.object(textbook_ocr, "schedule_textbook_resume"):
            result = asyncio.run(textbook_ocr.process_textbook_ocr_round(
                "stu", tb["id"], "f1", raw, ""))
        self.assertEqual(result.status, "waiting")
        self.assertTrue(result.state["configuration_blocked"])
        self.assertEqual(result.state["policy_generation"], 4)
        snapshot = ocr_policy.get_policy()
        self.assertEqual(snapshot["active_ocr_jobs"], 0)
        self.assertEqual(snapshot["active_ocr_pages"], 0)

    def test_startup_resume_enqueues_persisted_waiting_textbook(self):
        tb, _ = self._seed()
        tb_store.update_textbook("stu", tb["id"], status="ocr_waiting", ocr_state={
            "version": 1, "volumes": {"f1": {
                "status": "waiting", "pending_pages": [1],
                "successful_pages": [], "target_pages": [1],
                "next_retry_at": 9999999999.0}}})
        from app.agents.knowledge import textbook_builder
        with patch.object(textbook_builder, "enqueue_textbook_build") as enqueue:
            count = textbook_ocr.resume_pending_textbook_ocr()
        self.assertEqual(count, 1)
        enqueue.assert_called_once_with("stu", tb["id"], ocr_parallel=True,
                                        force_reextract=False, force_full_ocr=False,
                                        auto_retry=True)

    def test_reap_marks_pending_ocr_waiting_as_resumable(self):
        tb, _ = self._seed()
        tb_store.update_textbook("stu", tb["id"], status="building", ocr_state={
            "version": 1, "volumes": {"f1": {"status": "waiting", "pending_pages": [1],
                                               "successful_pages": [], "target_pages": [1],
                                               "next_retry_at": 9999999999}}})
        self.assertEqual(tb_store.reap_stale_builds(), 0)
        self.assertEqual(tb_store.find_textbook("stu", tb["id"])["status"], "ocr_waiting")


def _persistent_policy(**over):
    policy = {"failure_mode": "persistent_api", "max_attempts": 3,
              "retry_interval_seconds": 60, "request_timeout_seconds": 60,
              "policy_version": 2}
    policy.update(over)
    return policy


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


class TestBuildQueue(unittest.TestCase):
    """per-owner 构建队列（legacy 模式）：队首教材到达终态后才开建下一本。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        from app.agents.knowledge import store as kgs_mod
        from app.agents.knowledge import textbook_builder
        self.builder = textbook_builder
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(kgs_mod, "_KG_DIR", root / "knowledge"),
            patch.object(kgs_mod, "_CUSTOM_DIR", root / "knowledge" / "custom"),
        ]
        for p in self.patches:
            p.start()
        self._old_poll = textbook_builder.QUEUE_POLL_SECONDS
        textbook_builder.QUEUE_POLL_SECONDS = 0.02
        textbook_builder._BUILD_QUEUES.clear()
        textbook_builder._BUILD_LOCKS.clear()
        # legacy 模式：验证「严格一本接一本」的历史契约仍然成立。
        self._old_policy = textbook_pipeline._RUNTIME.policy
        textbook_pipeline._RUNTIME.policy = _pipeline_policy("legacy")

    def tearDown(self):
        textbook_pipeline._RUNTIME.policy = self._old_policy
        self.builder.QUEUE_POLL_SECONDS = self._old_poll
        self.builder._BUILD_QUEUES.clear()
        self.builder._BUILD_LOCKS.clear()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _seed_two(self):
        ta = tb_store.create_textbook("stu", file_id="fA", title="教材A")
        tb = tb_store.create_textbook("stu", file_id="fB", title="教材B")
        return ta, tb

    def test_next_book_waits_until_first_reaches_terminal(self):
        ta, tb = self._seed_two()
        events: list[tuple[str, str]] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            events.append(("build", tb_id))
            if tb_id == ta["id"]:
                # 首建即转入 OCR 等重试；0.15s 后由（模拟的）resume 驱动收敛
                tb_store.update_textbook(student_id, tb_id, status="ocr_waiting")
                async def finish():
                    await asyncio.sleep(0.15)
                    events.append(("terminal", tb_id))
                    tb_store.update_textbook(student_id, tb_id, status="ready")
                asyncio.get_running_loop().create_task(finish())
            else:
                tb_store.update_textbook(student_id, tb_id, status="ready")
            return None

        async def drive():
            self.assertTrue(self.builder.enqueue_textbook_build("stu", ta["id"]))
            self.assertTrue(self.builder.enqueue_textbook_build("stu", tb["id"]))
            worker = self.builder._BUILD_QUEUES["stu"]["worker"]
            await worker

        with patch.object(self.builder, "build_group_graph", side_effect=fake_build), \
             patch.object(self.builder, "build_textbook_graph", side_effect=fake_build):
            asyncio.run(drive())
        # B 的构建严格发生在 A 到达终态之后：不存在两本同时「建一半/等重试」
        self.assertEqual(events, [("build", ta["id"]), ("terminal", ta["id"]),
                                  ("build", tb["id"])])

    def test_deleted_book_unblocks_queue(self):
        ta, tb = self._seed_two()
        events: list[str] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            events.append(tb_id)
            if tb_id == ta["id"]:
                tb_store.update_textbook(student_id, tb_id, status="ocr_waiting")
                async def vanish():
                    await asyncio.sleep(0.1)
                    tb_store.remove_textbook(student_id, tb_id)
                asyncio.get_running_loop().create_task(vanish())
            else:
                tb_store.update_textbook(student_id, tb_id, status="ready")
            return None

        async def drive():
            self.builder.enqueue_textbook_build("stu", ta["id"])
            self.builder.enqueue_textbook_build("stu", tb["id"])
            await self.builder._BUILD_QUEUES["stu"]["worker"]

        with patch.object(self.builder, "build_group_graph", side_effect=fake_build), \
             patch.object(self.builder, "build_textbook_graph", side_effect=fake_build):
            asyncio.run(drive())
        self.assertEqual(events, [ta["id"], tb["id"]])

    def test_enqueue_without_running_loop_returns_false(self):
        self.assertFalse(self.builder.enqueue_textbook_build("stu", "tb_x"))

    def test_gate_drives_retry_in_worker_until_terminal(self):
        """ocr_waiting 到点的重试轮由队列门控就地驱动（轻量参数
        force_reextract=False + auto_retry），B 严格在 A 终态后才开建。"""
        ta, tb = self._seed_two()
        calls: list[tuple[str, dict]] = []

        async def fake_run(student_id, tb_id, **kw):
            calls.append((tb_id, kw))
            if tb_id == ta["id"] and sum(1 for c in calls if c[0] == ta["id"]) == 1:
                # 首建：进入等待重试，等待卷已到点（next_retry_at=0）
                tb_store.update_textbook(student_id, tb_id, status="ocr_waiting",
                                         ocr_state={"version": 1, "volumes": {
                                             "fA": {"status": "waiting",
                                                    "next_retry_at": 0.0}}})
            else:
                tb_store.update_textbook(student_id, tb_id, status="ready")

        async def drive():
            fa = self.builder.enqueue_textbook_build("stu", ta["id"])
            fb = self.builder.enqueue_textbook_build("stu", tb["id"])
            await asyncio.gather(fa, fb)

        with patch.object(self.builder, "run_textbook_build", side_effect=fake_run):
            asyncio.run(drive())
        self.assertEqual([c[0] for c in calls],
                         [ta["id"], ta["id"], tb["id"]])
        retry_kw = calls[1][1]
        self.assertFalse(retry_kw["force_reextract"])  # 重试轮轻量：不强制重抽 spec
        self.assertTrue(retry_kw["auto_retry"])
        self.assertTrue(retry_kw["ocr_parallel"])
        # 首建（入队项）不带 auto_retry——手动刷新项不受终态跳过守卫限制
        self.assertFalse(calls[0][1].get("auto_retry", False))

    def test_auto_retry_skips_terminal_record(self):
        ta, _tb = self._seed_two()
        tb_store.update_textbook("stu", ta["id"], status="ready")

        async def boom(*args, **kwargs):
            raise AssertionError("terminal record must not dispatch build")

        async def drive():
            future = self.builder.enqueue_textbook_build("stu", ta["id"],
                                                         auto_retry=True)
            await future

        with patch.object(self.builder, "build_group_graph", side_effect=boom), \
             patch.object(self.builder, "build_textbook_graph", side_effect=boom):
            asyncio.run(drive())
        self.assertEqual(tb_store.find_textbook("stu", ta["id"])["status"], "ready")

    def test_safe_build_waits_for_queue_item(self):
        """手动刷新经 per-owner 队列执行并等待完成（同步上下文回退直连另测）。"""
        from app.api.v1 import textbook as api_textbook
        ta, _tb = self._seed_two()
        seen: list[str] = []

        async def fake_run(student_id, tb_id, **kw):
            seen.append(tb_id)
            tb_store.update_textbook(student_id, tb_id, status="ready")

        async def drive():
            await api_textbook._safe_build("stu", ta["id"], ocr_parallel=True,
                                           skip_ocr=True)

        with patch.object(self.builder, "run_textbook_build", side_effect=fake_run):
            asyncio.run(drive())
        self.assertEqual(seen, [ta["id"]])
        self.assertEqual(tb_store.find_textbook("stu", ta["id"])["status"], "ready")


class TestBuildQueueParallelMode(unittest.TestCase):
    """per-owner 构建队列（parallel 模式）：不同书有界并发、同书绝不并行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        from app.agents.knowledge import store as kgs_mod
        from app.agents.knowledge import textbook_builder
        self.builder = textbook_builder
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(kgs_mod, "_KG_DIR", root / "knowledge"),
            patch.object(kgs_mod, "_CUSTOM_DIR", root / "knowledge" / "custom"),
        ]
        for p in self.patches:
            p.start()
        self._old_poll = textbook_builder.QUEUE_POLL_SECONDS
        textbook_builder.QUEUE_POLL_SECONDS = 0.02
        textbook_builder._BUILD_QUEUES.clear()
        textbook_builder._BUILD_LOCKS.clear()
        self._old_policy = textbook_pipeline._RUNTIME.policy
        textbook_pipeline._RUNTIME.policy = _pipeline_policy("parallel", build=2)

    def tearDown(self):
        textbook_pipeline._RUNTIME.policy = self._old_policy
        self.builder.QUEUE_POLL_SECONDS = self._old_poll
        self.builder._BUILD_QUEUES.clear()
        self.builder._BUILD_LOCKS.clear()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _seed_two(self):
        ta = tb_store.create_textbook("stu", file_id="fA", title="教材A")
        tb = tb_store.create_textbook("stu", file_id="fB", title="教材B")
        return ta, tb

    def test_parallel_mode_overlaps_builds(self):
        """书 A 转入 ocr_waiting 等重试期间，书 B 即可开建（资源池重叠）。"""
        ta, tb = self._seed_two()
        events: list[tuple[str, str]] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            events.append(("build", tb_id))
            if tb_id == ta["id"]:
                tb_store.update_textbook(student_id, tb_id, status="ocr_waiting")
                async def finish():
                    await asyncio.sleep(0.15)
                    events.append(("terminal", tb_id))
                    tb_store.update_textbook(student_id, tb_id, status="ready")
                asyncio.get_running_loop().create_task(finish())
            else:
                tb_store.update_textbook(student_id, tb_id, status="ready")
            return None

        async def drive():
            self.assertTrue(self.builder.enqueue_textbook_build("stu", ta["id"]))
            self.assertTrue(self.builder.enqueue_textbook_build("stu", tb["id"]))
            await self.builder._BUILD_QUEUES["stu"]["worker"]

        with patch.object(self.builder, "build_group_graph", side_effect=fake_build), \
             patch.object(self.builder, "build_textbook_graph", side_effect=fake_build):
            asyncio.run(drive())
        # B 的构建发生在 A 终态之前（并发重叠），但派发顺序仍是 A 先 B 后。
        self.assertEqual(events[0], ("build", ta["id"]))
        self.assertIn(("build", tb["id"]), events)
        self.assertLess(events.index(("build", tb["id"])),
                        events.index(("terminal", ta["id"])))
        self.assertEqual(tb_store.find_textbook("stu", tb["id"])["status"], "ready")

    def test_same_book_never_builds_concurrently(self):
        """同一本书重复入队：构建严格串行（第二次等第一次终态后开跑）。"""
        ta, _tb = self._seed_two()
        active = 0
        peak = 0
        order: list[str] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            nonlocal active, peak
            order.append(f"start:{len(order)}")
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            tb_store.update_textbook(student_id, tb_id, status="ready")
            active -= 1
            return None

        async def drive():
            fa = self.builder.enqueue_textbook_build("stu", ta["id"])
            fb = self.builder.enqueue_textbook_build("stu", ta["id"])
            await asyncio.gather(fa, fb)

        with patch.object(self.builder, "run_textbook_build", side_effect=fake_build):
            asyncio.run(drive())
        self.assertEqual(peak, 1)
        self.assertEqual(order, ["start:0", "start:1"])

    def test_build_concurrency_cap(self):
        """三本书、并发上限 2：峰值同时构建 ≤ 2，全部完成。"""
        ids = []
        for i in range(3):
            rec = tb_store.create_textbook("stu", file_id=f"f{i}", title=f"教材{i}")
            ids.append(rec["id"])
        active = 0
        peak = 0
        built: list[str] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03)
            built.append(tb_id)
            tb_store.update_textbook(student_id, tb_id, status="ready")
            active -= 1
            return None

        async def drive():
            futures = [self.builder.enqueue_textbook_build("stu", tid) for tid in ids]
            await asyncio.gather(*[f for f in futures if f])

        with patch.object(self.builder, "run_textbook_build", side_effect=fake_build):
            asyncio.run(drive())
        self.assertLessEqual(peak, 2)
        self.assertEqual(sorted(built), sorted(ids))


class TestBuildDeleteGuard(unittest.TestCase):
    """构建途中记录被删除：不把已删除 topic_key 的图谱/概念索引写回磁盘。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        from app.agents.knowledge import store as kgs_mod
        from app.agents.knowledge import textbook_builder
        self.builder = textbook_builder
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(kgs_mod, "_KG_DIR", root / "knowledge"),
            patch.object(kgs_mod, "_CUSTOM_DIR", root / "knowledge" / "custom"),
        ]
        for p in self.patches:
            p.start()
        ocr_policy._RUNTIME = ocr_policy._Runtime()
        textbook_builder._BUILD_LOCKS.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.builder._BUILD_LOCKS.clear()
        self.tmp.cleanup()

    def test_record_deleted_mid_build_writes_no_graph(self):
        from app.core.library import Library, library_data_dir, save_library
        lib = Library("stu")
        (library_data_dir("stu") / "f1.txt").write_text(
            "第一章 速度与加速度。运动的描述，匀变速直线运动的研究。", encoding="utf-8")
        lib.files.append({"id": "f1", "filename": "物理教材.pdf", "folder_id": "",
                          "char_count": 40, "chunk_count": 1, "orig_ext": "",
                          "kind": "textbook"})
        save_library(lib)
        tb = tb_store.create_textbook("stu", file_id="f1", title="物理教材")

        spec = {"subject": "物理", "level": "本科", "chapters": [
            {"name": "第一章", "concepts": [{"name": "速度", "difficulty": 2}]}]}

        async def spec_then_delete(*_a, **_k):
            # 模拟 LLM 抽取期间用户删除教材（长耗时窗口内的归档删除）
            tb_store.remove_textbook("stu", tb["id"])
            return spec

        with patch.object(self.builder, "_fast_path_spec",
                          side_effect=spec_then_delete):
            asyncio.run(self.builder.build_textbook_graph("stu", tb["id"], llm=object()))
        from app.agents.knowledge import store as kgs_mod
        self.assertIsNone(kgs_mod.load_custom_graph("stu", tb["topic_key"]))


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


if __name__ == "__main__":
    unittest.main()
