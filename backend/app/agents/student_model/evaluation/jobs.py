"""评价作业调度：lease、重试、优先级（plan §10.3）。

作业事实存于 journal（job_requested/job_leased/job_failed/…）；本模块是
纯调度逻辑：claim_next 按 §10.3 优先级取队首 job 并写 lease 事务。完成/
提交在 service（G2+）。状态机：

    queued → running → succeeded
                 ├→ retry_wait → queued
                 ├→ abstained
                 ├→ failed
                 └→ cancelled
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import schema as S
from .store import EvidenceJournal, get_journal, new_job_id

LEASE_GRACE_SECONDS = 0  # lease 到期即可被重新认领（无宽限）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(v: str) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class ClaimedJob:
    job: S.EvaluationJob
    lease_token: str
    lease_expires_at: str


def _claimable(rt, now: datetime) -> bool:
    job = rt.job
    if job.state == S.JobState.QUEUED:
        return True
    if job.state == S.JobState.RETRY_WAIT:
        nb = _parse(rt.retry_not_before)
        return nb is None or nb <= now
    # 崩溃恢复：running 且 lease 过期 → 可重新认领（§6.5 恢复未完成 lease）
    if job.state == S.JobState.RUNNING:
        exp = _parse(job.lease_expires_at)
        return exp is not None and exp <= now
    return False


class JobScheduler:
    """无独立线程的协作式调度器（worker 循环在 learner_runtime 启动，G2+）。
    进程内单例经 learner_runtime 组装。"""

    def __init__(self, lease_seconds: int = 150) -> None:
        self.lease_seconds = lease_seconds

    # -- enqueue --------------------------------------------------------
    def enqueue(self, student_id: str, *, kind: S.JobKind,
                source_id: str = "", source_revision: int = 1,
                workspace_id: str = "", scope_revision: str = "",
                priority: int = S.JobPriority.CURRENT_DIALOGUE.value,
                parent_job_id: str = "",
                prompt_binding: str = "") -> S.EvaluationJob:
        journal = get_journal(student_id)
        job = S.EvaluationJob(
            job_id=new_job_id(), kind=kind, source_id=source_id,
            source_revision=source_revision, workspace_id=workspace_id,
            scope_revision=scope_revision, state=S.JobState.QUEUED,
            priority=priority, parent_job_id=parent_job_id,
            prompt_binding=prompt_binding, created_at=S.utc_now_iso(),
            updated_at=S.utc_now_iso())
        journal.append([S.OpJobRequested(job=job)])
        return job

    # -- claim ----------------------------------------------------------
    def claim_next(self, student_id: str, *, workspace_id: str = "",
                   lease_seconds: int | None = None) -> ClaimedJob | None:
        journal = get_journal(student_id)
        state = journal.state()
        now = _now()
        candidates = [rt for rt in state.jobs.values()
                      if _claimable(rt, now)
                      and (not workspace_id
                           or rt.job.workspace_id == workspace_id)]
        if not candidates:
            return None
        candidates.sort(key=lambda rt: (rt.job.priority, rt.job.created_at,
                                        rt.job.job_id))
        rt = candidates[0]
        job = rt.job.model_copy(deep=True)
        token = "lease_" + uuid.uuid4().hex[:16]
        expires = _iso(now + timedelta(
            seconds=lease_seconds or self.lease_seconds))
        journal.append([S.OpJobLeased(
            job_id=job.job_id, lease_token=token, lease_expires_at=expires,
            worker="inproc")])
        job.state = S.JobState.RUNNING
        job.lease_token = token
        job.lease_expires_at = expires
        job.attempt_count += 1
        return ClaimedJob(job=job, lease_token=token,
                          lease_expires_at=expires)

    # -- transitions ----------------------------------------------------
    def fail(self, student_id: str, job_id: str, *, error_code: str,
             retryable: bool, transport_attempts: int = 1,
             retry_after_seconds: int = 30) -> S.JobState:
        journal = get_journal(student_id)
        rt = journal.state().jobs.get(job_id)
        attempts = (rt.job.attempt_count if rt else 0) or 1
        journal.append([S.OpJobFailed(
            job_id=job_id, error_code=error_code, retryable=retryable,
            attempt_count=attempts, transport_attempts=transport_attempts,
            retry_after_seconds=retry_after_seconds)])
        new_state = journal.state().jobs.get(job_id)
        return new_state.job.state if new_state else S.JobState.FAILED

    def cancel(self, student_id: str, job_id: str, *,
               reason: str = "") -> bool:
        journal = get_journal(student_id)
        rt = journal.state().jobs.get(job_id)
        if rt is None:
            return False
        if rt.job.state in (S.JobState.SUCCEEDED, S.JobState.ABSTAINED,
                            S.JobState.CANCELLED):
            return False
        journal.append([S.OpJobCancelled(job_id=job_id, reason=reason)])
        return True

    def cancel_scope_jobs(self, student_id: str, workspace_id: str, *,
                          reason: str) -> list[str]:
        """删除工作区/取消选卷：取消该 scope 未完成作业（§5.3）。"""
        journal = get_journal(student_id)
        cancelled: list[str] = []
        for rt in list(journal.state().jobs.values()):
            if rt.job.workspace_id != workspace_id:
                continue
            if rt.job.state in (S.JobState.SUCCEEDED, S.JobState.ABSTAINED,
                                S.JobState.CANCELLED, S.JobState.FAILED):
                continue
            if self.cancel(student_id, rt.job.job_id, reason=reason):
                cancelled.append(rt.job.job_id)
        return cancelled

    def lease_valid(self, student_id: str, job_id: str,
                    lease_token: str) -> bool:
        """旧 worker/超时回包不能越过新租约提交（§6.5）。"""
        rt = get_journal(student_id).state().jobs.get(job_id)
        if rt is None or rt.job.state != S.JobState.RUNNING:
            return False
        if rt.job.lease_token != lease_token:
            return False
        exp = _parse(rt.job.lease_expires_at)
        return exp is not None and exp > _now()
