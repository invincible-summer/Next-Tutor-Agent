"""Textbook 双模式接缝（ADR-0013 C1）。

核心契约：默认（无 TEMPORAL_ADDRESS）时 enqueue/spawn 走原进程内路径，
行为零变化；设置后 API 进程派发 workflow；worker 进程内即使设置了地址也
继续走进程内域队列（activity 复用，不递归派发）。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase


class _BuilderQueueFixture:
    """清空 builder 的进程内队列残留（worker task 一并取消）。"""

    def __enter__(self):
        from app.agents.knowledge import textbook_builder as builder
        self.builder = builder
        return self

    def __exit__(self, *exc):
        for queue in self.builder._BUILD_QUEUES.values():
            worker = queue.get("worker")
            if worker is not None and not worker.done():
                worker.cancel()
        self.builder._BUILD_QUEUES.clear()
        return False


class EnqueueSeamTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        from app.workflows import config as wf_config
        wf_config._worker_process = False
        super().tearDown()

    async def test_file_mode_uses_in_process_queue(self) -> None:
        from app.agents.knowledge import textbook_builder as builder

        with _BuilderQueueFixture():
            future = builder.enqueue_textbook_build(
                "stu_1", "tb_1", use_llm=True)
            self.assertIsNotNone(future)
            queue = builder._BUILD_QUEUES.get("stu_1")
            self.assertIsNotNone(queue)
            self.assertEqual(len(queue["items"]), 1)
            self.assertEqual(queue["items"][0]["kwargs"]["use_llm"], True)
            self.assertTrue(queue["worker"] is not None)

    async def test_temporal_mode_dispatches_workflow(self) -> None:
        from app.agents.knowledge import textbook_builder as builder
        from app.workflows import textbook as wf_textbook

        captured: list[wf_textbook.TextbookBuildIntent] = []

        async def fake_dispatch(intent) -> None:
            captured.append(intent)

        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch.object(wf_textbook, "dispatch_build_intent",
                                  fake_dispatch), \
                _BuilderQueueFixture():
            future = builder.enqueue_textbook_build(
                "stu_2", "tb_2", ocr_parallel=True, auto_retry=True)
            self.assertIsNotNone(future)
            await asyncio.wait_for(future, timeout=5)
            # 进程内队列绝不被触碰（不双驱动）。
            self.assertNotIn("stu_2", builder._BUILD_QUEUES)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].owner, "stu_2")
            self.assertEqual(captured[0].kwargs["auto_retry"], True)

    async def test_worker_process_keeps_in_process_queue(self) -> None:
        from app.agents.knowledge import textbook_builder as builder
        from app.workflows import config as wf_config

        wf_config._worker_process = True
        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                _BuilderQueueFixture():
            future = builder.enqueue_textbook_build("stu_3", "tb_3")
            self.assertIsNotNone(future)
            self.assertIn("stu_3", builder._BUILD_QUEUES)


class RefreshSeamTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_temporal_mode_dispatches_refresh_workflow(self) -> None:
        from app.api.v1 import textbook as tb_api
        from app.workflows import textbook as wf_textbook

        captured: list[wf_textbook.TextbookRefreshIntent] = []

        async def fake_start(intent, client=None) -> None:
            captured.append(intent)

        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch.object(wf_textbook, "start_refresh", fake_start):
            spawned = tb_api._spawn_refresh("stu_4", "tb_4", "graph_only")
            self.assertTrue(spawned)
            await asyncio.sleep(0)  # 让 fire-and-forget 任务跑完
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].mode, "graph_only")

    async def test_file_mode_spawns_local_task(self) -> None:
        from app.api.v1 import textbook as tb_api

        with mock.patch.object(
                tb_api, "_safe_refresh",
                new=mock.AsyncMock(return_value=None)) as refresh:
            spawned = tb_api._spawn_refresh("stu_5", "tb_5", "rag_graph")
            self.assertTrue(spawned)
            await asyncio.sleep(0)
            refresh.assert_awaited_once_with("stu_5", "tb_5", "rag_graph",
                                             ocr_parallel=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
