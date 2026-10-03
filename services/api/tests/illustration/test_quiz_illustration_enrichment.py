from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.agents.assessment import adaptive_test as cat
from app.agents.student_model.evaluation import schema as S
from app.api.v1 import assessment_illustration as illustration_api
from app.api.v1.assessment_illustration import (
    IllustrationRequest,
    _assessment_instance,
    _bound_instance,
    enrich_question_illustration,
)
from app.core import quiz_illustration_enrichment as enrichment
from app.core.quiz_illustration import (
    IllustrationValidationError,
    normalize_illustration,
    normalize_svg,
)
from tests.support.storage_sandbox import StorageSandboxTestCase


SVG_WITH_MARKER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-label="velocity diagram">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" viewBox="0 0 10 10">
    <path d="M 0 0 L 10 5 L 0 10 Z" style="fill:#000;stroke:#000;stroke-width:1"/>
  </marker>
</defs>
<line x1="80" y1="200" x2="520" y2="200" marker-end="url(#arrow)" style="stroke:#000;stroke-width:2"/>
<text x="300" y="180" text-anchor="middle" style="fill:#000">v</text>
</svg>"""

REQUIREMENTS = {"requirements": [{"question_slot": "q1", "illustration_needed": True,
    "scene_brief": "水平面滑块", "needs": [{"key": "scene", "name": "水平面滑块"}]}]}
SCENE = {"nodes": [{"id": "scene", "asset_id": "template.horizontal_block", "x": 16,
                   "y": 16, "scale": .9}], "alt": "水平面上的物体"}


def _requirements_response() -> str:
    return json.dumps(REQUIREMENTS, ensure_ascii=False)


def _scene_response(scene: dict | None = SCENE) -> str:
    return json.dumps({"questions": [{"diagram_scene": scene}]}, ensure_ascii=False)


def _task(question_id: str = "q_enrich_1") -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=question_id,
        question_revision=1,
        q_type=S.QuestionType.SHORT_ANSWER,
        stem="小车沿水平直线向右做匀速运动，速度大小为 v。说明速度方向。",
        answer="速度方向水平向右。",
        explanation="匀速直线运动的速度方向与运动方向一致。",
        rubric=[S.FrozenCriterion(id="c1", description="指出速度方向", weight=1.0)],
    )


def _illustration():
    return normalize_illustration({
        "kind": "svg",
        "alt": "小车速度箭头水平向右",
        "caption": "速度方向示意",
        "svg": SVG_WITH_MARKER,
    })


class FakeLLM:
    supports_images = True

    def __init__(self, responses: list[str | Exception], *, delay: float = 0.0):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []
        self.delay = delay

    async def complete(self, **_kwargs):
        self.calls += 1
        self.requests.append(_kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, {"completion_tokens": 24}


class TestSvgCompatibility(unittest.TestCase):
    def test_defs_marker_inline_style_and_root_metadata_are_normalized(self):
        normalized = normalize_svg(SVG_WITH_MARKER, alt="向右速度箭头", caption="速度示意图")
        self.assertIn("<defs>", normalized.svg)
        self.assertIn("marker-end=\"url(#arrow)\"", normalized.svg)
        self.assertNotIn(" style=", normalized.svg)
        self.assertNotIn("aria-label", normalized.svg)
        self.assertNotIn("role=", normalized.svg)
        illustration = normalize_illustration({
            "kind": "svg",
            "alt": "向右速度箭头",
            "caption": "速度示意图",
            "svg": SVG_WITH_MARKER,
        })
        self.assertEqual(illustration.sanitizer_version, 2)
        self.assertTrue(illustration.content_hash.startswith("sha256:"))
        round_trip = normalize_illustration(illustration.model_dump(mode="json"))
        self.assertEqual(round_trip.content_hash, illustration.content_hash)
        self.assertEqual(round_trip.svg, illustration.svg)

    def test_external_marker_reference_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace("url(#arrow)", "url(https://example.com/a.svg#arrow)")
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_unknown_local_marker_reference_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace("url(#arrow)", "url(#missing)")
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_style_element_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace("<defs>", "<defs><style>line{stroke:red}</style>")
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_arbitrary_css_property_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace(
            "style=\"stroke:#000;stroke-width:2\"",
            "style=\"stroke:#000;filter:url(#arrow)\"",
        )
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_event_handler_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace(
            '<line x1="80"', '<line onload="alert(1)" x1="80"')
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_foreign_object_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace(
            "</svg>", '<foreignObject x="0" y="0" width="20" height="20" /></svg>')
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)


class TestAssessmentIllustrationBinding(unittest.TestCase):
    def test_only_latest_question_in_active_instance_is_enrichable(self):
        first = S.QuestionRef(question_id="q_old", question_revision=1)
        current = S.QuestionRef(question_id="q_current", question_revision=2)
        instance = cat.CatInstance(
            assessment_id="asmt_1",
            illustration_request="required",
            question_refs=[first, current],
        )
        state = SimpleNamespace(assessments={"asmt_1": instance.to_detail()})
        self.assertIsNone(_bound_instance(state, "q_old", 1))
        bound = _bound_instance(state, "q_current", 2)
        self.assertIsNotNone(bound)
        self.assertEqual(bound.assessment_id, "asmt_1")
        self.assertEqual(bound.illustration_request, "required")

    def test_stopped_instance_keeps_membership_but_cannot_generate(self):
        current = S.QuestionRef(question_id="q_stopped", question_revision=1)
        instance = cat.CatInstance(
            assessment_id="asmt_stopped",
            status=cat.STATUS_STOPPED,
            question_refs=[current],
        )
        state = SimpleNamespace(assessments={instance.assessment_id: instance.to_detail()})
        self.assertIsNotNone(_assessment_instance(state, "q_stopped", 1))
        self.assertIsNone(_bound_instance(state, "q_stopped", 1))

    def test_private_store_rejects_path_aliases(self):
        for bad in ("../usr_other", "nested/usr_other", r"nested\usr_other", ".hidden"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                enrichment._safe_student(bad)
        self.assertEqual(enrichment._safe_student("usr_valid_123"), "usr_valid_123")


class TestAssessmentIllustrationApi(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_off_policy_is_not_required_for_both_implementations(self):
        from app.illustration import orchestrator
        for mode in ("v1", "v2"):
            for request in ("none", "auto"):
                with self.subTest(mode=mode, request=request):
                    task = _task("q_off_policy")
                    instance = cat.CatInstance(
                        assessment_id="asmt_off_policy",
                        illustration_mode=mode,
                        illustration_request=request,
                        question_refs=[S.QuestionRef(question_id=task.question_id, question_revision=1)],
                    )
                    state = SimpleNamespace(
                        tasks={task.question_id: {1: task}},
                        assessments={instance.assessment_id: instance.to_detail()},
                    )
                    with patch.object(illustration_api, "get_journal", return_value=SimpleNamespace(state=lambda: state)), \
                            patch.object(illustration_api, "resolve_illustration_policy", return_value="off") as policy, \
                            patch.object(illustration_api, "generate_assessment_illustration") as generate, \
                            patch.object(orchestrator, "start_job") as start:
                        result = await enrich_question_illustration(
                            task.question_id, IllustrationRequest(question_revision=1),
                            student_id="usr_off_policy")
                    policy.assert_called_once_with("usr_off_policy", request)
                    self.assertEqual(result["status"], "not_required")
                    self.assertIsNone(result["illustration"])
                    self.assertFalse(result["retryable"])
                    self.assertEqual(result["metrics"]["generation_calls"], 0)
                    generate.assert_not_called()
                    start.assert_not_called()

    async def test_reviewed_cache_is_readable_after_new_generation_switch_is_off(self):
        task = _task("q_cached")
        instance = cat.CatInstance(
            assessment_id="asmt_cached",
            status=cat.STATUS_STOPPED,
            illustration_request="required",
            question_refs=[S.QuestionRef(question_id=task.question_id, question_revision=1)],
        )
        state = SimpleNamespace(
            tasks={task.question_id: {1: task}},
            assessments={instance.assessment_id: instance.to_detail()},
        )
        journal = SimpleNamespace(state=lambda: state)
        cached = {"status": "ready", "illustration": _illustration()}
        with patch.object(illustration_api, "get_journal", return_value=journal), \
                patch.object(illustration_api, "get_cached_assessment_illustration",
                             return_value=cached), \
                patch.object(illustration_api, "resolve_illustration_policy",
                             side_effect=AssertionError("cache hit must precede switch")), \
                patch.object(illustration_api, "generate_assessment_illustration",
                             side_effect=AssertionError("cache hit must not generate")):
            result = await enrich_question_illustration(
                task.question_id, IllustrationRequest(question_revision=1),
                student_id="usr_cached")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["question_id"], task.question_id)
        self.assertEqual(result["metrics"]["cache_hit"], 1)
        self.assertEqual(result["illustration"]["sanitizer_version"], 2)

    async def test_stopped_cache_miss_never_spends_generation_budget(self):
        task = _task("q_stopped_miss")
        instance = cat.CatInstance(
            assessment_id="asmt_stopped_miss",
            status=cat.STATUS_STOPPED,
            illustration_request="required",
            question_refs=[S.QuestionRef(question_id=task.question_id, question_revision=1)],
        )
        state = SimpleNamespace(
            tasks={task.question_id: {1: task}},
            assessments={instance.assessment_id: instance.to_detail()},
        )
        journal = SimpleNamespace(state=lambda: state)
        with patch.object(illustration_api, "get_journal", return_value=journal), \
                patch.object(illustration_api, "get_cached_assessment_illustration",
                             return_value=None), \
                patch.object(illustration_api, "resolve_illustration_policy",
                             side_effect=AssertionError("historical miss must not resolve policy")), \
                patch.object(illustration_api, "generate_assessment_illustration",
                             side_effect=AssertionError("historical miss must not generate")):
            with self.assertRaises(HTTPException) as raised:
                await enrich_question_illustration(
                    task.question_id, IllustrationRequest(question_revision=1),
                    student_id="usr_stopped")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            raised.exception.detail["error"]["code"],
            "assessment_question_not_current",
        )


class TestIllustrationEnrichment(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        enrichment._inflight.clear()
        from app.core.config import settings
        components = patch.object(settings, "quiz_diagram_mode", "components")
        components.start()
        self.addCleanup(components.stop)
        from app.illustration import preview
        render = patch.object(preview, "render", return_value=b"synthetic-review-png")
        render.start()
        self.addCleanup(render.stop)

    async def test_required_enrichment_generates_audits_and_caches_without_regenerating_question(self):
        audited = json.dumps({"status": "passed", "issues": []})
        llm = FakeLLM([_requirements_response(), _scene_response(), audited])
        from app.core import quiz_illustration_policy as policy
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(policy, "account_allows_illustration_review", return_value=True):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_enrich", task=_task(), policy="required", llm=llm)
            self.assertEqual(result["status"], "ready")
            self.assertIsNotNone(result["illustration"])
            self.assertEqual(llm.calls, 3)
            self.assertLessEqual(result["metrics"]["generation_calls"], 3)

            cached = await enrichment.generate_assessment_illustration(
                student_id="usr_enrich", task=_task(), policy="required", llm=FakeLLM([]))
            self.assertEqual(cached["status"], "ready")
            self.assertEqual(cached["metrics"]["cache_hit"], 1)
            self.assertEqual(cached["metrics"]["generation_calls"], 0)

    async def test_auto_without_visual_need_stops_after_declaration(self):
        requirements = json.dumps({"requirements": [{
            "question_slot": "q1", "illustration_needed": False, "needs": []
        }]}, ensure_ascii=False)
        llm = FakeLLM([requirements])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_auto_text", task=_task("q_auto_text"), policy="auto", llm=llm)
        self.assertEqual(result["status"], "not_required")
        self.assertEqual(llm.calls, 1)

    async def test_required_null_fails_without_default_scene_or_text_regeneration(self):
        llm = FakeLLM([_requirements_response(), _scene_response(None)])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_required_null", task=_task(), policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "illustration_required_missing")
        self.assertIsNone(result["illustration"])
        self.assertNotIn("diagram_recovery", result["metrics"])
        self.assertEqual(llm.calls, 2)

    async def test_composition_transport_failure_does_not_build_an_unreviewed_default(self):
        llm = FakeLLM([_requirements_response(), TimeoutError("provider_timeout")])
        result = await enrichment.generate_assessment_illustration(
            student_id="usr_composition_timeout", task=_task("q_composition_timeout"),
            policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "provider_timeout")
        self.assertIsNone(result["illustration"])
        self.assertEqual(llm.calls, 2)
        self.assertIsNone(enrichment.get_cached_assessment_illustration(
            "usr_composition_timeout", "q_composition_timeout", 1))

    async def test_optional_missing_scene_remains_retryable_instead_of_caching_not_required(self):
        task = _task("q_optional_missing")
        result = await enrichment.generate_assessment_illustration(
            student_id="usr_optional_missing", task=task, policy="auto",
            llm=FakeLLM([_requirements_response(), _scene_response(None)]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "diagram_scene_missing")
        self.assertIsNone(enrichment.get_cached_assessment_illustration(
            "usr_optional_missing", task.question_id, 1))

    async def test_composer_sees_public_question_and_audit_receives_gold_separately(self):
        from app.core import quiz_illustration_policy as policy
        task = _task("q_gold_boundary").model_copy(update={
            "answer": "PRIVATE_ANSWER_SENTINEL",
            "explanation": "PRIVATE_EXPLANATION_SENTINEL",
            "rubric": [S.FrozenCriterion(id="private_rubric", description="PRIVATE_RUBRIC_SENTINEL", weight=1.0)],
        })
        before = task.model_dump_json()
        llm = FakeLLM([_requirements_response(), _scene_response(),
                       json.dumps({"status": "passed", "issues": []})])
        # Compilation/reviewed catalog behavior is covered by library tests.
        # Keep this data-distribution boundary independent of asset revisions.
        with patch.object(policy, "account_allows_illustration_review", return_value=True), \
                patch("app.diagrams.pipeline.compile_questions", return_value=[{
                    "illustration": _illustration().model_dump(mode="json"),
                }]):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_gold_boundary", task=task, policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        for request in llm.requests[:2]:
            context = request["messages"][1]["content"]
            self.assertIn(task.stem, context)
            self.assertNotIn("PRIVATE_", context)
        self.assertNotIn("answer", enrichment._task_payload(task))
        self.assertNotIn("explanation", enrichment._task_payload(task))
        self.assertNotIn("rubric", enrichment._task_payload(task))
        audit = llm.requests[2]["messages"][1]["content"][0]["text"]
        self.assertIn("审查专用 authoring_gold=", audit)
        self.assertIn(task.answer, audit)
        self.assertIn(task.explanation, audit)
        self.assertIn("PRIVATE_RUBRIC_SENTINEL", audit)
        self.assertEqual(task.model_dump_json(), before)

    async def test_visual_review_uses_actual_rendered_png(self):
        from app.core import quiz_illustration_policy as policy
        from app.illustration import preview
        llm = FakeLLM([_requirements_response(), _scene_response(),
                       json.dumps({"status": "passed", "issues": []})])
        with patch.object(policy, "account_allows_illustration_review", return_value=True), \
                patch.object(preview, "render", return_value=b"the-actual-normalized-png") as render:
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_png_review", task=_task("q_png_review"), policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        render.assert_called_once()
        content = llm.requests[2]["messages"][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        import base64
        encoded = content[1]["image_url"]["url"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), b"the-actual-normalized-png")
        self.assertEqual(render.call_args.args[0].svg, result["illustration"].svg)
        self.assertIn("实际解析几何=", content[0]["text"])

    async def test_enabled_review_without_image_support_fails_before_provider_calls(self):
        from app.core import quiz_illustration_policy as policy
        llm = FakeLLM([])
        llm.supports_images = False
        task = _task("q_text_only_review")
        with patch.object(policy, "account_allows_illustration_review", return_value=True):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_text_only_review", task=task, policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "provider_unavailable")
        self.assertEqual(llm.calls, 0)
        self.assertIsNone(enrichment.get_cached_assessment_illustration(
            "usr_text_only_review", task.question_id, 1))

    async def test_disabled_review_still_repairs_unsupported_numeric_control(self):
        from app.core import quiz_illustration_policy as policy
        from app.diagrams.catalog import catalog
        task = _task("q_numeric_guard").model_copy(update={"stem": "温度计示数为45 °C。"})
        requirement = json.dumps({"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "measure", "name": "温度计"}]}]})
        bad = {"alt": "测量器示意", "nodes": [{"id": "measure", "asset_id": "apparatus.thermometer",
            "version": catalog()[1]["apparatus.thermometer"].version, "x": 120, "y": 60,
            "params": {"reading": 30, "scale_labels": True}}]}
        fixed = json.loads(json.dumps(bad))
        fixed["nodes"][0]["params"]["reading"] = 45
        llm = FakeLLM([requirement, _scene_response(bad), _scene_response(fixed)])
        llm.supports_images = False
        with patch.object(policy, "account_allows_illustration_review", return_value=False):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_numeric_guard", task=task, policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(llm.calls, 3)
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        repair = json.loads(llm.requests[2]["messages"][-1]["content"])
        self.assertEqual(repair["machine_feedback"]["condition_issues"][0]["code"], "unsupported_reading")
        self.assertNotIn("authoring_gold", json.dumps(llm.requests))

    def test_contradictory_passed_audit_cannot_pass(self):
        result = enrichment._audit_payload(json.dumps({
            "status": "passed", "issues": ["unsupported_reading"]}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(enrichment._audit_payload('{"status":"passed"}')["status"], "failed")

    def test_presentation_warnings_do_not_override_scientific_issues(self):
        warning = enrichment._audit_payload(json.dumps({"status": "failed", "issues": [],
            "warnings": ["cosmetic_layout", "missing_optional_operation"]}))
        self.assertEqual(warning["status"], "passed")
        critical = enrichment._audit_payload(json.dumps({"status": "passed", "issues": ["scientific_mismatch"],
            "warnings": ["cosmetic_layout"]}))
        self.assertEqual(critical["status"], "failed")
        self.assertEqual(enrichment._audit_payload(json.dumps({"status": "failed", "issues": [],
            "warnings": ["unknown_freeform"]}))["status"], "failed")

    async def test_direct_svg_or_fragments_are_not_repaired_into_a_scene(self):
        for data in (
            {"questions": [{"illustration": {"kind": "svg", "svg": "<svg/>"}}]},
            {"questions": [{"diagram_scene": {**SCENE, "fragments": [{"svg": "<svg/>"}]}}]},
        ):
            with self.subTest(data=data):
                llm = FakeLLM([_requirements_response(), json.dumps(data)])
                result = await enrichment.generate_assessment_illustration(
                    student_id="usr_model_svg", task=_task("q_model_svg"),
                    policy="required", llm=llm)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["code"], "diagram_model_svg_forbidden")
                self.assertIsNone(result["illustration"])
                self.assertEqual(llm.calls, 2)

    async def test_cached_legacy_material_is_read_without_recompilation_or_gold_context(self):
        task = _task("q_legacy_material")
        historical = _illustration()
        enrichment._write_cached("usr_legacy_material", task.question_id, 1,
                                 status="ready", illustration=historical)
        with patch("app.diagrams.pipeline.compile_questions",
                   side_effect=AssertionError("legacy cache must not recompile")):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_legacy_material", task=task,
                policy="required", llm=FakeLLM([]))
        self.assertEqual(result["illustration"].content_hash, historical.content_hash)
        self.assertEqual(result["illustration"].svg, historical.svg)
        self.assertEqual(result["metrics"]["cache_hit"], 1)

    async def test_semantic_audit_repairs_and_reaudits_without_exposing_gold(self):
        failed_audit = json.dumps({"status": "failed", "issues": ["label_position", "PRIVATE_ANSWER_SENTINEL"]})
        llm = FakeLLM([_requirements_response(), _scene_response(), failed_audit,
                       _scene_response(), json.dumps({"status": "passed", "issues": []})])
        task = _task("q_repair").model_copy(update={"answer": "PRIVATE_ANSWER_SENTINEL"})
        before = task.model_dump_json()
        from app.core import quiz_illustration_policy as policy
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(policy, "account_allows_illustration_review", return_value=True):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_repair", task=task, policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(llm.calls, 5)
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        repair_context = llm.requests[3]["messages"][-1]["content"]
        self.assertIn("label_position", repair_context)
        self.assertNotIn("PRIVATE_", json.dumps(llm.requests[3]))
        self.assertIn("PRIVATE_ANSWER_SENTINEL", json.dumps(llm.requests[4]))
        self.assertEqual(task.model_dump_json(), before)

    async def test_failed_scene_is_repaired_from_machine_diagnostics(self):
        requirement = json.dumps({"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "chart", "name": "柱状图"}]}]})
        bad = {"alt": "甲乙丙三组已知数据", "nodes": [{"id": "chart", "asset_id": "chart.bar",
            "x": 20, "y": 20, "params": {"values": [10, 20, 15]}}]}
        fixed = json.loads(json.dumps(bad))
        fixed["nodes"][0]["params"]["labels"] = ["甲", "乙", "丙"]
        llm = FakeLLM([requirement, _scene_response(bad), _scene_response(fixed),
                       json.dumps({"status": "passed", "issues": []})])
        from app.core import quiz_illustration_policy as policy
        with patch.object(policy, "account_allows_illustration_review", return_value=True):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_scene_repair", task=_task("q_scene_repair"), policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(llm.calls, 4)
        repair = json.loads(llm.requests[2]["messages"][-1]["content"])
        self.assertEqual(repair["machine_feedback"]["nodes"][0]["missing_parameters"], ["labels"])
        self.assertEqual(repair["previous_scene"]["nodes"][0]["params"]["values"], [10, 20, 15])
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)

    async def test_semantic_repairs_stop_at_the_call_and_repair_limit(self):
        failed = json.dumps({"status": "failed", "issues": ["scientific_mismatch"]})
        llm = FakeLLM([_requirements_response(), _scene_response(), failed,
                       _scene_response(), failed, _scene_response(), failed])
        task = _task("q_repair_limit")
        from app.core import quiz_illustration_policy as policy
        with patch.object(policy, "account_allows_illustration_review", return_value=True):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_repair_limit", task=task, policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "illustration_audit_failed")
        self.assertEqual(llm.calls, 7)
        self.assertEqual(result["metrics"]["illustration_repairs"], 2)
        self.assertIsNone(enrichment.get_cached_assessment_illustration("usr_repair_limit", task.question_id, 1))

    async def test_final_audit_can_consume_the_reserved_time(self):
        from app.core.quiz_generation_budget import GenerationBudget
        budget = GenerationBudget(max_calls=3, calls=2, deadline=time.monotonic() + 1)
        llm = FakeLLM([json.dumps({"status": "passed", "issues": []})])
        result = await enrichment._complete(llm, budget, [], timeout=1, max_tokens=700)
        self.assertEqual(json.loads(result)["status"], "passed")
        self.assertEqual(budget.calls, 3)

    async def test_concurrent_requests_share_one_success_result(self):
        audited = json.dumps({"status": "passed", "issues": []})
        llm = FakeLLM([_requirements_response(), _scene_response(), audited], delay=0.01)
        task = _task("q_concurrent")
        from app.core import quiz_illustration_policy as policy
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(policy, "account_allows_illustration_review", return_value=True):
            first, second = await asyncio.gather(
                enrichment.generate_assessment_illustration(
                    student_id="usr_concurrent", task=task, policy="required", llm=llm),
                enrichment.generate_assessment_illustration(
                    student_id="usr_concurrent", task=task, policy="required", llm=llm),
            )
        self.assertEqual(first["status"], "ready")
        self.assertEqual(second["status"], "ready")
        self.assertEqual(llm.calls, 3)
        self.assertEqual(first["illustration"].content_hash,
                         second["illustration"].content_hash)
        self.assertEqual(first["metrics"]["generation_calls"], 3)
        self.assertEqual(second["metrics"]["generation_calls"], 3)

    async def test_concurrent_failure_is_single_flight_not_second_generation(self):
        llm = FakeLLM([json.dumps({"requirements": "bad"}), _scene_response(None)], delay=0.01)
        task = _task("q_concurrent_failure")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            first, second = await asyncio.gather(
                enrichment.generate_assessment_illustration(
                    student_id="usr_concurrent_failure", task=task,
                    policy="required", llm=llm),
                enrichment.generate_assessment_illustration(
                    student_id="usr_concurrent_failure", task=task,
                    policy="required", llm=llm),
            )
        self.assertEqual(first["status"], "failed")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(first["code"], "illustration_required_missing")
        self.assertEqual(first["code"], second["code"])
        self.assertIsNone(first["illustration"])
        self.assertEqual(llm.calls, 2)
        self.assertEqual(enrichment._inflight, {})

    async def test_timeout_does_not_start_a_followup_call(self):
        llm = FakeLLM([_requirements_response()], delay=0.05)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(enrichment, "ILLUSTRATION_DEADLINE_SECONDS", 0.03), \
                patch.object(enrichment, "GENERATION_CALL_TIMEOUT_SECONDS", 0.02), \
                patch.object(enrichment, "FINAL_AUDIT_RESERVE_SECONDS", 0.005):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_timeout", task=_task("q_timeout"), policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["illustration"])
        self.assertNotIn("diagram_recovery", result["metrics"])
        self.assertEqual(llm.calls, 1)
        self.assertLessEqual(result["metrics"]["generation_calls"], 1)

    async def test_identical_compositions_are_not_translated_to_change_the_hash(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            first = await enrichment.generate_assessment_illustration(
                student_id="usr_adaptive_visuals", task=_task("q_adaptive_1"),
                policy="required", llm=FakeLLM([_requirements_response(), _scene_response()]))
            second = await enrichment.generate_assessment_illustration(
                student_id="usr_adaptive_visuals", task=_task("q_adaptive_2"),
                policy="required", llm=FakeLLM([_requirements_response(), _scene_response()]))
        self.assertEqual(first["status"], "ready")
        self.assertEqual(second["status"], "ready")
        self.assertEqual(first["illustration"].content_hash,
                         second["illustration"].content_hash)
        self.assertEqual(first["illustration"].svg, second["illustration"].svg)
        self.assertNotIn("diagram_deduplicated", second["metrics"])


SVG_V21 = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 400 300">
<defs><marker id="a" markerWidth="8px" refX="4" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#000"/></marker></defs>
<rect x="20" y="20" width="200px" height="100" fill="#eef" stroke="#000" stroke-width="2px" fill-opacity="0.5" opacity="0.9"/>
<line x1="20" y1="150" x2="300" y2="150" stroke="#000" marker-end="url(#a)" stroke-miterlimit="4"/>
<text x="30" y="60" font-size="18px" font-family="Arial, sans-serif" font-weight="bold" font-style="italic" fill="#000" dominant-baseline="middle" dy="-5">F = ma</text>
<text x="30" y="90" style="font-size:14px;fill:#333;stroke-opacity:0.2;letter-spacing:2">note</text>
<g opacity="0.8"><circle cx="350" cy="250" r="10" fill="black"/></g>
</svg>"""


