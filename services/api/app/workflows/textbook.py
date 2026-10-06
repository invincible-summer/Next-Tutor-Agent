"""Textbook durable workflows（documents task queue，ADR-0013）。

迁移原则（wrap-as-activity，不拆域代码）：

- 每个**构建 intent**（上传自动构建/手动重建/重启恢复）是一个
  ``TextbookBuildWorkflow``；其唯一 activity 在 worker 进程内调用现有
  ``enqueue_textbook_build`` 走域自身的 per-owner 队列（FIFO /
  ``build_concurrency`` / 同书互斥 / 停滞看门狗语义零漂移），并 await
  队列项 Future 到终态。Temporal 只提供 durable dispatch：worker 崩溃后
  activity 重试重新入队，域状态机（build_job intent / 卷级 ocr_state）
  幂等收敛。
- 每个**手动刷新**（rebuild_graph 三模式）是一个
  ``TextbookRefreshWorkflow``；activity 在 worker 进程内执行现有
  ``_safe_refresh``（per-owner 刷新锁 + 内部经队列的构建 + RAG 收尾）。
- 域事实源不变：教材记录 JSON（status/progress/build_job/ocr_state）仍由
  域代码原子写；workflow 不承载业务状态。
- 进程角色（``app.workflows.config``）：API 进程派发 workflow；worker 进程
  内 activity 复用进程内域队列，绝不递归再派发。
- documents 队列保持单 worker 实例消费（域队列在 worker 进程内存中，
  与文件模式同一约束）；多实例横向扩容不在本批范围。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy

from app.workflows.runtime import (
    TASK_QUEUE_DOCUMENTS,
    get_client,
    join_workflow_id,
)

log = logging.getLogger(__name__)

#: 单个 intent 的墙钟上限：队列等待（多本 × OCR 小时级重试）+ 构建。
_SCHEDULE_TO_CLOSE = timedelta(days=7)
#: worker 死亡/任务悬挂的检测窗口；activity 内每 10 秒心跳一次。
_HEARTBEAT_TIMEOUT = timedelta(minutes=10)
#: 基建层失败（worker 崩溃、网络）才重试；activity 自身有异常网、不抛业务错。
_RETRY_POLICY = RetryPolicy(initial_interval=timedelta(seconds=30),
                            maximum_interval=timedelta(minutes=10),
                            maximum_attempts=20)


@dataclass
class TextbookBuildIntent:
    owner: str
    tb_id: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    # 租户上下文（WS5c）；默认 "" = 旧作用域，兼容在途 workflow。
    tenant_id: str = ""


@dataclass
class TextbookRefreshIntent:
    owner: str
    tb_id: str
    mode: str
    ocr_parallel: bool = True
    tenant_id: str = ""


def build_intent_workflow_id(owner: str, tb_id: str, unique: str) -> str:
    return join_workflow_id("textbook-build", owner, tb_id, unique)


def refresh_workflow_id(owner: str, tb_id: str, unique: str) -> str:
    return join_workflow_id("textbook-refresh", owner, tb_id, unique)


def _new_intent_key() -> str:
    # Workflow id 的一次性成分在派发侧铸造（workflow 代码保持确定性纪律）。
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------

@workflow.defn(name="textbook.build_intent")
class TextbookBuildWorkflow:
    """一次构建 intent 的 durable 执行（队列门控到书级终态）。"""

    @workflow.run
    async def run(self, intent: TextbookBuildIntent) -> str:
        return await workflow.execute_activity(
            run_build_intent_activity, args=[intent],
            task_queue=TASK_QUEUE_DOCUMENTS,
            schedule_to_close_timeout=_SCHEDULE_TO_CLOSE,
            heartbeat_timeout=_HEARTBEAT_TIMEOUT,
            retry_policy=_RETRY_POLICY)


@workflow.defn(name="textbook.refresh")
class TextbookRefreshWorkflow:
    """一次手动刷新（rag_graph/full_ocr/quality_ocr/graph_only）。"""

    @workflow.run
    async def run(self, intent: TextbookRefreshIntent) -> str:
        return await workflow.execute_activity(
            run_refresh_activity, args=[intent],
            task_queue=TASK_QUEUE_DOCUMENTS,
            schedule_to_close_timeout=_SCHEDULE_TO_CLOSE,
            heartbeat_timeout=_HEARTBEAT_TIMEOUT,
            retry_policy=_RETRY_POLICY)


# ---------------------------------------------------------------------------
# activities（worker 进程内执行；复用域入口，不复制实现）
# ---------------------------------------------------------------------------

async def _run_with_heartbeat(coro) -> str:
    """执行域协程并周期心跳；取消异常原样上抛（交给重试策略）。"""
    task = asyncio.ensure_future(coro)
    try:
        while not task.done():
            activity.heartbeat()
            await asyncio.sleep(10)
        activity.heartbeat()
        return "done" if task.exception() is None else "domain-error"
    except asyncio.CancelledError:
        task.cancel()
        raise


@activity.defn(name="textbook.build_intent.run")
async def run_build_intent_activity(intent: TextbookBuildIntent) -> str:
    from app.agents.knowledge.textbook_builder import enqueue_textbook_build
    from app.persistence.documents import tenant_scope

    with tenant_scope(intent.tenant_id):
        future = enqueue_textbook_build(
            intent.owner, intent.tb_id, **intent.kwargs)
    if future is None:
        # 无事件循环（理论不可达：activity 必在循环内）——按已受理返回，
        # 域 intent 已持久化，worker 启动恢复会重入队。
        return "no-loop"
    return await _run_with_heartbeat(future)


@activity.defn(name="textbook.refresh.run")
async def run_refresh_activity(intent: TextbookRefreshIntent) -> str:
    # 刷新的复合编排（RAG 重建 → 队列构建 → 收尾）历史住在 API 模块；
    # wrap-as-activity 原则下原地复用，不搬领域代码。
    from app.api.v1.textbook import _safe_refresh
    from app.persistence.documents import tenant_scope

    with tenant_scope(intent.tenant_id):
        return await _run_with_heartbeat(
            _safe_refresh(intent.owner, intent.tb_id, intent.mode,
                          ocr_parallel=intent.ocr_parallel))


TEXTBOOK_WORKFLOWS: tuple[type, ...] = (
    TextbookBuildWorkflow, TextbookRefreshWorkflow)
TEXTBOOK_ACTIVITIES = (run_build_intent_activity, run_refresh_activity)


# ---------------------------------------------------------------------------
# API 侧派发（fire-and-forget；需要等待的调用方自行 await handle.result()）
# ---------------------------------------------------------------------------

async def start_build_intent(intent: TextbookBuildIntent,
                             client: Client | None = None) -> WorkflowHandle:
    connected = client or await get_client()
    return await connected.start_workflow(
        TextbookBuildWorkflow.run, intent,
        id=build_intent_workflow_id(
            intent.owner, intent.tb_id, _new_intent_key()),
        task_queue=TASK_QUEUE_DOCUMENTS,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)


async def start_refresh(intent: TextbookRefreshIntent,
                        client: Client | None = None) -> WorkflowHandle:
    connected = client or await get_client()
    return await connected.start_workflow(
        TextbookRefreshWorkflow.run, intent,
        id=refresh_workflow_id(
            intent.owner, intent.tb_id, _new_intent_key()),
        task_queue=TASK_QUEUE_DOCUMENTS,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)


async def dispatch_build_intent(intent: TextbookBuildIntent) -> None:
    """派发并等待 workflow 终态（供 enqueue 的后台任务结算 Future）。

    workflow 失败/取消不在此抛出——域记录（build_job 状态机）才是事实源，
    Future 结算本身即可让上游（手动刷新等待）继续。
    """
    try:
        handle = await start_build_intent(intent)
        await handle.result()
    except Exception:
        log.warning("temporal textbook build workflow ended abnormally: "
                    "%s/%s", intent.owner, intent.tb_id, exc_info=True)
