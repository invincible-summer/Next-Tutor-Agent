"""Contract tests: 非 OCR 教材图谱构建的重启恢复（plan.md §9-§14, Phase 1）。

当前行为（必须先失败证明 gap）：
  reap_stale_builds() 把一切非 OCR-pending 的 building 记录直接判
  graph_failed，要求用户手动点「重建图谱」。

目标行为：
  building + build_job.intent + source 文件在 → reconcile 后重新入队
  （build_job.state=queued），由 lifespan 恢复流程重进现有 per-owner 队列；
  普通进程重启不是失败。

Phase 4 实现后全绿。
"""
from __future__ import annotations

import asyncio

import unittest

from tests.storage_sandbox import StorageSandboxTestCase


def _make_textbook_with_source(case, sid="student_recovery", status="building"):
    """建一本有源文件文本的教材（group 形态），并置目标状态。"""
    from app.core import textbook as tb
    from app.core.library import library_data_dir
    data_dir = library_data_dir(sid)
    data_dir.mkdir(parents=True, exist_ok=True)
    fid = "filerec01"
    (data_dir / f"{fid}.txt").write_text(
        "第三章 ZX-17 定理\nZX-17 定理的右端常数为 314159。\n" * 20,
        encoding="utf-8")
    rec = tb.create_group(sid, file_ids=[fid], title="恢复测试教材")
    if status != "building":
        tb.update_textbook(sid, rec["id"], status=status)
    return rec


