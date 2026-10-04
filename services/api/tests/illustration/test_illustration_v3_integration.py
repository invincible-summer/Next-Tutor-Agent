"""V3 authenticated jobs and multi-question publication use real storage/PNG."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from app.agents.assessment import adaptive_test as cat
from app.agents.assessment.manager import register_task_snapshots, task_snapshot_from_quiz_dict
from app.core.config import settings
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.core.quiz_verify import generate_verified_questions
from app.illustration import orchestrator, persistence
from app.prompts.registry import get
from tests.illustration import test_illustration_jobs as job_fixtures
PRIVATE_GOLD = job_fixtures.PRIVATE_GOLD
QueueLLM = job_fixtures.QueueLLM
from tests.support.storage_sandbox import StorageSandboxTestCase


SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400">'
       '<rect x="230" y="160" width="140" height="80" fill="white" stroke="black"/>'
       '<path d="M40 240 H600" fill="none" stroke="black"/></svg>')
DECLARATION = {"visual_role": "supplemental", "description": "表示物体与水平面的接触", "needs": []}
DRAFT = {"svg": SVG, "alt": "水平面上的物体", "used_materials": []}
REVIEW = {"status": "passed", "issues": []}


class V3IntegrationTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    # Reuse the real JWT/ASGI fixture, not its V2-specific tests.
    asyncSetUp = job_fixtures.IllustrationJobApiTest.asyncSetUp
    asyncTearDown = job_fixtures.IllustrationJobApiTest.asyncTearDown
    register_task = job_fixtures.IllustrationJobApiTest.register_task
    request = job_fixtures.IllustrationJobApiTest.request
    start = job_fixtures.IllustrationJobApiTest.start
    finish = job_fixtures.IllustrationJobApiTest.finish
    assert_public = job_fixtures.IllustrationJobApiTest.assert_public

    def bind(self, task, **kwargs):
        instance = job_fixtures.IllustrationJobApiTest.bind(self, task, **kwargs)
        instance.illustration_mode = "v3"
        cat.save_instance(self.owner, instance)
        return instance

    async def test_cat_and_job_routes_share_v3_frozen_artifact_without_gold_exposure(self):
        client = QueueLLM([DECLARATION, DRAFT, REVIEW])
        with patch.object(settings, "llm_supports_images", True), patch.object(orchestrator, "get_llm", return_value=client):
            response = await self.request("POST", f"/assessment/questions/{self.task.question_id}/illustration",
                                          json={"question_revision": 1})
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()["job_id"]
            same = await self.start()
            self.assertEqual(same.json()["job_id"], job_id)
            job = await self.finish(job_id)
        self.assertEqual(job["pipeline_mode"], "v3")
        self.assertEqual(job["status"], "ready", job.get("failure"))
        self.assertEqual(len(client.requests), 3)
        for request in client.requests[:2]:
            self.assertNotIn(PRIVATE_GOLD, json.dumps(request, ensure_ascii=False))
        artifact = persistence.read(self.owner, "artifacts", job["artifact_id"])
        self.assertEqual(artifact["source"]["pipeline_mode"], "v3")
        self.assertEqual(artifact["source"]["review_gates"], {"machine": "passed", "combined": "passed"})
        frozen = await self.request("GET", f"/questions/{self.task.question_id}/illustration?question_revision=1")
        self.assert_public(frozen.json())
        self.assertEqual(frozen.json()["illustration"]["schema_version"], 3)
        self.instance.status = cat.STATUS_STOPPED
        cat.save_instance(self.owner, self.instance)
        with patch.object(settings, "quiz_svg_enabled", False):
            cached = await self.request("POST", f"/assessment/questions/{self.task.question_id}/illustration",
                                        json={"question_revision": 1})
        self.assertEqual(cached.json()["artifact_id"], artifact["artifact_id"])

    async def test_failed_v3_retry_keeps_version_and_failed_run(self):
        failure = QueueLLM([RuntimeError("provider synthetic failure")])
        with patch.object(settings, "llm_supports_images", True), patch.object(orchestrator, "get_llm", return_value=failure):
            response = await self.start()
            failed = await self.finish(response.json()["job_id"])
        self.assertEqual(failed["status"], "failed")
        client = QueueLLM([DECLARATION, DRAFT, REVIEW])
        with patch.object(settings, "llm_supports_images", True), patch.object(orchestrator, "get_llm", return_value=client):
            retry = await self.request("POST", f"/illustration-jobs/{failed['job_id']}/retry")
            ready = await self.finish(retry.json()["job_id"])
        self.assertEqual(ready["status"], "ready", ready.get("failure"))
        self.assertEqual(ready["pipeline_mode"], "v3")
        self.assertNotEqual(ready["run_id"], failed["run_id"])
        self.assertEqual(persistence.read(self.owner, "jobs", failed["job_id"])["status"], "failed")

    async def test_invalid_authoring_contract_can_reauthor_once_in_the_same_mode(self):
        class ReauthorLLM:
            supports_images = True
            authoring_calls = 0
            async def complete(self, **kwargs):
                system = kwargs["messages"][0]["content"]
                if get("quiz_illustration_v3_authoring").text in system:
                    self.authoring_calls += 1
                    spec = {"visual_role": "supplemental", "description": "物体在水平面上"}
                    if self.authoring_calls == 1:
                        spec.update(visual_role="essential", drawing_inputs=[
                            {"id": "invalid id", "description": "绘图输入", "value": 1}])
                    result = {"questions": [{"type": "short_answer", "stem": "物体放在水平面上，请说明支持力方向。",
                        "answer": "竖直向上。", "explanation": "支持力垂直于接触面，因此水平面提供竖直向上的支持力。", "visual_spec": spec}]}
                elif get("quiz_illustration_v3_requirements").text in system:
                    result = DECLARATION
                elif get("quiz_illustration_v3_composer").text in system:
                    result = DRAFT
                else:
                    result = REVIEW
                return json.dumps(result, ensure_ascii=False), {}
        budget = GenerationBudget(max_calls=2, max_repairs=1)
        provider = ReauthorLLM()
        rows, meta = await generate_verified_questions(BudgetedLLM(provider, budget), student_id=self.owner,
            illustration_mode="v3", make_prompt=lambda: "生成一道合成支持力题目",
            parse=lambda raw: json.loads(raw)["questions"], topic="支持力", grade="初中",
            temperature=.1, max_tokens=4000, illustration_policy="required", verify_mode="basic")
        self.assertEqual(len(rows), 1, meta)
        self.assertEqual(budget.calls, 2)
        self.assertEqual(meta["generation_calls"], 5)
        self.assertEqual(rows[0]["diagram_source"]["pipeline_mode"], "v3")
        self.assertEqual(meta["illustration_failures"][0]["code"], "invalid_contract")

    async def test_five_ordinary_questions_do_not_spend_text_budget_on_images(self):
        questions = [{"type": "short_answer", "stem": f"物体{i}放在水平面上，描述其受到的支持力方向。",
            "answer": "竖直向上。", "explanation": "水平面给物体的支持力沿竖直方向指向上方。",
            "visual_spec": {"visual_role": "supplemental", "description": "物体与水平面"},
            "rubric_criteria": [{"id": "c1", "description": "说明支持力方向", "weight": 1}]}
            for i in range(1, 6)]

        class RoutingLLM:
            supports_images = True
            def __init__(self):
                self.calls = []
            async def complete(self, **kwargs):
                system = kwargs["messages"][0]["content"]
                self.calls.append(system)
                if get("quiz_illustration_v3_authoring").text in system:
                    result = {"questions": questions}
                elif get("quiz_illustration_v3_requirements").text in system:
                    result = DECLARATION
                elif get("quiz_illustration_v3_composer").text in system:
                    result = DRAFT
                else:
                    content = kwargs["messages"][1]["content"]
                    audit = json.loads(content[0]["text"])
                    self.assert_grounding = "合成教材依据：支持力垂直于水平面向上。" in audit["authoring_gold"]["grounding_context"]
                    result = REVIEW
                return json.dumps(result, ensure_ascii=False), {"completion_tokens": 10}

        provider = RoutingLLM()
        budget = GenerationBudget(max_calls=1)
        with patch.object(settings, "llm_supports_images", True), patch.object(settings, "quiz_illustration_max_repairs", 2):
            rows, meta = await generate_verified_questions(BudgetedLLM(provider, budget),
                student_id=self.owner, illustration_mode="v3", make_prompt=lambda: "出五道合成题",
                parse=lambda raw: json.loads(raw)["questions"], topic="支持力", grade="初中",
                grounding_context="合成教材依据：支持力垂直于水平面向上。",
                temperature=.1, max_tokens=4000, illustration_policy="required", verify_mode="basic")
        self.assertEqual(len(rows), 5, meta)
        self.assertEqual(budget.calls, 1)
        self.assertEqual(len(provider.calls), 16)
        self.assertEqual(meta["generation_calls"], 16)
        self.assertTrue(provider.assert_grounding)
        # Chat cards assign their canonical task identity after generation.
        tasks = [task_snapshot_from_quiz_dict({**row, "question_id": f"q_chat_canonical_{i}"})
                 for i, row in enumerate(rows)]
        register_task_snapshots(self.owner, tasks)
        for task in tasks:
            self.assertEqual(task.material_contract.question_ref, task.question_id)
            artifact = persistence.read(self.owner, "artifacts", task.illustration_artifact_id)
            self.assertEqual(artifact["pipeline_mode"], "v3")
            self.assertEqual(artifact["question_id"], task.question_id)
        changed = tasks[0].model_dump(mode="json")
        changed["diagram_source"]["content_hash"] = "sha256:unreviewed"
        with self.assertRaisesRegex(ValueError, "illustration_source_mismatch"):
            type(tasks[0]).model_validate(changed)


if __name__ == "__main__":
    unittest.main()
