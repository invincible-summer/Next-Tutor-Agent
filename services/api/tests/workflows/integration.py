"""Integration lane: real Temporal server behind TEST_TEMPORAL_ADDRESS.

The module intentionally carries no ``test_`` prefix: neither unittest
discovery nor the CI shard planner collects it into the ordinary matrix.
It runs in the CI ``backend-temporal`` job (temporal service container)
and locally::

    cd deploy/local && docker compose --profile temporal up -d
    TEST_TEMPORAL_ADDRESS=127.0.0.1:7233 \\
        python -m tests tests.workflows.integration

Acceptance criterion (ADR-0013 C1): a textbook build intent dispatched
from the API role reaches a worker running the real documents lane, rides
the domain per-owner queue in the worker process, and settles the book to
a terminal status on disk — durable dispatch with zero file-mode drift.
"""
from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase

_TEMPORAL_ADDRESS = os.getenv("TEST_TEMPORAL_ADDRESS", "").strip()


@unittest.skipUnless(_TEMPORAL_ADDRESS,
                     "TEST_TEMPORAL_ADDRESS not set — Temporal integration "
                     "skipped")
class TextbookDurableIntegrationTest(StorageSandboxTestCase,
                                     unittest.IsolatedAsyncioTestCase):
    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from app.workflows import config as wf_config

        cls._saved_env = {
            "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS")}
        os.environ["TEMPORAL_ADDRESS"] = _TEMPORAL_ADDRESS
        # 测试进程扮演 worker：activity 内 enqueue 必须走进程内域队列，
        # 而不是递归派发 workflow（与 worker.py 的 mark_worker_process 对应）。
        wf_config._worker_process = True

    @classmethod
    def tearDownClass(cls) -> None:
        from app.workflows import config as wf_config

        wf_config._worker_process = False
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def test_build_intent_rides_domain_queue_to_terminal(self) -> None:
        from app.core import textbook as tb_store
        from app.workflows import runtime as wf_runtime
        from app.workflows import textbook as wf_textbook

        student = f"stu_{uuid.uuid4().hex[:8]}"
        seed = uuid.uuid4().hex[:8]
        record = tb_store.create_textbook(
            student, file_id=f"file_{seed}", title="集成测试教材",
            subject="数学", level="senior_2")
        tb_id = record["id"]

        # 域桩：真实构建依赖 LLM/OCR；集成车道只验证「API 派发 → worker 执行
        # → 进程内域队列 → 状态机结算」的 durable 链路本身。
        from app.agents.knowledge import textbook_builder as builder

        async def run_stub(sid: str, tid: str, **kwargs) -> None:
            tb_store.update_textbook(sid, tid, status="ready")

        from temporalio.worker import Worker

        wf_runtime.reset_client_cache()
        worker = Worker(
            await wf_runtime.get_client(),
            task_queue=wf_runtime.TASK_QUEUE_DOCUMENTS,
            workflows=list(wf_textbook.TEXTBOOK_WORKFLOWS),
            activities=list(wf_textbook.TEXTBOOK_ACTIVITIES))
        try:
            async with worker:
                with mock.patch.object(builder, "run_textbook_build",
                                       run_stub):
                    handle = await wf_textbook.start_build_intent(
                        wf_textbook.TextbookBuildIntent(
                            owner=student, tb_id=tb_id,
                            kwargs={"use_llm": False, "auto_retry": True}))
                    await asyncio.wait_for(handle.result(), timeout=60)
            final = tb_store.find_textbook(student, tb_id) or {}
            self.assertEqual(final.get("status"), "ready")
            job = final.get("build_job") or {}
            self.assertEqual(job.get("state"), "ready")
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()


@unittest.skipUnless(_TEMPORAL_ADDRESS,
                     "TEST_TEMPORAL_ADDRESS not set — Temporal integration "
                     "skipped")
