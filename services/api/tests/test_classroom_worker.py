"""课堂 worker 回归（plan.md D04 / §15.3）。

覆盖：queued job 被调度执行至发布；重启扫描把 running 恢复为 queued
（recovery_count 累计、超限 failed）；owner 并发 1（同 owner 串行）；
shutdown 宽限不把可恢复 job 标 failed；排队上限（每 owner 3）。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.classroom_fake_llm import FakeClassroomLLM  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import worker as worker_mod  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.classroom.pipeline import PipelineDeps  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, PipelineTestBase, WS  # noqa: E402


@dataclass
class _OkReport:
    ok: bool = True
    issues: list = field(default_factory=list)


def _fake_deps(**overrides) -> PipelineDeps:
    values = dict(llm=FakeClassroomLLM(),
                  layout_check=lambda html: _OkReport())
    values.update(overrides)
    return PipelineDeps(**values)


class GatedFakeLLM(FakeClassroomLLM):
    """可阻塞的 fake：第一次 complete 挂起直到 gate 放行。"""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def complete(self, messages, **kwargs):
        self.started.set()
        await self.gate.wait()
        return await super().complete(messages, **kwargs)


async def _wait_terminal(owner, ws, lesson, job_id,
                         timeout=8.0) -> sc.GenerationJob:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        job = store.load_job(owner, ws, lesson, job_id)
        if job is not None and job.state in (
                sc.JobState.succeeded, sc.JobState.failed,
                sc.JobState.cancelled, sc.JobState.awaiting_outline,
                sc.JobState.needs_input):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("job 未在时限内到达终态")


class WorkerRunTests(PipelineTestBase):
    def test_queued_job_executed_to_publish(self) -> None:
        async def scenario() -> None:
            worker = worker_mod.ClassroomWorker(
                deps_factory=lambda: _fake_deps())
            try:
                await worker.start()
                lesson_id, job_id = self._make_job()
                worker.enqueue(OWNER, WS, lesson_id, job_id)
                job = await _wait_terminal(OWNER, WS, lesson_id, job_id)
                self.assertEqual(job.state, sc.JobState.succeeded,
                                 msg=str(job.last_error))
                self.assertEqual(
                    store.load_lesson(OWNER, WS, lesson_id)
                    .latest_ready_revision, 1)
            finally:
                await worker.stop()
        asyncio.run(scenario())

    def test_startup_scan_recovers_running_job(self) -> None:
        async def scenario() -> None:
            lesson_id, job_id = self._make_job()

            def to_running(job: sc.GenerationJob) -> None:
                job.state = sc.JobState.running
            store.update_job(OWNER, WS, lesson_id, job_id, to_running)
            worker = worker_mod.ClassroomWorker(
                deps_factory=lambda: _fake_deps())
            try:
                await worker.start()  # 扫描 → queued + recovery_count=1 → 执行
                job = await _wait_terminal(OWNER, WS, lesson_id, job_id)
                self.assertEqual(job.state, sc.JobState.succeeded,
                                 msg=str(job.last_error))
                self.assertGreaterEqual(job.recovery_count, 1)
            finally:
                await worker.stop()
        asyncio.run(scenario())

    def test_recovery_over_limit_marks_failed(self) -> None:
        lesson_id, job_id = self._make_job()

        def stale(job: sc.GenerationJob) -> None:
            job.state = sc.JobState.running
            job.recovery_count = 3
        store.update_job(OWNER, WS, lesson_id, job_id, stale)
        worker = worker_mod.ClassroomWorker(
            deps_factory=lambda: _fake_deps())

        async def scenario() -> None:
            await worker.start()
            await asyncio.sleep(0.2)
            await worker.stop()
        asyncio.run(scenario())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertEqual(job.state, sc.JobState.failed)
        self.assertIn("显式重试", job.last_error or "")


class WorkerConcurrencyTests(PipelineTestBase):
    def test_owner_serialized_and_shutdown_keeps_recoverable(self) -> None:
        async def scenario() -> None:
            gated = GatedFakeLLM()
            worker = worker_mod.ClassroomWorker(
                deps_factory=lambda: _fake_deps(llm=gated))
            first = self._make_job()
            second = self._make_job()
            try:
                await worker.start()
                worker.enqueue(OWNER, WS, *first)
                await gated.started.wait()
                worker.enqueue(OWNER, WS, *second)
                await asyncio.sleep(0.3)
                # 同 owner 并发 1：恰好一个在跑，另一个保持 queued。
                # （启动扫描按目录序入队，先跑哪个是合法的两种情况之一。）
                self.assertEqual(len(worker._tasks), 1)
                states = {}
                for lesson_id, job_id in (first, second):
                    job = store.load_job(OWNER, WS, lesson_id, job_id)
                    states[job_id] = job.state
                running = [j for j, s in states.items()
                           if s == sc.JobState.running]
                queued = [j for j, s in states.items()
                          if s == sc.JobState.queued]
                self.assertEqual(len(running), 1,
                                 f"同 owner 必须串行：{states}")
                self.assertEqual(len(queued), 1, f"{states}")
                self.assertIn(list(worker._tasks)[0].job, running)
            finally:
                # shutdown：在途任务被取消 → 该 job 停在 running（可恢复），
                # 绝不标 failed；未开始的另一个保持 queued
                await worker.stop()
            for lesson_id, job_id in (first, second):
                job = store.load_job(OWNER, WS, lesson_id, job_id)
                if job.job_id in states and states[job.job_id] == \
                        sc.JobState.running:
                    self.assertEqual(
                        job.state, sc.JobState.running,
                        "取消的在途 job 必须保持可恢复状态")
                else:
                    self.assertEqual(job.state, sc.JobState.queued)
        asyncio.run(scenario())


class QueueCapacityTests(PipelineTestBase):
    def test_owner_queue_limit(self) -> None:
        for _ in range(worker_mod.limits.QUEUE_PER_OWNER):
            self._make_job()
        with self.assertRaises(ClassroomError) as ctx:
            worker_mod.assert_queue_capacity(OWNER)
        self.assertEqual(ctx.exception.code, "quota_exceeded")

    def test_counts_only_queued(self) -> None:
        lesson_id, job_id = self._make_job()
        self.assertEqual(worker_mod.count_queued_jobs(OWNER), 1)

        def to_failed(job: sc.GenerationJob) -> None:
            job.state = sc.JobState.failed
        store.update_job(OWNER, WS, lesson_id, job_id, to_failed)
        self.assertEqual(worker_mod.count_queued_jobs(OWNER), 0)


if __name__ == "__main__":
    unittest.main()
