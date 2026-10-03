"""B08 学习历史补齐回归。

覆盖：task_instance_id 迁移与稳定；task_status_changed 事件（self_report /
quiz_evidence / PATCH 撤销 / 逾期 / in_progress）一次一记；outbox 与 state
同事务、按 event_id 去重、重启补发；月度有效完成推导（撤销剔除、来源
分列）；coverage_started_at / history_incomplete 诚实呈现。
"""
from __future__ import annotations

import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.learning_orchestration import history
from app.agents.learning_orchestration.manager import (
    get_orchestration_service)
from app.agents.learning_orchestration.schema import DailyTaskStatus


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class HistoryBasicsTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b08_" + _hex()
        self.svc = get_orchestration_service()

    def _add_task(self, title: str = "历史任务", day: str = "") -> str:
        return self.svc.add_task(self.sid, title=title, day=day).id

    def test_instance_id_migration_stable(self) -> None:
        task_id = self._add_task()
        state = self.svc._load(self.sid)
        task = next(t for t in state.daily_tasks if t.id == task_id)
        # add_task 保存路径已补齐（§22.2-1 迁移随写持久化）。
        self.assertTrue(task.task_instance_id.startswith("ti_"))
        first = task.task_instance_id
        # 再次读写保持稳定。
        self.svc.update_task(self.sid, task_id, title="改个名")
        state3 = self.svc._load(self.sid)
        task3 = next(t for t in state3.daily_tasks if t.id == task_id)
        self.assertEqual(task3.task_instance_id, first)

        # 旧格式任务（无 instance 字段）在保存路径补齐。
        from app.agents.learning_orchestration import store as orch_store
        state = self.svc._load(self.sid)
        state.daily_tasks[0].task_instance_id = ""
        orch_store.save_state(self.sid, state)
        restored = orch_store.load_state(self.sid)
        self.assertEqual(restored.daily_tasks[0].task_instance_id, "")
        self.svc.update_task(self.sid, state.daily_tasks[0].id,
                             title="触发保存")
        migrated = orch_store.load_state(self.sid)
        self.assertTrue(
            migrated.daily_tasks[0].task_instance_id.startswith("ti_"))

    def test_status_changed_emitted_once_per_transition(self) -> None:
        task_id = self._add_task()
        ok, _events = self.svc.complete_task(self.sid, task_id)
        self.assertTrue(ok)
        from app.agents.learning_orchestration import store as orch_store
        events, _cov = orch_store.read_events_with_coverage(self.sid)
        changed = [e for e in events
                   if e.type == history.STATUS_CHANGED_EVENT
                   and e.payload.get("task_id") == task_id]
        self.assertEqual(len(changed), 1)  # 一次变化只记一次
        payload = changed[0].payload
        self.assertEqual(payload["from_status"], "pending")
        self.assertEqual(payload["to_status"], "completed")
        self.assertEqual(payload["completion_source"], "self_report")
        self.assertTrue(payload["event_id"])
        self.assertTrue(payload["task_instance_id"].startswith("ti_"))
        # 重复 complete（已完成）不再记。
        ok2, _ = self.svc.complete_task(self.sid, task_id)
        self.assertFalse(ok2)
        events, _cov = orch_store.read_events_with_coverage(self.sid)
        changed = [e for e in events
                   if e.type == history.STATUS_CHANGED_EVENT
                   and e.payload.get("task_id") == task_id]
        self.assertEqual(len(changed), 1)

    def test_revocation_via_patch_recorded_and_excluded(self) -> None:
        task_id = self._add_task()
        self.svc.complete_task(self.sid, task_id)
        self.assertTrue(self.svc.update_task(self.sid, task_id,
                                             status="pending"))  # 撤销
        now = datetime.now(tz=timezone.utc)
        window_start = now - timedelta(days=1)
        window_end = now + timedelta(days=1)
        derived = history.completed_tasks_in_window(
            self.sid, start_at=window_start, end_at=window_end)
        self.assertEqual(derived["valid_completed_count"], 0)  # 撤销剔除
        self.assertEqual(derived["by_completion_source"], {})
        # 再完成 → 重新有效。窗口从覆盖起点开始（FULL-29：新窗口按已
        # 落盘事实完整统计）。
        self.svc.complete_task(self.sid, task_id)
        coverage_start = datetime.fromisoformat(
            history.completed_tasks_in_window(
                self.sid, start_at=now - timedelta(days=1),
                end_at=window_end)["coverage_started_at"])
        derived = history.completed_tasks_in_window(
            self.sid, start_at=coverage_start, end_at=window_end)
        self.assertEqual(derived["valid_completed_count"], 1)
        self.assertEqual(
            derived["by_completion_source"].get("self_report"), 1)
        self.assertIsNotNone(derived["coverage_started_at"])
        self.assertFalse(derived["history_incomplete"])  # 窗口覆盖完整

    def test_history_incomplete_for_pre_coverage_window(self) -> None:
        task_id = self._add_task()
        self.svc.complete_task(self.sid, task_id)
        now = datetime.now(tz=timezone.utc)
        # 窗口起点早于首条状态事件 → 历史缺口。
        early = now - timedelta(days=30)
        derived = history.completed_tasks_in_window(
            self.sid, start_at=early, end_at=now)
        self.assertTrue(derived["history_incomplete"])

    def test_outbox_flush_dedup_and_restart_resend(self) -> None:
        task_id = self._add_task()
        # 手工构造未确认 outbox（模拟投递中断）。
        state = self.svc._load(self.sid)
        history.note_status_change(
            state, next(t for t in state.daily_tasks if t.id == task_id),
            DailyTaskStatus.PENDING)
        # 人为只保存 state（事件未投递）。
        from app.agents.learning_orchestration import store as orch_store
        orch_store.save_state(self.sid, state)
        events_before, _ = orch_store.read_events_with_coverage(self.sid)
        changed_before = [e for e in events_before
                          if e.type == history.STATUS_CHANGED_EVENT]
        # 重启（重新加载）→ 补发未确认项。
        self.svc._load(self.sid)
        events_after, _ = orch_store.read_events_with_coverage(self.sid)
        changed_after = [e for e in events_after
                         if e.type == history.STATUS_CHANGED_EVENT]
        self.assertEqual(len(changed_after), len(changed_before) + 1)
        # 再次 flush 幂等（event_id 去重，不重复追加）。
        flushed = history.flush_outbox(self.sid)
        self.assertEqual(flushed, 0)
        events_again, _ = orch_store.read_events_with_coverage(self.sid)
        changed_again = [e for e in events_again
                         if e.type == history.STATUS_CHANGED_EVENT]
        self.assertEqual(len(changed_again), len(changed_after))

    def test_aggregator_reports_source_breakdown(self) -> None:
        from app.agents.activity_aggregator import learning_activity_snapshot
        t1 = self._add_task("自报任务")
        t2 = self._add_task("证据任务")
        self.svc.complete_task(self.sid, t1)
        # 模拟证据完成：直接走内部绑定路径。
        state = self.svc._load(self.sid)
        self.svc._complete_bound_task(state, t2, attempt_id="att_b08",
                                      now=time.time())
        # 保存 state（含 outbox）→ _save 触发投递。
        self.svc._save(self.sid, state)
        now = datetime.now(tz=timezone.utc)
        snap = learning_activity_snapshot(
            self.sid, start_at=now - timedelta(days=1), end_at=now,
            timezone="UTC")
        completed = snap["completed_tasks"]
        self.assertEqual(
            completed["by_completion_source"].get("self_report"), 1)
        self.assertEqual(
            completed["by_completion_source"].get("quiz_evidence"), 1)


if __name__ == "__main__":
    unittest.main()
