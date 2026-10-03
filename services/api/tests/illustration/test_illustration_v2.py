"""Synthetic scientific scenes; real Chromium metrics/PNG, keyless LLM doubles."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.support.storage_sandbox import StorageSandboxTestCase
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
    def test_essential_public_fact_requires_literal_value_support(self):
        contract, _, _, _ = cases()["buoyancy"]
        raw = contract.model_dump(mode="json", exclude={"contract_hash"})
        raw["facts"].append({"id": "invented_fill", "entity_id": "beaker", "type": "scalar",
            "value": .5, "unit": "height_fraction", "source_ref": "stem", "source_quote": "beaker",
            "display_policy": "explicit", "predicate": "fill"})
        with self.assertRaises(ValueError):
            QuestionMaterialContract.model_validate(raw)
        raw["facts"][-1].update(source_ref="blueprint", source_quote="设计液面高度0.5")
        self.assertEqual(QuestionMaterialContract.model_validate(raw).facts[-1].value, .5)

    def test_qualitative_projection_preserves_public_state_and_real_numeric_conditions(self):
        from app.illustration.contracts import material_contract
        stem = "烧杯中的水用于温度测量。"
        raw = {"visual_role": "essential", "entities": [{"id": "beaker", "name": "烧杯",
            "source_quote": "烧杯"}], "facts": [{"id": "liquid", "type": "scalar", "entity_id": "beaker",
            "value": .5, "unit": "height_fraction", "predicate": "fill", "source_ref": "stem",
            "source_quote": "烧杯中的水", "display_policy": "explicit"}],
            "presentation_constraints": {"preferred_material_names": ["烧杯温度测量"]}}
        contract = material_contract({"stem": stem, "material_contract": raw}, question_ref="qualitative")
        self.assertEqual((contract.facts[0].type, contract.facts[0].predicate, contract.facts[0].value),
            ("state", "liquid_present", True))
        raw["facts"][0].update(type="scalar", value=.5, unit="height_fraction", predicate="fill",
            source_ref="blueprint", source_quote="设计液位0.5")
        self.assertEqual(material_contract({"stem": stem, "material_contract": raw}, question_ref="blueprint").facts[0].value, .5)
        raw["facts"][0].update(source_ref="stem", source_quote="液面高度占比0.5")
        self.assertEqual(material_contract({"stem": stem+"液面高度占比0.5", "material_contract": raw}, question_ref="quantitative").facts[0].type, "scalar")

    def test_self_relation_is_rejected_before_retrieval(self):
        data = fixture("mathematics_extended.unit_circle_projection")[0].model_dump(mode="json", exclude={"contract_hash"})
        data["required_relations"] = [{"id": "internal", "type": "inside",
            "from_entity": "object", "to_entity": "object", "source_quote": "object"}]
        with self.assertRaises(ValueError) as caught:
            QuestionMaterialContract.model_validate(data)
        errors = caught.exception.errors()
        cause = errors[0]["ctx"]["error"]
        self.assertEqual(cause.code, "invalid_contract")
        self.assertEqual(cause.target, "internal:self_relation")

    def test_internal_structure_is_preserved_and_requires_independent_png_review(self):
        from app.illustration.contracts import material_contract
        from app.illustration.review import review
        contract, brief, bundle, scene = fixture("biology.stomata")
        raw = contract.model_dump(mode="json", exclude={"contract_hash"})
        raw["required_relations"] = [{"id": "structure", "type": "inside",
            "from_entity": "object", "to_entity": "object", "source_ref": "stem", "source_quote": "object"}]
        contract = material_contract({"stem": contract.public_question.stem, "material_contract": raw}, question_ref="synthetic_internal")
        self.assertEqual(contract.required_relations, [])
        self.assertEqual([row.id for row in contract.internal_relations], ["structure"])
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertNotIn("structure", compiled.source.layout_report.verified_relations)
        png = preview.render(compiled.illustration)
        for joint in (False, True):
            with self.assertRaises(IllustrationError):
                asyncio.run(review(QueueLLM({"status": "passed"}), contract, compiled, png, joint=joint))
            result = asyncio.run(review(QueueLLM({"status": "passed", "verified_relations": ["structure"]}),
                contract, compiled, png, joint=joint))
            self.assertEqual(result.verified_relations, ["structure"])
        with self.assertRaises(ValueError):
            material_contract({"stem": "object", "material_contract": raw}, question_ref="frozen_internal", frozen=True)

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
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(compiled.source.scene.relations[0].start.instance, "main:spring")
        self.assertEqual(compiled.source.scene.relations[0].end.instance, "main:wall")
        contract, brief, bundle, scene = cases()["series"]
        scene.relations = [SceneRelation(relation_id="ambiguous", type="series", medium="wire",
            start={"instance": "main", "port": "terminal_left"},
            end={"instance": "main", "port": "terminal_right"})]
        with self.assertRaises(IllustrationError):
            compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        contract, brief, bundle, scene = cases()["thermal"]
        scene.relations = [SceneRelation(relation_id="immersed", type="immersed_in",
            contract_relation_id="r0", start={"instance": "main", "region": "thermometer:bulb"},
            end={"instance": "main", "region": "beaker:liquid"})]
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(compiled.source.scene.relations[0].start.instance, "main:thermometer")
        self.assertEqual(compiled.source.scene.relations[0].start.region, "bulb")
        scene.relations[0].end.instance, scene.relations[0].end.region = "main", "beaker:fake_region"
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

    def test_requirements_schema_uses_the_explicit_material_interface(self):
        from app.illustration.requirements import declare
        contract, brief, _bundle, _scene = fixture("geography_extended.glacier")
        contract.presentation_constraints.preferred_material_names = ["冰川地貌"]
        llm = QueueLLM(brief.model_dump(mode="json"))
        asyncio.run(declare(llm, contract, "required"))
        payload = json.loads(llm.requests[0]["messages"][1]["content"])
        schema = payload["schema"]
        self.assertEqual(schema["properties"]["visual_role"], {"const": "supplemental"})
        self.assertEqual(schema["$defs"]["MaterialNeed"]["properties"]["name"]["enum"], ["冰川地貌"])
        self.assertIn("section", schema["properties"]["view"]["enum"])
        choices = schema["$defs"]["MaterialNeed"]["allOf"][0]["then"]["properties"]["capabilities"]["items"]["enum"]
        self.assertEqual(choices, ["static_illustration"])

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

    def test_containment_repair_changes_only_an_unbound_schematic_dimension(self):
        contract, brief, bundle, scene = fixture("recipe.buoyancy_measurement",
            parameters={"reading": (3, "N"), "maximum": (5, "N"), "fill": (.5, "height_fraction")},
            qualitative=["radius"], essential=True,
            relations=[("immersed_in", "ball", "beaker"), ("suspended_from", "ball", "dynamometer")])
        from app.illustration.layout import instantiate_scene
        from app.illustration.validators import validate_relations
        from app.illustration.contracts import LayoutReport
        placed, _bindings, relations = instantiate_scene(scene, contract)
        with self.assertRaises(IllustrationError) as caught:
            validate_relations(placed, relations, contract, LayoutReport())
        self.assertEqual(caught.exception.details["type"], "immersed_in")
        self.assertEqual(len(caught.exception.details["container_bounds"]), 4)
        fitted = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(fitted.source.resolved_parameters["main:beaker"]["fill"], .5)
        self.assertLess(fitted.source.resolved_parameters["main:ball"]["radius"], 34)
        self.assertIn("fit_schematic_containment", [row["reason"] for row in fitted.source.layout_report.adjustments])
        self.assertEqual(scene.asset_instances[0].params, {})
        raw = {"base_scene_hash": digest(scene.model_dump(mode="json")), "operations": [
            {"op": "set_param", "instance_id": "main", "key": "radius", "value": 20}]}
        updated = composition.apply_patch(scene, ScenePatchV2.model_validate(raw),
            contract=contract, brief=brief, bundle=bundle)
        compiled = compile_scene(updated, contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(compiled.source.resolved_parameters["main:beaker"]["fill"], .5)
        self.assertEqual(compiled.source.resolved_parameters["main:ball"]["radius"], 20)
        for key, fact, value in [("reading", "f_reading", 4), ("fill", "f_fill", .7), ("radius", "", 100)]:
            invalid = {**raw, "operations": [{"op": "set_param", "instance_id": "main", "key": key,
                "fact_id": fact, "value": value}]}
            with self.assertRaises(IllustrationError):
                updated = composition.apply_patch(scene, ScenePatchV2.model_validate(invalid),
                    contract=contract, brief=brief, bundle=bundle)
                compile_scene(updated, contract=contract, brief=brief, bundle=bundle)

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

    def test_frozen_text_extraction_and_internal_protocol_recovery(self):
        from app.illustration.contracts import material_contract
        contract, brief, _, scene = cases()["horizontal_block"]
        frozen = material_contract({"stem": contract.public_question.stem},
            question_ref="q_frozen", frozen=True)
        material = contract.model_dump(mode="json", include={"visual_role", "entities", "facts", "required_relations"})
        good = {"material": material, "brief": brief.model_dump(mode="json")}
        bad = brief.model_dump(mode="json")
        review = {"status": "passed"}
        llm = QueueLLM(bad, good, scene.model_dump(mode="json"), review, review)
        result = asyncio.run(orchestrator.workflow(llm, frozen, "required", frozen=True))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        self.assertEqual(result["metrics"]["generation_calls"], 5)
        self.assertEqual(result["contract"].public_question, frozen.public_question)
        payload = json.loads(llm.requests[0]["messages"][1]["content"])
        self.assertTrue(payload["extract_material_from_public_question"])
        self.assertEqual(payload["schema"]["required"], ["material", "brief"])
        self.assertIn("Entity", payload["schema"]["$defs"])
        self.assertNotIn("presentation_constraints", payload["schema"]["properties"]["material"]["properties"])
        feedback = json.loads(llm.requests[1]["messages"][1]["content"])["repair_feedback"]
        self.assertEqual(feedback["target"], "main:references")

    def test_extraction_rejects_unquoted_facts_and_role_changes(self):
        from app.illustration.contracts import material_contract
        from app.illustration.requirements import accept_declaration
        contract, brief, _, _ = cases()["horizontal_block"]
        frozen = material_contract({"stem": contract.public_question.stem}, question_ref="q_frozen", frozen=True)
        material = contract.model_dump(mode="json", include={"visual_role", "entities", "facts", "required_relations"})
        material["entities"][0]["source_quote"] = "题面不存在的物体"
        with self.assertRaises(IllustrationError):
            accept_declaration(json.dumps({"material": material, "brief": brief.model_dump(mode="json")}), frozen, "required")
        material["visual_role"] = "essential"
        with self.assertRaises(IllustrationError):
            accept_declaration(json.dumps({"material": material, "brief": brief.model_dump(mode="json")}), frozen, "required")

    def test_new_frozen_projection_keeps_qualitative_water_and_rejects_wrong_direction(self):
        from app.illustration.contracts import material_contract, canonical_function_expression
        from app.illustration.requirements import accept_declaration, capability_guide
        stem = "烧杯中有水，酒精灯放在三脚架下方。"
        frozen = material_contract({"stem": stem}, question_ref="q_water", frozen=True)
        material = {"visual_role": "supplemental", "entities": [{"id": "beaker", "name": "烧杯",
            "source_ref": "stem", "source_quote": "烧杯中有水"}], "facts": [
            {"id": "water", "entity_id": "beaker", "type": "scalar", "predicate": "fill",
             "value": .5, "unit": "height_fraction", "display_policy": "depict_only",
             "source_ref": "stem", "source_quote": "烧杯中有水"}]}
        brief = VisualBriefV2(visual_role="supplemental", purpose="水存在的定性示意",
            needs=[{"need_id": "water", "name": "烧杯", "entity_ids": ["beaker"], "fact_bindings": ["water"]}])
        contract, _ = accept_declaration(json.dumps({"material": material, "brief": brief.model_dump(mode="json")}), frozen, "required")
        self.assertEqual(contract.facts[0].type, "state")
        self.assertEqual(contract.facts[0].predicate, "liquid_present")
        self.assertIs(contract.facts[0].value, True)
        self.assertEqual(contract.facts[0].display_policy, "explicit")
        for quote in ("烧杯内有水，液面占高度的0.8", "烧杯内有半杯水"):
            numeric_question = material_contract({"stem": quote}, question_ref="q_numeric_water", frozen=True)
            numeric_material = copy.deepcopy(material)
            numeric_material["entities"][0]["source_quote"] = quote
            numeric_material["facts"][0]["source_quote"] = quote
            with self.assertRaises(IllustrationError):
                accept_declaration(json.dumps({"material": numeric_material, "brief": brief.model_dump(mode="json")}), numeric_question, "required")
        frozen.presentation_constraints.to_scale = True
        with self.assertRaises(IllustrationError):
            accept_declaration(json.dumps({"material": material, "brief": brief.model_dump(mode="json")}), frozen, "required")
        frozen.presentation_constraints.to_scale = False
        partially_bound = frozen.model_copy(deep=True)
        partially_bound.facts = [fact.model_copy(update={"entity_id": ""}) for fact in contract.facts]
        partially_bound.contract_hash = ""
        partially_bound = QuestionMaterialContract.model_validate(partially_bound.model_dump(mode="json"))
        original_facts = copy.deepcopy(partially_bound.facts)
        with self.assertRaises(IllustrationError):
            accept_declaration(json.dumps({"material": {"visual_role": "supplemental", "entities": [], "facts": []},
                "brief": brief.model_dump(mode="json")}), partially_bound, "required")
        self.assertEqual(partially_bound.facts, original_facts)
        material["required_relations"] = [{"id": "bad_direction", "type": "ordered_left_to_right",
            "from_entity": "beaker", "to_entity": "beaker", "source_ref": "stem",
            "source_quote": "酒精灯放在三脚架下方"}]
        with self.assertRaises(IllustrationError) as failure:
            accept_declaration(json.dumps({"material": material, "brief": brief.model_dump(mode="json")}), frozen, "required")
        self.assertEqual(failure.exception.target, "bad_direction:type")
        self.assertEqual(canonical_function_expression("y=x²"), "x**2")
        guide = capability_guide("甲、乙、丙分别收集10、20、15份问卷。")
        self.assertIn("柱状图", [row["name"] for row in guide["relevant_materials"]])

    def test_composition_schema_mistake_recovers_with_same_authorized_bundle(self):
        contract, brief, _, scene = cases()["horizontal_block"]
        bad = scene.model_dump(mode="json")
        bad["asset_instances"][0]["entity_map"]["block"] = "foreign_entity"
        review = {"status": "passed"}
        llm = QueueLLM(brief.model_dump(mode="json"), bad, scene.model_dump(mode="json"), review, review)
        result = asyncio.run(orchestrator.workflow(llm, contract, "required"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["generation_calls"], 5)
        payload = json.loads(llm.requests[2]["messages"][1]["content"])
        self.assertEqual(payload["repair_feedback"]["code"], "scene_asset_not_authorized")
        self.assertEqual(payload["visual_contract"], json.loads(llm.requests[1]["messages"][1]["content"])["visual_contract"])

    def test_joint_repair_stays_in_same_request_and_cosmetic_warnings_pass(self):
        contract, brief, _, scene = cases()["horizontal_block"]
        visual = {"status": "passed"}
        private = "synthetic_gold_feedback_secret"
        joint = {"status": "failed", "issues": [
            {"code": "label_layout", "target": "main", "repairable": True,
                "description": private, "suggested_operation": private},
            {"code": private, "target": private, "repairable": True, "description": private}]}
        patch_data = {"action": "patch", "base_scene_hash": digest(scene.model_dump(mode="json")),
            "operations": [{"op": "move_instance", "instance_id": "main", "x": 32, "y": 16}]}
        warning = {"status": "failed", "issues": [{"code": "extra_whitespace", "target": "canvas", "severity": "warning"}]}
        llm = QueueLLM(brief.model_dump(mode="json"), scene.model_dump(mode="json"), visual, joint,
            patch_data, warning, visual)
        result = asyncio.run(orchestrator.workflow(llm, contract, "required"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["generation_calls"], 7)
        self.assertEqual(result["reviews"]["visual"]["status"], "passed")
        self.assertIn("warning_only_failed_status_normalized", result["reviews"]["visual"]["rationale_codes"])
        content = llm.requests[4]["messages"][1]["content"]
        self.assertNotIn(private, json.dumps(content))
        feedback = json.loads(content[0]["text"])["issues"]
        self.assertEqual(feedback[0]["target"], "main")
        self.assertEqual(feedback[1]["target"], "canvas")
        self.assertEqual(feedback[1]["code"], "scientific_mismatch")

    def test_invalid_persisted_contract_is_not_reported_as_provider_failure(self):
        contract, _, _, _ = cases()["horizontal_block"]
        job = orchestrator._new_job(contract, "required")
        job["owner_epoch"] = persistence.epoch("usr_invalid_contract")
        job["contract"]["entities"][0]["source_quote"] = "不存在的引用"
        llm = QueueLLM()
        asyncio.run(orchestrator._run("usr_invalid_contract", job, llm))
        self.assertEqual(job["failure"]["code"], "invalid_contract")
        self.assertFalse(llm.requests)

    def test_point_names_use_actual_ink_clearance_inside_hollow_figures(self):
        from app.illustration.contracts import Annotation
        contract, brief, bundle, scene = cases()["circumcircle"]
        contract.public_question.stem += "圆心O。"
        contract.required_marks = ["O"]
        scene.annotations = [Annotation(annotation_id="center", text="O",
            target={"instance": "main:circle", "local_point": [80, 80]}, placement="near_point")]
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        label = compiled.source.layout_report.bounds["center"]
        circle = compiled.source.layout_report.bounds["main:circle"]
        self.assertLess(abs(label[0]-circle[0]-circle[2]/2), 40)
        self.assertLess(abs(label[1]-circle[1]-circle[3]/2), 40)
        self.assertNotIn('stroke-dasharray', compiled.illustration.svg)
        self.assertTrue(preview.render(compiled.illustration).startswith(b"\x89PNG"))

    def test_recipe_label_resolves_only_the_unique_fact_owner(self):
        from app.illustration.contracts import Annotation
        contract, brief, bundle, scene = cases()["thermal"]
        scene.annotations = [Annotation(annotation_id="reading", text="45",
            target={"instance": "main", "region": "body"}, fact_refs=["f_reading"])]
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertEqual(compiled.source.scene.annotations[0].target.instance, "main:thermometer")
        scene.annotations[0].fact_refs = []
        valid = cases()["thermal"][3]
        review = {"status": "passed"}
        llm = QueueLLM(brief.model_dump(mode="json"), scene.model_dump(mode="json"),
            valid.model_dump(mode="json"), review, review)
        result = asyncio.run(orchestrator.workflow(llm, contract, "required"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["illustration_repairs"], 1)
        self.assertEqual(result["metrics"]["generation_calls"], 5)

        invalid = copy.deepcopy(valid)
        invalid.asset_instances[0].entity_map["invalid_registered_child"] = invalid.asset_instances[0].entity_map.pop("thermometer")
        invalid.annotations = [Annotation(annotation_id="reading", text="45",
            target={"instance": "main", "region": "body"}, fact_refs=["f_reading"])]
        with self.assertRaises(IllustrationError) as failure:
            composition.validate_scene(invalid, contract, brief, bundle)
        self.assertEqual(failure.exception.code, "scene_schema_invalid")
        llm = QueueLLM(brief.model_dump(mode="json"), invalid.model_dump(mode="json"),
            valid.model_dump(mode="json"), review, review)
        result = asyncio.run(orchestrator.workflow(llm, contract, "required"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["generation_calls"], 5)

    def test_review_resolves_recipe_scale_from_actual_child_parameters(self):
        from app.illustration.review import review
        contract, brief, bundle, scene = cases()["buoyancy"]
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        llm = QueueLLM({"status": "passed", "verified_facts": ["f_reading", "f_maximum"]})
        asyncio.run(review(llm, contract, compiled, preview.render(compiled.illustration)))
        payload = json.loads(llm.requests[0]["messages"][1]["content"][0]["text"])
        calibration = payload["calibration_instances"]["main"]["children"]["dynamometer"]
        self.assertEqual(calibration["range"], [0, 5])
        self.assertEqual(calibration["smallest_division"], .5)
        self.assertEqual(calibration["numbered_interval"], 1)

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
