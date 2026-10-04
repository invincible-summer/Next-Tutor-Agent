"""Synthetic V3 requests exercise full references, real PNG and safe review."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from tests.support.storage_sandbox import StorageSandboxTestCase
from app.core.config import settings
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.diagrams import materials
from app.illustration import v3, v3_retrieval
from app.illustration.contracts import IllustrationError
from app.illustration.v3_contracts import (CandidateBundleV3, DrawingInputV3,
    MaterialNeedV3, QuestionVisualContractV3, SvgDraftV3, material_contract)


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><rect x="80" y="100" width="160" height="100" fill="#fff" stroke="#000" stroke-width="2"/><line x1="40" y1="220" x2="580" y2="220" stroke="#000" stroke-width="2"/></svg>'
CAR_REQUIREMENTS = {"visual_role": "supplemental", "description": "小车在水平导轨上的合成示意图",
    "needs": [{"need_id": "car", "name": "小车", "synonyms": ["cart", "trolley"], "purpose": "参考带轮车体"}]}
PASSED = {"status": "passed"}


def draft(svg=SVG, **kwargs):
    return {"svg": svg, "alt": "合成装置示意图", "used_materials": [], **kwargs}


def contract(**kwargs):
    return QuestionVisualContractV3(question_ref="q_v3_synthetic",
        public_question={"stem": "合成练习：小车在水平导轨上运动，请识别车体与轨道。"},
        authoring_gold={"correct_answer": "private_synthetic_gold"}, **kwargs)


def payload(request):
    content = request["messages"][1]["content"]
    return json.loads(content if isinstance(content, str) else content[0]["text"])


class QueueLLM:
    supports_images = True
    def __init__(self, *values):
        self.values, self.requests = list(values), []
    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        if not self.values:
            raise AssertionError("unexpected model call")
        value = self.values.pop(0)
        if callable(value):
            value = value(kwargs)
        return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), {"completion_tokens": 10}


class IllustrationV3Test(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        for name, value in (("quiz_illustration_max_calls", 10), ("quiz_illustration_max_repairs", 2),
                ("quiz_illustration_deadline_seconds", 120), ("quiz_illustration_visual_review", "active")):
            self._patches.append(patch.object(settings, name, value))
            self._patches[-1].start()

    def test_long_svg_attribute_feedback_locates_the_generic_limit(self):
        points = " ".join(f"{i/10:g},100" for i in range(500))
        invalid = SVG.replace("</svg>", f'<polyline points="{points}" stroke="black"/></svg>')
        llm = QueueLLM(CAR_REQUIREMENTS, draft(invalid), draft(), PASSED)
        result = asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(result["status"], "ready")
        feedback = payload(llm.requests[2])["repair_feedback"]["svg_feedback"]
        self.assertEqual(feedback["attributes"][0]["attribute"], "points")
        self.assertIn("2048", feedback["attributes"][0]["rule"])
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)

    def test_essential_calibration_requires_actual_review_evidence(self):
        bound = contract(visual_role="essential", drawing_inputs=[
            {"id": "reading", "description": "待读数", "value": 3, "display": "depict_only"},
            {"id": "division", "description": "最小分度", "value": 1, "display": "explicit"}])
        requirements = {"visual_role": "essential", "description": "带标定的合成测量图", "needs": []}
        llm = QueueLLM(requirements, draft(),
            {"status": "passed", "verified_facts": ["reading"]},
            {"status": "passed", "verified_facts": ["reading", "division"], "observed_values": {"reading": 3}})
        result = asyncio.run(v3.workflow(llm, bound, "required"))
        self.assertEqual(result["metrics"]["protocol_corrections"], 1)
        self.assertGreater(len(llm.requests[2]["messages"][1]["content"]), 2)

    def test_requirement_metadata_cannot_change_frozen_drawing_data(self):
        bound = contract(drawing_inputs=[{"id": "mass", "description": "公开质量", "value": 2, "unit": "kg"}])
        declaration = {"visual_role": "supplemental", "description": "公开条件的合成图", "needs": [],
            "drawing_inputs": [{"id": "mass", "description": "重新措辞的质量", "value": 2, "unit": "kg", "source_quote": "物体质量2kg"}]}
        accepted, _ = asyncio.run(v3.declare(QueueLLM(declaration), bound, "required"))
        self.assertEqual(accepted.drawing_inputs, bound.drawing_inputs)
        declaration["drawing_inputs"][0]["value"] = 3
        with self.assertRaises(IllustrationError):
            asyncio.run(v3.declare(QueueLLM(declaration), bound, "required"))

    def test_observed_numeric_mismatch_cannot_publish_with_a_passed_id(self):
        bound = contract(visual_role="essential", drawing_inputs=[
            {"id": "reading", "description": "待读数", "value": 3, "display": "depict_only"}])
        requirements = {"visual_role": "essential", "description": "带标定的合成测量图", "needs": []}
        llm = QueueLLM(requirements, draft(),
            {"status": "passed", "verified_facts": ["reading"], "observed_values": {"reading": 2}},
            draft(SVG.replace('x="80"', 'x="90"')),
            {"status": "passed", "verified_facts": ["reading"], "observed_values": {"reading": 3}})
        result = asyncio.run(v3.workflow(llm, bound, "required"))
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        self.assertEqual(payload(llm.requests[3])["repair_feedback"]["issues"],
                         [{"code": "reading_mismatch", "target": "reading"}])

    def test_real_svg_reference_can_be_modified_and_combined_with_self_drawn_rails(self):
        def car_and_rails(request):
            rows = payload(request)["reference_materials"]["materials"]
            material = next(row for row in rows if row["asset_id"] == "mechanics.cart")
            inner = material["svg"].split(">", 1)[1].rsplit("</svg>", 1)[0]
            # Reference body modified; guide rails are newly authored geometry.
            inner = inner.replace("#eef2f6", "#fff")
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560">' + (
                '<g transform="translate(240 180) scale(1.2)">'+inner+'</g>'
                '<line x1="100" y1="338" x2="840" y2="338" stroke="#000" stroke-width="2"/>'
                '<line x1="100" y1="348" x2="840" y2="348" stroke="#000" stroke-width="2"/></svg>')
            return draft(svg, used_materials=[{"asset_id": material["asset_id"], "version": material["version"]}])
        llm = QueueLLM(CAR_REQUIREMENTS, car_and_rails, PASSED)
        result = asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["png"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(result["metrics"]["generation_calls"], 3)
        source = result["compiled"].source
        self.assertEqual(source.asset_versions, {"mechanics.cart": 1})
        self.assertEqual(source.pipeline_mode, "v3")
        self.assertEqual(source.review_gates, {"machine": "passed", "combined": "passed"})
        self.assertNotIn("resolved_parameters", source.model_dump())
        self.assertEqual(result["compiled"].illustration.schema_version, 3)
        for request in llm.requests[:2]:
            self.assertNotIn("private_synthetic_gold", json.dumps(request))
        self.assertIn("private_synthetic_gold", json.dumps(llm.requests[2]))
        self.assertEqual(llm.requests[2]["messages"][1]["content"][1]["type"], "image_url")

    def test_fuzzy_retrieval_uses_synonyms_and_keeps_unknown_needs_drawable(self):
        bundle = v3_retrieval.retrieve([MaterialNeedV3(need_id="car", name="实验推车", synonyms=["小车"]),
            MaterialNeedV3(need_id="absent", name="zxqvphkzzzzzzzz")], education_level="unknown_grade")
        self.assertIn("mechanics.cart", [row.asset_id for row in bundle.assets])
        self.assertEqual(bundle.needs[1].candidate_ids, [])
        self.assertLessEqual(len(bundle.assets), 12)
        self.assertTrue(all(len(row.candidate_ids) <= 3 for row in bundle.needs))
        self.assertEqual(bundle.retrieval_trace["full_svg_bytes"], sum(len(row.svg.encode()) for row in bundle.assets))
        self.assertTrue(all(row.svg.endswith("</svg>") for row in bundle.assets))
        requirements = {"visual_role": "supplemental", "description": "画未登记的简单构造", "needs": []}
        result = asyncio.run(v3.workflow(QueueLLM(requirements, draft(), PASSED), contract(), "required"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["compiled"].source.asset_versions, {})

    def test_source_budget_omits_whole_references_without_truncating(self):
        need = [MaterialNeedV3(need_id="car", name="小车")]
        with patch.object(v3_retrieval, "MAX_SOURCE_BYTES", 600):
            bundle = v3_retrieval.retrieve(need)
        self.assertLessEqual(sum(len(row.svg.encode()) for row in bundle.assets), 600)
        self.assertGreater(bundle.retrieval_trace["omitted_for_source_budget"], 0)
        self.assertTrue(all(row.svg.endswith("</svg>") for row in bundle.assets))

    def test_private_and_disabled_materials_are_not_exposed(self):
        body = dict(title="合成独有参考车体", svg=SVG, aliases=["synthetic_owned_cart"], enabled=True)
        own = materials.save("v3_owner_a", materials.MaterialInput(**body))
        materials.save("v3_owner_b", materials.MaterialInput(**{**body, "title": "别人的合成独有参考车体"}))
        with materials.owner_context("v3_owner_a"):
            bundle = v3_retrieval.retrieve([MaterialNeedV3(need_id="owned", name="synthetic_owned_cart")])
            self.assertEqual([row.asset_id for row in bundle.assets if row.asset_id.startswith("material.")], ["material."+own["id"]])
        materials.save("v3_owner_a", materials.MaterialInput(**{**body, "enabled": False, "base_revision": 1}), asset_id=own["id"])
        with materials.owner_context("v3_owner_a"):
            bundle = v3_retrieval.retrieve([MaterialNeedV3(need_id="owned", name="synthetic_owned_cart")])
            self.assertFalse(any(row.asset_id.startswith("material.") for row in bundle.assets))

    def test_reference_limit_gives_all_needs_one_reference_before_alternatives(self):
        needs = [MaterialNeedV3(need_id=f"n{index}", name=f"synthetic{index}") for index in range(12)]
        def ranked(need, _rows, **_kwargs):
            return [{"asset_id": f"{need.need_id}.{choice}", "title": need.name} for choice in range(3)]
        with patch.object(v3_retrieval, "_inventory", return_value=[]), \
                patch.object(v3_retrieval, "_rank", side_effect=ranked), \
                patch.object(v3_retrieval, "asset_card", side_effect=lambda aid: {"asset_id": aid, "version": 1}), \
                patch.object(v3_retrieval, "material_drawing", return_value=SimpleNamespace(svg=lambda: SVG)), \
                patch.object(v3_retrieval, "for_asset", return_value={"version": "1", "hints": {}}):
            bundle = v3_retrieval.retrieve(needs)
        self.assertEqual(len(bundle.assets), 12)
        self.assertTrue(all(len(row.candidate_ids) == 1 for row in bundle.needs))

    def test_protocol_corrections_do_not_consume_drawing_repairs(self):
        bad_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><script>alert(1)</script></svg>'
        llm = QueueLLM("unparseable", CAR_REQUIREMENTS, {"svg": SVG}, draft(bad_svg), draft(), PASSED)
        result = asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(result["metrics"]["generation_calls"], 6)
        self.assertEqual(result["metrics"]["protocol_corrections"], 2)
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        self.assertNotIn("script", result["compiled"].illustration.svg)

    def test_review_repair_feedback_does_not_contain_private_prose(self):
        failed = {"status": "failed", "issues": [{"code": "data_mismatch", "target": "canvas",
            "repairable": True, "description": "private_synthetic_gold", "suggested_operation": "reveal_private_gold"}]}
        llm = QueueLLM(CAR_REQUIREMENTS, draft(), failed, draft(SVG.replace('x="80"', 'x="90"')), PASSED)
        result = asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        self.assertEqual(payload(llm.requests[3])["repair_feedback"]["issues"], [{"code": "data_mismatch", "target": "canvas"}])
        self.assertNotIn("private_synthetic_gold", json.dumps(llm.requests[3]))
        self.assertNotIn("reveal_private_gold", json.dumps(llm.requests[3]))

    def test_real_out_of_bounds_extent_reaches_drawing_repair(self):
        escaped = SVG.replace('x="80"', 'x="-40"')
        llm = QueueLLM(CAR_REQUIREMENTS, draft(escaped), draft(), PASSED)
        result = asyncio.run(v3.workflow(llm, contract(), "required"))
        geometry = payload(llm.requests[2])["repair_feedback"]["geometry_feedback"]
        self.assertLess(geometry["rendered_bounds"][0], 0)
        self.assertEqual(geometry["canvas_bounds"], [0, 0, 640, 400])
        self.assertEqual(len(result["compiled"].source.rendered_bounds), 4)

    def test_essential_depicted_value_reaches_composer_without_gold_and_normal_ticks_are_allowed(self):
        drawing = {"id": "reading", "description": "仪器待读数", "value": 3, "unit": "N", "display": "depict_only"}
        bound = contract(visual_role="essential", description="绘制合成测量装置", drawing_inputs=[drawing])
        requirements = {"visual_role": "essential", "description": "绘制合成测量装置", "needs": []}
        tick_svg = SVG.replace("</svg>", '<text x="260" y="200" font-size="20">3</text></svg>')
        llm = QueueLLM(requirements, draft(tick_svg), {"status": "passed", "verified_facts": ["reading"], "observed_values": {"reading": 3}})
        result = asyncio.run(v3.workflow(llm, bound, "required"))
        self.assertEqual(payload(llm.requests[1])["question_visual_contract"]["drawing_inputs"][0]["value"], 3)
        self.assertNotIn("private_synthetic_gold", json.dumps(llm.requests[1]))
        self.assertIn(">3</text>", result["compiled"].illustration.svg)
        bundle = CandidateBundleV3(catalog_version="synthetic")
        redacted = v3.compile_svg(SvgDraftV3(**draft(alt="待读数为3N", caption="读数3N")), contract=bound, bundle=bundle)
        self.assertNotIn("3N", redacted.illustration.alt + redacted.illustration.caption)
        self.assertNotIn("3N", redacted.illustration.svg)
        self.assertEqual(redacted.source.review_gates["combined"], "unreviewed")

    def test_frozen_projection_only_uses_public_data_and_never_becomes_essential(self):
        question = {"stem": "合成题：物体质量为 2 kg，请描述运动。", "correct_answer": "9",
            "visual_spec": {"visual_role": "essential", "description": "合成运动示意",
                "drawing_inputs": [{"id": "mass", "description": "质量", "value": 2, "unit": "kg",
                    "source_quote": "物体质量为 2 kg"}, {"id": "reading", "description": "待读数", "value": 9,
                    "display": "depict_only"}]}}
        bound = material_contract(question, question_ref="q_frozen", frozen=True)
        self.assertEqual(bound.visual_role, "supplemental")
        self.assertEqual([row.id for row in bound.drawing_inputs], ["mass"])
        with self.assertRaises(ValueError):
            contract(visual_role="essential", frozen_question=True)

    def test_extra_retrieval_is_once_and_sources_remain_authorized(self):
        request = {"action": "request_materials", "needs": [{"need_id": "circle", "name": "圆"}]}
        llm = QueueLLM(CAR_REQUIREMENTS, request, draft(), PASSED)
        result = asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(result["metrics"]["generation_calls"], 4)
        added = payload(llm.requests[2])
        self.assertFalse(added["additional_retrieval_allowed"])
        self.assertLessEqual(len(added["reference_materials"]["materials"]), 12)
        bundle = v3_retrieval.retrieve([MaterialNeedV3(need_id="car", name="小车")])
        with self.assertRaises(IllustrationError):
            v3.compile_svg(SvgDraftV3(**draft(used_materials=[{"asset_id": "material.m_"+"0"*32, "version": 1}])),
                contract=contract(), bundle=bundle)

    def test_visual_provider_and_active_review_are_required(self):
        llm = QueueLLM()
        llm.supports_images = False
        with self.assertRaises(IllustrationError) as caught:
            asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(caught.exception.code, "provider_unavailable")
        llm = QueueLLM(CAR_REQUIREMENTS, draft())
        with patch.object(settings, "quiz_illustration_visual_review", "off"):
            with self.assertRaises(IllustrationError) as caught:
                asyncio.run(v3.workflow(llm, contract(), "required"))
        self.assertEqual(caught.exception.code, "visual_review_failed")

    def test_phase_deadline_and_child_budget_prevent_outer_text_budget_consumption(self):
        llm = QueueLLM(CAR_REQUIREMENTS, draft(), PASSED)
        outer_budget = GenerationBudget(max_calls=1)
        result = asyncio.run(v3.workflow(BudgetedLLM(llm, outer_budget), contract(), "required"))
        self.assertEqual(result["metrics"]["generation_calls"], 3)
        self.assertEqual(outer_budget.calls, 0)
        with self.assertRaises(IllustrationError) as caught:
            asyncio.run(v3.workflow(QueueLLM(CAR_REQUIREMENTS), contract(), "required", phase_deadline=time.monotonic()-1))
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_generate_question_preserves_lightweight_contract_and_one_review(self):
        question = {"stem": "合成练习：识别小车。", "correct_answer": "车体", "type": "short_answer",
            "visual_spec": {"visual_role": "supplemental", "description": "小车示意图"}}
        out = asyncio.run(v3.generate_question(QueueLLM(CAR_REQUIREMENTS, draft(), PASSED), question, "required"))
        self.assertEqual(out["material_contract"]["schema_version"], 3)
        self.assertEqual(out["visual_spec"], question["visual_spec"] | {"drawing_inputs": []})
        self.assertEqual(out["illustration_review"], {"machine": "passed", "combined": "passed"})
        self.assertEqual(out["_illustration_metrics"]["generation_calls"], 3)

    def test_contract_keeps_actual_answer_and_rubric_for_shared_registration(self):
        question = {"stem": "合成题：物体运动。", "answer": "合成答案", "explanation": "合成解析",
            "rubric": {"criteria": [{"id": "c1", "description": "合成条件", "weight": 1}]},
            "visual_spec": {"visual_role": "supplemental", "description": "合成示意图"}}
        bound = material_contract(question, question_ref="q_gold")
        self.assertEqual(bound.authoring_gold["answer"], question["answer"])
        self.assertEqual(bound.authoring_gold["rubric"], question["rubric"])
        self.assertNotIn("合成答案", json.dumps(bound.composer_view(), ensure_ascii=False))
        self.assertEqual(material_contract({"stem": "合成题：读取图中温度。"}, question_ref="q_essential").visual_role, "essential")

    def test_no_image_declaration_clears_authoring_projection_and_failed_calls_report_metrics(self):
        question = {"stem": "合成题：说出定义。", "answer": "合成定义", "material_contract": {"visual_role": "supplemental"},
            "visual_spec": {"visual_role": "supplemental", "description": "可选示意图"}}
        out = asyncio.run(v3.generate_question(QueueLLM({"visual_role": "none"}), question, "auto"))
        self.assertIsNone(out["illustration"])
        self.assertNotIn("material_contract", out)
        self.assertNotIn("visual_spec", out)
        events = []
        def failed_call(_request):
            raise RuntimeError("synthetic provider failure")
        with self.assertRaises(RuntimeError):
            asyncio.run(v3.generate_question(QueueLLM(failed_call), question, "required",
                stage=lambda name, value: events.append((name, value))))
        self.assertTrue(events[-1][1]["metrics_final"])
        self.assertEqual(events[-1][1]["generation_calls"], 1)