class TestTextbookBuildRecoveryContract(StorageSandboxTestCase):

    def test_set_and_clear_build_job(self):
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb_id = rec["id"]
        updated = tb.set_build_job(sid, tb_id, state="queued", phase="prepare",
                                   mode="auto", intent={"use_llm": True})
        self.assertIsNotNone(updated)
        loaded = tb.find_textbook(sid, tb_id)
        job = loaded.get("build_job") or {}
        self.assertEqual(job.get("state"), "queued")
        self.assertEqual(job.get("phase"), "prepare")
        self.assertEqual(job.get("intent"), {"use_llm": True})
        self.assertEqual(job.get("schema_version"), 1)
        tb.clear_build_job(sid, tb_id)
        self.assertFalse((tb.find_textbook(sid, tb_id) or {}).get("build_job"))

    def test_concept_extract_restart_reenqueues(self):
        """building + phase=concept_extract 重启 → 重新入队而非 graph_failed。"""
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="concept_extract", mode="auto",
                         attempt=1, intent={"use_llm": True})
        report = tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        job = (loaded or {}).get("build_job") or {}
        self.assertEqual(job.get("state"), "queued",
                         "重启后 build_job 应置 queued 等待重入队")
        self.assertNotEqual(loaded.get("status"), "graph_failed",
                            "非 OCR 构建中断不应被判为 graph_failed")
        self.assertEqual(len(report.recovered), 1)
        self.assertEqual(report.recovered[0][1], rec["id"])

    def test_graph_merge_restart_reenqueues(self):
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="graph_merge", mode="auto", attempt=1,
                         intent={"use_llm": True})
        tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        self.assertEqual((loaded.get("build_job") or {}).get("state"), "queued")

    def test_ocr_pending_still_goes_to_ocr_waiting(self):
        """OCR 可恢复记录继续走现有 ocr_waiting 路径，不进 graph 恢复。"""
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb.update_textbook(sid, rec["id"], ocr_state={
            "volumes": {"v1": {"status": "ocr", "pending_pages": [1, 2],
                               "target_pages": [1, 2, 3],
                               "successful_pages": [3]}}})
        report = tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        self.assertEqual(loaded.get("status"), "ocr_waiting")
        self.assertEqual(report.recovered, [])

    def test_reconcile_is_idempotent(self):
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="concept_extract", attempt=1,
                         intent={"use_llm": True})
        tb.reconcile_stale_builds()
        first = tb.find_textbook(sid, rec["id"])
        report2 = tb.reconcile_stale_builds()
        second = tb.find_textbook(sid, rec["id"])
        self.assertEqual((first.get("build_job") or {}).get("state"), "queued")
        self.assertEqual((second.get("build_job") or {}).get("state"), "queued")
        self.assertEqual(report2.recovered, [], "重复 reconcile 不得重复恢复")

    def test_ready_records_untouched(self):
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid, status="ready")
        report = tb.reconcile_stale_builds()
        self.assertEqual(tb.find_textbook(sid, rec["id"])["status"], "ready")
        self.assertEqual(report.recovered, [])

    def test_source_missing_marks_failed(self):
        from app.core import textbook as tb
        from app.core.library import library_data_dir
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        (library_data_dir(sid) / "filerec01.txt").unlink()
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="concept_extract", attempt=1,
                         intent={"use_llm": True})
        report = tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        self.assertIn(loaded.get("status"), ("failed", "graph_failed"))
        self.assertEqual((loaded.get("build_job") or {}).get("state"), "failed")
        self.assertEqual((loaded.get("build_job") or {}).get("last_error"),
                         "source_missing")

    def test_legacy_building_without_job_synthesizes_intent(self):
        """旧记录（无 build_job）+ 源文件在 → 合成默认 intent 重新入队。"""
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        report = tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        job = loaded.get("build_job") or {}
        self.assertEqual(job.get("state"), "queued")
        self.assertTrue(job.get("intent"), "legacy 记录必须合成默认 intent")
        self.assertEqual(len(report.recovered), 1)

    def test_graph_failed_history_not_auto_rerun(self):
        """已终态 graph_failed 的旧失败不自动重跑（plan §14.5）。"""
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid, status="graph_failed")
        tb.set_build_job(sid, rec["id"], state="failed", attempt=1,
                         intent={"use_llm": True})
        report = tb.reconcile_stale_builds()
        self.assertEqual(report.recovered, [])
        self.assertEqual(tb.find_textbook(sid, rec["id"])["status"],
                         "graph_failed")

    def test_retry_exhausted_not_requeued(self):
        """同一 intent 自动恢复超过上限（3）→ retry_exhausted 终态。"""
        from app.core import textbook as tb
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="concept_extract", attempt=3,
                         intent={"use_llm": True})
        report = tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        self.assertEqual(loaded.get("status"), "graph_failed")
        self.assertEqual((loaded.get("build_job") or {}).get("state"), "failed")
        self.assertEqual((loaded.get("build_job") or {}).get("last_error"),
                         "retry_exhausted")
        self.assertEqual(report.recovered, [])

    def test_enqueue_persists_intent_and_resume_requeues(self):
        """enqueue 前持久化 intent；resume 把 reconcile 后的 queued job 重入队。"""
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder as builder
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        # 模拟重启中断：曾有 intent 的 running job。
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="graph_merge", attempt=1,
                         intent={"use_llm": False, "skip_ocr": True})
        tb.reconcile_stale_builds()

        async def _drive():
            # 直接调用 resume（lifespan 中的入口）；记录非终态会入队。
            # 不真正跑 LLM：把 run_textbook_build 打桩观察 intent 传递。
            calls: list[dict] = []

            async def _fake_run(student_id, tb_id, **kwargs):
                calls.append({"sid": student_id, "tb_id": tb_id, **kwargs})

            original = builder.run_textbook_build
            builder.run_textbook_build = _fake_run
            try:
                resumed = await builder.resume_interrupted_textbook_builds()
                # 给事件循环一个 tick 让队列 worker 消费。
                await asyncio.sleep(0.05)
                return resumed, calls
            finally:
                builder.run_textbook_build = original

        resumed, calls = asyncio.run(_drive())
        self.assertEqual(resumed, 1)
        self.assertTrue(calls, "恢复项必须真的重入现有队列执行")
        self.assertEqual(calls[0]["tb_id"], rec["id"])
        self.assertTrue(calls[0].get("auto_retry"), "恢复项带 auto_retry 语义")
        self.assertFalse(calls[0].get("use_llm"))
        self.assertTrue(calls[0].get("skip_ocr"))
        # 执行侧 worker 已把 job 置 running（_mark_build_running 在 run 前，
        # 但 run 被打桩——直接验证打桩路径不写状态即可，真实路径由
        # test_worker_marks_running_and_attempt 验证）。

    def test_worker_marks_running_and_attempt(self):
        """run_textbook_build 执行时 build_job 置 running 且 attempt 递增。"""
        from unittest.mock import patch
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder as builder
        sid = "student_recovery"
        rec = _make_textbook_with_source(self, sid)
        tb.set_build_job(sid, rec["id"], state="queued", attempt=1,
                         intent={"use_llm": False})

        async def _noop_build(*a, **k):
            return None

        async def _drive():
            # 打桩内部 build，只验证 run_textbook_build 的 job 记账。
            with patch.object(builder, "build_group_graph", _noop_build), \
                 patch.object(builder, "build_textbook_graph", _noop_build):
                await builder.run_textbook_build(sid, rec["id"], use_llm=False)

        asyncio.run(_drive())
        job = (tb.find_textbook(sid, rec["id"]) or {}).get("build_job") or {}
        self.assertEqual(job.get("state"), "running")
        self.assertEqual(job.get("attempt"), 2, "worker 开始时 attempt+1")

    def test_public_namespace_recovers_like_private(self):
        """public namespace 的恢复逻辑与 private 一致（plan §14.11）。"""
        from app.core import textbook as tb
        from app.core.library import library_data_dir
        sid = tb.PUBLIC_STUDENT_ID
        data_dir = library_data_dir(sid)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "filerec02.txt").write_text(
            "第一章 公共教材\n公共教材包含公开知识点内容。\n" * 20,
            encoding="utf-8")
        rec = tb.create_group(sid, file_ids=["filerec02"], title="公共恢复测试")
        tb.set_build_job(sid, rec["id"], state="running",
                         phase="concept_extract", attempt=1,
                         intent={"use_llm": True})
        report = tb.reconcile_stale_builds()
        loaded = tb.find_textbook(sid, rec["id"])
        self.assertEqual((loaded.get("build_job") or {}).get("state"), "queued")
        self.assertEqual(len(report.recovered), 1)
        self.assertEqual(report.recovered[0][0], sid)


if __name__ == "__main__":
    unittest.main()
