"""站内助手数据投影回归（A03 / GAP-03/04/05）。

覆盖：learning_activity_snapshot 的空态/作答口径/归属/时区、M9 事件
严格读取与任务完成下界、M7 窗口投影的完整性与坏行、GAP-04 只读任务
快照不落盘、daily_counts 不再引用废弃 lr 读取。
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents import activity_aggregator
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation import store as st

SID = "usr_proj_test"


def _now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _receipt(source_id: str, *, observed_at: str, ws: str = "",
             attempt_id: str = "") -> S.SourceReceipt:
    return S.SourceReceipt(
        source_id=source_id, source_revision=1,
        kind=S.SourceKind.ASSESSMENT, observed_at=observed_at,
        workspace_id_at_observation=ws, canonical_text="answer text",
        attempt_id=attempt_id)


def _graded_result(verdict: str = "correct") -> S.TaskResult:
    return S.TaskResult(
        question_ref=S.QuestionRef(question_id="q1", question_revision=1),
        grading_status=S.GradingStatus.GRADED,
        verdict=S.Verdict(verdict))


def _window(hours: float = 24.0):
    end = datetime.now(timezone.utc)
    return end - timedelta(hours=hours), end


class ActivitySnapshotTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        st.reset_journal_cache()

    def test_empty_user_is_empty_not_error(self) -> None:
        start, end = _window()
        snap = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="Asia/Shanghai")
        self.assertEqual(snap["metric_version"], 2)
        self.assertEqual(snap["sources"]["learning_evidence"]["status"], "empty")
        self.assertEqual(snap["recorded_learning_days"], 0)
        self.assertTrue(snap["complete"])
        self.assertEqual(snap["answers"]["answer_attempt_count"], 0)

    def test_answer_metrics_and_local_days(self) -> None:
        journal = st.get_journal(SID)
        now_iso = _now_iso_z()
        journal.append([
            S.OpSourceRegistered(source=_receipt(
                "src_a", observed_at=now_iso, ws="ws_physics")),
            S.OpResultCommitted(
                job_id="job_a", source_id="src_a", source_revision=1,
                scope_revision="sr1", task_result=_graded_result(),
                interpretation_id="interp_a"),
            S.OpSourceRegistered(source=_receipt(
                "src_mc", observed_at=now_iso, ws="ws_physics")),
            # MC 仅判分：当前解释 ID 为空串，task_result 挂在 "" 解释上。
            S.OpResultCommitted(
                job_id="job_mc", source_id="src_mc", source_revision=1,
                scope_revision="sr1", task_result=_graded_result("wrong"),
                interpretation_id=""),
            S.OpSourceRegistered(source=_receipt(
                "src_pending", observed_at=now_iso, ws="ws_physics")),
        ])
        start, end = _window()
        snap = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="Asia/Shanghai")
        answers = snap["answers"]
        self.assertEqual(answers["answer_attempt_count"], 3)
        self.assertEqual(answers["graded_answer_count"], 2)  # 含 MC
        self.assertEqual(answers["pending_answer_count"], 1)
        self.assertEqual(answers["pending_buckets"]["pending"], 1)
        self.assertEqual(snap["days_by_workspace"].get("ws_physics"), 1)
        self.assertEqual(snap["recorded_learning_days"], 1)

    def test_attempt_dedup_and_archived_excluded(self) -> None:
        journal = st.get_journal(SID)
        now_iso = _now_iso_z()
        journal.append([
            S.OpSourceRegistered(source=_receipt(
                "src_1", observed_at=now_iso, attempt_id="att_9")),
            S.OpSourceRegistered(source=_receipt(
                "src_2", observed_at=now_iso, attempt_id="att_9")),
        ])
        # 手工归档 src_2
        state = journal.state()
        state.sources["src_2"].availability = "archived"
        start, end = _window()
        snap = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="UTC")
        self.assertEqual(snap["answers"]["answer_attempt_count"], 1)
        self.assertEqual(
            snap["sources"]["learning_evidence"]["archived_skipped"], 1)

    def test_workspace_filter_excludes_unattributed(self) -> None:
        journal = st.get_journal(SID)
        now_iso = _now_iso_z()
        journal.append([
            S.OpSourceRegistered(source=_receipt(
                "src_a", observed_at=now_iso, ws="ws_a")),
            S.OpSourceRegistered(source=_receipt(
                "src_b", observed_at=now_iso, ws="ws_b")),
            S.OpSourceRegistered(source=_receipt(
                "src_none", observed_at=now_iso, ws="")),
        ])
        start, end = _window()
        snap = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="UTC",
            workspace_ids=["ws_a"])
        self.assertEqual(list(snap["days_by_workspace"].keys()), ["ws_a"])
        self.assertFalse(snap["unscoped"]["included"])
        self.assertEqual(snap["recorded_learning_days"], 1)
        # 全局模式保留未归属分组
        snap_global = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="UTC")
        self.assertTrue(snap_global["unscoped"]["included"])
        self.assertEqual(snap_global["unscoped"]["days"], 1)
        self.assertEqual(len(snap_global["days_by_workspace"]), 2)

    def test_teaching_log_global_only_and_review_events(self) -> None:
        from app.agents.teaching_engine import teaching_log as tlog
        tlog.record_turn_outcome(
            SID, "concept_x", mode="socratic", outcome="correct")
        from app.agents.learning_orchestration import store as orch_store
        from app.agents.learning_orchestration.schema import OrchestrationEvent
        orch_store.append_event(SID, OrchestrationEvent(
            type="task_completed", payload={"task_id": "t1"}))
        orch_store.append_event(SID, OrchestrationEvent(
            type="srs_review", payload={"concept_id": "c1"}))
        # 这些不应计入任务完成/复习
        orch_store.append_event(SID, OrchestrationEvent(
            type="task_launched", payload={"task_id": "t1"}))
        orch_store.append_event(SID, OrchestrationEvent(
            type="quiz_evidence", payload={"task_id": "t2"}))

        start, end = _window()
        snap = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="UTC")
        completed = snap["completed_tasks"]
        # §22.2-6（B08）：legacy task_completed 事件缺 instance 字段，
        # 只作已知下界；不伪装成完整总数。
        self.assertEqual(completed["known_minimum"], 1)
        self.assertTrue(completed["history_incomplete"])
        self.assertIsNone(completed["value"])
        self.assertFalse(completed["complete"])
        self.assertEqual(snap["sources"]["orchestration_events"]
                         ["review_submissions"], 1)
        # 教学轮在全局计入 unscoped
        self.assertGreaterEqual(snap["unscoped"]["days"], 1)
        # 单工作区查询排除教学与复习（无归属）
        snap_filtered = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="UTC",
            workspace_ids=["ws_a"])
        self.assertEqual(snap_filtered["recorded_learning_days"], 0)
        self.assertIn("仅计入全局统计",
                      snap_filtered["sources"]["teaching_log"]["note"])

    def test_invalid_timezone_falls_back_utc(self) -> None:
        start, end = _window()
        snap = activity_aggregator.learning_activity_snapshot(
            SID, start_at=start, end_at=end, timezone="Mars/Olympus")
        self.assertEqual(snap["window"]["timezone"], "UTC")
        self.assertTrue(any(n["code"] == "timezone_invalid"
                            for n in snap["notices"]))

    def test_daily_counts_reads_journal_after_gap03_fix(self) -> None:
        journal = st.get_journal(SID)
        now_iso = _now_iso_z()
        journal.append([
            S.OpSourceRegistered(source=_receipt(
                "src_z", observed_at=now_iso, ws="ws_a")),
            S.OpResultCommitted(
                job_id="job_z", source_id="src_z", source_revision=1,
                scope_revision="sr", task_result=_graded_result(),
                interpretation_id="i_z"),
        ])
        rows = activity_aggregator.daily_counts(SID, days=3)
        self.assertEqual(sum(r["answers"] for r in rows), 1)
        # 未判分来源不算作答
        journal.append([S.OpSourceRegistered(source=_receipt(
            "src_ungraded", observed_at=now_iso, ws="ws_a"))])
        rows = activity_aggregator.daily_counts(SID, days=3)
        self.assertEqual(sum(r["answers"] for r in rows), 1)

    def test_last_learned_concept_no_lr_crash(self) -> None:
        # GAP-03：旧路径引用未定义 lr；修复后无数据也不应抛错。
        self.assertEqual(activity_aggregator.last_learned_concept(SID), "")


class TeachingWindowReportTest(StorageSandboxTestCase):
    def test_window_filter_and_invalid_lines(self) -> None:
        from app.agents.evaluation import window as eval_window
        path = eval_window.eval_store._resolve(
            SID, ext=".eval_traces.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        recent_ts = end.timestamp() - 3600
        old_ts = (end - timedelta(days=30)).timestamp()
        rows = [
            {"id": "tr1", "ts": recent_ts, "mode": "socratic",
             "outcome": "correct", "failure_type": "none"},
            {"id": "tr2", "ts": recent_ts, "mode": "expository",
             "outcome": "wrong", "failure_type": "depth_mismatch"},
            {"id": "tr3", "ts": old_ts, "mode": "socratic",
             "outcome": "correct"},
        ]
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.write("this is not json\n")
        report = eval_window.teaching_window_report(
            SID, start_at=start, end_at=end)
        self.assertEqual(report["scope_mode"], "account")
        self.assertEqual(report["total_turns"], 2)
        self.assertEqual(report["coverage"]["invalid_count"], 1)
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["status"], "partial")
        self.assertIsNotNone(report["coverage"]["earliest_available_at"])
        self.assertEqual(len(report["top_strategies"]) >= 0, True)

    def test_missing_file_is_empty(self) -> None:
        from app.agents.evaluation import window as eval_window
        start, end = _window()
        report = eval_window.teaching_window_report(
            "usr_no_traces_here", start_at=start, end_at=end)
        self.assertEqual(report["status"], "empty")
        self.assertEqual(report["total_turns"], 0)
        self.assertTrue(report["coverage"]["complete"])


class SavedTasksSnapshotTest(StorageSandboxTestCase):
    def test_read_only_no_persist(self) -> None:
        from app.agents.learning_orchestration import store as orch_store
        from app.agents.learning_orchestration.schema import DailyTask, DailyTaskStatus
        from app.agents.learning_orchestration.manager import get_orchestration_service
        day = time.strftime("%Y-%m-%d")
        state = orch_store.load_state(SID)
        state.daily_tasks = [
            DailyTask(id="t1", day=day, concept_id="c1",
                      status=DailyTaskStatus.COMPLETED, completed_at=time.time()),
            DailyTask(id="t2", day=day, concept_id="c2", status=DailyTaskStatus.PENDING),
        ]
        self.assertTrue(orch_store.save_state(SID, state))
        state_path = orch_store._resolve(SID, ext=".orchestration.json")
        mtime_before = state_path.stat().st_mtime_ns

        service = get_orchestration_service()
        snap = asyncio.run(service.saved_tasks_snapshot(SID))
        self.assertTrue(snap["read_only"])
        self.assertEqual(len(snap["today"]), 2)
        self.assertEqual(len(snap["open"]), 1)
        self.assertEqual(snap["open"][0]["id"], "t2")
        self.assertEqual(len(snap["recently_completed"]), 1)
        self.assertFalse(snap["coverage"]["history_complete"])
        # 只读：文件未被重写
        self.assertEqual(state_path.stat().st_mtime_ns, mtime_before)


class UxActivityMetricVersionTest(StorageSandboxTestCase):
    def test_activity_carries_metric_version(self) -> None:
        from app.agents.ux_intelligence import manager as ux_manager
        payload = ux_manager.get_ux_service().activity(SID, days=7)
        self.assertEqual(payload.get("metric_version"), 2)
        self.assertIn("days", payload)
        self.assertIn("streak_days", payload)


if __name__ == "__main__":
    unittest.main()
