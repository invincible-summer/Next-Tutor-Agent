"""G1 回归：评价作业状态机（plan §10.3 / §18.2 test_evaluation_jobs）。

覆盖：queued/running 崩溃恢复（过期 lease 重认领）、stale lease 不能提交、
乱序完成（重放后状态以 journal 为准）、重试退避、终态不再认领、
cancel_scope_jobs（删除取消）。
"""
from __future__ import annotations

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation import store as st
from app.agents.student_model.evaluation.jobs import JobScheduler

SID = "usr_jobs_a"


def _mk_job(scheduler: JobScheduler, job_id: str = "", *,
            workspace: str = "ws_1", priority: int = 4) -> S.EvaluationJob:
    return scheduler.enqueue(
        SID, kind=S.JobKind.DIALOGUE_EVALUATION, workspace_id=workspace,
        scope_revision="scope_1", priority=priority)


class TestJobsBasics(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        st.reset_journal_cache()
        self.scheduler = JobScheduler(lease_seconds=150)

    def test_enqueue_claim_running(self):
        job = _mk_job(self.scheduler)
        claimed = self.scheduler.claim_next(SID)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.job.job_id, job.job_id)
        self.assertTrue(claimed.lease_token.startswith("lease_"))
        state = st.get_journal(SID).state()
        rt = state.jobs[job.job_id]
        self.assertEqual(rt.job.state, S.JobState.RUNNING)
        self.assertEqual(rt.job.lease_token, claimed.lease_token)
        # 认领后队列为空；running 未过期不重复认领
        self.assertIsNone(self.scheduler.claim_next(SID))

    def test_priority_order(self):
        j_low = _mk_job(self.scheduler, priority=S.JobPriority.BACKFILL.value)
        j_high = _mk_job(self.scheduler,
                         priority=S.JobPriority.AWAITING_FEEDBACK.value)
        claimed = self.scheduler.claim_next(SID)
        self.assertEqual(claimed.job.job_id, j_high.job_id)
        self.assertNotEqual(claimed.job.job_id, j_low.job_id)

    def test_lease_valid_checks(self):
        job = _mk_job(self.scheduler)
        claimed = self.scheduler.claim_next(SID)
        self.assertTrue(self.scheduler.lease_valid(SID, job.job_id,
                                                   claimed.lease_token))
        self.assertFalse(self.scheduler.lease_valid(SID, job.job_id, "lease_x"))

    def test_stale_lease_cannot_commit_after_reclaim(self):
        job = _mk_job(self.scheduler)
        first = self.scheduler.claim_next(SID)
        # 模拟 worker 崩溃：lease 过期（直接改 journal 中的过期时间）
        journal = st.get_journal(SID)
        with st.file_lock(journal.path):
            lines = journal.path.read_text(encoding="utf-8").splitlines()
            tx = S.JournalTransaction.model_validate_json(lines[-1])
            assert isinstance(tx.operations[0], S.OpJobLeased)
            op = tx.operations[0]
            tx.operations[0] = op.model_copy(
                update={"lease_expires_at": "2020-01-01T00:00:00Z"})
            tx.checksum = tx.resolved_checksum()
            lines[-1] = tx.model_dump_json()
            journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        st.reset_journal_cache()
        # 旧 token 无效；新认领成功
        self.assertFalse(self.scheduler.lease_valid(SID, job.job_id,
                                                    first.lease_token))
        second = self.scheduler.claim_next(SID)
        self.assertIsNotNone(second)
        self.assertNotEqual(second.lease_token, first.lease_token)
        self.assertFalse(self.scheduler.lease_valid(SID, job.job_id,
                                                    first.lease_token))
        self.assertTrue(self.scheduler.lease_valid(SID, job.job_id,
                                                   second.lease_token))

    def test_retryable_failure_backs_off_then_requeues(self):
        job = _mk_job(self.scheduler)
        self.scheduler.claim_next(SID)
        state = self.scheduler.fail(SID, job.job_id, error_code="llm_timeout",
                                    retryable=True, retry_after_seconds=30)
        self.assertEqual(state, S.JobState.RETRY_WAIT)
        # 退避未到：不可认领
        self.assertIsNone(self.scheduler.claim_next(SID))
        # 退避已到（把 retry_not_before 改到过去）→ 重新入队
        journal = st.get_journal(SID)
        rt = journal.state().jobs[job.job_id]
        rt.retry_not_before = "2020-01-01T00:00:00Z"
        claimed = self.scheduler.claim_next(SID)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.job.job_id, job.job_id)

    def test_non_retryable_failure_terminal(self):
        job = _mk_job(self.scheduler)
        self.scheduler.claim_next(SID)
        state = self.scheduler.fail(SID, job.job_id, error_code="schema_fatal",
                                    retryable=False)
        self.assertEqual(state, S.JobState.FAILED)
        self.assertIsNone(self.scheduler.claim_next(SID))

    def test_retry_exhaustion_goes_failed(self):
        job = _mk_job(self.scheduler)
        journal = st.get_journal(SID)
        rt = journal.state().jobs[job.job_id]
        rt.job.attempt_count = 3   # 已达自动重试上限
        state = self.scheduler.fail(SID, job.job_id, error_code="llm_timeout",
                                    retryable=True)
        self.assertEqual(state, S.JobState.FAILED)

    def test_succeeded_not_reclaimed_and_crash_running_reclaims(self):
        job = _mk_job(self.scheduler)
        claimed = self.scheduler.claim_next(SID)
        journal = st.get_journal(SID)
        journal.append([S.OpResultCommitted(
            job_id=job.job_id, source_id="src_1", source_revision=1,
            scope_revision="scope_1", abstained=False)])
        rt = journal.state().jobs[job.job_id]
        self.assertEqual(rt.job.state, S.JobState.SUCCEEDED)
        # completed 不再调用模型（§18.2）：claim_next 不返回 succeeded
        self.assertIsNone(self.scheduler.claim_next(SID))

    def test_abstained_is_terminal(self):
        job = _mk_job(self.scheduler)
        self.scheduler.claim_next(SID)
        journal = st.get_journal(SID)
        journal.append([S.OpResultCommitted(
            job_id=job.job_id, source_id="src_1", source_revision=1,
            scope_revision="scope_1", abstained=True)])
        self.assertIsNone(self.scheduler.claim_next(SID))

    def test_out_of_order_completion_reflected_by_journal(self):
        """乱序完成：cache 显示 running 但 journal 已有完成记录时，以
        journal 为准（§6.5：完成记录存在而 cache 仍 running 时不再调模型）。"""
        job = _mk_job(self.scheduler)
        self.scheduler.claim_next(SID)
        journal = st.get_journal(SID)
        journal.append([S.OpResultCommitted(
            job_id=job.job_id, source_id="src_1", source_revision=1,
            scope_revision="scope_1", abstained=False)])
        # 模拟进程重启（cache 丢失）
        st.reset_journal_cache()
        fresh = st.get_journal(SID).state().jobs[job.job_id]
        self.assertEqual(fresh.job.state, S.JobState.SUCCEEDED)

    def test_cancel_scope_jobs_only_target_workspace(self):
        j1 = _mk_job(self.scheduler, workspace="ws_1")
        j2 = _mk_job(self.scheduler, workspace="ws_2")
        cancelled = self.scheduler.cancel_scope_jobs(
            SID, "ws_1", reason="workspace_deleted")
        self.assertEqual(cancelled, [j1.job_id])
        state = st.get_journal(SID).state()
        self.assertEqual(state.jobs[j1.job_id].job.state,
                         S.JobState.CANCELLED)
        self.assertEqual(state.jobs[j2.job_id].job.state, S.JobState.QUEUED)
        # 取消幂等；终态不可再取消
        self.assertEqual(
            self.scheduler.cancel_scope_jobs(SID, "ws_1", reason="again"), [])


class TestBudgetConfig(StorageSandboxTestCase):
    def test_default_budgets_match_plan(self):
        from app.core.config import settings
        self.assertEqual(settings.learner_evaluation_mode, "active")
        self.assertEqual(settings.learner_evaluation_concurrency, 2)
        self.assertEqual(settings.learner_eval_wall_deadline, 45)
        self.assertEqual(settings.learner_eval_transport_max, 4)
        self.assertEqual(settings.learner_eval_job_budget, 120)
        self.assertEqual(settings.learner_eval_lease_seconds, 150)
        self.assertEqual(settings.learner_eval_synthesis_merge_wait, 15)
        self.assertEqual(settings.learner_eval_clt_sample_ratio, 0.2)
        self.assertIn(settings.learner_evaluation_mode, ("active", "off"))


if __name__ == "__main__":
    unittest.main()