class TestSvgV21Compatibility(unittest.TestCase):
    def test_common_presentation_attributes_are_accepted(self):
        normalized = normalize_svg(SVG_V21, alt="常见属性测试", caption="v2.1")
        self.assertIn('font-size="18"', normalized.svg)
        self.assertIn('font-family="sans-serif"', normalized.svg)
        self.assertIn('stroke-width="2"', normalized.svg)
        self.assertIn('opacity="0.9"', normalized.svg)
        self.assertIn('width="200"', normalized.svg)
        self.assertIn('markerWidth="8"', normalized.svg)
        illustration = normalize_illustration({
            "kind": "svg", "alt": "常见属性测试", "caption": "v2.1",
            "svg": SVG_V21})
        round_trip = normalize_illustration(illustration.model_dump(mode="json"))
        self.assertEqual(round_trip.content_hash, illustration.content_hash)

    def test_class_attribute_stays_rejected(self):
        raw = SVG_V21.replace("<rect ", '<rect class="shape" ', 1)
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_model_extra_keys_and_long_text_are_tolerated(self):
        """模型附带的额外字段/超长 caption 不得整体拒绝为 invalid_schema。"""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200">'
               '<line x1="10" y1="100" x2="280" y2="100" stroke="#000"/></svg>')
        illustration = normalize_illustration({
            "kind": "svg", "alt": "图" * 700, "caption": "注" * 300,
            "svg": svg, "note": "model chatter"})
        self.assertEqual(len(illustration.alt), 600)
        self.assertEqual(len(illustration.caption), 120)
        round_trip = normalize_illustration(illustration.model_dump(mode="json"))
        self.assertEqual(round_trip.content_hash, illustration.content_hash)


class TestReviewSwitchBehavior(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        enrichment._inflight.clear()
        from app.core.config import settings
        components = patch.object(settings, "quiz_diagram_mode", "components")
        components.start()
        self.addCleanup(components.stop)

    async def test_disabled_illustration_review_skips_audit_llm_call(self):
        from app.core import quiz_illustration_policy as policy
        llm = FakeLLM([_requirements_response(), _scene_response()])  # audit call intentionally absent
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(policy, "account_allows_illustration_review",
                             return_value=False):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_no_review", task=_task("q_no_review"),
                policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(llm.calls, 2)
        self.assertIsNotNone(result["illustration"])


if __name__ == "__main__":
    unittest.main()
