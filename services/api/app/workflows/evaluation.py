"""Evaluation durable supervisor（evaluation task queue，ADR-0013 C4）。

迁移原则（同 textbook/classroom 批次）：**域 worker 整体随 activity 移到
worker 进程**，不在 workflow 里重写 claim/调度逻辑。

- ``EvaluationSupervisorWorkflow``（单实例，id ``evaluation-supervisor``）以
  有界时间片 activity 驱动现有 ``EvaluationWorker`` + ``DailyPlanner``：
  首个时间片启动两者（worker 首轮扫描即恢复——journal 的 claimable 语义
  已覆盖 queued/retry_wait/过期 lease，无需额外对账），随后每片只是心跳
  存活；worker 崩溃后 activity 重试自动拉起。
- API 进程不再持有 worker/planner：``notify_evaluation_worker()`` 在 API
  进程自然 no-op（未注册实例），新作业由 worker 自身的 2s 空闲轮询认领
  （与 classroom adopt 同一「磁盘是事实源」模式，受理延迟 ≤ 轮询间隔）。
- 域不变量全部保留：journal 仍是事实源，lease 150s / 过期重认领 /
  outbox 推进 / 同 (user, workspace) 串行语义零漂移；Temporal 只保证
  「worker 进程在，调度就在」。
- 关闭语义：时间片 activity 取消（worker 进程停机）时优雅 stop——停止
  认领、等待在途租约（30s 宽限，与文件模式 lifespan 停机一致）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy

from app.workflows.runtime import (
    TASK_QUEUE_EVALUATION,
    get_client,
    join_workflow_id,
)

log = logging.getLogger(__name__)

#: 单个监督时间片长度；workflow 以此为粒度续命并在计数后 continue-as-new。
_SLICE_SECONDS = 15.0
_SLICE_HEARTBEAT_SECONDS = 5.0
#: continue-as-new 前的最大时间片数（~2h），防 history 无界增长。
_MAX_SLICES_PER_RUN = 500
_SLICE_START_TO_CLOSE = timedelta(minutes=5)
_SLICE_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
#: 基建层失败（worker 崩溃/网络）持续重试；监督本身没有业务失败。
_SLICE_RETRY = RetryPolicy(initial_interval=timedelta(seconds=10),
                           maximum_interval=timedelta(minutes=2))


def supervisor_workflow_id() -> str:
    return join_workflow_id("evaluation-supervisor")


# ---------------------------------------------------------------------------
# workflow
# ---------------------------------------------------------------------------

@workflow.defn(name="evaluation.supervisor")
class EvaluationSupervisorWorkflow:
    """单实例监督循环：保证 evaluation worker + daily planner 持续运行。"""

    @workflow.run
    async def run(self) -> None:
        slices = 0
        while True:
            await workflow.execute_activity(
                supervise_evaluation_slice, args=[],
                task_queue=TASK_QUEUE_EVALUATION,
                start_to_close_timeout=_SLICE_START_TO_CLOSE,
                heartbeat_timeout=_SLICE_HEARTBEAT_TIMEOUT,
                retry_policy=_SLICE_RETRY)
            slices += 1
            if slices >= _MAX_SLICES_PER_RUN:
                workflow.continue_as_new()


# ---------------------------------------------------------------------------
# activity（worker 进程内执行；复用域 worker/planner，不复制调度逻辑）
# ---------------------------------------------------------------------------

@activity.defn(name="evaluation.supervisor.slice")
async def supervise_evaluation_slice() -> str:
    from app.core import learner_runtime
    from app.agents.student_model.evaluation.schedule import get_daily_planner
    from app.agents.student_model.evaluation.worker import get_evaluation_worker

    worker = get_evaluation_worker()
    planner = get_daily_planner()
    enabled = learner_runtime.evaluation_enabled()
    try:
        if enabled and not worker.status()["running"]:
            worker.start()
            log.info("evaluation supervisor: worker started")
        if enabled and not planner.status()["running"]:
            planner.start()
            log.info("evaluation supervisor: daily planner started")
        beats = int(_SLICE_SECONDS / _SLICE_HEARTBEAT_SECONDS)
        for _ in range(beats):
            activity.heartbeat()
            await asyncio.sleep(_SLICE_HEARTBEAT_SECONDS)
        activity.heartbeat()
        return "ok"
    except asyncio.CancelledError:
        # worker 进程停机：优雅停止认领，给在途租约宽限（与文件模式
        # lifespan 停机同一语义）。
        with contextlib.suppress(Exception):
            await planner.stop()
        with contextlib.suppress(Exception):
            await worker.stop()
        raise


EVALUATION_WORKFLOWS: tuple[type, ...] = (EvaluationSupervisorWorkflow,)
EVALUATION_ACTIVITIES = (supervise_evaluation_slice,)


# ---------------------------------------------------------------------------
# API 侧派发（main.py durable 分支确保监督 workflow 存在）
# ---------------------------------------------------------------------------

async def ensure_supervisor(client: Client | None = None) -> WorkflowHandle:
    """幂等启动监督 workflow（已存在则复用；worker 未上线时保持 pending）。"""
    connected = client or await get_client()
    return await connected.start_workflow(
        EvaluationSupervisorWorkflow.run,
        id=supervisor_workflow_id(),
        task_queue=TASK_QUEUE_EVALUATION,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
