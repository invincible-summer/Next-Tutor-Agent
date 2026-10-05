"""Evaluation durable supervisor（ADR-0013 C4）。

确定性测试用 temporalio 内置 time-skipping test server；slice activity 以
同名替身注册。监督 workflow 是无限循环：测试验证「时间片持续调度 + 取消
即停 + 幂等 ensure」即可；域行为（claim/lease/outbox/每日关窗）由
tests/agents/student_model 的既有套件覆盖。真实 slice activity 对域
worker/planner 的启停驱动由集成车道（integration.py）验证。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase

try:
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from app.workflows.evaluation import (
        EVALUATION_WORKFLOWS,
        EvaluationSupervisorWorkflow,
        ensure_supervisor,
        supervisor_workflow_id,
    )
    _TEMPORALIO_AVAILABLE = True
except Exception:  # pragma: no cover
    _TEMPORALIO_AVAILABLE = False


@unittest.skipUnless(_TEMPORALIO_AVAILABLE, "temporalio not installed")
class EvaluationSupervisorWorkflowTest(StorageSandboxTestCase,
                                       unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        try:
            self.env = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"temporal test server unavailable: {exc}")
        self.slices = 0

        @activity.defn(name="evaluation.supervisor.slice")
        async def fake_slice() -> str:
            self.slices += 1
            await asyncio.sleep(0.02)  # 防无限循环空转烧 history
            return "ok"

        self.worker = Worker(
            self.env.client, task_queue="evaluation",
            workflows=list(EVALUATION_WORKFLOWS),
            activities=[fake_slice])

    async def asyncTearDown(self) -> None:
        await self.worker.shutdown()
        await self.env.shutdown()
        await super().asyncTearDown()

    async def test_supervisor_slices_until_cancelled(self) -> None:
        async with self.worker:
            handle = await ensure_supervisor(client=self.env.client)
            self.assertEqual(handle.id, supervisor_workflow_id())
            deadline = asyncio.get_running_loop().time() + 10
            while self.slices < 3 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            self.assertGreaterEqual(self.slices, 3)
            await handle.cancel()
            with self.assertRaises(WorkflowFailureError):
                await handle.result()

    async def test_ensure_supervisor_is_idempotent(self) -> None:
        async with self.worker:
            first = await ensure_supervisor(client=self.env.client)
            second = await ensure_supervisor(client=self.env.client)
            self.assertEqual(first.id, second.id)
            await first.cancel()
            with self.assertRaises(WorkflowFailureError):
                await first.result()


class EvaluationSeamTest(StorageSandboxTestCase):
    """slice activity 对域 worker/planner 的启停驱动（不依赖 test server）。"""

    def test_slice_starts_worker_and_planner(self) -> None:
        from app.agents.student_model.evaluation import schedule as sched_mod
        from app.agents.student_model.evaluation import worker as worker_mod
        from app.core import learner_runtime
        from app.workflows import evaluation as wf_evaluation

        events: list[str] = []

        class FakeWorker:
            def status(self) -> dict:
                return {"running": "worker:start" in events}

            def start(self) -> None:
                events.append("worker:start")

            async def stop(self) -> None:
                events.append("worker:stop")

        class FakePlanner:
            def status(self) -> dict:
                return {"running": "planner:start" in events}

            def start(self) -> None:
                events.append("planner:start")

            async def stop(self) -> None:
                events.append("planner:stop")

        fake_worker, fake_planner = FakeWorker(), FakePlanner()
        with mock.patch.object(learner_runtime, "evaluation_enabled",
                               return_value=True), \
                mock.patch.object(worker_mod, "get_evaluation_worker",
                                  return_value=fake_worker), \
                mock.patch.object(sched_mod, "get_daily_planner",
                                  return_value=fake_planner), \
                mock.patch.object(wf_evaluation, "_SLICE_SECONDS", 0.0), \
                mock.patch.object(wf_evaluation, "_SLICE_HEARTBEAT_SECONDS",
                                  0.01), \
                mock.patch.object(wf_evaluation.activity, "heartbeat"):
            result = asyncio.run(wf_evaluation.supervise_evaluation_slice())
        self.assertEqual(result, "ok")
        self.assertIn("worker:start", events)
        self.assertIn("planner:start", events)

    def test_slice_skips_domain_when_evaluation_disabled(self) -> None:
        from app.agents.student_model.evaluation import schedule as sched_mod
        from app.agents.student_model.evaluation import worker as worker_mod
        from app.core import learner_runtime
        from app.workflows import evaluation as wf_evaluation

        events: list[str] = []

        class FakeWorker:
            def status(self) -> dict:
                return {"running": False}

            def start(self) -> None:
                events.append("worker:start")

            async def stop(self) -> None:
                events.append("worker:stop")

        class FakePlanner:
            def status(self) -> dict:
                return {"running": False}

            def start(self) -> None:
                events.append("planner:start")

            async def stop(self) -> None:
                events.append("planner:stop")

        with mock.patch.object(learner_runtime, "evaluation_enabled",
                               return_value=False), \
                mock.patch.object(worker_mod, "get_evaluation_worker",
                                  return_value=FakeWorker()), \
                mock.patch.object(sched_mod, "get_daily_planner",
                                  return_value=FakePlanner()), \
                mock.patch.object(wf_evaluation, "_SLICE_SECONDS", 0.0), \
                mock.patch.object(wf_evaluation, "_SLICE_HEARTBEAT_SECONDS",
                                  0.01), \
                mock.patch.object(wf_evaluation.activity, "heartbeat"):
            result = asyncio.run(wf_evaluation.supervise_evaluation_slice())
        self.assertEqual(result, "ok")
        self.assertEqual(events, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
