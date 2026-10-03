"""Synthetic scientific scenes; real Chromium metrics/PNG, keyless LLM doubles."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.storage_sandbox import StorageSandboxTestCase
from app.core.config import settings
from app.diagrams.catalog import digest
from app.diagrams.semantics import COMPONENTS, RECIPES, asset_card, catalog_version
from app.illustration import composition, orchestrator, persistence, preview, retrieval
from app.illustration.contracts import (CandidateBundleV2, QuestionMaterialContract,
    SceneDraftV2, ScenePatchV2, VisualBriefV2, IllustrationError)
from app.illustration.layout import compile_scene


def fixture(asset_id, *, parameters=None, qualitative=(), params=None, essential=False,
            relations=(), scale=1, x=30, y=15):
    """All material is synthetic; no asset preview readings become question facts."""
    parameters = parameters or {}
    recipe = RECIPES.get(asset_id)
    roles = [row[0] for row in recipe.children] if recipe else ["object"]
    entities = [{"id": role, "name": role, "source_ref": "stem", "source_quote": role} for role in roles]
    facts = []
    for key, (value, unit) in parameters.items():
        role = recipe.bindings[key][0] if recipe else "object"
        facts.append({"id": "f_"+key, "entity_id": role,
            "type": "data" if isinstance(value, list) else "state" if isinstance(value, bool) else
                "function" if key == "function" else "scalar",
            "value": value, "unit": unit, "source_ref": "blueprint" if essential else "stem",
            "source_quote": str(value), "display_policy": "depict_only" if essential and key == "reading" else "explicit"})
    stem = "合成练习：" + "；".join(roles + [str(value) for value, _ in parameters.values()])
    if asset_id == "recipe.inscribed_triangle":
        stem += "；圆内接正三角形。"
    required = [{"id": "r"+str(i), "type": kind, "from_entity": a, "to_entity": b,
                 "source_ref": "stem", "source_quote": stem} for i, (kind, a, b) in enumerate(relations)]
    contract = QuestionMaterialContract(question_ref="q_synthetic", public_question={"stem": stem},
        visual_role="essential" if essential else "supplemental", entities=entities,
        facts=facts, required_relations=required,
        presentation_constraints={"target_width": 960}, authoring_gold={"answer": "synthetic_gold"})
    brief = VisualBriefV2(visual_role=contract.visual_role, purpose="核对合成教材结构",
        needs=[{"need_id": "main", "name": asset_card(asset_id)["title"], "entity_ids": roles,
                "fact_bindings": [f["id"] for f in facts]}])
    card = asset_card(asset_id)
    bundle = CandidateBundleV2(catalog_version=catalog_version(), metadata_version="2.0.0",
        retrieval_trace={}, needs=[{"need_id": "main", "matched": True, "candidate_ids": [asset_id]}], assets=[card])
    instance = {"instance_id": "main", "need_id": "main", "asset_id": asset_id, "version": card["version"],
        "entity_id": "" if recipe else "object", "entity_map": {r: r for r in roles} if recipe else {},
        "fact_bindings": {key: "f_"+key for key in parameters}, "non_quantitative": list(qualitative),
        "params": params or {}, "anchor_intent": "关系明确并留白", "x": x, "y": y, "scale": scale}
    scene = SceneDraftV2(asset_instances=[instance], alt="合成教材示意图")
    return contract, brief, bundle, scene


def cases():
    return {
        "heating": fixture("recipe.heating_beaker", parameters={"lit": (True, "")}, qualitative=["fill"],
            params={"fill": .45}, relations=[("supported_by", "beaker", "mesh"), ("supported_by", "mesh", "tripod")]),
        "buoyancy": fixture("recipe.buoyancy_measurement", parameters={"reading": (3, "N"), "maximum": (5, "N")},
            qualitative=["fill", "radius"], params={"fill": .7, "scale_labels": True}, essential=True,
            relations=[("immersed_in", "ball", "beaker"), ("suspended_from", "ball", "dynamometer")]),
        "thermal": fixture("recipe.thermal", parameters={"reading": (45, "°C")}, qualitative=["fill"],
            params={"fill": .7, "scale_labels": True}, relations=[("immersed_in", "thermometer", "beaker")]),
        "spring_vertical": fixture("recipe.spring_vertical", qualitative=["length"],
            relations=[("suspended_from", "weight", "spring")]),
        "spring_horizontal": fixture("recipe.spring_horizontal", qualitative=["length"],
            relations=[("supported_by", "block", "plane"), ("connected", "spring", "block")]),
        "horizontal_block": fixture("recipe.horizontal_block", relations=[("supported_by", "block", "plane")]),
        "series": fixture("recipe.series_circuit", parameters={"closed": (True, "")}, essential=True,
            relations=[("series", "battery", "ammeter"), ("series", "ammeter", "resistor"),
                       ("series", "resistor", "switch"), ("series", "switch", "battery")]),
        "filtration": fixture("recipe.filtration", qualitative=["fill"],
            relations=[("connected", "funnel", "beaker")]),
        "circumcircle": fixture("recipe.inscribed_triangle", qualitative=["radius"]),
        "bar": fixture("recipe.bar_table", parameters={"values": ([10, 20, 15], ""), "labels": (["甲", "乙", "丙"], "")}),
        "dynamometer": fixture("measurement.dynamometer", parameters={"reading": (3, "N"), "maximum": (5, "N")},
            params={"scale_labels": True}, essential=True, scale=2.2, x=200, y=40),
        "function": fixture("function.quadratic", parameters={"function": ("x**2", ""), "x_range": ([-3, 3], ""),
            "y_range": ([-1, 9], "")}, qualitative=["x_label", "y_label", "show_ticks"], scale=1.2),
    }


class QueueLLM:
    supports_images = True
    def __init__(self, *values):
        self.values, self.requests = list(values), []
    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        if not self.values:
            raise AssertionError("unexpected LLM call")
        return json.dumps(self.values.pop(0), ensure_ascii=False), {"completion_tokens": 10}


class IllustrationV2Test(StorageSandboxTestCase):
    def test_terminal_annotations_keep_their_actual_ports_clear_of_wires(self):
        from app.illustration.layout import overlaps
        contract, brief, bundle, scene = cases()["series"]
        contract.required_marks = ["+", "−"]
        scene = SceneDraftV2.model_validate({**scene.model_dump(mode="json"), "annotations": [
            {"annotation_id": "positive", "text": "+", "target": {
                "instance": "main:ammeter", "port": "terminal_left"}, "placement": "outside_left"},
            {"annotation_id": "negative", "text": "−", "target": {
                "instance": "main:ammeter", "port": "terminal_right"}, "placement": "outside_right"}]})
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        report = compiled.source.layout_report
        positive, negative = report.bounds["positive"], report.bounds["negative"]
        self.assertLess(positive[0], negative[0])
        self.assertFalse(overlaps(positive, negative))
        for name, port in [("positive", "terminal_left"), ("negative", "terminal_right")]:
            point = report.ports["main:ammeter"][port]
            self.assertLessEqual(abs(report.bounds[name][0]+report.bounds[name][2]/2-point[0]),
                report.bounds[name][2]/2+12.01)
            self.assertFalse(overlaps(report.bounds[name], report.bounds["main:ammeter"]))
        self.assertTrue(preview.render(compiled.illustration).startswith(b"\x89PNG"))
        scene.annotations[0].target.port = "fake_terminal"
        with self.assertRaises(IllustrationError):
            compile_scene(scene, contract=contract, brief=brief, bundle=bundle)

    def test_recipe_shorthand_only_resolves_a_unique_registered_child_port(self):
        from app.illustration.contracts import SceneRelation
        contract, brief, bundle, scene = cases()["spring_horizontal"]
        scene.relations = [SceneRelation(relation_id="fixed", type="connected", medium="rope",
            start={"instance": "main", "port": "spring_start"},
            end={"instance": "main", "port": "fixed_end"})]
        compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(scene.relations[0].start.instance, "main:spring")
        self.assertEqual(scene.relations[0].end.instance, "main:wall")
        contract, brief, bundle, scene = cases()["series"]
        scene.relations = [SceneRelation(relation_id="ambiguous", type="series", medium="wire",
            start={"instance": "main", "port": "terminal_left"},
            end={"instance": "main", "port": "terminal_right"})]
        with self.assertRaises(IllustrationError):
            compile_scene(scene, contract=contract, brief=brief, bundle=bundle)

    def test_inventory_and_capability_filter(self):
        self.assertGreaterEqual(len(COMPONENTS), 30)
        self.assertGreaterEqual(len(RECIPES), 10)
        brief = VisualBriefV2(visual_role="supplemental", purpose="烧杯中的液体",
            needs=[{"need_id": "beaker", "name": "烧杯", "entity_ids": ["beaker"], "capabilities": ["liquid_fill", "open_top"]}])
        bundle = retrieval.retrieve(brief)
        self.assertIn("vessel.beaker", bundle.needs[0].candidate_ids)
        self.assertLessEqual(len(bundle.needs[0].candidate_ids), 6)
        self.assertTrue(all({"liquid_fill", "open_top"} <= set(a["capabilities"]) for a in bundle.assets))

    def test_scientific_scenes_real_png(self):
        for name, (contract, brief, bundle, scene) in cases().items():
            with self.subTest(name=name):
                compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
                png = preview.render(compiled.illustration)
                self.assertTrue(png.startswith(b"\x89PNG"))
                self.assertEqual(compiled.illustration.schema_version, 3)
                self.assertEqual(set(compiled.source.layout_report.verified_relations), {r.id for r in contract.required_relations})

    def test_instance_geometry_changes_with_bound_reading(self):
        contract, brief, bundle, scene = cases()["dynamometer"]
        first = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        data = contract.model_dump(mode="json", exclude={"contract_hash"})
        data["facts"][0]["value"] = 1
        second = compile_scene(scene, contract=QuestionMaterialContract.model_validate(data), brief=brief, bundle=bundle)
        self.assertNotEqual(first.illustration.svg, second.illustration.svg)
        self.assertNotIn("synthetic_gold", first.illustration.svg)
        self.assertNotIn("3 N", first.illustration.alt)

    def test_missing_parameter_and_foreign_candidate_fail_closed(self):
        contract, brief, bundle, scene = cases()["dynamometer"]
        raw = scene.model_dump(mode="json")
        raw["asset_instances"][0]["fact_bindings"].pop("reading")
        with self.assertRaises(IllustrationError) as result:
            compile_scene(SceneDraftV2.model_validate(raw), contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(result.exception.code, "missing_fact_binding")
        raw["asset_instances"][0]["asset_id"] = "measurement.voltmeter_real"
        with self.assertRaises(IllustrationError) as result:
            composition.validate_scene(SceneDraftV2.model_validate(raw), contract, brief, bundle)
        self.assertEqual(result.exception.code, "scene_asset_not_authorized")

    def test_patch_preserves_facts_and_checks_base(self):
        contract, brief, bundle, scene = cases()["dynamometer"]
        patch_data = {"base_scene_hash": digest(scene.model_dump(mode="json")),
            "operations": [{"op": "move_instance", "instance_id": "main", "x": 220, "y": 45}]}
        updated = composition.apply_patch(scene, ScenePatchV2.model_validate(patch_data), contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(updated.asset_instances[0].fact_bindings, scene.asset_instances[0].fact_bindings)
        with self.assertRaises(IllustrationError) as result:
            composition.apply_patch(updated, ScenePatchV2.model_validate(patch_data), contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(result.exception.code, "patch_conflict")

    def test_composer_never_sees_gold_or_depicted_value(self):
        contract, _, _, _ = cases()["dynamometer"]
        public = contract.composer_view()
        self.assertNotIn("authoring_gold", public)
        self.assertNotIn("value", public["facts"][0])
        self.assertNotIn("source_quote", public["facts"][0])

    def test_real_preview_review_workflow_four_calls(self):
        contract, brief, _, scene = cases()["horizontal_block"]
        review = {"status": "passed", "issues": [], "verified_facts": []}
        llm = QueueLLM(brief.model_dump(mode="json"), scene.model_dump(mode="json"), review, review)
        with patch.object(settings, "quiz_illustration_visual_review", "active"):
            result = asyncio.run(orchestrator.workflow(llm, contract, "required"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["generation_calls"], 4)
        self.assertEqual(result["compiled"].source.review_gates["visual"], "passed")
        self.assertEqual(llm.requests[2]["messages"][1]["content"][1]["type"], "image_url")

    def test_image_unsupported_never_publishes(self):
        contract, brief, _, scene = cases()["horizontal_block"]
        llm = QueueLLM(brief.model_dump(mode="json"), scene.model_dump(mode="json"))
        llm.supports_images = False
        with patch.object(settings, "quiz_illustration_visual_review", "active"):
            with self.assertRaises(IllustrationError) as result:
                asyncio.run(orchestrator.workflow(llm, contract, "required"))
        self.assertEqual(result.exception.code, "provider_unavailable")

    def test_owner_singleflight_retry_and_purge_no_resurrection(self):
        contract, brief, _, scene = cases()["horizontal_block"]
        async def run():
            review = {"status": "passed"}
            llm = QueueLLM(brief.model_dump(mode="json"), scene.model_dump(mode="json"), review, review)
            first = orchestrator.start_job("usr_test", contract, "auto", llm=llm)
            same = orchestrator.start_job("usr_test", contract, "auto", llm=llm)
            self.assertEqual(first["job_id"], same["job_id"])
            task = next(iter(orchestrator._running.values()))
            await task
            job = persistence.read("usr_test", "jobs", first["job_id"])
            self.assertEqual(job["status"], "ready")
            self.assertIsNone(persistence.read("usr_other", "jobs", first["job_id"]))
            self.assertEqual(orchestrator.start_job("usr_test", contract, "off")["artifact_id"], job["artifact_id"])
            previous_epoch = persistence.epoch("usr_test")
            persistence.purge("usr_test")
            self.assertFalse(persistence.owner_dir("usr_test").exists())
            with self.assertRaises(IllustrationError):
                persistence.write("usr_test", "jobs", first["job_id"], job, expected_epoch=previous_epoch)
            self.assertFalse(persistence.owner_dir("usr_test").exists())
        with patch.object(settings, "quiz_illustration_visual_review", "active"), patch(
                "app.core.quiz_illustration_policy.account_allows_illustration", return_value=True):
            asyncio.run(run())
