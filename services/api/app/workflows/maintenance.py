"""Maintenance durable ticks + 大账号 purge（maintenance task queue，ADR-0013 C5）。

把 API lifespan 里的三类定时维护（briefing tick / trash cleanup /
assistant draft purge）迁到 Temporal Schedule，worker 启动时幂等注册：

- 每个 Schedule 周期启动一个 ``MaintenanceTickWorkflow``（id 带
  ``{{.ScheduledTimestamp}}`` 模板，每次触发唯一）；其唯一 activity 调
  用现有幂等域 tick（认领/账本去重在域内），失败不暂停 Schedule——
  下个周期自然重试。overlap 默认 SKIP：worker 慢时不堆积 tick。
- briefing/drafts 跟随站点助手开关（关闭时跳过注册，重启 worker 时
  幂等补齐）；trash 间隔读全局策略。
- 大账号 purge：``AccountPurgeWorkflow`` 在 worker 进程执行现有
  ``account_data.purge_account``（整链不可恢复删除，activity 崩溃后
  重试继续删残余），API 路由 await workflow 终态保持响应契约不变；
  file 模式仍在线程内同步执行。
- site-assistant 的 turns / 恢复逻辑仍在 API 进程（请求生命周期，
  不迁移清单，ADR-0013）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleState,
    WorkflowHandle,
)
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy

from app.workflows.runtime import (
    TASK_QUEUE_MAINTENANCE,
    get_client,
    join_workflow_id,
)

log = logging.getLogger(__name__)

_TICK_HEARTBEAT_SECONDS = 5.0
_TICK_START_TO_CLOSE = timedelta(minutes=10)
_TICK_RETRY = RetryPolicy(initial_interval=timedelta(seconds=30),
                          maximum_interval=timedelta(minutes=5),
                          maximum_attempts=3)
#: purge 整链（聊天/档案/图谱/回收站/账号记录）可能分钟级。
_PURGE_SCHEDULE_TO_CLOSE = timedelta(hours=1)
_PURGE_HEARTBEAT_TIMEOUT = timedelta(minutes=10)
_PURGE_RETRY = RetryPolicy(initial_interval=timedelta(seconds=30),
                           maximum_interval=timedelta(minutes=5),
                           maximum_attempts=3)


@dataclass
class MaintenanceTickIntent:
    kind: str  # briefing | trash | drafts


@dataclass
class AccountPurgeIntent:
    user_id: str


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------

@workflow.defn(name="maintenance.tick")
class MaintenanceTickWorkflow:
    """一次幂等维护 tick（由 Temporal Schedule 周期触发）。"""

    @workflow.run
    async def run(self, intent: MaintenanceTickIntent) -> str:
        return await workflow.execute_activity(
            run_maintenance_tick_activity, args=[intent],
            task_queue=TASK_QUEUE_MAINTENANCE,
            start_to_close_timeout=_TICK_START_TO_CLOSE,
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=_TICK_RETRY)


@workflow.defn(name="account.purge")
class AccountPurgeWorkflow:
    """大账号不可恢复删除（durable：API 请求超时不再中断删除链）。"""

    @workflow.run
    async def run(self, intent: AccountPurgeIntent) -> dict[str, Any]:
        return await workflow.execute_activity(
            run_account_purge_activity, args=[intent],
            task_queue=TASK_QUEUE_MAINTENANCE,
            schedule_to_close_timeout=_PURGE_SCHEDULE_TO_CLOSE,
            heartbeat_timeout=_PURGE_HEARTBEAT_TIMEOUT,
            retry_policy=_PURGE_RETRY)


MAINTENANCE_WORKFLOWS: tuple[type, ...] = (
    MaintenanceTickWorkflow, AccountPurgeWorkflow)


# ---------------------------------------------------------------------------
# activities（worker 进程内执行；复用域幂等入口，不复制实现）
# ---------------------------------------------------------------------------

async def _run_with_heartbeat(coro):
    """执行协程并周期心跳；取消异常原样上抛。"""
    task = asyncio.ensure_future(coro)
    try:
        while not task.done():
            activity.heartbeat()
            await asyncio.sleep(_TICK_HEARTBEAT_SECONDS)
        activity.heartbeat()
        return task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise


def _tick_coro(kind: str):
    if kind == "briefing":
        from app.agents.site_assistant import notifications
        return asyncio.to_thread(notifications.scheduler_tick)
    if kind == "trash":
        from app.core.trash import cleanup_expired
        return asyncio.to_thread(cleanup_expired)
    if kind == "drafts":
        return _purge_assistant_drafts()
    raise ValueError(f"unknown maintenance kind: {kind}")


async def _purge_assistant_drafts() -> int:
    """站点助手草稿清扫（与文件模式 lifespan 循环同一过程体）。"""
    from app.agents.site_assistant import store as asst_store
    purged = 0
    if asst_store._ASSISTANT_DIR.is_dir():
        for path in asst_store._ASSISTANT_DIR.iterdir():
            if path.is_dir():
                purged += await asyncio.to_thread(
                    asst_store.purge_expired_drafts, path.name) or 0
    return purged


@activity.defn(name="maintenance.tick.run")
async def run_maintenance_tick_activity(
        intent: MaintenanceTickIntent) -> str:
    result = await _run_with_heartbeat(_tick_coro(intent.kind))
    # tick 返回值只是诊断计数，不进入 workflow 结果合同。
    return "done"


@activity.defn(name="account.purge.run")
async def run_account_purge_activity(
        intent: AccountPurgeIntent) -> dict[str, Any]:
    from app.core import account_data
    return await _run_with_heartbeat(
        asyncio.to_thread(account_data.purge_account, intent.user_id))


MAINTENANCE_ACTIVITIES = (run_maintenance_tick_activity,
                          run_account_purge_activity)


# ---------------------------------------------------------------------------
# Schedule 注册（worker 启动幂等执行；API 进程不注册）
# ---------------------------------------------------------------------------

def _schedule_specs() -> dict[str, tuple[timedelta, str]]:
    """schedule_id -> (间隔, tick kind)。开关关闭的域不注册。"""
    from app.core.config import settings
    specs: dict[str, tuple[timedelta, str]] = {}
    if settings.site_assistant_proactive_enabled:
        specs["maintenance-briefing"] = (timedelta(seconds=60), "briefing")
    from app.core.trash import get_global_policy
    trash_seconds = max(300, int(get_global_policy()
                                 ["cleanup_interval_seconds"]))
    specs["maintenance-trash"] = (timedelta(seconds=trash_seconds), "trash")
    if settings.site_assistant_enabled:
        specs["maintenance-drafts"] = (timedelta(hours=1), "drafts")
    return specs


async def ensure_schedules(client: Client | None = None) -> dict[str, str]:
    """幂等注册 maintenance 定时（已存在则复用；间隔变更需删除后重建）。"""
    connected = client or await get_client()
    outcome: dict[str, str] = {}
    for schedule_id, (every, kind) in _schedule_specs().items():
        try:
            await connected.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow=MaintenanceTickWorkflow.run,
                        args=[MaintenanceTickIntent(kind=kind)],
                        # 服务端模板：每次触发生成唯一 id，规避 id 复用冲突。
                        id=f"maintenance-tick-{kind}-{{{{.ScheduledTimestamp}}}}",
                        task_queue=TASK_QUEUE_MAINTENANCE),
                    spec=ScheduleSpec(
                        intervals=[ScheduleIntervalSpec(every=every)]),
                    state=ScheduleState(
                        note="ADR-0013 C5 maintenance tick")),
            )
            outcome[schedule_id] = "created"
        except ScheduleAlreadyRunningError:
            outcome[schedule_id] = "existing"
    return outcome


# ---------------------------------------------------------------------------
# API 侧派发（账号删除路由 durable 分支）
# ---------------------------------------------------------------------------

async def start_account_purge(intent: AccountPurgeIntent,
                              client: Client | None = None) -> WorkflowHandle:
    connected = client or await get_client()
    return await connected.start_workflow(
        AccountPurgeWorkflow.run, intent,
        id=join_workflow_id("account-purge", intent.user_id,
                            uuid.uuid4().hex[:16]),
        task_queue=TASK_QUEUE_MAINTENANCE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)


async def purge_account_durable(user_id: str) -> dict[str, Any]:
    """账号删除路由的双模式入口（响应契约两种模式一致）。

    durable 模式走 workflow——大账号删除可能分钟级，不再被 API 请求
    超时中断（activity 崩溃后重试继续删残余）；file 模式在线程内执行
    同一域函数，行为与旧同步路由零漂移。
    """
    from app.workflows.config import temporal_configured
    if not temporal_configured():
        from app.core import account_data
        return await asyncio.to_thread(account_data.purge_account, user_id)
    handle = await start_account_purge(AccountPurgeIntent(user_id=user_id))
    return await handle.result()
