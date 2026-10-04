"""Quiz 题图 durable workflow（media task queue，ADR-0013 C3）。

一个 job 一个 workflow（id ``quiz-illustration:{owner}:{job_id}``）。run
activity 在 worker 进程内执行域 ``orchestrator._run``；worker 崩溃/心跳
超时等**非取消**失败后，settle activity 把磁盘记录结算为
``failed/run_interrupted``（retryable）。共享装配与语义见
``illustration_common``（不重试的预算理由、epoch 硬闸、best-effort 取消）。
"""
from __future__ import annotations

from temporalio import workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import ActivityError, CancelledError

from app.workflows.illustration_common import (
    QuizIllustrationIntent,
    quiz_workflow_id,
    run_activity_options,
    run_quiz_job_activity,
    settle_activity_options,
    settle_quiz_job_activity,
)
from app.workflows.runtime import TASK_QUEUE_MEDIA, get_client


@workflow.defn(name="illustration.quiz_job")
class QuizIllustrationWorkflow:
    """一次 quiz 配图生成的 durable 执行（含崩溃结算兜底）。"""

    @workflow.run
    async def run(self, intent: QuizIllustrationIntent) -> str:
        try:
            return await workflow.execute_activity(
                run_quiz_job_activity, args=[intent],
                **run_activity_options())
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                # 取消在途（purge/删除级联）：域内 epoch/补偿闸负责善后，
                # 取消本身就是期望终态，不做崩溃结算。
                raise
            return await workflow.execute_activity(
                settle_quiz_job_activity, args=[intent],
                **settle_activity_options())


QUIZ_ILLUSTRATION_WORKFLOWS = (QuizIllustrationWorkflow,)
QUIZ_ILLUSTRATION_ACTIVITIES = (
    run_quiz_job_activity, settle_quiz_job_activity)


async def start_quiz_job(intent: QuizIllustrationIntent,
                         client: Client | None = None) -> WorkflowHandle:
    """API 侧派发入口（fire-and-forget；域事实源在 job 记录）。"""
    connected = client or await get_client()
    return await connected.start_workflow(
        QuizIllustrationWorkflow.run, intent,
        id=quiz_workflow_id(intent.owner, intent.job_id),
        task_queue=TASK_QUEUE_MEDIA,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
