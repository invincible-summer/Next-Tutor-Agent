"""ClassroomWorker adopt 模式（durable lane，ADR-0013 C2）。

无 enqueue 钩子时（API 进程不再持有 worker），调度循环靠周期扫描磁盘
queued job 补登记——本测试证明「落盘即被收养并执行到终态」，且文件模式
默认关闭该行为。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.support.classroom_fake_llm import FakeClassroomLLM  # noqa: E402
from tests.support.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import storage as store  # noqa: E402
from app.classroom import worker as worker_mod  # noqa: E402
from app.classroom.pipeline import PipelineDeps  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.classroom.test_classroom_pipeline import OWNER, PipelineTestBase, WS  # noqa: E402


@dataclass
class _OkReport:
    ok: bool = True
    issues: list = field(default_factory=list)


def _fake_deps() -> PipelineDeps:
    return PipelineDeps(llm=FakeClassroomLLM(),
                        layout_check=lambda html: _OkReport())


class AdoptModeTests(PipelineTestBase):
    def test_adopt_mode_picks_up_disk_queued_job(self) -> None:
        async def scenario() -> None:
            worker = worker_mod.ClassroomWorker(
                deps_factory=lambda: _fake_deps())
            worker.enable_adopt_mode(interval=0.1)
            try:
                await worker.start()
                # 不调用 worker.enqueue——job 只落盘（API 进程语义）。
                lesson_id, job_id = self._make_job()
                deadline = asyncio.get_running_loop().time() + 10
                job = None
                while asyncio.get_running_loop().time() < deadline:
                    job = store.load_job(OWNER, WS, lesson_id, job_id)
                    if job is not None and job.state in (
                            sc.JobState.succeeded, sc.JobState.failed,
                            sc.JobState.cancelled):
                        break
                    await asyncio.sleep(0.05)
                assert job is not None
                self.assertEqual(job.state, sc.JobState.succeeded)
                # 收养幂等：终态后重复扫描不产生新登记。
                self.assertLessEqual(worker.adopt_queued_jobs(), 0)
            finally:
                await worker.stop()

        asyncio.run(scenario())

    def test_adopt_disabled_by_default_in_file_mode(self) -> None:
        async def scenario() -> None:
            worker = worker_mod.ClassroomWorker(
                deps_factory=lambda: _fake_deps())
            try:
                await worker.start()
                self.assertTrue(worker.is_running())
                lesson_id, job_id = self._make_job()
                await asyncio.sleep(0.4)  # 数个调度周期
                job = store.load_job(OWNER, WS, lesson_id, job_id)
                assert job is not None
                # 文件模式默认不收养：无 enqueue 钩子时保持 queued。
                self.assertEqual(job.state, sc.JobState.queued)
            finally:
                await worker.stop()

        asyncio.run(scenario())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
