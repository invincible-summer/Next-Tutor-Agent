"""Illustration durable workflows（ADR-0013 C3）。

确定性 workflow 测试（temporalio time-skipping test server + 同名假
activity）覆盖编排契约：run 成功、run 崩溃 → settle 兜底、取消不作崩溃
结算、API 派发入口 round-trip。双模式接缝（file/temporal/worker 进程三
态）另以 Seams 段覆盖；域行为本身由 tests/illustration 既有套件守护
（file 模式路径零变化）。真服务器集成车道见 integration.py。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase


async def _hanging_run(owner, job, llm=None):
    """替身域执行：挂起直至被取消（保持 _running 注册表占用，便于断言）。"""
    await asyncio.Event().wait()

try:  # temporalio 在基础 requirements；守卫 import 让极端裁剪环境可跳过。
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from app.workflows.illustration_quiz import (
        QUIZ_ILLUSTRATION_WORKFLOWS,
        QuizIllustrationIntent,
        QuizIllustrationWorkflow,
        start_quiz_job,
    )
    from app.workflows.illustration_scenario import (
        SCENARIO_ILLUSTRATION_WORKFLOWS,
        ScenarioIllustrationIntent,
        ScenarioIllustrationWorkflow,
        start_scenario_job,
    )
    _TEMPORALIO_AVAILABLE = True
except Exception:  # pragma: no cover
    _TEMPORALIO_AVAILABLE = False


@unittest.skipUnless(_TEMPORALIO_AVAILABLE, "temporalio not installed")
class IllustrationWorkflowEnvTest(StorageSandboxTestCase,
                                  unittest.IsolatedAsyncioTestCase):
    """每个用例独立 test server + 注册同名假 activity 的 worker。"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        try:
            self.env = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:  # test server 不可用（离线裁剪环境）
            self.skipTest(f"temporal test server unavailable: {exc}")
        self.quiz_runs: list[QuizIllustrationIntent] = []
        self.quiz_settles: list[QuizIllustrationIntent] = []
        self.scenario_runs: list[ScenarioIllustrationIntent] = []
        self.scenario_settles: list[ScenarioIllustrationIntent] = []
        self.run_behavior: dict[str, str] = {}  # "quiz"/"scenario" -> 行为
        self.run_gate = asyncio.Event()  # behavior == "wait" 时挂起 activity
        env_test = self

        @activity.defn(name="illustration.quiz_job.run")
        async def fake_quiz_run(intent: QuizIllustrationIntent) -> str:
            env_test.quiz_runs.append(intent)
            return await env_test._fake_run_behavior("quiz")

        @activity.defn(name="illustration.quiz_job.settle")
        async def fake_quiz_settle(intent: QuizIllustrationIntent) -> str:
            env_test.quiz_settles.append(intent)
            return "settled"

        @activity.defn(name="illustration.scenario_job.run")
        async def fake_scenario_run(intent: ScenarioIllustrationIntent) -> str:
            env_test.scenario_runs.append(intent)
            return await env_test._fake_run_behavior("scenario")

        @activity.defn(name="illustration.scenario_job.settle")
        async def fake_scenario_settle(
                intent: ScenarioIllustrationIntent) -> str:
            env_test.scenario_settles.append(intent)
            return "settled"

        self.fake_activities = (
            fake_quiz_run, fake_quiz_settle, fake_scenario_run,
            fake_scenario_settle)
        self.worker = Worker(
            self.env.client, task_queue="media",
            workflows=list(QUIZ_ILLUSTRATION_WORKFLOWS
                           + SCENARIO_ILLUSTRATION_WORKFLOWS),
            activities=list(self.fake_activities))

    async def _fake_run_behavior(self, lane: str) -> str:
        behavior = self.run_behavior.get(lane, "done")
        if behavior == "crash":
            raise RuntimeError("worker died")
        if behavior == "wait":
            await self.run_gate.wait()
        return "done"

    async def asyncTearDown(self) -> None:
        await self.worker.shutdown()
        await self.env.shutdown()
        await super().asyncTearDown()

    def _quiz_intent(self) -> QuizIllustrationIntent:
        return QuizIllustrationIntent(owner="stu_1", job_id="illjob_x")

    def _scenario_intent(self) -> ScenarioIllustrationIntent:
        return ScenarioIllustrationIntent(
            owner="stu_1", session_id="scene_y", job_id="scenejob_z")

    async def test_quiz_job_runs_activity_to_terminal(self) -> None:
        async with self.worker:
            handle = await self.env.client.start_workflow(
                QuizIllustrationWorkflow.run, self._quiz_intent(),
                id="wf-test-quiz-1", task_queue="media")
            self.assertEqual(await handle.result(), "done")
        self.assertEqual(len(self.quiz_runs), 1)
        self.assertEqual(self.quiz_runs[0].job_id, "illjob_x")
        self.assertEqual(self.quiz_settles, [])

    async def test_quiz_job_crash_settles_interrupted(self) -> None:
        self.run_behavior["quiz"] = "crash"
        async with self.worker:
            handle = await self.env.client.start_workflow(
                QuizIllustrationWorkflow.run, self._quiz_intent(),
                id="wf-test-quiz-2", task_queue="media")
            self.assertEqual(await handle.result(), "settled")
        self.assertEqual(len(self.quiz_settles), 1)
        self.assertEqual(self.quiz_settles[0].owner, "stu_1")

    async def test_quiz_job_cancellation_is_not_a_crash(self) -> None:
        # 取消（purge/删除级联）不触发崩溃结算——善后属于域内 epoch/墓碑。
        self.run_behavior["quiz"] = "wait"
        async with self.worker:
            handle = await self.env.client.start_workflow(
                QuizIllustrationWorkflow.run, self._quiz_intent(),
                id="wf-test-quiz-3", task_queue="media")
            await asyncio.sleep(0.2)  # 让 run activity 进入挂起
            await handle.cancel()
            with self.assertRaises(WorkflowFailureError):
                await handle.result()
            self.run_gate.set()
        self.assertEqual(len(self.quiz_runs), 1)
        self.assertEqual(self.quiz_settles, [])

    async def test_scenario_job_crash_settles_interrupted(self) -> None:
        self.run_behavior["scenario"] = "crash"
        async with self.worker:
            handle = await self.env.client.start_workflow(
                ScenarioIllustrationWorkflow.run, self._scenario_intent(),
                id="wf-test-scene-1", task_queue="media")
            self.assertEqual(await handle.result(), "settled")
        self.assertEqual(len(self.scenario_settles), 1)
        self.assertEqual(self.scenario_settles[0].session_id, "scene_y")

    async def test_dispatch_helpers_round_trip(self) -> None:
        # start_quiz_job/start_scenario_job 是 API 侧派发入口：经真实 client
        # 提交（media 队列），activity 收到的 intent 字段不丢。
        async with self.worker:
            handle = await start_quiz_job(self._quiz_intent(),
                                          client=self.env.client)
            self.assertEqual(await handle.result(), "done")
            scene_handle = await start_scenario_job(
                self._scenario_intent(), client=self.env.client)
            self.assertEqual(await scene_handle.result(), "done")
        self.assertEqual(len(self.quiz_runs), 1)
        self.assertEqual(self.quiz_runs[0].job_id, "illjob_x")
        self.assertEqual(len(self.scenario_runs), 1)
        self.assertEqual(self.scenario_runs[0].job_id, "scenejob_z")


class SeamsTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    """双模式接缝：file 模式零变化；temporal 模式 API 派发 / worker 进程内。"""

    def tearDown(self) -> None:
        from app.workflows import config as wf_config
        wf_config._worker_process = False
        super().tearDown()

    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for name, value in (("quiz_svg_enabled", True),):
            setting = mock.patch.object(settings, name, value)
            setting.start()
            self._patches.append(setting)
        self._cleanup_running()

    def _cleanup_running(self) -> None:
        from app.illustration import orchestrator, scenario
        for registry in (orchestrator._running, scenario._running):
            for task in list(registry.values()):
                task.cancel()
            registry.clear()

    async def _new_session(self, owner: str) -> str:
        from app.illustration import scenario
        from app.illustration.scenario_contracts import CreateSession
        return scenario.create_session(owner, CreateSession())["session_id"]

    async def _start_turn(self, owner: str, session_id: str):
        from app.illustration import scenario
        from app.illustration.scenario_contracts import SceneTurn
        return scenario.start_turn(owner, session_id, SceneTurn(
            message="画一个滑块", mode="v1", base_revision=0,
            selected_materials=[]))

    async def test_file_mode_launches_in_process_scenario(self) -> None:
        from app.illustration import orchestrator, scenario
        with mock.patch.object(scenario, "_run", new=_hanging_run):
            session_id = await self._new_session("stu_scene_a")
            job = await self._start_turn("stu_scene_a", session_id)
            await asyncio.sleep(0)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(len(scenario._running), 1)
            self.assertEqual(len(orchestrator._running), 0)

    async def test_temporal_mode_dispatches_scenario_workflow(self) -> None:
        from app.illustration import scenario
        from app.workflows import illustration_common

        captured: list[illustration_common.ScenarioIllustrationIntent] = []

        async def fake_start(intent, client=None) -> None:
            captured.append(intent)

        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch("app.workflows.illustration_scenario.start_scenario_job",
                           fake_start):
            session_id = await self._new_session("stu_scene_b")
            job = await self._start_turn("stu_scene_b", session_id)
            await asyncio.sleep(0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].owner, "stu_scene_b")
        self.assertEqual(captured[0].job_id, job["job_id"])
        self.assertEqual(captured[0].session_id, session_id)
        self.assertEqual(len(scenario._running), 0)  # 不双驱动

    async def test_worker_process_keeps_in_process_scenario(self) -> None:
        from app.illustration import scenario
        from app.workflows import config as wf_config
        wf_config._worker_process = True
        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch.object(scenario, "_run", new=_hanging_run):
            session_id = await self._new_session("stu_scene_c")
            await self._start_turn("stu_scene_c", session_id)
            await asyncio.sleep(0)
            self.assertEqual(len(scenario._running), 1)

    async def test_quiz_dispatch_failure_settles_interrupted(self) -> None:
        from app.illustration import orchestrator, persistence

        async def broken_start(intent, client=None) -> None:
            raise ConnectionError("temporal down")

        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch("app.workflows.illustration_quiz.start_quiz_job",
                           broken_start):
            job = {"job_id": "illjob_d1", "status": "queued",
                   "owner_epoch": persistence.epoch("stu_quiz_a")}
            persistence.write("stu_quiz_a", "jobs", "illjob_d1", job)
            orchestrator._dispatch_quiz_job("stu_quiz_a", job)
            await asyncio.sleep(0)
        settled = persistence.read("stu_quiz_a", "jobs", "illjob_d1")
        self.assertEqual(settled["status"], "failed")
        self.assertEqual(settled["failure"]["code"], "run_interrupted")

    async def test_quiz_dispatch_temporal_mode(self) -> None:
        from app.illustration import orchestrator, persistence
        from app.workflows import illustration_common

        captured: list[illustration_common.QuizIllustrationIntent] = []

        async def fake_start(intent, client=None) -> None:
            captured.append(intent)

        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch("app.workflows.illustration_quiz.start_quiz_job",
                           fake_start):
            job = {"job_id": "illjob_d2", "status": "queued",
                   "owner_epoch": persistence.epoch("stu_quiz_b")}
            persistence.write("stu_quiz_b", "jobs", "illjob_d2", job)
            orchestrator._dispatch_quiz_job("stu_quiz_b", job)
            await asyncio.sleep(0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].owner, "stu_quiz_b")
        # 磁盘记录保持 queued（workflow 负责推进与结算）。
        self.assertEqual(
            persistence.read("stu_quiz_b", "jobs", "illjob_d2")["status"],
            "queued")

    async def test_recover_job_dual_mode(self) -> None:
        from app.illustration import orchestrator, persistence

        def seed(owner: str) -> dict:
            job = {"job_id": "illjob_r_" + owner, "status": "queued",
                   "stage": "created", "owner_epoch": persistence.epoch(owner)}
            persistence.write(owner, "jobs", job["job_id"], job)
            return job

        # file 模式：无本地任务 → 内联标中断（既有行为）。
        job = seed("stu_rec_a")
        current = orchestrator.recover_job("stu_rec_a", job)
        self.assertEqual(current["status"], "failed")

        # temporal 模式：结算责任在 workflow，读路径只返回现状。
        job = seed("stu_rec_b")
        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}):
            current = orchestrator.recover_job("stu_rec_b", job)
        self.assertEqual(current["status"], "queued")

    async def test_recover_session_dual_mode(self) -> None:
        from app.illustration import persistence, scenario

        async def seed_and_orphan(owner: str) -> str:
            """落一个 queued job 后把本地任务掐掉（模拟进程重启残留）。"""
            session_id = await self._new_session(owner)
            with mock.patch.object(scenario, "_run", new=_hanging_run):
                await self._start_turn(owner, session_id)
                await asyncio.sleep(0)
            row = persistence.read(owner, "sessions", session_id)
            task = scenario._running.pop(
                (str(persistence.owner_dir(owner)), row["active_job_id"]))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return session_id

        # file 模式：_recover_session 内联结算 run_interrupted。
        session_a = await seed_and_orphan("stu_sess_a")
        scenario.get_session("stu_sess_a", session_a)
        row = persistence.read("stu_sess_a", "sessions", session_a)
        self.assertIsNone(row["active_job_id"])
        self.assertEqual(
            persistence.read("stu_sess_a", "scenario_jobs",
                             row["turns"][0]["job_id"])["failure"]["code"],
            "run_interrupted")

        # temporal 模式：同一磁盘残留保持现状（workflow settle 兜底），
        # 下一轮询见终态。
        session_b = await seed_and_orphan("stu_sess_b")
        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}):
            scenario.get_session("stu_sess_b", session_b)
        row = persistence.read("stu_sess_b", "sessions", session_b)
        self.assertIsNotNone(row["active_job_id"])
        self.assertIsNone(
            persistence.read("stu_sess_b", "scenario_jobs",
                             row["turns"][0]["job_id"]).get("failure"))

    async def test_delete_session_cancels_durable_workflows(self) -> None:
        from app.illustration import scenario
        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch("app.workflows.illustration_scenario.start_scenario_job",
                           new=mock.AsyncMock(return_value=None)), \
                mock.patch("app.workflows.illustration_common.cancel_owner_workflows") as cancel:
            session_id = await self._new_session("stu_del")
            job = await self._start_turn("stu_del", session_id)
            await asyncio.sleep(0)
            scenario.delete_session("stu_del", session_id)
        cancel.assert_called_once_with("stu_del", scenario_ids=[job["job_id"]])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