class ClassroomSupervisorIntegrationTest(StorageSandboxTestCase,
                                         unittest.IsolatedAsyncioTestCase):
    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from app.workflows import config as wf_config

        cls._saved_env = {
            "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS")}
        os.environ["TEMPORAL_ADDRESS"] = _TEMPORAL_ADDRESS
        wf_config._worker_process = True

    @classmethod
    def tearDownClass(cls) -> None:
        from app.workflows import config as wf_config

        wf_config._worker_process = False
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def test_supervisor_drives_domain_worker(self) -> None:
        """监督 workflow → slice activity → 域 worker 启动/心跳/取消停机。

        域 worker 以替身注入（真实 ClassroomWorker 的调度/恢复行为由
        tests/classroom 套件覆盖）；这里验证 durable 链路本身。
        """
        from temporalio.worker import Worker
        from app.classroom import worker as classroom_worker_module
        from app.workflows import classroom as wf_classroom
        from app.workflows import runtime as wf_runtime

        events: list[str] = []

        class FakeDomainWorker:
            def is_running(self) -> bool:
                return "start" in events

            def enable_adopt_mode(self, interval: float = 2.0) -> None:
                events.append(f"adopt:{interval}")

            async def start(self) -> None:
                events.append("start")

            async def stop(self) -> None:
                events.append("stop")

        def fake_get_worker() -> FakeDomainWorker:
            return fake

        fake = FakeDomainWorker()
        wf_runtime.reset_client_cache()
        worker = Worker(
            await wf_runtime.get_client(),
            task_queue=wf_runtime.TASK_QUEUE_CLASSROOM,
            workflows=list(wf_classroom.CLASSROOM_WORKFLOWS),
            activities=list(wf_classroom.CLASSROOM_ACTIVITIES))
        try:
            with mock.patch.object(classroom_worker_module, "get_worker",
                                   fake_get_worker):
                async with worker:
                    handle = await wf_classroom.ensure_supervisor()
                    deadline = asyncio.get_running_loop().time() + 30
                    while "start" not in events and \
                            asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(0.1)
                    self.assertIn("start", events)
                    self.assertTrue(any(e.startswith("adopt:")
                                        for e in events))
                    await handle.cancel()
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()


@unittest.skipUnless(_TEMPORAL_ADDRESS,
                     "TEST_TEMPORAL_ADDRESS not set — Temporal integration "
                     "skipped")
