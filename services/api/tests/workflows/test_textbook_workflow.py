"""Textbook durable workflows（ADR-0013 C1）。

确定性 workflow 测试用 temporalio 内置 time-skipping test server（无外部
依赖）；activity 以同名替身注册，断言 workflow 编排（activity 调用、参数、
终态）。域行为本身由 tests/agents/knowledge 的既有套件覆盖（file 模式路径
不变）。真服务器集成车道见 tests/workflows/integration.py。
"""
from __future__ import annotations

import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase

try:  # temporalio 在基础 requirements；守卫 import 让极端裁剪环境可跳过。
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from app.workflows.textbook import (
        TEXTBOOK_WORKFLOWS,
        TextbookBuildIntent,
        TextbookRefreshIntent,
        TextbookRefreshWorkflow,
        TextbookBuildWorkflow,
        start_build_intent,
        start_refresh,
    )
    _TEMPORALIO_AVAILABLE = True
except Exception:  # pragma: no cover
    _TEMPORALIO_AVAILABLE = False


@unittest.skipUnless(_TEMPORALIO_AVAILABLE, "temporalio not installed")
class TextbookWorkflowEnvTest(StorageSandboxTestCase,
                              unittest.IsolatedAsyncioTestCase):
    """每个用例独立 test server + 注册同名假 activity 的 worker。"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        try:
            self.env = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:  # test server 不可用（离线裁剪环境）
            self.skipTest(f"temporal test server unavailable: {exc}")
        self.captured_builds: list[TextbookBuildIntent] = []
        self.captured_refreshes: list[TextbookRefreshIntent] = []

        @activity.defn(name="textbook.build_intent.run")
        async def fake_build(intent: TextbookBuildIntent) -> str:
            self.captured_builds.append(intent)
            return "done"

        @activity.defn(name="textbook.refresh.run")
        async def fake_refresh(intent: TextbookRefreshIntent) -> str:
            self.captured_refreshes.append(intent)
            return "done"

        self.fake_activities = (fake_build, fake_refresh)
        # 真实队列名（documents）：dispatch helper 硬编码该队列，隔离的
        # test server 里没有其它 worker，同名无冲突。
        self.worker = Worker(
            self.env.client, task_queue="documents",
            workflows=list(TEXTBOOK_WORKFLOWS),
            activities=list(self.fake_activities))

    async def asyncTearDown(self) -> None:
        await self.worker.shutdown()
        await self.env.shutdown()
        await super().asyncTearDown()

    def _intent(self) -> TextbookBuildIntent:
        return TextbookBuildIntent(
            owner="stu_1", tb_id="tb_9", kwargs={
                "ocr_parallel": True, "force_reextract": False,
                "use_llm": True, "auto_retry": True})

    async def test_build_intent_workflow_runs_activity_to_terminal(self) -> None:
        async with self.worker:
            handle = await self.env.client.start_workflow(
                TextbookBuildWorkflow.run, self._intent(),
                id="wf-test-build-1", task_queue="documents")
            self.assertEqual(await handle.result(), "done")
        self.assertEqual(len(self.captured_builds), 1)
        self.assertEqual(self.captured_builds[0].owner, "stu_1")
        self.assertEqual(self.captured_builds[0].kwargs["auto_retry"], True)

    async def test_refresh_workflow_runs_activity_to_terminal(self) -> None:
        async with self.worker:
            handle = await self.env.client.start_workflow(
                TextbookRefreshWorkflow.run,
                TextbookRefreshIntent(owner="stu_1", tb_id="tb_9",
                                      mode="graph_only"),
                id="wf-test-refresh-1", task_queue="documents")
            self.assertEqual(await handle.result(), "done")
        self.assertEqual(len(self.captured_refreshes), 1)
        self.assertEqual(self.captured_refreshes[0].mode, "graph_only")

    async def test_dispatch_helpers_round_trip(self) -> None:
        # start_build_intent/start_refresh 是 API 侧派发入口：经真实 client
        # 提交（documents 队列），activity 收到的 intent 字段不丢。
        async with self.worker:
            handle = await start_build_intent(self._intent(),
                                              client=self.env.client)
            self.assertEqual(await handle.result(), "done")
            refresh_handle = await start_refresh(
                TextbookRefreshIntent(owner="stu_1", tb_id="tb_9",
                                      mode="rag_graph"),
                client=self.env.client)
            self.assertEqual(await refresh_handle.result(), "done")
        self.assertEqual(len(self.captured_builds), 1)
        self.assertEqual(self.captured_builds[0].tb_id, "tb_9")
        self.assertEqual(len(self.captured_refreshes), 1)
        self.assertEqual(self.captured_refreshes[0].ocr_parallel, True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
