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


if __name__ == "__main__":
    unittest.main()