class IllustrationDurableIntegrationTest(StorageSandboxTestCase,
                                         unittest.IsolatedAsyncioTestCase):
    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from app.workflows import config as wf_config

        cls._saved_env = {
            "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS")}
        os.environ["TEMPORAL_ADDRESS"] = _TEMPORAL_ADDRESS
        wf_config._worker_process = True

    @classmethod
    def tearDownClass(cls) -> None:
        from app.workflows import config as wf_config

        wf_config._worker_process = False
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _seed_quiz_job(self, owner: str) -> str:
        from app.illustration import persistence

        job_id = "illjob_" + uuid.uuid4().hex[:12]
        persistence.write(owner, "jobs", job_id, {
            "job_id": job_id, "run_id": "illrun_" + job_id,
            "status": "queued", "stage": "created",
            "owner_epoch": persistence.epoch(owner),
            "question_id": "q_i", "question_revision": 1})
        return job_id

    async def test_quiz_job_reaches_ready(self) -> None:
        """API 派发 → media worker → 域 _run（桩）→ 磁盘终态。"""
        from app.illustration import orchestrator, persistence
        from app.workflows import illustration_quiz
        from app.workflows import runtime as wf_runtime
        from temporalio.worker import Worker

        owner = f"stu_{uuid.uuid4().hex[:8]}"
        job_id = self._seed_quiz_job(owner)

        async def run_stub(owner_arg, job, llm) -> None:
            job.update(status="ready")
            persistence.write(owner_arg, "jobs", job["job_id"], job,
                              expected_epoch=job["owner_epoch"])

        wf_runtime.reset_client_cache()
        worker = Worker(
            await wf_runtime.get_client(),
            task_queue=wf_runtime.TASK_QUEUE_MEDIA,
            workflows=list(illustration_quiz.QUIZ_ILLUSTRATION_WORKFLOWS),
            activities=list(illustration_quiz.QUIZ_ILLUSTRATION_ACTIVITIES))
        try:
            async with worker:
                with mock.patch.object(orchestrator, "_run", run_stub):
                    handle = await illustration_quiz.start_quiz_job(
                        illustration_quiz.QuizIllustrationIntent(
                            owner=owner, job_id=job_id))
                    await asyncio.wait_for(handle.result(), timeout=60)
            final = persistence.read(owner, "jobs", job_id) or {}
            self.assertEqual(final.get("status"), "ready")
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()

    async def test_quiz_job_worker_crash_settles_interrupted(self) -> None:
        """run activity 基建级失败 → settle activity 兜底结算 run_interrupted。"""
        from app.illustration import persistence
        from app.workflows import illustration_common, illustration_quiz
        from app.workflows import runtime as wf_runtime
        from temporalio.worker import Worker

        owner = f"stu_{uuid.uuid4().hex[:8]}"
        job_id = self._seed_quiz_job(owner)

        def crashed_heartbeat(coro):
            coro.close()  # 不留未 await 的协程
            raise RuntimeError("worker died")

        wf_runtime.reset_client_cache()
        worker = Worker(
            await wf_runtime.get_client(),
            task_queue=wf_runtime.TASK_QUEUE_MEDIA,
            workflows=list(illustration_quiz.QUIZ_ILLUSTRATION_WORKFLOWS),
            activities=list(illustration_quiz.QUIZ_ILLUSTRATION_ACTIVITIES))
        try:
            async with worker:
                with mock.patch.object(illustration_common,
                                       "_run_with_heartbeat",
                                       crashed_heartbeat):
                    handle = await illustration_quiz.start_quiz_job(
                        illustration_quiz.QuizIllustrationIntent(
                            owner=owner, job_id=job_id))
                    await asyncio.wait_for(handle.result(), timeout=60)
            final = persistence.read(owner, "jobs", job_id) or {}
            self.assertEqual(final.get("status"), "failed")
            self.assertEqual(final.get("failure", {}).get("code"),
                             "run_interrupted")
            self.assertTrue(final.get("failure", {}).get("retryable"))
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()

    async def test_scenario_job_worker_crash_settles_interrupted(self) -> None:
        """情景配图同一兜底链路：session active_job 清空、job 标中断。"""
        from app.illustration import persistence, scenario
        from app.illustration.scenario_contracts import CreateSession
        from app.workflows import illustration_common, illustration_scenario
        from app.workflows import runtime as wf_runtime
        from temporalio.worker import Worker

        owner = f"stu_{uuid.uuid4().hex[:8]}"
        session_id = scenario.create_session(owner, CreateSession())["session_id"]
        job_id = "scenejob_" + uuid.uuid4().hex[:12]
        persistence.write(owner, "scenario_jobs", job_id, {
            "job_id": job_id, "session_id": session_id, "turn_id": "turn_1",
            "mode": "v1", "status": "queued", "stage": "preparing",
            "base_revision": 0, "revision": None, "artifact_id": None,
            "failure": None, "created_at": 0.0, "updated_at": 0.0,
            "owner_epoch": persistence.epoch(owner), "attempt": 1})
        session = persistence.read(owner, "sessions", session_id)
        session.update(active_job_id=job_id, turns=[{
            "turn_id": "turn_1", "message": "m", "mode": "v1",
            "selected_materials": [], "job_id": job_id, "status": "queued",
            "revision": None, "created_at": 0.0, "request_id": None,
            "source_revision": 0}])
        persistence.write(owner, "sessions", session_id, session)

        def crashed_heartbeat(coro):
            coro.close()
            raise RuntimeError("worker died")

        wf_runtime.reset_client_cache()
        worker = Worker(
            await wf_runtime.get_client(),
            task_queue=wf_runtime.TASK_QUEUE_MEDIA,
            workflows=list(illustration_scenario.SCENARIO_ILLUSTRATION_WORKFLOWS),
            activities=list(illustration_scenario.SCENARIO_ILLUSTRATION_ACTIVITIES))
        try:
            async with worker:
                with mock.patch.object(illustration_common,
                                       "_run_with_heartbeat",
                                       crashed_heartbeat):
                    handle = await illustration_scenario.start_scenario_job(
                        illustration_scenario.ScenarioIllustrationIntent(
                            owner=owner, session_id=session_id, job_id=job_id))
                    await asyncio.wait_for(handle.result(), timeout=60)
            final = persistence.read(owner, "scenario_jobs", job_id) or {}
            self.assertEqual(final.get("status"), "failed")
            self.assertEqual(final.get("failure", {}).get("code"),
                             "run_interrupted")
            self.assertIsNone(
                (persistence.read(owner, "sessions", session_id) or {})
                .get("active_job_id"))
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()


@unittest.skipUnless(_TEMPORAL_ADDRESS,
                     "TEST_TEMPORAL_ADDRESS not set — Temporal integration "
                     "skipped")
