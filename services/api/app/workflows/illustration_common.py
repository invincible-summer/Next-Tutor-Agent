"""Illustration durable jobs 共享装配（media task queue，ADR-0013 C3）。

quiz 题图与工具助手情景配图是两个独立业务 workflow（见
``illustration_quiz.py`` / ``illustration_scenario.py``），共享本模块：

- intent 与 workflow id 构造（id 由 owner + job_id 派生，稳定可追溯）；
- 心跳执行器：run activity 在 worker 进程内直接执行域 ``_run(owner, job,
  llm=None)``（wrap-as-activity，不拆域代码；worker 侧 ``get_llm`` 自建
  客户端，与文件模式同一解析路径）；
- 结算语义：run activity **不重试**（``maximum_attempts=1``）——配图生成
  按次消耗模型预算，崩溃后自动重跑等于隐性双倍扣费。worker 崩溃/心跳
  超时后由 workflow 的 settle activity 把磁盘记录结算为
  ``failed/run_interrupted``（retryable，用户显式重试），与文件模式
  「进程内任务消失 → 读路径标中断」同一用户可见语义。
- 取消/清理（owner purge、session 删除）：磁盘化 epoch 失效是硬闸，
  workflow cancel 只是省模型开销的 best-effort（fire-and-forget，同步
  上下文可用——不依赖 API 进程的事件循环）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import RetryPolicy
from temporalio.service import RPCError, RPCStatusCode

from app.workflows.runtime import (
    TASK_QUEUE_MEDIA,
    get_client,
    join_workflow_id,
)

log = logging.getLogger(__name__)

#: 心跳周期；worker 进程死亡后在 heartbeat_timeout 内被服务端判定失败。
_HEARTBEAT_SECONDS = 5.0
#: 生成预算墙钟 ≤120s（settings.quiz_illustration_deadline_seconds 上限），
#: 加上队列等待与结算，1 小时封顶绰绰有余。
_SCHEDULE_TO_CLOSE = timedelta(hours=1)
_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
#: run 不重试：见模块 docstring 的预算语义。崩溃 → settle 结算中断。
_RUN_RETRY = RetryPolicy(maximum_attempts=1)
#: settle 持续重试直到有 media worker 存活（结算必须最终落地）。
_SETTLE_RETRY = RetryPolicy(initial_interval=timedelta(seconds=10),
                            maximum_interval=timedelta(minutes=10))
_SETTLE_START_TO_CLOSE = timedelta(minutes=1)


@dataclass
class QuizIllustrationIntent:
    owner: str
    job_id: str
    # 租户上下文（WS5c）：派发进程的文档隔离键，activity 内恢复。
    # 默认 "" = 旧作用域，兼容在途 workflow。
    tenant_id: str = ""


@dataclass
class ScenarioIllustrationIntent:
    owner: str
    session_id: str
    job_id: str
    tenant_id: str = ""


def quiz_workflow_id(owner: str, job_id: str) -> str:
    return join_workflow_id("quiz-illustration", owner, job_id)


def scenario_workflow_id(owner: str, job_id: str) -> str:
    return join_workflow_id("scenario-illustration", owner, job_id)


def dispatching() -> bool:
    """域接缝统一判定：本进程是否应把 job 派发为 durable workflow。

    API 进程派发；worker 进程内（activity 复用域代码）保持进程内路径。
    """
    from app.workflows.config import is_worker_process, temporal_configured
    return temporal_configured() and not is_worker_process()


def run_activity_options() -> dict:
    return {
        "task_queue": TASK_QUEUE_MEDIA,
        "schedule_to_close_timeout": _SCHEDULE_TO_CLOSE,
        "heartbeat_timeout": _HEARTBEAT_TIMEOUT,
        "retry_policy": _RUN_RETRY,
    }


def settle_activity_options() -> dict:
    return {
        "task_queue": TASK_QUEUE_MEDIA,
        "start_to_close_timeout": _SETTLE_START_TO_CLOSE,
        "retry_policy": _SETTLE_RETRY,
    }


# ---------------------------------------------------------------------------
# activities（worker 进程内执行；复用域入口，不复制实现）
# ---------------------------------------------------------------------------

async def _run_with_heartbeat(coro) -> str:
    """执行域协程并周期心跳；取消异常原样上抛（结算交给 workflow 语义）。

    域协程自身的异常由域内 handler 落盘结算，这里返回 ``domain-error``
    不上抛——workflow 视角 run 成功结束，不触发崩溃结算路径。
    """
    task = asyncio.ensure_future(coro)
    try:
        while not task.done():
            activity.heartbeat()
            await asyncio.sleep(_HEARTBEAT_SECONDS)
        activity.heartbeat()
        return "done" if task.exception() is None else "domain-error"
    except asyncio.CancelledError:
        task.cancel()
        raise


@activity.defn(name="illustration.quiz_job.run")
async def run_quiz_job_activity(intent: QuizIllustrationIntent) -> str:
    from app.illustration import orchestrator, persistence
    from app.persistence.documents import tenant_scope

    with tenant_scope(intent.tenant_id):
        job = persistence.read(intent.owner, "jobs", intent.job_id)
    if not job or job.get("status") not in {"queued", "running"}:
        # 记录已终态/被删（cutover 残留重放）：无事可做。
        return "already-settled"
    with tenant_scope(intent.tenant_id):
        return await _run_with_heartbeat(
            orchestrator._run(intent.owner, job, None))


@activity.defn(name="illustration.quiz_job.settle")
async def settle_quiz_job_activity(intent: QuizIllustrationIntent) -> str:
    from app.illustration import orchestrator
    from app.illustration.contracts import IllustrationError

    from app.persistence.documents import tenant_scope

    try:
        with tenant_scope(intent.tenant_id):
            return orchestrator._settle_interrupted(intent.owner,
                                                    intent.job_id)
    except (ValueError, IllustrationError):
        # 损坏/缺失/已被 epoch 闸拒：无需（也无法）结算。
        return "gone"


@activity.defn(name="illustration.scenario_job.run")
async def run_scenario_job_activity(
        intent: ScenarioIllustrationIntent) -> str:
    from app.illustration import persistence, scenario
    from app.persistence.documents import tenant_scope

    with tenant_scope(intent.tenant_id):
        job = persistence.read(intent.owner, "scenario_jobs",
                               intent.job_id)
    if not job or job.get("status") not in {"queued", "running"}:
        return "already-settled"
    with tenant_scope(intent.tenant_id):
        return await _run_with_heartbeat(
            scenario._run(intent.owner, job, None))


@activity.defn(name="illustration.scenario_job.settle")
async def settle_scenario_job_activity(
        intent: ScenarioIllustrationIntent) -> str:
    from app.illustration import scenario

    from app.persistence.documents import tenant_scope

    try:
        with tenant_scope(intent.tenant_id):
            return scenario._settle_interrupted(
                intent.owner, intent.session_id, intent.job_id)
    except (ValueError, scenario.SceneError):
        return "gone"


ILLUSTRATION_QUIZ_ACTIVITIES = (
    run_quiz_job_activity, settle_quiz_job_activity)
ILLUSTRATION_SCENARIO_ACTIVITIES = (
    run_scenario_job_activity, settle_scenario_job_activity)


# ---------------------------------------------------------------------------
# API 侧派发入口在 illustration_quiz.py / illustration_scenario.py
# （与 workflow 类同模块，保持 C1 惯用法）；以下是取消与存活性工具。
# ---------------------------------------------------------------------------

async def workflow_alive(workflow_id: str,
                         client: Client | None = None) -> bool | None:
    """Return running / terminal-or-missing / temporarily unknown.

    A failed describe is not evidence that the workflow disappeared. Only
    a confirmed terminal state or NOT_FOUND permits interrupted settlement.
    """
    try:
        connected = client or await get_client()
        description = await connected.get_workflow_handle(
            workflow_id).describe()
        status = getattr(description, "status", None)
        if status is None:
            status = getattr(
                getattr(description, "raw_info", None), "status", None)
        if status == WorkflowExecutionStatus.RUNNING:
            return True
        if status in set(WorkflowExecutionStatus) - {
                WorkflowExecutionStatus.RUNNING}:
            return False
        return None
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return False
        log.warning("Temporal describe unavailable; recovery deferred (%s)",
                    exc.status.name)
        return None
    except Exception as exc:
        log.warning("Temporal describe unavailable; recovery deferred (%s)",
                    type(exc).__name__)
        return None


def cancel_owner_workflows(owner: str, *, quiz_ids=(), scenario_ids=()) -> None:
    """Best-effort 取消该 owner 的在途 illustration workflow。

    从同步上下文（purge、session 删除路由）调用：detached 线程 + 独立
    Client 连接（进程级缓存 client 绑定 API 事件循环，不能跨线程/循环
    复用），失败静默——epoch 闸才是正确性保障。
    """
    workflow_ids = [quiz_workflow_id(owner, job_id) for job_id in quiz_ids]
    workflow_ids += [scenario_workflow_id(owner, job_id)
                     for job_id in scenario_ids]
    if not workflow_ids:
        return

    def _cancel() -> None:
        try:
            asyncio.run(_cancel_ids(workflow_ids))
        except Exception:
            pass

    threading.Thread(target=_cancel, daemon=True,
                     name="illustration-wf-cancel").start()


async def _cancel_ids(workflow_ids: list[str]) -> None:
    from temporalio.client import Client as TemporalClient

    from app.workflows.config import temporal_address, temporal_namespace
    client = await TemporalClient.connect(
        temporal_address(), namespace=temporal_namespace())
    for workflow_id in workflow_ids:
        try:
            await client.get_workflow_handle(workflow_id).cancel()
        except Exception:
            log.debug("workflow cancel skipped: %s", workflow_id,
                      exc_info=True)
