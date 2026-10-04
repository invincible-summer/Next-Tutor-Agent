"""Synthetic scene requests exercise shared engines, PNG review and ownership."""
from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.core.quiz_illustration import normalize_illustration
from app.diagrams import materials
from app.diagrams.materials import MaterialInput, owner_context
from app.diagrams.semantics import asset_card
from app.identity.security import create_token
from app.identity.store import create_user
from app.illustration import persistence, scenario, scenario_engine, v3_retrieval
from app.illustration.contracts import IllustrationError
from app.illustration.references import ReferenceError, selected_cards, selected_bundle_v2, selected_bundle_v3
from app.illustration.scenario_contracts import MaterialSelection, SceneText, SceneVisualContract
from app.main import create_app
from tests.illustration.test_illustration_jobs import ready_responses
from tests.illustration.test_illustration_v3 import SVG, QueueLLM, draft, payload
from tests.illustration.test_quiz_illustration_enrichment import REQUIREMENTS, SCENE
from tests.support.storage_sandbox import StorageSandboxTestCase

PASSED = {"status": "passed", "issues": []}
REQUEST = "合成绘图：滑块 block 放在水平面 plane 上。"
V3_REQUIREMENTS = {"description": "小车与轨道的示意图", "needs": [
    {"need_id": "car", "name": "小车", "synonyms": ["cart"], "purpose": "参考车体"}]}


def result():
    image = normalize_illustration({"kind": "svg", "svg": SVG, "alt": "合成情景图"})
    return {"illustration": image.model_dump(mode="json"), "source": {"scene": {}},
        "review": PASSED, "png": b"\x89PNG\r\n\x1a\nsynthetic-test", "metrics": {"generation_calls": 3}}


class ScenarioEngineTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        for name, value in (("quiz_illustration_max_calls", 10), ("quiz_illustration_max_repairs", 2),
                ("quiz_illustration_deadline_seconds", 120), ("quiz_svg_enabled", True)):
            setting = patch.object(settings, name, value); setting.start(); self._patches.append(setting)

    async def generate(self, llm, mode, *, cards=None, previous=None):
        return await scenario_engine.generate_scene(llm, owner="scene_alice", mode=mode,
            messages=[REQUEST], latest_request=REQUEST, cards=cards, previous=previous)

    async def test_v1_shared_component_compiler_real_png(self):
        llm = QueueLLM(REQUIREMENTS, SCENE, PASSED)
        out = await self.generate(llm, "v1")
        self.assertTrue(out["png"].startswith(b"\x89PNG"))
        self.assertEqual(llm.requests[-1]["messages"][1]["content"][1]["type"], "image_url")
        self.assertNotIn("question_ref", str(out))

    async def test_v2_shared_scientific_compile_selected_candidates(self):
        responses = ready_responses(); first = copy.deepcopy(responses[0]); first["material"].pop("visual_role")
        llm = QueueLLM(first, responses[1], PASSED)
        out = await self.generate(llm, "v2", cards=[asset_card("recipe.horizontal_block")])
        self.assertTrue(out["png"].startswith(b"\x89PNG"))
        proposed = payload(llm.requests[1])
        self.assertEqual([row["asset_id"] for row in proposed["candidate_bundle"]["assets"]], ["recipe.horizontal_block"])
        self.assertEqual(out["metrics"]["generation_calls"], 3)

    async def test_v2_automatic_material_request_retrieves_once(self):
        responses = ready_responses(); first = copy.deepcopy(responses[0]); first["material"].pop("visual_role")
        llm = QueueLLM(first, {"action": "request_materials", "needs": first["brief"]["needs"]}, responses[1], PASSED)
        with patch.object(scenario_engine.retrieval, "retrieve", wraps=scenario_engine.retrieval.retrieve) as retrieve:
            out = await self.generate(llm, "v2")
        self.assertEqual(retrieve.call_count, 2)
        self.assertEqual(out["metrics"]["generation_calls"], 4)

    async def test_v3_selected_versions_previous_artifact_real_png(self):
        cards = [asset_card("mechanics.cart")]
        llm = QueueLLM(V3_REQUIREMENTS, draft(used_materials=[{"asset_id": cards[0]["asset_id"], "version": cards[0]["version"]}]), PASSED)
        previous = {"illustration": {"svg": SVG}, "source": {"draft": draft()}}
        out = await self.generate(llm, "v3", cards=cards, previous=previous)
        self.assertTrue(out["png"].startswith(b"\x89PNG"))
        self.assertEqual(out["source"]["asset_versions"], {cards[0]["asset_id"]: cards[0]["version"]})
        self.assertEqual(payload(llm.requests[1])["revision_context"]["previous_artifact"], previous)
        self.assertFalse(payload(llm.requests[1])["additional_retrieval_allowed"])
        self.assertEqual(len(out["source"]["rendered_bounds"]), 4)

    async def test_v3_automatic_additional_retrieval_and_selected_boundary(self):
        extra = {"action": "request_materials", "needs": [{"need_id": "track", "name": "水平轨道"}]}
        llm = QueueLLM(V3_REQUIREMENTS, extra, draft(), PASSED)
        with patch.object(v3_retrieval, "retrieve", wraps=v3_retrieval.retrieve) as retrieve:
            await self.generate(llm, "v3")
        self.assertEqual(retrieve.call_count, 2)
        selected_llm = QueueLLM(V3_REQUIREMENTS, extra, draft(), PASSED)
        with patch.object(v3_retrieval, "retrieve", wraps=v3_retrieval.retrieve) as retrieve:
            await self.generate(selected_llm, "v3", cards=[asset_card("mechanics.cart")])
        self.assertEqual(retrieve.call_count, 0)
        self.assertFalse(payload(selected_llm.requests[2])["additional_retrieval_allowed"])

    async def test_unrepairable_png_audit_cannot_publish(self):
        llm = QueueLLM(V3_REQUIREMENTS, draft(), {"status": "failed", "issues": [{"code": "scientific_mismatch", "repairable": False}]})
        with self.assertRaises(IllustrationError) as raised:
            await self.generate(llm, "v3")
        self.assertTrue(raised.exception.details["unrepairable"])
        self.assertEqual(len(llm.requests), 3)

    def test_drawing_inputs_require_literal_user_source(self):
        with self.assertRaises(ValidationError):
            SceneVisualContract(source=SceneText(content="质量为2kg。"), description="物体", drawing_inputs=[
                {"id": "mass", "description": "质量", "value": 3, "source_quote": "质量为2kg"}])
        value = SceneVisualContract(source=SceneText(content="质量为2kg。"), description="物体", drawing_inputs=[
            {"id": "mass", "description": "质量", "value": 2, "source_quote": "质量为2kg"}])
        self.assertEqual(value.drawing_inputs[0].value, 2)

    def test_v2_selected_material_capability_filter(self):
        from app.illustration.contracts import VisualBriefV2
        brief = VisualBriefV2(visual_role="supplemental", purpose="表示仪器刻度", needs=[{"need_id": "scale", "name": "仪器", "capabilities": ["reading_binding"]}])
        with self.assertRaises(IllustrationError) as raised:
            selected_bundle_v2(brief, [asset_card("mechanics.cart")])
        self.assertEqual(raised.exception.code, "candidate_capability_mismatch")

    def test_private_public_revision_visibility_and_source_budget(self):
        private = materials.save("scene_alice", MaterialInput(title="合成私有", svg=SVG, enabled=True))
        public = materials.save("scene_alice", MaterialInput(title="合成公共", svg=SVG, enabled=True, scope="public"), admin=True)
        selected = MaterialSelection(asset_id="material."+private["id"], version=1)
        self.assertEqual(selected_cards("scene_alice", [selected])[0]["version"], 1)
        with self.assertRaises(ReferenceError): selected_cards("scene_bob", [selected])
        self.assertEqual(selected_cards("scene_bob", [MaterialSelection(asset_id="material."+public["id"], version=1)])[0]["source_scope"], "public")
        with self.assertRaises(ReferenceError): selected_cards("scene_alice", [selected.model_copy(update={"version": 99})])
        materials.save("scene_alice", MaterialInput(title="合成新版", svg=SVG, enabled=True, base_revision=1), asset_id=private["id"])
        with owner_context("scene_alice"):
            old = selected_cards("scene_alice", [selected]); self.assertEqual(selected_bundle_v3([], old).assets[0].version, 1)
            with patch("app.illustration.references.MAX_SOURCE_BYTES", 10):
                with self.assertRaises(ReferenceError): selected_bundle_v3([], old)


class ScenarioApiTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        setting = patch.object(settings, "quiz_svg_enabled", True); setting.start(); self._patches.append(setting)
        provider = patch.object(scenario, "get_llm", return_value=QueueLLM()); provider.start(); self._patches.append(provider)
        self.owner, self.other = "usr_scenario_alice", "usr_scenario_bob"
        for owner in (self.owner, self.other): create_user(owner+"@test.local", owner, "unused", user_id=owner)
        self.headers = {"Authorization": "Bearer "+create_token(self.owner)}
        self.other_headers = {"Authorization": "Bearer "+create_token(self.other)}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test", headers=self.headers)
        await self.new_session()

    async def new_session(self):
        created = await self.client.post("/api/v1/tools/illustration/sessions", json={}); self.assertEqual(created.status_code, 200)
        self.session_id = created.json()["session_id"]; self.path = "/api/v1/tools/illustration/sessions/"+self.session_id

    async def asyncTearDown(self):
        tasks = [task for (root, _), task in list(scenario._running.items()) if root == str(persistence.owner_dir(self.owner))]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()

    async def finish(self, job_id):
        running = scenario._running.get((str(persistence.owner_dir(self.owner)), job_id))
        if running: await running
        response = await self.client.get("/api/v1/tools/illustration/jobs/"+job_id); self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_idempotency_busy_cas_and_owner_boundary(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def gated(*args, **kwargs): started.set(); await release.wait(); return result()
        with patch.object(scenario, "generate_scene", side_effect=gated):
            body = {"message": "绘制合成情景", "mode": "v3", "request_id": "request_1"}
            first = await self.client.post(self.path+"/turns", json=body); await started.wait(); job_id = first.json()["job_id"]
            duplicate = await self.client.post(self.path+"/turns", json=body); self.assertEqual(duplicate.json()["job_id"], job_id)
            busy = await self.client.post(self.path+"/turns", json={"message": "并发请求"}); self.assertEqual(busy.json()["detail"]["code"], "illustration_session_busy")
            self.assertEqual((await self.client.get(self.path, headers=self.other_headers)).status_code, 404)
            self.assertEqual((await self.client.get("/api/v1/tools/illustration/jobs/"+job_id, headers=self.other_headers)).status_code, 404)
            release.set(); self.assertEqual((await self.finish(job_id))["revision"], 1)
            self.assertEqual((await self.client.post(self.path+"/turns", json=body)).json()["job_id"], job_id)
            conflict = await self.client.post(self.path+"/turns", json={"message": "过期版本"}); self.assertEqual(conflict.json()["detail"]["code"], "illustration_revision_conflict")
        session = (await self.client.get(self.path)).json(); self.assertEqual(session["turns"][0]["request_id"], "request_1")
        self.assertNotIn("source", session["revisions"][0]); self.assertFalse(list((self.root/"students").glob("*.learning_evidence.jsonl")))

    async def test_multiturn_and_historical_branch(self):
        calls = []
        async def generate(*args, **kwargs): calls.append(kwargs); return result()
        with patch.object(scenario, "generate_scene", side_effect=generate):
            for current, message, source in ((0, "原始需求", None), (1, "追加需求", None), (2, "修改第一版", 1)):
                body = {"message": message, "mode": "v3", "base_revision": current}
                if source is not None: body["source_revision"] = source
                response = await self.client.post(self.path+"/turns", json=body); self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((await self.finish(response.json()["job_id"]))["revision"], current+1)
        self.assertEqual(calls[1]["messages"], ["原始需求", "追加需求"]); self.assertEqual(calls[2]["messages"], ["原始需求", "修改第一版"])
        self.assertIsNotNone(calls[2]["previous"]); self.assertEqual(len((await self.client.get(self.path)).json()["revisions"]), 3)

    async def test_failed_retry_same_turn_and_restart_recovery(self):
        with patch.object(scenario, "generate_scene", side_effect=IllustrationError("provider_unavailable")):
            job_id = (await self.client.post(self.path+"/turns", json={"message": "合成失败"})).json()["job_id"]
            self.assertEqual((await self.finish(job_id))["status"], "failed")
        with patch.object(scenario, "generate_scene", return_value=result()):
            retry = await self.client.post("/api/v1/tools/illustration/jobs/"+job_id+"/retry"); self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual((await self.finish(job_id))["status"], "ready")
        self.assertEqual(len((await self.client.get(self.path)).json()["turns"]), 1)
        session = persistence.read(self.owner, "sessions", self.session_id); job = persistence.read(self.owner, "scenario_jobs", job_id)
        job.update(status="running", stage="composing", base_revision=1); session["active_job_id"] = job_id
        persistence.write(self.owner, "scenario_jobs", job_id, job); persistence.write(self.owner, "sessions", self.session_id, session)
        restarted = await self.client.get("/api/v1/tools/illustration/jobs/"+job_id); self.assertEqual(restarted.json()["failure"]["code"], "run_interrupted")

    async def test_delete_and_owner_epoch_fence_late_results(self):
        for purge in (False, True):
            if purge: await self.new_session()
            started, release = asyncio.Event(), asyncio.Event()
            async def ignores_cancel(*args, **kwargs):
                started.set()
                try: await release.wait()
                except asyncio.CancelledError: pass
                return result()
            with patch.object(scenario, "generate_scene", side_effect=ignores_cancel):
                job_id = (await self.client.post(self.path+"/turns", json={"message": "在途请求"})).json()["job_id"]
                task = scenario._running[(str(persistence.owner_dir(self.owner)), job_id)]; await started.wait()
                if purge: await asyncio.to_thread(persistence.purge, self.owner)
                else: self.assertEqual((await self.client.delete(self.path)).status_code, 200)
                release.set(); await task
            self.assertEqual((await self.client.get(self.path)).status_code, 404)
            self.assertFalse(list((persistence.owner_dir(self.owner)/"scenario_revisions").glob("*.json")))
            if purge: self.assertFalse(persistence.owner_dir(self.owner).exists())

    async def test_context_turn_limit(self):
        session = persistence.read(self.owner, "sessions", self.session_id); session["turns"] = [{"status": "failed"}]*60
        persistence.write(self.owner, "sessions", self.session_id, session)
        response = await self.client.post(self.path+"/turns", json={"message": "超出轮数"}); self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "illustration_context_limit")

    async def test_http_real_png_job(self):
        llm = QueueLLM(V3_REQUIREMENTS, draft(), PASSED)
        with patch.object(scenario, "get_llm", return_value=llm):
            response = await self.client.post(self.path+"/turns", json={"message": REQUEST, "mode": "v3"}); self.assertEqual(response.status_code, 200, response.text)
            job = await self.finish(response.json()["job_id"])
        self.assertEqual(job["status"], "ready", job); self.assertEqual(job["mode"], "v3")
        self.assertTrue((persistence.owner_dir(self.owner)/"previews"/(job["artifact_id"]+".png")).read_bytes().startswith(b"\x89PNG"))
