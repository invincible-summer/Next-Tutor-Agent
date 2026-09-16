from __future__ import annotations

import asyncio
import json
import tempfile
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


SVG_WITH_MARKER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-label="velocity diagram">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" viewBox="0 0 10 10">
    <path d="M 0 0 L 10 5 L 0 10 Z" style="fill:#000;stroke:#000;stroke-width:1"/>
  </marker>
</defs>
<line x1="80" y1="200" x2="520" y2="200" marker-end="url(#arrow)" style="stroke:#000;stroke-width:2"/>
<text x="300" y="180" text-anchor="middle" style="fill:#000">v</text>
</svg>"""


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


def _generated(svg: str = SVG_WITH_MARKER, *, alt: str = "小车速度箭头水平向右") -> str:
    return json.dumps({
        "illustration": {
            "kind": "svg",
            "alt": alt,
            "caption": "速度方向示意",
            "svg": svg,
        }
    }, ensure_ascii=False)


def _illustration():
    return normalize_illustration({
        "kind": "svg",
        "alt": "小车速度箭头水平向右",
        "caption": "速度方向示意",
        "svg": SVG_WITH_MARKER,
    })


class FakeLLM:
    def __init__(self, responses: list[str], *, delay: float = 0.0):
        self.responses = list(responses)
        self.calls = 0
        self.delay = delay

    async def complete(self, **_kwargs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0), {"completion_tokens": 24}


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


class TestAssessmentIllustrationApi(unittest.IsolatedAsyncioTestCase):
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


class TestIllustrationEnrichment(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        enrichment._inflight.clear()

    async def test_required_enrichment_generates_audits_and_caches_without_regenerating_question(self):
        audited = json.dumps({"status": "passed", "issues": []})
        llm = FakeLLM([_generated(), audited])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_enrich", task=_task(), policy="required", llm=llm)
            self.assertEqual(result["status"], "ready")
            self.assertIsNotNone(result["illustration"])
            self.assertEqual(llm.calls, 2)
            self.assertLessEqual(result["metrics"]["generation_calls"], 3)

            cached = await enrichment.generate_assessment_illustration(
                student_id="usr_enrich", task=_task(), policy="required", llm=FakeLLM([]))
            self.assertEqual(cached["status"], "ready")
            self.assertEqual(cached["metrics"]["cache_hit"], 1)
            self.assertEqual(cached["metrics"]["generation_calls"], 0)

    async def test_required_null_is_failure_not_text_question_regeneration(self):
        llm = FakeLLM([json.dumps({"illustration": None})])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_required_null", task=_task(), policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "illustration_required_missing")
        self.assertEqual(llm.calls, 1)

    async def test_semantic_audit_repair_is_sanitized_and_reaudited_once(self):
        repaired_svg = SVG_WITH_MARKER.replace(
            'x2="520" y2="200"', 'x2="500" y2="200"')
        audit_repair = json.dumps({
            "status": "repair",
            "issues": ["label_position"],
            "illustration": {
                "kind": "svg",
                "alt": "水平向右的速度箭头",
                "caption": "速度方向示意",
                "svg": repaired_svg,
            },
        }, ensure_ascii=False)
        final_audit = json.dumps({"status": "passed", "issues": []})
        llm = FakeLLM([_generated(), audit_repair, final_audit])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_repair", task=_task("q_repair"), policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(llm.calls, 3)
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        self.assertIn('x2="500"', result["illustration"].svg)

    async def test_concurrent_requests_share_one_success_result(self):
        audited = json.dumps({"status": "passed", "issues": []})
        llm = FakeLLM([_generated(), audited], delay=0.01)
        task = _task("q_concurrent")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            first, second = await asyncio.gather(
                enrichment.generate_assessment_illustration(
                    student_id="usr_concurrent", task=task, policy="required", llm=llm),
                enrichment.generate_assessment_illustration(
                    student_id="usr_concurrent", task=task, policy="required", llm=llm),
            )
        self.assertEqual(first["status"], "ready")
        self.assertEqual(second["status"], "ready")
        self.assertEqual(llm.calls, 2)
        self.assertEqual(first["illustration"].content_hash,
                         second["illustration"].content_hash)
        self.assertEqual(first["metrics"]["generation_calls"], 2)
        self.assertEqual(second["metrics"]["generation_calls"], 2)

    async def test_concurrent_failure_is_single_flight_not_second_generation(self):
        llm = FakeLLM([json.dumps({"illustration": None})], delay=0.01)
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
        self.assertEqual(second["code"], "illustration_required_missing")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(enrichment._inflight, {})

    async def test_timeout_does_not_start_a_followup_call(self):
        llm = FakeLLM([_generated()], delay=0.05)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(enrichment, "ILLUSTRATION_DEADLINE_SECONDS", 0.03), \
                patch.object(enrichment, "GENERATION_CALL_TIMEOUT_SECONDS", 0.02), \
                patch.object(enrichment, "FINAL_AUDIT_RESERVE_SECONDS", 0.005), \
                patch.object(enrichment, "REPAIR_MIN_REMAINING_SECONDS", 0.01):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_timeout", task=_task("q_timeout"), policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(llm.calls, 1)
        self.assertLessEqual(result["metrics"]["generation_calls"], 1)


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


class TestReviewSwitchBehavior(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        enrichment._inflight.clear()

    async def test_disabled_illustration_review_skips_audit_llm_call(self):
        from app.core import quiz_illustration_policy as policy
        llm = FakeLLM([_generated()])  # audit response intentionally absent
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(policy, "account_allows_illustration_review",
                             return_value=False):
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_no_review", task=_task("q_no_review"),
                policy="required", llm=llm)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(llm.calls, 1)
        self.assertIsNotNone(result["illustration"])


if __name__ == "__main__":
    unittest.main()
