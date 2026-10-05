"""Classroom durable supervisor（classroom task queue，ADR-0013 C2）。

迁移原则（同 textbook 批次）：**调度器整体随 activity 移到 worker 进程**，
不在 workflow 里重写一份调度逻辑。

- ``ClassroomSupervisorWorkflow``（单实例，id ``classroom-supervisor``）以
  有界时间片 activity 驱动现有 ``ClassroomWorker``：首个时间片执行
  ``worker.start()``（启动扫描恢复：running→queued、pending publish 补齐），
  随后每片只是心跳存活；worker 崩溃后 activity 重试自动拉起并重新扫描。
- worker 进程内启用 ``adopt`` 模式：调度循环周期扫描磁盘 queued job 补
  登记内存 pending 表（事实源是 job.json）——API 进程无需 enqueue 钩子，
  lesson 创建/retry/continue 的 CAS 落盘后 ≤2s 内被收养。
- 域不变量全部保留（ADR-0013 §13.4）：stage artifact / budget / epoch /
  publish validation 仍在 job.json 与 store；Temporal 只保证「worker 进程
  在，调度就在」。job_events SSE 与 job 轮询 DTO 不变。
- 关闭语义：时间片 activity 取消（worker 进程停机）时执行 ``worker.stop()``
  ——在途 job 有 10 秒检查点宽限，可恢复 job 绝不标 failed（与文件模式
  lifespan 停机一致）。
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
    TASK_QUEUE_CLASSROOM,
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
    return join_workflow_id("classroom-supervisor")


# ---------------------------------------------------------------------------
# workflow
# ---------------------------------------------------------------------------

@workflow.defn(name="classroom.supervisor")
class ClassroomSupervisorWorkflow:
    """单实例监督循环：保证 classroom worker 持续运行。"""

    @workflow.run
    async def run(self) -> None:
        slices = 0
        while True:
            await workflow.execute_activity(
                supervise_classroom_slice, args=[],
                task_queue=TASK_QUEUE_CLASSROOM,
                start_to_close_timeout=_SLICE_START_TO_CLOSE,
                heartbeat_timeout=_SLICE_HEARTBEAT_TIMEOUT,
                retry_policy=_SLICE_RETRY)
            slices += 1
            if slices >= _MAX_SLICES_PER_RUN:
                workflow.continue_as_new()


# ---------------------------------------------------------------------------
# activity（worker 进程内执行；复用域 worker，不复制调度逻辑）
# ---------------------------------------------------------------------------

@activity.defn(name="classroom.supervisor.slice")
async def supervise_classroom_slice() -> str:
    from app.classroom import worker as classroom_worker_module

    worker = classroom_worker_module.get_worker()
    try:
        if not worker.is_running():
            worker.enable_adopt_mode()
            await worker.start()
            log.info("classroom supervisor: worker started (adopt mode)")
        beats = int(_SLICE_SECONDS / _SLICE_HEARTBEAT_SECONDS)
        for _ in range(beats):
            activity.heartbeat()
            await asyncio.sleep(_SLICE_HEARTBEAT_SECONDS)
        activity.heartbeat()
        return "ok"
    except asyncio.CancelledError:
        # worker 进程停机：给在途 job 检查点宽限再退出（与文件模式
        # lifespan 停机同一语义）。
        with contextlib.suppress(Exception):
            await worker.stop()
        raise


CLASSROOM_WORKFLOWS: tuple[type, ...] = (ClassroomSupervisorWorkflow,)
CLASSROOM_ACTIVITIES = (supervise_classroom_slice,)


# ---------------------------------------------------------------------------
# API 侧派发（main.py durable 分支确保监督 workflow 存在）
# ---------------------------------------------------------------------------

async def ensure_supervisor(client: Client | None = None) -> WorkflowHandle:
    """幂等启动监督 workflow（已存在则复用；worker 未上线时保持 pending）。"""
    connected = client or await get_client()
    return await connected.start_workflow(
        ClassroomSupervisorWorkflow.run,
        id=supervisor_workflow_id(),
        task_queue=TASK_QUEUE_CLASSROOM,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
