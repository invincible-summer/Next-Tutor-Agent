"""Textbook OCR scheduler: queue build and delete guards."""
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
