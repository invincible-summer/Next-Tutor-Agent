"""R01（update_plan §4）：评价作业后台 worker。

生产执行入口——按 JobKind 路由到正确执行器（来源解释 / 复核 / 综合 /
backfill / CLT），lifespan 启动；重启恢复 = 首轮扫描认领 queued/
retry_wait/过期 lease（claimable 语义已覆盖，§6.5）。作业完成后推进
outbox（M9 消费 + consumer_ack）。shutdown 停止认领并关闭共享 LLM 客户端。

调度纪律：
- 同 (user, workspace) 串行（learner_runtime.workspace_lock）；
- 跨用户公平：按最旧可认领作业排序；
- GET/受理路径零阻塞：enqueue 后 `wake()` 唤醒，空闲轮询退避。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from . import schema as S
from .jobs import JobScheduler, job_deadline

log = logging.getLogger(__name__)

POLL_IDLE_SECONDS = 2.0          # 空闲轮询间隔（wake 可即时打断）
MAX_JOBS_PER_PASS = 16           # 单轮处理上限（跨用户公平切片）


class EvaluationWorker:
    """进程内单例；lifespan 启动/停止。测试可直接调 process_pass()。"""

    def __init__(self, scheduler: JobScheduler | None = None,
                 runner_provider: Callable[[], Any] | None = None,
                 ) -> None:
        from app.core import learner_runtime
        self._scheduler = scheduler
        self._runner_provider = runner_provider or \
            learner_runtime.get_evaluation_runner
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._processed = 0
        self._errors = 0
        self._last_pass_at = 0.0
        self._inflight = 0

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="eval-worker")

    async def stop(self) -> None:
        """停止认领；等待在途租约完成；关闭共享 LLM 客户端。"""
        self._stopping = True
        self.wake()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        await self._close_runner_client()

    async def _close_runner_client(self) -> None:
        try:
            from app.core import learner_runtime
            runner = learner_runtime._RUNNER  # noqa: SLF001 - shutdown 路径
            client = getattr(runner, "_client", None)
            if client is not None and hasattr(client, "client"):
                closer = getattr(client.client, "aclose", None) or \
                    getattr(client.client, "close", None)
                if closer is not None:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
        except Exception:
            log.warning("evaluation runner client close failed", exc_info=True)

    def wake(self) -> None:
        """受理路径新作业入队后唤醒（空闲唤醒目标 ≤1s，§6.5）。"""
        self._wakeup.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "stopping": self._stopping,
            "inflight": self._inflight,
            "processed_total": self._processed,
            "errors_total": self._errors,
            "last_pass_at": self._last_pass_at,
        }

    # -- main loop ------------------------------------------------------
    async def _run(self) -> None:
        log.info("evaluation worker started")
        while not self._stopping:
            try:
                did = await self.process_pass()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("evaluation worker pass crashed")
                did = 0
            if did == 0 and not self._stopping:
                try:
                    await asyncio.wait_for(self._wakeup.wait(),
                                           timeout=POLL_IDLE_SECONDS)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
                self._wakeup.clear()

    # -- discovery ------------------------------------------------------
    def _students_with_jobs(self) -> list[tuple[float, str]]:
        """有待认领作业的学生，按最旧可认领作业时间排序（跨用户公平）。

        STUDENTS_DIR/JOURNAL_SUFFIX 动态取自 store 模块——测试沙箱会
        重定向这些常量，模块级绑定会拿到重定向前的生产路径。
        """
        from . import store as store_mod
        from .store import get_journal
        out: list[tuple[float, str]] = []
        if not store_mod.STUDENTS_DIR.exists():
            return out
        for path in store_mod.STUDENTS_DIR.glob(
                f"*{store_mod.JOURNAL_SUFFIX}"):
            sid = path.name[: -len(store_mod.JOURNAL_SUFFIX)]
            try:
                state = get_journal(sid).state()
            except Exception:
                continue
            oldest = self._oldest_claimable(state)
            if oldest is not None:
                out.append((oldest, sid))
        out.sort()
        return out

    @staticmethod
    def _oldest_claimable(state) -> float | None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        best: float | None = None
        for rt in state.jobs.values():
            job = rt.job
            claimable = job.state in (S.JobState.QUEUED,)
            if job.state == S.JobState.RETRY_WAIT:
                claimable = not rt.retry_not_before or \
                    rt.retry_not_before <= S.utc_now_iso()
            if not claimable:
                continue
            ts = time.mktime(time.strptime(job.created_at,
                                           "%Y-%m-%dT%H:%M:%SZ")) \
                if job.created_at else 0.0
            if best is None or ts < best:
                best = ts
        _ = now
        return best

    # -- dispatch -------------------------------------------------------
    async def process_pass(self) -> int:
        """处理一轮（≤MAX_JOBS_PER_PASS）。返回处理数量。"""
        self._last_pass_at = time.time()
        processed = 0
        for _oldest, sid in self._students_with_jobs():
            if processed >= MAX_JOBS_PER_PASS or self._stopping:
                break
            while processed < MAX_JOBS_PER_PASS and not self._stopping:
                claimed = self._scheduler_ref().claim_next(sid)
                if claimed is None:
                    break
                processed += 1
                self._inflight += 1
                try:
                    await self._dispatch(sid, claimed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._errors += 1
                    log.exception(
                        "worker dispatch failed: %s %s",
                        claimed.job.kind.value, claimed.job.job_id)
                finally:
                    self._inflight -= 1
                self._processed += 1
                # 作业终态后推进 outbox（R19：M9 消费 + ack）
                try:
                    self._advance_outbox(sid)
                except Exception:
                    log.exception("outbox advance failed for %s", sid)
        return processed

    def _scheduler_ref(self) -> JobScheduler:
        if self._scheduler is not None:
            return self._scheduler
        from app.core import learner_runtime
        return learner_runtime.get_scheduler()

    async def _dispatch(self, student_id: str, claimed) -> str:
        job = claimed.job
        runner = self._runner_provider()
        kind = job.kind
        if kind == S.JobKind.ASSESSMENT_EVALUATION:
            from app.agents.assessment.manager import run_assessment_job
            return await run_assessment_job(student_id, claimed,
                                            runner=runner)
        if kind == S.JobKind.DIALOGUE_EVALUATION:
            from .evaluator import run_dialogue_job
            return await run_dialogue_job(student_id, claimed, runner=runner)
        if kind == S.JobKind.REVIEW:
            from .evaluator import run_review_job
            return await run_review_job(student_id, claimed, runner=runner)
        if kind in (S.JobKind.SYNTHESIS_CONCEPT, S.JobKind.SYNTHESIS_SESSION,
                    S.JobKind.SYNTHESIS_WORKSPACE):
            from .evaluator import run_synthesis_job
            scope_type = {
                S.JobKind.SYNTHESIS_CONCEPT: S.ScopeType.CONCEPT,
                S.JobKind.SYNTHESIS_SESSION: S.ScopeType.SESSION,
                S.JobKind.SYNTHESIS_WORKSPACE: S.ScopeType.WORKSPACE,
            }[kind]
            return await run_synthesis_job(
                student_id, claimed, runner=runner, scope_type=scope_type)
        if kind == S.JobKind.BACKFILL:
            # R22：backfill 保持原 source_kind 语义——按来源类型路由
            from .store import get_journal
            src = get_journal(student_id).state().sources.get(job.source_id)
            if src is not None and \
                    src.receipt.kind == S.SourceKind.DIALOGUE:
                from .evaluator import run_dialogue_job
                return await run_dialogue_job(student_id, claimed,
                                               runner=runner)
            from app.agents.assessment.manager import run_assessment_job
            return await run_assessment_job(student_id, claimed,
                                            runner=runner)
        if kind == S.JobKind.CLT_REVIEW:
            return await self._run_clt_review(student_id, claimed, runner)
        # CHILD_GROUP 等未知类型：明确取消并记录（未知版本进可观察错误，
        # 不无限重试，§6.2）
        self._scheduler_ref().cancel(
            student_id, job.job_id, reason=f"unknown_kind:{kind.value}")
        log.warning("worker cancelled unknown job kind %s", kind.value)
        return "cancelled"

    async def _run_clt_review(self, student_id: str, claimed,
                              runner) -> str:
        """C8/P8（§9.10）：讲解完成后的教学设计复盘抽样。无足够输入时
        abstain（不能用输出长度推算认知负荷）。"""
        from .store import get_journal
        journal = get_journal(student_id)
        state = journal.state()
        job = claimed.job
        src = state.sources.get(job.source_id)
        if src is None:
            self._scheduler_ref().cancel(student_id, job.job_id,
                                         reason="source_gone")
            return "cancelled"
        receipt = src.receipt
        from .llm import build_system_message
        user = S.canonical_json({
            "讲解片段": receipt.canonical_text[:8000],
            "observed_at": receipt.observed_at,
            "workspace_id": receipt.workspace_id_at_observation,
        })
        system = build_system_message(
            "teaching_clt_review", output_model=S.TeachingDesignReview)
        out = await runner.run_structured(
            deadline_at=job_deadline(claimed.job),
            system=system, user=user, output_model=S.TeachingDesignReview,
            max_output_tokens=3000)
        if out.parsed is None:
            self._scheduler_ref().fail(
                student_id, job.job_id,
                error_code=out.error_code or "llm_failed",
                retryable=out.retryable_error,
                transport_attempts=out.transport_attempts)
            return "failed"
        review: S.TeachingDesignReview = out.parsed
        # C8 结果只作为下轮教学建议（§9.10），不写学生状态——以 job 终态
        # + outbox 事件投递给教学侧；journal 不新增学习观察。
        review_outbox = [{
            "event_id": f"clt_{job.job_id}", "consumer": "teaching",
            "kind": "clt_review",
            "items": [i.model_dump() for i in review.items[:6]],
            "priority_adjustment": (review.priority_adjustment.model_dump()
                                    if review.priority_adjustment else None),
            "limits": list(review.limits[:8]),
            "source_id": src.receipt.source_id,
        }]
        journal.append([
            S.OpJobInputPrepared(
                job_id=job.job_id, input_hash="clt_" + job.job_id[4:],
                prompt_binding="teaching_clt_review@1.0.0",
                generation=state.generation),
            S.OpResultCommitted(
                job_id=job.job_id, source_id=src.receipt.source_id,
                source_revision=src.receipt.source_revision,
                scope_revision=src.receipt.scope_revision or "no_scope",
                abstained=not bool(review.items),
                outbox=review_outbox),
        ], expected_generation=state.generation)
        return "succeeded" if review.items else "abstained"

    def _advance_outbox(self, student_id: str) -> None:
        """R19：outbox 幂等消费。

        - m9：learning_orchestration.consume_evaluation_outbox（内部
          consumer_ack；M9 侧 attempt_id 去重双层幂等）。
        - synthesis：concept_dirty 事件 → 同区重综合入队（request_
          resynthesis 自带"同区已排队复用"去重）；事件完成后 ack。"""
        from .store import get_journal
        journal = get_journal(student_id)
        state = journal.state()
        consumers = {consumer for (_eid, consumer) in state.outbox_unacked}
        if "m9" in consumers:
            from app.agents.learning_orchestration import (
                get_orchestration_service)
            get_orchestration_service().consume_evaluation_outbox(student_id)
        if "synthesis" in consumers:
            dirty_ws: set[str] = set()
            for (eid, consumer), item in state.outbox_unacked.items():
                if consumer != "synthesis":
                    continue
                ws = str(item.get("workspace_id") or "")
                if ws:
                    dirty_ws.add(ws)
                    continue
                # resync_<source_id> / resync_<judgment_id> 事件：从 journal
                # 事实解析归属工作区
                token = eid.removeprefix("resync_")
                src = state.sources.get(token)
                if src is not None:
                    if src.receipt.workspace_id_at_observation:
                        dirty_ws.add(
                            src.receipt.workspace_id_at_observation)
                    continue
                for judgment in state.judgments.values():
                    if judgment.judgment_id == token and judgment.workspace_id:
                        dirty_ws.add(judgment.workspace_id)
            from . import lifecycle
            for ws in sorted(dirty_ws):
                lifecycle.request_resynthesis(
                    student_id, ws, reason="concept_dirty")
            # synthesis 消费者职责完成（重综合已入队）→ ack
            acks = [S.OpConsumerAck(event_id=eid, consumer="synthesis")
                    for (eid, consumer) in state.outbox_unacked
                    if consumer == "synthesis"]
            # teaching 消费者（C8 复盘建议）：建议性事件，完成投递记录后
            # 即时 ack（R20 教学侧读取在同一 pass 内完成，不积压重投）。
            acks += [S.OpConsumerAck(event_id=eid, consumer="teaching")
                     for (eid, consumer) in state.outbox_unacked
                     if consumer == "teaching"]
            if acks:
                journal.append(acks)


_WORKER: EvaluationWorker | None = None


def get_evaluation_worker() -> EvaluationWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = EvaluationWorker()
    return _WORKER


def set_evaluation_worker(worker: EvaluationWorker | None) -> None:
    global _WORKER
    _WORKER = worker


def notify_evaluation_worker() -> None:
    """受理路径新作业入队后的唤醒钩子（未运行时安全 no-op）。"""
    if _WORKER is not None:
        _WORKER.wake()
