"""Textbook OCR scheduler: parallel build-queue mode."""
from __future__ import annotations
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import library as library_mod
from app.core import textbook as tb_store
from app.core import textbook_ocr
from app.core import textbook_pipeline
from app.core import ocr_policy
from app.core.library import Library, library_data_dir, save_library
"""Durable textbook OCR retry rounds; chat OCR is tested separately."""
def _pipeline_policy(mode: str, *, build: int = 2, volume: int = 2, llm: int = 4) -> dict:
    return {"mode": mode, "build_concurrency": build, "volume_concurrency": volume,
            "llm_concurrency": llm, "updated_at": 0.0, "version": 1}
def _pdf(pages: int = 1) -> bytes:
    import fitz
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    raw = doc.tobytes()
    doc.close()
    return raw
def _persistent_policy(**over):
    policy = {"failure_mode": "persistent_api", "max_attempts": 3,
              "retry_interval_seconds": 60, "request_timeout_seconds": 60,
              "policy_version": 2}
    policy.update(over)
    return policy
if __name__ == "__main__":
    unittest.main()
class TestBuildQueueParallelMode(unittest.TestCase):
    """per-owner 构建队列（parallel 模式）：不同书有界并发、同书绝不并行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        from app.agents.knowledge import store as kgs_mod
        from app.agents.knowledge import textbook_builder
        self.builder = textbook_builder
        self.patches = [
            patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(kgs_mod, "_KG_DIR", root / "knowledge"),
            patch.object(kgs_mod, "_CUSTOM_DIR", root / "knowledge" / "custom"),
        ]
        for p in self.patches:
            p.start()
        self._old_poll = textbook_builder.QUEUE_POLL_SECONDS
        textbook_builder.QUEUE_POLL_SECONDS = 0.02
        textbook_builder._BUILD_QUEUES.clear()
        textbook_builder._BUILD_LOCKS.clear()
        self._old_policy = textbook_pipeline._RUNTIME.policy
        textbook_pipeline._RUNTIME.policy = _pipeline_policy("parallel", build=2)

    def tearDown(self):
        textbook_pipeline._RUNTIME.policy = self._old_policy
        self.builder.QUEUE_POLL_SECONDS = self._old_poll
        self.builder._BUILD_QUEUES.clear()
        self.builder._BUILD_LOCKS.clear()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _seed_two(self):
        ta = tb_store.create_textbook("stu", file_id="fA", title="教材A")
        tb = tb_store.create_textbook("stu", file_id="fB", title="教材B")
        return ta, tb

    def test_parallel_mode_overlaps_builds(self):
        """书 A 转入 ocr_waiting 等重试期间，书 B 即可开建（资源池重叠）。"""
        ta, tb = self._seed_two()
        events: list[tuple[str, str]] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            events.append(("build", tb_id))
            if tb_id == ta["id"]:
                tb_store.update_textbook(student_id, tb_id, status="ocr_waiting")
                async def finish():
                    await asyncio.sleep(0.15)
                    events.append(("terminal", tb_id))
                    tb_store.update_textbook(student_id, tb_id, status="ready")
                asyncio.get_running_loop().create_task(finish())
            else:
                tb_store.update_textbook(student_id, tb_id, status="ready")
            return None

        async def drive():
            self.assertTrue(self.builder.enqueue_textbook_build("stu", ta["id"]))
            self.assertTrue(self.builder.enqueue_textbook_build("stu", tb["id"]))
            await self.builder._BUILD_QUEUES["stu"]["worker"]

        with patch.object(self.builder, "build_group_graph", side_effect=fake_build), \
             patch.object(self.builder, "build_textbook_graph", side_effect=fake_build):
            asyncio.run(drive())
        # B 的构建发生在 A 终态之前（并发重叠），但派发顺序仍是 A 先 B 后。
        self.assertEqual(events[0], ("build", ta["id"]))
        self.assertIn(("build", tb["id"]), events)
        self.assertLess(events.index(("build", tb["id"])),
                        events.index(("terminal", ta["id"])))
        self.assertEqual(tb_store.find_textbook("stu", tb["id"])["status"], "ready")

    def test_same_book_never_builds_concurrently(self):
        """同一本书重复入队：构建严格串行（第二次等第一次终态后开跑）。"""
        ta, _tb = self._seed_two()
        active = 0
        peak = 0
        order: list[str] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            nonlocal active, peak
            order.append(f"start:{len(order)}")
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            tb_store.update_textbook(student_id, tb_id, status="ready")
            active -= 1
            return None

        async def drive():
            fa = self.builder.enqueue_textbook_build("stu", ta["id"])
            fb = self.builder.enqueue_textbook_build("stu", ta["id"])
            await asyncio.gather(fa, fb)

        with patch.object(self.builder, "run_textbook_build", side_effect=fake_build):
            asyncio.run(drive())
        self.assertEqual(peak, 1)
        self.assertEqual(order, ["start:0", "start:1"])

    def test_build_concurrency_cap(self):
        """三本书、并发上限 2：峰值同时构建 ≤ 2，全部完成。"""
        ids = []
        for i in range(3):
            rec = tb_store.create_textbook("stu", file_id=f"f{i}", title=f"教材{i}")
            ids.append(rec["id"])
        active = 0
        peak = 0
        built: list[str] = []

        async def fake_build(student_id, tb_id, llm=None, **kw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03)
            built.append(tb_id)
            tb_store.update_textbook(student_id, tb_id, status="ready")
            active -= 1
            return None

        async def drive():
            futures = [self.builder.enqueue_textbook_build("stu", tid) for tid in ids]
            await asyncio.gather(*[f for f in futures if f])

        with patch.object(self.builder, "run_textbook_build", side_effect=fake_build):
            asyncio.run(drive())
        self.assertLessEqual(peak, 2)
        self.assertEqual(sorted(built), sorted(ids))