class EvaluationSupervisorIntegrationTest(StorageSandboxTestCase,
                                          unittest.IsolatedAsyncioTestCase):
    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from app.workflows import config as wf_config

        cls._saved_env = {
            "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS")}
        os.environ["TEMPORAL_ADDRESS"] = _TEMPORAL_ADDRESS
        wf_config._worker_process = True

    @classmethod
    def tearDownClass(cls) -> None:
        from app.workflows import config as wf_config

        wf_config._worker_process = False
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def test_supervisor_drives_domain_worker_and_planner(self) -> None:
        """监督 workflow → slice activity → 域 worker/planner 启动/心跳。

        域 worker/planner 以替身注入（真实 claim/lease/outbox/关窗行为由
        tests/agents/student_model 套件覆盖）；这里验证 durable 链路本身。
        """
        from temporalio.worker import Worker
        from app.agents.student_model.evaluation import schedule as sched_mod
        from app.agents.student_model.evaluation import worker as worker_mod
        from app.core import learner_runtime
        from app.workflows import evaluation as wf_evaluation
        from app.workflows import runtime as wf_runtime

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

        wf_runtime.reset_client_cache()
        worker = Worker(
            await wf_runtime.get_client(),
            task_queue=wf_runtime.TASK_QUEUE_EVALUATION,
            workflows=list(wf_evaluation.EVALUATION_WORKFLOWS),
            activities=list(wf_evaluation.EVALUATION_ACTIVITIES))
        try:
            with mock.patch.object(learner_runtime, "evaluation_enabled",
                                   return_value=True), \
                    mock.patch.object(worker_mod, "get_evaluation_worker",
                                      return_value=FakeWorker()), \
                    mock.patch.object(sched_mod, "get_daily_planner",
                                      return_value=FakePlanner()):
                async with worker:
                    handle = await wf_evaluation.ensure_supervisor()
                    deadline = asyncio.get_running_loop().time() + 30
                    while "planner:start" not in events and \
                            asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(0.1)
                    self.assertIn("worker:start", events)
                    self.assertIn("planner:start", events)
                    await handle.cancel()
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()


@unittest.skipUnless(_TEMPORAL_ADDRESS,
                     "TEST_TEMPORAL_ADDRESS not set — Temporal integration "
                     "skipped")
class MaintenanceScheduleIntegrationTest(StorageSandboxTestCase,
                                         unittest.IsolatedAsyncioTestCase):
    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from app.workflows import config as wf_config

        cls._saved_env = {
            "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS")}
        os.environ["TEMPORAL_ADDRESS"] = _TEMPORAL_ADDRESS
        wf_config._worker_process = True

    @classmethod
    def tearDownClass(cls) -> None:
        from app.workflows import config as wf_config

        wf_config._worker_process = False
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def test_schedules_register_and_tick(self) -> None:
        """ensure_schedules 幂等注册；trigger 触发一次真实 tick workflow。"""
        from temporalio.worker import Worker
        from app.workflows import maintenance as wf_maintenance
        from app.workflows import runtime as wf_runtime

        async def trash_stub() -> None:
            pass  # cleanup_expired 在空沙箱数据根上本就无事务可做

        wf_runtime.reset_client_cache()
        client = await wf_runtime.get_client()
        worker = Worker(
            client, task_queue=wf_runtime.TASK_QUEUE_MAINTENANCE,
            workflows=list(wf_maintenance.MAINTENANCE_WORKFLOWS),
            activities=list(wf_maintenance.MAINTENANCE_ACTIVITIES))
        try:
            async with worker:
                first = await wf_maintenance.ensure_schedules(client=client)
                second = await wf_maintenance.ensure_schedules(client=client)
                self.assertIn("maintenance-trash", first)
                self.assertEqual(
                    second["maintenance-trash"], "existing")
                described = await client.get_schedule_handle(
                    "maintenance-trash").describe()
                self.assertTrue(described.schedule.spec.intervals)
                # 手动触发一轮，确认 Schedule → workflow → activity 链路。
                await client.get_schedule_handle(
                    "maintenance-trash").trigger()
                deadline = asyncio.get_running_loop().time() + 60
                tick_done = False
                while asyncio.get_running_loop().time() < deadline:
                    async for row in client.list_workflows(
                            query="ExecutionStatus = 'Completed'"):
                        if row.id.startswith(
                                "maintenance-tick-trash-"):
                            tick_done = True
                            break
                    if tick_done:
                        break
                    await asyncio.sleep(0.5)
                self.assertTrue(tick_done)
                for schedule_id in first:
                    await client.get_schedule_handle(schedule_id).delete()
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()

    async def test_account_purge_workflow(self) -> None:
        from temporalio.worker import Worker
        from app.workflows import maintenance as wf_maintenance
        from app.workflows import runtime as wf_runtime

        wf_runtime.reset_client_cache()
        client = await wf_runtime.get_client()
        worker = Worker(
            client, task_queue=wf_runtime.TASK_QUEUE_MAINTENANCE,
            workflows=list(wf_maintenance.MAINTENANCE_WORKFLOWS),
            activities=list(wf_maintenance.MAINTENANCE_ACTIVITIES))
        try:
            async with worker:
                # 沙箱数据根 + 不存在的账号：purge 链幂等完成并返回报告。
                report = await wf_maintenance.purge_account_durable(
                    f"usr_gone_{uuid.uuid4().hex[:8]}")
            self.assertIsInstance(report, dict)
            self.assertIn("status", report)
        finally:
            await worker.shutdown()
            wf_runtime.reset_client_cache()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
