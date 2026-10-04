"""Classroom durable supervisor（ADR-0013 C2）。

确定性测试用 temporalio 内置 time-skipping test server；slice activity 以
同名替身注册。监督 workflow 是无限循环：测试验证「时间片持续调度 + 取消
即停」即可，域调度行为（并发/轮转/恢复）由 tests/classroom 的既有套件与
adopt 模式测试覆盖。
"""
from __future__ import annotations

import asyncio
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase

try:
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from app.workflows.classroom import (
        CLASSROOM_WORKFLOWS,
        ClassroomSupervisorWorkflow,
        ensure_supervisor,
        supervisor_workflow_id,
    )
    _TEMPORALIO_AVAILABLE = True
except Exception:  # pragma: no cover
    _TEMPORALIO_AVAILABLE = False


@unittest.skipUnless(_TEMPORALIO_AVAILABLE, "temporalio not installed")
class ClassroomSupervisorWorkflowTest(StorageSandboxTestCase,
                                      unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        try:
            self.env = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"temporal test server unavailable: {exc}")
        self.slices = 0

        @activity.defn(name="classroom.supervisor.slice")
        async def fake_slice() -> str:
            nonlocal self
            self.slices += 1
            await asyncio.sleep(0.02)  # 防无限循环空转烧 history
            return "ok"

        self.worker = Worker(
            self.env.client, task_queue="classroom",
            workflows=list(CLASSROOM_WORKFLOWS),
            activities=[fake_slice])

    async def asyncTearDown(self) -> None:
        await self.worker.shutdown()
        await self.env.shutdown()
        await super().asyncTearDown()

    async def test_supervisor_slices_until_cancelled(self) -> None:
        async with self.worker:
            handle = await ensure_supervisor(client=self.env.client)
            self.assertEqual(handle.id, supervisor_workflow_id())
            # 持续分片（≥3 片证明循环），随后取消即停。
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
