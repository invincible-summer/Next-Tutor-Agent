"""Authenticated v2 job regressions with synthetic material and real PNGs.

Provider responses are deterministic doubles; composition, storage, JWT
authorization, browser measurement and final PNG rendering use production code.
"""
from __future__ import annotations

import asyncio
import copy
import json
import unittest
from unittest.mock import patch

import httpx

from app.agents.assessment import adaptive_test as cat
from app.agents.assessment.manager import register_task_snapshot
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.api.v1 import illustration_jobs as jobs_api
from app.core.config import settings
from app.core.quiz_illustration import normalize_illustration
from app.diagrams.semantics import asset_card
from app.identity.security import create_token
from app.identity.store import create_user
from app.illustration import orchestrator, persistence
from app.illustration.contracts import VisualBriefV2, SceneDraftV2
from app.main import create_app
from tests.support.storage_sandbox import StorageSandboxTestCase


PRIVATE_GOLD = "private_answer_canary_94731"
PRIVATE_REVIEW = "private_reviewer_canary_94731"
PUBLIC_JOB_FIELDS = {
    "status", "job_id", "question_id", "question_revision", "visual_role",
    "artifact_id", "illustration", "failure", "code", "retryable", "progress",
}


def legacy_illustration():
    return normalize_illustration({
        "kind": "svg", "alt": "水平面上的滑块",
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400">'
               '<line x1="40" y1="260" x2="600" y2="260" stroke="black"/>'
               '<rect x="240" y="180" width="160" height="80" '
               'fill="white" stroke="black"/></svg>',
    })


def ready_responses():
    """Only facts quoted from the registered, frozen stem may be extracted."""
    material = {
        "visual_role": "supplemental",
        "entities": [
            {"id": "block", "name": "滑块", "source_ref": "stem", "source_quote": "滑块 block"},
            {"id": "plane", "name": "水平面", "source_ref": "stem", "source_quote": "水平面 plane"},
        ],
        "facts": [],
        "required_relations": [{"id": "support", "type": "supported_by",
            "from_entity": "block", "to_entity": "plane", "source_ref": "stem",
            "source_quote": "滑块 block 放在水平面 plane 上"}],
    }
    card = asset_card("recipe.horizontal_block")
    brief = VisualBriefV2(visual_role="supplemental", purpose="表示滑块与水平面的接触关系",
        needs=[{"need_id": "main", "name": card["title"], "entity_ids": ["block", "plane"]}])
    scene = SceneDraftV2(asset_instances=[{
        "instance_id": "main", "need_id": "main", "asset_id": card["asset_id"],
        "version": card["version"], "entity_map": {"block": "block", "plane": "plane"},
        "anchor_intent": "滑块底部接触水平面，周围留白", "x": 30, "y": 15, "scale": 1,
    }], alt="水平面上的滑块")
    review = {"status": "passed", "issues": [], "verified_facts": []}
    return [{"brief": brief.model_dump(mode="json"), "material": material},
            scene.model_dump(mode="json"), review, review]


class QueueLLM:
    supports_images = True

    def __init__(self, responses, *, gated=False):
        self.responses = copy.deepcopy(responses)
        self.requests = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not gated:
            self.release.set()

    async def complete(self, **kwargs):
        self.started.set()
        await self.release.wait()
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected provider call")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return json.dumps(value, ensure_ascii=False), {"completion_tokens": 16}


class IllustrationJobApiTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for field, value in (
            ("quiz_svg_enabled", True), ("quiz_illustration_pipeline", "v2"),
            ("quiz_illustration_visual_review", "active"),
            ("quiz_illustration_max_calls", 6), ("quiz_illustration_max_repairs", 0),
            ("quiz_illustration_deadline_seconds", 45),
        ):
            setting = patch.object(settings, field, value)
            setting.start()
            self._patches.append(setting)
        self.owner = "usr_illustration_alice"
        self.other = "usr_illustration_bob"
        for owner in (self.owner, self.other):
            create_user(owner + "@test.local", owner, "unused", user_id=owner)
        self.headers = {"Authorization": "Bearer " + create_token(self.owner)}
        self.other_headers = {"Authorization": "Bearer " + create_token(self.other)}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
            base_url="http://test", headers=self.headers)
        self.task = self.register_task()
        self.instance = self.bind(self.task)

    async def asyncTearDown(self):
        roots = {str(persistence.owner_dir(owner)) for owner in (self.owner, self.other)}
        tasks = [task for (root, _), task in list(orchestrator._running.items()) if root in roots]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()

    def register_task(self, question_id="q_illustration", *, owner=None, illustration=None):
        task = S.TaskSnapshot(question_id=question_id, question_revision=1,
            q_type=S.QuestionType.SHORT_ANSWER,
            stem="合成题：滑块 block 放在水平面 plane 上。请说明受到的支持力方向。",
            answer=PRIVATE_GOLD, explanation="私有评分依据",
            rubric=[S.FrozenCriterion(id="c1", description="说明支持力方向", weight=1)],
            illustration=illustration, visual_role="supplemental" if illustration else "none",
            verification=S.TaskVerification(illustration_check="passed" if illustration else "not_required"))
        register_task_snapshot(owner or self.owner, task)
        return task

    def bind(self, task, *, status=cat.STATUS_ACTIVE, request="required"):
        instance = cat.CatInstance(assessment_id="asmt_" + task.question_id,
            status=status, illustration_request=request,
            question_refs=[S.QuestionRef(question_id=task.question_id, question_revision=1)])
        cat.save_instance(self.owner, instance)
        return instance

    def seed_job(self, status="failed", *, shadow=False, task=None, failure=None):
        task = task or self.task
        job = orchestrator._new_job(jobs_api.task_contract(task), "required", shadow=shadow)
        job.update(status=status, owner_epoch=persistence.epoch(self.owner))
        if status == "failed":
            job["failure"] = failure or {"code": "provider_unavailable", "retryable": True}
        persistence.stage(self.owner, job, "failed" if status == "failed" else "created")
        return job

    async def request(self, method, path, **kwargs):
        return await self.client.request(method, "/api/v1" + path, **kwargs)

    async def start(self, *, question_id=None):
        return await self.request("POST", "/quiz/illustration-jobs", json={
            "question_id": question_id or self.task.question_id, "question_revision": 1})

    async def finish(self, job_id):
        task = orchestrator._running.get((str(persistence.owner_dir(self.owner)), job_id))
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=35)
        job = persistence.read(self.owner, "jobs", job_id)
        self.assertIsNotNone(job)
        return job

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], code)

    def assert_public(self, payload):
        self.assertEqual(set(payload), PUBLIC_JOB_FIELDS)
        serialized = json.dumps(payload, ensure_ascii=False)
        for private in (PRIVATE_GOLD, PRIVATE_REVIEW, "authoring_gold", "fact_bindings",
                        "source_quote", "scene_hash", "contract_hash", "run_id", "review_gates"):
            self.assertNotIn(private, serialized)

    async def test_all_job_routes_require_authentication(self):
        job = self.seed_job()
        paths = [("POST", "/quiz/illustration-jobs", {"json": {
            "question_id": self.task.question_id, "question_revision": 1}}),
            ("GET", "/illustration-jobs/" + job["job_id"], {}),
            ("POST", "/illustration-jobs/" + job["job_id"] + "/retry", {}),
            ("GET", "/questions/" + self.task.question_id + "/illustration?question_revision=1", {})]
        for method, path, kwargs in paths:
            with self.subTest(path=path):
                response = await self.request(method, path, headers={"Authorization": ""}, **kwargs)
                self.assertEqual(response.status_code, 401, response.text)

    async def test_owner_isolation_even_when_both_own_the_same_question_identity(self):
        job = self.seed_job()
        self.register_task(owner=self.other)
        for method, path in (("GET", "/illustration-jobs/" + job["job_id"]),
                             ("POST", "/illustration-jobs/" + job["job_id"] + "/retry")):
            self.assert_error(await self.request(method, path, headers=self.other_headers),
                              404, "illustration_job_not_found")
        foreign = self.register_task("q_alice_only")
        self.assert_error(await self.request("POST", "/quiz/illustration-jobs",
            headers=self.other_headers, json={"question_id": foreign.question_id, "question_revision": 1}),
            404, "question_not_found")
        self.assert_error(await self.request("GET", "/questions/" + foreign.question_id +
            "/illustration?question_revision=1", headers=self.other_headers), 404, "question_not_found")
        bob = await self.request("GET", "/questions/" + self.task.question_id +
            "/illustration?question_revision=1", headers=self.other_headers)
        self.assertEqual(bob.json()["status"], "not_required")
        self.assertIsNone(bob.json()["illustration"])
        self.assertIsNone(persistence.read(self.other, "jobs", job["job_id"]))
        self.assertFalse(persistence.owner_dir(self.other).exists())

    async def test_clients_cannot_supply_identity_material_gold_or_svg(self):
        with patch.object(jobs_api, "start_job") as start:
            for field, value in (("student_id", self.other), ("answer", PRIVATE_GOLD),
                    ("material_contract", {}), ("scene", {}), ("svg", "<svg/>"), ("policy", "required")):
                with self.subTest(field=field):
                    response = await self.request("POST", "/quiz/illustration-jobs", json={
                        "question_id": self.task.question_id, "question_revision": 1, field: value})
                    self.assertEqual(response.status_code, 422, response.text)
            for revision in (0, 1_000_001):
                response = await self.request("POST", "/quiz/illustration-jobs", json={
                    "question_id": self.task.question_id, "question_revision": revision})
                self.assertEqual(response.status_code, 422, response.text)
            start.assert_not_called()

    async def test_duplicate_starts_share_one_run_and_publish_real_png_without_changing_question(self):
        before = get_journal(self.owner).state().tasks[self.task.question_id][1].model_dump(mode="json")
        llm = QueueLLM(ready_responses(), gated=True)
        with patch.object(orchestrator, "get_llm", return_value=llm):
            first = await self.start()
            self.assertEqual(first.status_code, 200, first.text)
            self.assert_public(first.json())
            await asyncio.wait_for(llm.started.wait(), timeout=5)
            duplicate = await self.start()
            assessment = await self.request("POST", "/assessment/questions/" + self.task.question_id +
                "/illustration", json={"question_revision": 1})
            for response in (duplicate, assessment):
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["job_id"], first.json()["job_id"])
                self.assert_public(response.json())
            self.assertEqual(len(list((persistence.owner_dir(self.owner) / "jobs").glob("*.json"))), 1)
            llm.release.set()
            job = await self.finish(first.json()["job_id"])
        self.assertEqual(job["status"], "ready", str(job.get("failure")))
        self.assertEqual(len(llm.requests), 4)
        self.assertEqual(llm.requests[2]["messages"][1]["content"][1]["type"], "image_url")
        artifact = persistence.read(self.owner, "artifacts", job["artifact_id"])
        self.assertIn("source", artifact)
        self.assertIn("review", artifact)
        self.assertEqual(artifact["source"]["review_gates"], {"machine": "passed", "visual": "passed", "joint": "passed"})
        png = persistence.owner_dir(self.owner) / "previews" / (job["artifact_id"] + ".png")
        self.assertTrue(png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(before, get_journal(self.owner).state().tasks[self.task.question_id][1].model_dump(mode="json"))
        for path in ("/illustration-jobs/" + job["job_id"],
                     "/questions/" + self.task.question_id + "/illustration?question_revision=1"):
            response = await self.request("GET", path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_public(response.json())
            self.assertEqual(response.json()["illustration"]["schema_version"], 3)

        # Historical material keeps its artifact identity through catalog,
        # account policy and assessment lifecycle changes.
        self.instance.status = cat.STATUS_STOPPED
        cat.save_instance(self.owner, self.instance)
        with patch.object(settings, "quiz_svg_enabled", False), patch.object(
                jobs_api, "resolve_illustration_policy", side_effect=AssertionError("historical read used generation policy")), \
                patch.object(orchestrator, "get_llm", side_effect=AssertionError("historical read called provider")):
            replay = await self.start()
            self.assertEqual(replay.json()["artifact_id"], job["artifact_id"])
            self.assert_public(replay.json())
            frozen = await self.request("GET", "/questions/" + self.task.question_id + "/illustration?question_revision=1")
            self.assertEqual(frozen.json()["artifact_id"], job["artifact_id"])
            self.assert_error(await self.request("POST", "/illustration-jobs/" + job["job_id"] + "/retry"),
                409, "illustration_frozen")
        self.assertEqual(len(list((persistence.owner_dir(self.owner) / "artifacts").glob("*.json"))), 1)

    async def test_failure_projection_drops_private_diagnostics_and_unrecognized_codes(self):
        job = self.seed_job(failure={"code": "visual_review_failed", "retryable": True,
            "failed_stage": "scene_proposed", "facts": [PRIVATE_GOLD], "review": PRIVATE_REVIEW,
            "scene": {"source_quote": PRIVATE_GOLD}})
        response = await self.request("GET", "/illustration-jobs/" + job["job_id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_public(response.json())
        self.assertEqual(response.json()["failure"], {"code": "visual_review_failed", "retryable": True})
        job["failure"]["code"] = PRIVATE_REVIEW
        persistence.write(self.owner, "jobs", job["job_id"], job)
        response = await self.request("GET", "/illustration-jobs/" + job["job_id"])
        self.assert_public(response.json())
        self.assertEqual(response.json()["code"], "provider_unavailable")

    async def test_failed_retry_gets_new_job_and_run_and_preserves_the_failed_run(self):
        failed_llm = QueueLLM([RuntimeError("synthetic provider interruption")])
        with patch.object(orchestrator, "get_llm", return_value=failed_llm):
            first = await self.start()
            failed = await self.finish(first.json()["job_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure"]["code"], "provider_unavailable")
        original = copy.deepcopy(persistence.read(self.owner, "runs", failed["run_id"]))
        self.instance.illustration_request = "auto"
        cat.save_instance(self.owner, self.instance)
        no_visual = QueueLLM([{"visual_role": "none", "purpose": "文字题无需示意图", "needs": []}])
        with patch.object(orchestrator, "get_llm", return_value=no_visual):
            retry = await self.request("POST", "/illustration-jobs/" + failed["job_id"] + "/retry")
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertNotEqual(retry.json()["job_id"], failed["job_id"])
            new_job = await self.finish(retry.json()["job_id"])
        self.assertNotEqual(new_job["run_id"], failed["run_id"])
        self.assertEqual(new_job["status"], "not_required")
        self.assertEqual(persistence.read(self.owner, "runs", failed["run_id"]), original)
        self.assertEqual(persistence.read(self.owner, "jobs", failed["job_id"])["status"], "failed")

    async def test_retry_of_orphaned_running_job_recovers_and_starts_on_the_first_request(self):
        orphan = self.seed_job("running")
        self.instance.illustration_request = "auto"
        cat.save_instance(self.owner, self.instance)
        llm = QueueLLM([{"visual_role": "none", "purpose": "不需要配图", "needs": []}])
        with patch.object(orchestrator, "get_llm", return_value=llm):
            retry = await self.request("POST", "/illustration-jobs/" + orphan["job_id"] + "/retry")
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertNotEqual(retry.json()["job_id"], orphan["job_id"])
            await self.finish(retry.json()["job_id"])
        recovered = persistence.read(self.owner, "jobs", orphan["job_id"])
        self.assertEqual(recovered["failure"]["code"], "run_interrupted")

    async def test_older_failed_run_cannot_overwrite_a_ready_artifact(self):
        old = self.seed_job()
        ready = self.seed_job("ready")
        image = legacy_illustration().model_dump(mode="json")
        artifact_id = "ill_test_frozen"
        persistence.write(self.owner, "artifacts", artifact_id,
            {"artifact_id": artifact_id, "illustration": image}, immutable=True)
        ready.update(stage="frozen", artifact_id=artifact_id)
        persistence.write(self.owner, "jobs", ready["job_id"], ready)
        with patch.object(jobs_api, "start_job") as start:
            self.assert_error(await self.request("POST", "/illustration-jobs/" + old["job_id"] + "/retry"),
                409, "illustration_frozen")
            self.assertEqual((await self.start()).json()["job_id"], ready["job_id"])
            start.assert_not_called()
        self.assertEqual(persistence.read(self.owner, "artifacts", artifact_id)["illustration"], image)

    async def test_frozen_snapshot_reads_ignore_switches_and_do_not_start_jobs(self):
        task = self.register_task("q_historical_snapshot", illustration=legacy_illustration())
        self.bind(task, status=cat.STATUS_STOPPED)
        with patch.object(settings, "quiz_svg_enabled", False), patch.object(jobs_api, "start_job") as start:
            for method, path, kwargs in (
                ("POST", "/quiz/illustration-jobs", {"json": {"question_id": task.question_id, "question_revision": 1}}),
                ("GET", "/questions/" + task.question_id + "/illustration?question_revision=1", {}),
                ("POST", "/assessment/questions/" + task.question_id + "/illustration", {"json": {"question_revision": 1}}),
            ):
                response = await self.request(method, path, **kwargs)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "ready")
                self.assertEqual(response.json()["illustration"], task.illustration.model_dump(mode="json"))
            start.assert_not_called()
        self.assertFalse(persistence.owner_dir(self.owner).exists())

    async def test_only_current_active_assessment_question_can_start_or_retry(self):
        older = self.register_task("q_old_cat")
        self.instance.question_refs.insert(0, S.QuestionRef(question_id=older.question_id, question_revision=1))
        cat.save_instance(self.owner, self.instance)
        historical = self.seed_job(task=older)
        stopped = self.register_task("q_stopped_cat")
        self.bind(stopped, status=cat.STATUS_STOPPED)
        unbound = self.register_task("q_unbound")
        with patch.object(jobs_api, "start_job") as start:
            for task in (older, stopped, unbound):
                if task is older:
                    response = await self.request("POST", "/illustration-jobs/" + historical["job_id"] + "/retry")
                else:
                    response = await self.start(question_id=task.question_id)
                self.assert_error(response, 404, "assessment_question_not_current")
            start.assert_not_called()

    async def test_disabled_switch_blocks_new_generation_and_failed_retry(self):
        failed = self.seed_job()
        unstarted = self.register_task("q_disabled_new")
        self.bind(unstarted)
        with patch.object(settings, "quiz_svg_enabled", False), patch.object(jobs_api, "start_job") as start:
            self.assert_error(await self.start(question_id=unstarted.question_id), 409, "illustration_disabled")
            self.assert_error(await self.request("POST", "/illustration-jobs/" + failed["job_id"] + "/retry"),
                409, "illustration_disabled")
            start.assert_not_called()

    async def test_shadow_jobs_never_cross_public_routes(self):
        shadow = self.seed_job("ready", shadow=True)
        for method, path in (("GET", "/illustration-jobs/" + shadow["job_id"]),
                             ("POST", "/illustration-jobs/" + shadow["job_id"] + "/retry")):
            self.assert_error(await self.request(method, path), 404, "illustration_job_not_found")
        historical = await self.request("GET", "/questions/" + self.task.question_id + "/illustration?question_revision=1")
        self.assertEqual(historical.json()["status"], "not_required")
        self.instance.illustration_request = "auto"
        cat.save_instance(self.owner, self.instance)
        llm = QueueLLM([{"visual_role": "none", "purpose": "文字题", "needs": []}])
        with patch.object(orchestrator, "get_llm", return_value=llm):
            live = await self.start()
            self.assertEqual(live.status_code, 200, live.text)
            self.assertNotEqual(live.json()["job_id"], shadow["job_id"])
            await self.finish(live.json()["job_id"])

    async def test_purge_cancels_an_inflight_job_without_recreating_owner_storage(self):
        llm = QueueLLM(ready_responses(), gated=True)
        with patch.object(orchestrator, "get_llm", return_value=llm):
            response = await self.start()
            await asyncio.wait_for(llm.started.wait(), timeout=5)
            task = orchestrator._running[(str(persistence.owner_dir(self.owner)), response.json()["job_id"])]
            persistence.purge(self.owner)
            llm.release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(persistence.owner_dir(self.owner).exists())
        self.assert_error(await self.request("GET", "/illustration-jobs/" + response.json()["job_id"]),
            404, "illustration_job_not_found")
        self.assertFalse(persistence.owner_dir(self.owner).exists())

    async def test_purge_between_job_read_and_recovery_returns_404_and_cannot_resurrect(self):
        recover = jobs_api.recover_job
        for endpoint in ("create", "read", "retry", "frozen"):
            job = self.seed_job("running")
            def deleting_recover(owner, stale_job):
                persistence.purge(owner)
                return recover(owner, stale_job)
            with self.subTest(endpoint=endpoint), patch.object(jobs_api, "recover_job", side_effect=deleting_recover):
                if endpoint == "create":
                    response = await self.start()
                elif endpoint == "frozen":
                    response = await self.request("GET", "/questions/" + self.task.question_id + "/illustration?question_revision=1")
                else:
                    response = await self.request("POST" if endpoint == "retry" else "GET",
                        "/illustration-jobs/" + job["job_id"] + ("/retry" if endpoint == "retry" else ""))
                self.assert_error(response, 404, "illustration_job_not_found")
                self.assertFalse(persistence.owner_dir(self.owner).exists())


if __name__ == "__main__":
    unittest.main()
