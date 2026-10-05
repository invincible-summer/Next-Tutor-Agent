"""工具助手情景配图 durable workflow（media task queue，ADR-0013 C3）。

一个 job 一个 workflow（id ``scenario-illustration:{owner}:{job_id}``）。
run activity 在 worker 进程内执行域 ``scenario._run``；崩溃结算、epoch
硬闸与取消语义见 ``illustration_common``。与 quiz workflow 相互独立
（业务边界、审核/发布合同不共享），只共享底层装配。
"""
from __future__ import annotations

from temporalio import workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import ActivityError, CancelledError

from app.workflows.illustration_common import (
    ScenarioIllustrationIntent,
    run_activity_options,
    run_scenario_job_activity,
    scenario_workflow_id,
    settle_activity_options,
    settle_scenario_job_activity,
)
from app.workflows.runtime import TASK_QUEUE_MEDIA, get_client


@workflow.defn(name="illustration.scenario_job")
class ScenarioIllustrationWorkflow:
    """一次情景配图轮次的 durable 执行（含崩溃结算兜底）。"""

    @workflow.run
    async def run(self, intent: ScenarioIllustrationIntent) -> str:
        try:
            return await workflow.execute_activity(
                run_scenario_job_activity, args=[intent],
                **run_activity_options())
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                raise
            return await workflow.execute_activity(
                settle_scenario_job_activity, args=[intent],
                **settle_activity_options())


SCENARIO_ILLUSTRATION_WORKFLOWS = (ScenarioIllustrationWorkflow,)
SCENARIO_ILLUSTRATION_ACTIVITIES = (
    run_scenario_job_activity, settle_scenario_job_activity)


async def start_scenario_job(intent: ScenarioIllustrationIntent,
                             client: Client | None = None) -> WorkflowHandle:
    """API 侧派发入口（fire-and-forget；域事实源在 session/job 记录）。"""
    connected = client or await get_client()
    return await connected.start_workflow(
        ScenarioIllustrationWorkflow.run, intent,
        id=scenario_workflow_id(intent.owner, intent.job_id),
        task_queue=TASK_QUEUE_MEDIA,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
