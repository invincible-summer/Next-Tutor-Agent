"""Textbook build pipeline scheduling policy tests.

策略只治理执行调度：legacy 模式强制所有有效并发为 1；动态 LLM 门支持在线
resize 且 FIFO 准入（legacy 下调用顺序与历史串行一致）。
"""
from __future__ import annotations
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from app.api.v1.admin import TextbookPipelineRequest
from app.core import textbook_pipeline


class TestTextbookPipelinePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(
            textbook_pipeline, "_POLICY_FILE", Path(self.tmp.name) / "pipeline.json")
        self.path_patch.start()
        self.old_runtime = textbook_pipeline._RUNTIME
        textbook_pipeline._RUNTIME = textbook_pipeline._Runtime()

    def tearDown(self):
        textbook_pipeline._RUNTIME = self.old_runtime
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_request_bounds(self):
        first = TextbookPipelineRequest()
        self.assertEqual(first.mode, "parallel")
        self.assertEqual(first.build_concurrency, 2)
        self.assertEqual(TextbookPipelineRequest(mode="legacy").mode, "legacy")
        for bad in (0, 5):
            with self.assertRaises(Exception):
                TextbookPipelineRequest(build_concurrency=bad)
        with self.assertRaises(Exception):
            TextbookPipelineRequest(llm_concurrency=9)
        with self.assertRaises(Exception):
            TextbookPipelineRequest(mode="turbo")

    def test_bootstrap_defaults_to_parallel(self):
        snapshot = textbook_pipeline.get_policy()
        self.assertEqual(snapshot["mode"], "parallel")
        self.assertEqual(snapshot["effective_limits"], {"build": 2, "volume": 2, "llm": 4})

    def test_legacy_forces_all_limits_to_one(self):
        async def scenario():
            return await textbook_pipeline.set_policy(
                "legacy", build_concurrency=3, volume_concurrency=3, llm_concurrency=6)
        snapshot = asyncio.run(scenario())
        self.assertEqual(snapshot["mode"], "legacy")
        # 配置值保留，生效值全部强制 1（历史严格串行）。
        self.assertEqual(snapshot["build_concurrency"], 3)
        self.assertEqual(snapshot["effective_limits"], {"build": 1, "volume": 1, "llm": 1})
        self.assertEqual(textbook_pipeline.build_concurrency(), 1)
        self.assertEqual(textbook_pipeline.volume_concurrency(), 1)
        self.assertEqual(textbook_pipeline.llm_concurrency(), 1)
        persisted = textbook_pipeline._read_policy()
        self.assertEqual(persisted["mode"], "legacy")

    def test_set_policy_rejects_out_of_range(self):
        async def bad_mode():
            await textbook_pipeline.set_policy("turbo", 2, 2, 4)
        with self.assertRaises(ValueError):
            asyncio.run(bad_mode())
        async def bad_build():
            await textbook_pipeline.set_policy("parallel", 5, 2, 4)
        with self.assertRaises(ValueError):
            asyncio.run(bad_build())

    def test_gate_caps_concurrency_and_resizes_online(self):
        async def scenario():
            await textbook_pipeline.set_policy("parallel", 2, 2, 3)
            active = 0
            peak_before = 0
            peak_after = 0
            order: list[int] = []
            guard = asyncio.Lock()

            async def call(i: int, hold: float):
                nonlocal active, peak_before, peak_after
                async with textbook_pipeline.llm_gate():
                    async with guard:
                        active += 1
                        if len(order) < 6:
                            peak_before = max(peak_before, active)
                        else:
                            peak_after = max(peak_after, active)
                        order.append(i)
                    await asyncio.sleep(hold)
                    async with guard:
                        active -= 1

            tasks = [asyncio.create_task(call(i, 0.02)) for i in range(6)]
            await asyncio.sleep(0.01)
            await textbook_pipeline.set_policy("parallel", 2, 2, 1)
            await asyncio.gather(*tasks)
            return peak_before, peak_after, order
        peak_before, peak_after, order = asyncio.run(scenario())
        self.assertLessEqual(peak_before, 3)
        self.assertLessEqual(peak_after, 3)
        self.assertEqual(sorted(order), list(range(6)))

    def test_gate_legacy_admits_in_creation_order(self):
        async def scenario():
            await textbook_pipeline.set_policy("legacy", 1, 1, 1)
            order: list[int] = []

            async def call(i: int):
                async with textbook_pipeline.llm_gate():
                    order.append(i)
                    await asyncio.sleep(0.005)
            await asyncio.gather(*[call(i) for i in range(8)])
            return order
        self.assertEqual(asyncio.run(scenario()), list(range(8)))


if __name__ == "__main__":
    unittest.main()
