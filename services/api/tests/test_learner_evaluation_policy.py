"""阶段C回归（update_plan §5–§6）：管理员可选评价方式与每日零点批次。

- 策略持久化/CAS/白名单/时区校验；默认方案 1；普通用户无修改权。
- daily 模式：受理冻结 eligible_after/本地活动日；零点前不可认领；
- 日边界：本地 23:59:59 与次日 00:00:00 分属正确日窗；
- 2→1 切换立即释放 backlog；关闭窗口幂等、no_observation 明确记录；
- 方案 2 开放题 task_only 立即反馈 + learner 零点解释（同一 journal）；
- 普通用户只读 schedule DTO。
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.identity import store as id_store
from app.identity.security import create_token, hash_password
from app.agents.student_model.evaluation import dialogue as dlg
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation import schedule as sched
from app.agents.student_model.evaluation.jobs import JobScheduler
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime
from app.core import learner_evaluation_policy as lep
from app.core.session import TutorSession, save_session
from app.core.workspace import Workspace, save_workspace
from tests.storage_sandbox import StorageSandboxTestCase

SID = "usr_policy_a"
WS = "ws_policy"
ADMIN = "usr_policy_admin"


class PolicyFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        id_store.create_user("admin@example.com", "管理员",
                             hash_password("pw123456"), user_id=ADMIN,
                             role="admin")
        self.admin_token = create_token(ADMIN)
        id_store.create_user("user@example.com", "学生",
                             hash_password("pw123456"), user_id=SID)
        self.user_token = create_token(SID)
        self.client = TestClient(create_app())
        save_workspace(Workspace(workspace_id=WS, name="物理",
                                 student_id=SID))
        self.session = TutorSession(session_id="sess_policy", grade="高中",
                                    student_id=SID, workspace_id=WS)
        save_session(self.session)

    def tearDown(self) -> None:
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def admin_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.admin_token}"}

    def user_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.user_token}"}

    def set_schedule(self, schedule: str, tz: str = "Asia/Singapore") -> dict:
        current = self.client.get(
            "/api/v1/admin/learner-evaluation-policy",
            headers=self.admin_headers()).json()["policy"]
        r = self.client.put(
            "/api/v1/admin/learner-evaluation-policy",
            json={"evaluation_schedule": schedule, "timezone": tz,
                  "daily_local_time": "00:00",
                  "expected_revision": current["revision"]},
            headers=self.admin_headers())
        assert r.status_code == 200, r.text
        return r.json()


class _FixedScopeResolverLike:
    """固定 scope（任意 workspace 名可用），绕开教材/图谱装配。"""

    def __init__(self, concepts):
        import app.agents.student_model.evaluation.schema as ES
        self._scope = ES.EvaluationScope(
            workspace_id="ws_placeholder", scope_revision="sr_policy",
            selected_volumes=[], allowed_concepts=concepts,
            graph_revisions=[], unresolved_graph_count=0)

    def resolve(self, student_id, workspace_id):
        return self._scope.model_copy(
            update={"workspace_id": workspace_id})

    def invalidate(self, *a, **kw):
        pass


class TestPolicyApi(PolicyFixture):
    def test_default_is_immediate(self):
        r = self.client.get("/api/v1/admin/learner-evaluation-policy",
                            headers=self.admin_headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["policy"]["evaluation_schedule"], "immediate")
        self.assertEqual(body["policy"]["timezone"], "Asia/Singapore")
        self.assertEqual(body["policy"]["daily_local_time"], "00:00")
        self.assertTrue(body["service_enabled"])
        self.assertIn("next_run_utc", body)

    def test_non_admin_cannot_read_or_write(self):
        r = self.client.get("/api/v1/admin/learner-evaluation-policy",
                            headers=self.user_headers())
        self.assertEqual(r.status_code, 403)
        r = self.client.put(
            "/api/v1/admin/learner-evaluation-policy",
            json={"evaluation_schedule": "daily_midnight",
                  "timezone": "Asia/Singapore", "expected_revision": 1},
            headers=self.user_headers())
        self.assertEqual(r.status_code, 403)

    def test_save_daily_then_read_back_with_revision_bump(self):
        out = self.set_schedule("daily_midnight")
        self.assertEqual(out["policy"]["evaluation_schedule"],
                         "daily_midnight")
        self.assertEqual(out["policy"]["revision"], 2)
        # 读回确认
        body = self.client.get(
            "/api/v1/admin/learner-evaluation-policy",
            headers=self.admin_headers()).json()
        self.assertEqual(body["policy"]["evaluation_schedule"],
                         "daily_midnight")
        self.assertTrue(body["next_run_utc"])

    def test_cas_conflict_409(self):
        self.set_schedule("daily_midnight")
        r = self.client.put(
            "/api/v1/admin/learner-evaluation-policy",
            json={"evaluation_schedule": "immediate",
                  "timezone": "Asia/Singapore", "expected_revision": 1},
            headers=self.admin_headers())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(
            r.json()["detail"]["error"]["code"], "policy_revision_conflict")

    def test_invalid_timezone_and_schedule_rejected(self):
        r = self.client.put(
            "/api/v1/admin/learner-evaluation-policy",
            json={"evaluation_schedule": "immediate",
                  "timezone": "Mars/Olympus", "expected_revision": 1},
            headers=self.admin_headers())
        self.assertEqual(r.status_code, 422)
        r = self.client.put(
            "/api/v1/admin/learner-evaluation-policy",
            json={"evaluation_schedule": "hourly",
                  "timezone": "Asia/Singapore", "expected_revision": 1},
            headers=self.admin_headers())
        self.assertEqual(r.status_code, 422)
        r = self.client.put(
            "/api/v1/admin/learner-evaluation-policy",
            json={"evaluation_schedule": "immediate",
                  "timezone": "Asia/Singapore", "daily_local_time": "03:30",
                  "expected_revision": 1},
            headers=self.admin_headers())
        self.assertEqual(r.status_code, 422)

    def test_user_readonly_schedule_dto(self):
        r = self.client.get("/api/v1/learner-evaluation/schedule",
                            headers=self.user_headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["evaluation_schedule"], "immediate")
        self.assertIn("立即", body["description"])
        self.set_schedule("daily_midnight")
        body = self.client.get("/api/v1/learner-evaluation/schedule",
                               headers=self.user_headers()).json()
        self.assertEqual(body["evaluation_schedule"], "daily_midnight")
        self.assertIn("前一自然日", body["description"])
        self.assertTrue(body["next_run_utc"])


class TestDailyScheduling(PolicyFixture):
    """§6.2/§6.3：受理冻结归属、零点门、日边界、2→1 释放、窗口关闭。"""

    def _register_message(self, mid: str) -> str:
        from tests.test_evaluation_worker import MSG
        sid = dlg.register_dialogue_source(
            student_id=SID, session=self.session,
            message={"role": "user", "content": MSG, "message_id": mid})
        self.assertIsNotNone(sid)
        return sid

    def test_daily_mode_defers_eligibility_and_local_date(self):
        self.set_schedule("daily_midnight")
        src = self._register_message("m_daily_1")
        state = get_journal(SID).state()
        job = next(rt.job for rt in state.jobs.values()
                   if rt.job.source_id == src)
        self.assertEqual(job.schedule_mode, "daily_midnight")
        self.assertTrue(job.eligible_after_utc > S.utc_now_iso())
        self.assertRegex(job.local_activity_date, r"^\d{4}-\d{2}-\d{2}$")
        # 零点前不可认领
        scheduler = JobScheduler()
        self.assertIsNone(scheduler.claim_next(SID))
        # 冻结的 eligible 到期后可认领（改到过去模拟零点已过）
        with sched.get_journal(SID).path.open(encoding="utf-8") as f:
            lines = f.read().splitlines()
        tx = S.JournalTransaction.model_validate_json(lines[-1])
        self.assertIsNotNone(get_journal(SID).state().jobs[job.job_id])
        journal = get_journal(SID)
        import json as _json
        rebuilt = []
        for line in lines:
            tx = _json.loads(line)
            for op in tx.get("operations", []):
                if op.get("op") == "job_requested":
                    op["job"]["eligible_after_utc"] = "2020-01-01T00:00:00Z"
            model = S.JournalTransaction.model_validate(tx)
            model.checksum = model.resolved_checksum()
            rebuilt.append(model)
        with journal.path.open("w", encoding="utf-8") as f:
            for tx in rebuilt:
                f.write(tx.model_dump_json() + chr(10))
        from app.agents.student_model.evaluation.store import \
            reset_journal_cache
        reset_journal_cache()
        self.assertIsNotNone(scheduler.claim_next(SID))

    def test_immediate_mode_has_no_eligible_gate(self):
        src = self._register_message("m_imm_1")
        state = get_journal(SID).state()
        job = next(rt.job for rt in state.jobs.values()
                   if rt.job.source_id == src)
        self.assertEqual(job.schedule_mode, "immediate")
        self.assertEqual(job.eligible_after_utc, "")
        self.assertIsNotNone(JobScheduler().claim_next(SID))

    def test_day_boundary_attribution(self):
        # §5.1：本地 23:59:59 属 D 日；00:00:00 属 D+1 日
        policy = {"evaluation_schedule": "daily_midnight",
                  "timezone": "Asia/Singapore", "revision": 1}
        late = lep.scheduling_facts("2026-09-14T15:59:59Z", policy)
        early = lep.scheduling_facts("2026-09-14T16:00:00Z", policy)
        self.assertEqual(late["local_activity_date"], "2026-09-14")
        self.assertEqual(early["local_activity_date"], "2026-09-15")
        self.assertEqual(late["eligible_after_utc"], "2026-09-14T16:00:00Z")
        self.assertEqual(early["eligible_after_utc"], "2026-09-15T16:00:00Z")
        # 零点评的是前一天
        win = lep.day_window_utc("2026-09-14", "Asia/Singapore")
        self.assertEqual(win, ("2026-09-13T16:00:00Z",
                               "2026-09-14T16:00:00Z"))

    def test_switch_to_immediate_releases_backlog(self):
        self.set_schedule("daily_midnight")
        src = self._register_message("m_rel_1")
        out = self.set_schedule("immediate")
        self.assertEqual(out["released_backlog"], 1)
        state = get_journal(SID).state()
        job = next(rt.job for rt in state.jobs.values()
                   if rt.job.source_id == src)
        self.assertEqual(job.eligible_after_utc, "")
        self.assertIsNotNone(JobScheduler().claim_next(SID))

    def test_close_due_windows_idempotent_with_counts(self):
        self.set_schedule("daily_midnight")
        src = self._register_message("m_close_1")
        # 把 job 推到终态（succeeded）并把 local_date 归到昨天
        journal = get_journal(SID)
        state = journal.state()
        job_id = next(rt.job.job_id for rt in state.jobs.values()
                      if rt.job.source_id == src)
        from datetime import timedelta
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)) \
            .strftime("%Y-%m-%d")
        journal.append([
            S.OpJobRescheduled(job_id=job_id, reason="test"),
            S.OpJobCancelled(job_id=job_id, reason="test_cancel"),
        ])
        # 直接重写 job 的调度字段到昨天（模拟昨日活动）
        import json as _json
        with journal.path.open(encoding="utf-8") as f:
            lines = f.read().splitlines()
        rebuilt = []
        for line in lines:
            tx = _json.loads(line)
            for op in tx.get("operations", []):
                if op.get("op") == "job_requested":
                    op["job"]["local_activity_date"] = yesterday
                    op["job"]["eligible_after_utc"] = "2020-01-01T00:00:00Z"
            model = S.JournalTransaction.model_validate(tx)
            model.checksum = model.resolved_checksum()
            rebuilt.append(model)
        with journal.path.open("w", encoding="utf-8") as f:
            for tx in rebuilt:
                f.write(tx.model_dump_json() + chr(10))
        from app.agents.student_model.evaluation.store import \
            reset_journal_cache
        reset_journal_cache()

        closed = sched.close_due_day_windows()
        self.assertGreaterEqual(closed, 1)
        state = get_journal(SID).state()
        batch = state.daily_batches.get(yesterday)
        self.assertIsNotNone(batch)
        self.assertEqual(batch["candidate_source_ids"], [src])
        self.assertEqual(batch["evaluated_count"], 0)
        self.assertEqual(batch["failed_count"], 0)
        self.assertTrue(batch["no_observation"])
        # 幂等：再关一次不重复
        self.assertEqual(sched.close_due_day_windows(), 0)

    def test_worker_completes_eligible_daily_job(self):
        """零点已过（eligible 到期）→ worker 正常执行当日候选。"""
        from app.agents.student_model.evaluation.worker import EvaluationWorker
        from tests.test_evaluation_worker import _EvalRunner, _concept
        # R05 CAS：注册与执行必须同一 scope_revision——受理前装 resolver
        from app.agents.student_model.evaluation.scope import set_scope_resolver
        set_scope_resolver(_FixedScopeResolverLike([_concept()]))
        self.set_schedule("daily_midnight")
        src = self._register_message("m_run_1")
        journal = get_journal(SID)
        import json as _json
        with journal.path.open(encoding="utf-8") as f:
            lines = f.read().splitlines()
        rebuilt = []
        for line in lines:
            tx = _json.loads(line)
            for op in tx.get("operations", []):
                if op.get("op") == "job_requested":
                    op["job"]["eligible_after_utc"] = "2020-01-01T00:00:00Z"
            model = S.JournalTransaction.model_validate(tx)
            model.checksum = model.resolved_checksum()
            rebuilt.append(model)
        with journal.path.open("w", encoding="utf-8") as f:
            for tx in rebuilt:
                f.write(tx.model_dump_json() + chr(10))
        from app.agents.student_model.evaluation.store import \
            reset_journal_cache
        reset_journal_cache()

        runner = _EvalRunner()
        asyncio.run(EvaluationWorker(
            runner_provider=lambda: runner).process_pass())
        state = get_journal(SID).state()
        rt = next(rt for rt in state.jobs.values()
                  if rt.job.source_id == src)
        self.assertEqual(rt.job.state, S.JobState.SUCCEEDED,
                         f"code={rt.last_error_code}")
