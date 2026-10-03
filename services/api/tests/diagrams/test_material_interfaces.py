"""Library-wide protocol and arbitrary uploaded parameter controls."""
import json
import asyncio
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app.diagrams import materials
from app.diagrams.catalog import catalog
from app.diagrams.guidance import GUIDE_DIR
from app.diagrams.material_templates import TEMPLATES
from app.diagrams.semantics import asset_card, parameter_semantics, RECIPES
from app.diagrams.svg_bindings import program
from app.illustration.contracts import IllustrationError, MaterialFact, material_contract
from app.illustration.layout import _parameters, compile_scene
from tests.support.storage_sandbox import StorageSandboxTestCase


class MaterialInterfacesTest(StorageSandboxTestCase):
    def test_ai_draft_retry_reports_attribute_schema_errors_and_never_publishes(self):
        from app.diagrams.svg_bindings import SvgBinding
        self.assertNotIn("d", SvgBinding.model_json_schema()["properties"]["attribute"]["enum"])
        template = TEMPLATES[2]
        invalid = json.loads(json.dumps(template))
        invalid["parameterization"]["bindings"][0]["attribute"] = "d"
        class RetryLLM:
            async def complete(self, **kwargs):
                self.payload = json.loads(kwargs["messages"][1]["content"])
                value = invalid if not self.payload["validation_error"] else template
                return json.dumps({key: value[key] for key in ("svg", "parameterization")}), {}
        client = RetryLLM()
        draft = asyncio.run(materials.generate_draft("可调文字流程", llm=client))
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(client.payload["validation_feedback"][0]["location"][-1], "attribute")
        self.assertNotIn("path", client.payload["binding_attribute_support"])
        self.assertEqual(materials.visible("usr_synthetic_draft"), [])

    def test_accessibility_absence_is_not_a_prohibited_visible_mark(self):
        from tests.illustration.test_illustration_v2 import fixture
        from app.illustration.composition import validate_scene
        from app.illustration.contracts import Annotation, VisualBriefV2
        from app.illustration.retrieval import retrieve
        contract, brief, bundle, scene = fixture("waves.magnet")
        contract.prohibited_additions = ["磁场线"]
        contract.required_marks = ["磁场线"]
        scene.alt = "条形磁铁，无磁场线。"
        validate_scene(scene, contract, brief, bundle)
        scene.annotations = [Annotation(annotation_id="forbidden", text="磁场线",
            target={"instance": "main"})]
        with self.assertRaises(IllustrationError): validate_scene(scene, contract, brief, bundle)
        bundle = retrieve(VisualBriefV2(visual_role="supplemental", purpose="角度投影",
            view="coordinate_plane", needs=[{"need_id": "circle", "name": "单位圆正弦投影",
                "capabilities": ["geometry_construction"]}]))
        self.assertIn("mathematics_extended.unit_circle_projection", bundle.needs[0].candidate_ids)

    def test_isolated_materials_use_the_measured_hull_for_readable_uniform_scaling(self):
        from tests.illustration.test_illustration_v2 import fixture
        contract, brief, bundle, scene = fixture("geometry.fraction_bar", parameters={
            "count": (8, ""), "filled": (3, "")})
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        bounds = compiled.source.layout_report.bounds["main"]
        self.assertGreater(bounds[2], 400)
        self.assertAlmostEqual(bounds[0]+bounds[2]/2, scene.canvas.width/2)
        self.assertEqual(compiled.source.resolved_parameters["main"]["filled"], 3)

    def test_internal_annotation_points_follow_transformed_features_without_creating_ports(self):
        from pydantic import ValidationError
        from app.illustration.contracts import Annotation, Target, SceneRelation
        from app.illustration.composition import validate_scene
        from app.illustration.layout import overlaps
        from tests.illustration.test_illustration_v2 import fixture
        contract, brief, bundle, scene = fixture("earth.water_cycle")
        contract.required_marks = ["蒸发", "降水"]
        scene.annotations = [Annotation(annotation_id=name, text=text, target={
            "instance": "main", "local_point": point}, placement="outside_top")
            for name, text, point in [("evaporation", "蒸发", (310, 190)),
                ("precipitation", "降水", (65, 160))]]
        validate_scene(scene, contract, brief, bundle)
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        bounds = compiled.source.layout_report.bounds
        self.assertGreater(bounds["evaporation"][0], bounds["main"][0])
        self.assertLess(bounds["precipitation"][0], bounds["main"][0])
        self.assertFalse(overlaps(bounds["evaporation"], bounds["precipitation"]))
        scene.annotations[0].target.local_point = (4096, 4096)
        with self.assertRaises(IllustrationError):
            compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        scene.relations = [SceneRelation(relation_id="fake", type="connected", start={
            "instance": "main", "local_point": [10, 10]}, end={"instance": "main"})]
        with self.assertRaises(IllustrationError):
            validate_scene(scene, contract, brief, bundle)
        for point in ([float("nan"), 1], [-1, 0], [0, 4097]):
            with self.assertRaises(ValidationError): Target(instance="main", local_point=point)
        with self.assertRaises(ValidationError):
            Target(instance="main", port="fake", local_point=[1, 2])

    def test_leader_locates_a_real_internal_point_and_public_facts_remain_explicit(self):
        from app.illustration.contracts import Annotation
        from tests.illustration.test_illustration_v2 import fixture
        from app.illustration.preview import render
        contract, brief, bundle, scene = fixture("earth.water_cycle")
        contract.required_marks = ["太阳"]
        scene.annotations = [Annotation(annotation_id="sun", text="太阳", leader=True,
            target={"instance": "main", "local_point": [67, 51]})]
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        self.assertIn('stroke-dasharray=', compiled.illustration.svg)
        self.assertTrue(render(compiled.illustration).startswith(b"\x89PNG"))
        raw = contract.model_dump(mode="json")
        raw["facts"] = [{"id": "given", "type": "state", "entity_id": "object", "value": True,
            "source_ref": "stem", "source_quote": "合成练习", "display_policy": "depict_only"}]
        projected = material_contract({"stem": contract.public_question.stem, "material_contract": raw}, question_ref="given")
        self.assertEqual(projected.facts[0].display_policy, "explicit")
        raw["facts"][0]["source_ref"] = "blueprint"
        raw["visual_role"] = "essential"
        projected = material_contract({"stem": contract.public_question.stem, "material_contract": raw}, question_ref="hidden")
        self.assertEqual(projected.facts[0].display_policy, "depict_only")

    def test_authoring_uses_the_active_contract_without_material_preview_images(self):
        from app.core.config import settings
        from app.core.quiz_verify import generate_verified_questions
        from app.prompts.registry import get
        class EmptyLLM:
            supports_images = True
            async def complete(self, **kwargs):
                self.request = kwargs
                return '{"questions":[]}', {}
        client = EmptyLLM()
        with patch.object(settings, "quiz_illustration_pipeline", "v2"), patch(
                "app.illustration.preview.browser", side_effect=AssertionError("no material previews")):
            asyncio.run(generate_verified_questions(client, student_id="usr_synthetic_authoring",
                make_prompt=lambda: "请用条形磁铁出题", parse=lambda raw: [], topic="条形磁铁",
                grade="初中", temperature=.1, max_tokens=1000, illustration_policy="required", max_attempts=1))
        system, user = client.request["messages"]
        self.assertIn(get("quiz_illustration_authoring").text, system["content"])
        self.assertNotIn('"preview_cards":', system["content"])
        self.assertIn('"named_material_svg_sources":', system["content"])
        from app.illustration.requirements import named_material_sources
        sources = named_material_sources("条形磁铁")
        self.assertEqual([source["asset_id"] for source in sources], ["waves.magnet"])
        self.assertTrue(sources[0]["svg"].endswith("</svg>"))
        self.assertEqual(named_material_sources("未指定素材的泛用主题"), [])
        self.assertIsInstance(user["content"], str)

    def test_named_private_interface_does_not_borrow_unselected_builtin_parameters(self):
        from app.illustration.requirements import capability_guide, named_material_sources
        owner = "usr_synthetic_named_static"
        saved = materials.save(owner, materials.MaterialInput(title="新建静态烧杯", enabled=True,
            svg=TEMPLATES[1]["svg"]))
        with materials.owner_context(owner):
            sources = named_material_sources(saved["title"])
            guide = capability_guide("烧杯盛水，使用新建静态烧杯",
                selected_asset_ids=[row["asset_id"] for row in sources])
        self.assertEqual(len(guide["relevant_materials"]), 1)
        self.assertEqual(guide["relevant_materials"][0]["name"], saved["title"])
        self.assertEqual(guide["relevant_materials"][0]["parameters"], {})

    def test_composer_reads_full_authorized_svg_without_an_inventory_or_thumbnails(self):
        from app.illustration.composition import compose
        from tests.illustration.test_illustration_v2 import fixture, QueueLLM
        from app.diagrams.semantics import instantiate_asset
        contract, brief, bundle, scene = fixture("biology.stomata")
        client = QueueLLM(scene.model_dump(mode="json"))
        asyncio.run(compose(client, contract, brief, bundle))
        payload = json.loads(client.requests[0]["messages"][1]["content"])
        sources = payload["material_svg_sources"]
        self.assertEqual([row["asset_id"] for row in sources], ["biology.stomata"])
        expected = instantiate_asset("biology.stomata", bundle.assets[0]["version"], {}).drawing.svg()
        self.assertEqual(sources[0]["svg"], expected)
        self.assertNotIn("image_url", client.requests[0]["messages"][1]["content"])
        self.assertNotIn("width", payload["scene_schema"]["$defs"]["Canvas"]["properties"])
        binding_schema = payload["scene_schema"]["$defs"]["AssetInstance"]["allOf"][0]["then"]["properties"]
        self.assertEqual(binding_schema["fact_bindings"]["properties"], {})
        self.assertFalse(binding_schema["fact_bindings"]["additionalProperties"])

    def test_numeric_scales_derive_from_bound_facts_and_preserve_the_reading(self):
        from tests.illustration.test_illustration_v2 import fixture
        from app.diagrams.semantics import resolved_calibration
        contract, _, _, scene = fixture("measurement.dynamometer", essential=True,
            parameters={"reading": (2, "N"), "maximum": (4, "N")})
        resolved, bindings = _parameters(scene.asset_instances[0], contract)
        self.assertTrue(resolved["scale_labels"])
        self.assertEqual(resolved["reading"], 2)
        self.assertEqual(bindings["reading"]["display_policy"], "depict_only")
        calibration = resolved_calibration(asset_card("measurement.dynamometer"), resolved)
        self.assertEqual(calibration["range"], [0, 4])
        self.assertEqual(calibration["smallest_division"], .4)
        self.assertEqual(calibration["numbered_interval"], .8)
        self.assertNotIn("reading", calibration)
        children = resolved_calibration(asset_card("recipe.buoyancy_measurement"), resolved)["children"]
        self.assertEqual(children["dynamometer"]["smallest_division"], .4)
        fixed = resolved_calibration(asset_card("recipe.thermal"), {"reading": 45})["children"]["thermometer"]
        self.assertEqual(fixed["division_count"], 20)
        self.assertEqual(fixed["smallest_division"], 5)
        self.assertEqual(fixed["numbered_interval"], 20)
        self.assertNotIn("reading", fixed)
        scene.asset_instances[0].fact_bindings.clear()
        with self.assertRaises(IllustrationError):
            _parameters(scene.asset_instances[0], contract)

    def test_presentation_proposals_are_not_scientific_facts_even_on_scaled_axes(self):
        from tests.illustration.test_illustration_v2 import fixture
        contract, _, _, _ = fixture("function.quadratic", parameters={
            "function": ("x**2", ""), "x_range": ([-2, 2], ""), "y_range": ([-1, 5], "")})
        contract.presentation_constraints.to_scale = True
        contract.presentation_constraints.preferred_material_names = ["二次函数"]
        raw = contract.model_dump(mode="json")
        raw["facts"].append({"id": "cosmetic", "entity_id": "object", "type": "label", "value": "x",
            "predicate": "x_label", "source_ref": "stem", "source_quote": "横轴", "display_policy": "explicit"})
        question = {"stem": contract.public_question.stem, "material_contract": raw}
        resolved = material_contract(question, question_ref="synthetic_projection")
        self.assertEqual([f.id for f in resolved.facts], [f.id for f in contract.facts])
        with self.assertRaises(ValueError):
            material_contract(question, question_ref="synthetic_frozen", frozen=True)
        # A custom label is still a factual claim requiring literal support.
        raw["facts"][-1]["value"] = "distance"
        with self.assertRaises(ValueError):
            material_contract(question, question_ref="synthetic_custom")

    def test_unambiguous_fact_map_canonicalization_never_expands_authority(self):
        from tests.illustration.test_illustration_v2 import fixture
        from app.illustration.composition import validate_scene
        contract, brief, bundle, scene = fixture("geometry.fraction_bar",
            parameters={"count": (8, ""), "filled": (3, "")})
        node = scene.asset_instances[0]
        node.entity_map, node.fact_bindings = node.fact_bindings, {}
        validate_scene(scene, contract, brief, bundle)
        self.assertEqual(node.entity_map, {})
        self.assertEqual(node.fact_bindings["filled"], "f_filled")
        node.entity_map = {"count": "f_count"}
        brief.needs[0].fact_bindings.remove("f_count")
        with self.assertRaises(IllustrationError):
            validate_scene(scene, contract, brief, bundle)

    def test_function_notation_normalization_keeps_literal_support_and_safe_ast(self):
        from app.diagrams.mathematics import expression
        from app.diagrams.schema import DiagramError
        fact = MaterialFact(id="curve", type="function", value="y=x^2", predicate="function",
            source_ref="stem", source_quote="y=x^2", display_policy="explicit")
        self.assertEqual(fact.value, "x**2")
        self.assertEqual(expression(fact.value)(3), 9)
        with self.assertRaises(DiagramError):
            expression("__import__('os').system('true')")

    def test_every_registered_material_has_the_same_persisted_interface(self):
        for aid in {*catalog()[1], *RECIPES}:
            with self.subTest(asset=aid):
                card = asset_card(aid)
                stored = json.loads((GUIDE_DIR / aid / "material.json").read_text())["interface"]
                self.assertEqual(stored["parameters"], card["parameters"])
                self.assertEqual(stored["schema_version"], "1.0.0")
                for spec in card["parameters"].values():
                    self.assertIn("role", spec)
                    self.assertIn("fact_types", spec)
                    self.assertIn("default_rule", spec)
                    if spec["role"] in {"data", "function", "range"}:
                        self.assertFalse(spec["non_quantitative_allowed"])

    def test_wrong_semantic_binding_rejected_by_compiler_not_only_prompt(self):
        from tests.illustration.test_illustration_v2 import fixture
        contract, _, _, scene = fixture("function.quadratic", parameters={
            "function": ("x**2", ""), "x_range": ([-2, 2], ""), "y_range": ([-1, 5], "")})
        node = scene.asset_instances[0]
        node.fact_bindings["x_label"] = node.fact_bindings["function"]
        with self.assertRaises(IllustrationError):
            _parameters(node, contract)
        node.fact_bindings.pop("x_label")
        contract.facts[0].predicate = "unrelated_condition"
        with self.assertRaises(IllustrationError):
            _parameters(node, contract)

    def test_generic_uploaded_text_and_geometry_use_frozen_facts_and_version(self):
        from tests.illustration.test_illustration_v2 import fixture
        template = TEMPLATES[2]
        owner = "usr_synthetic_interface"
        body = materials.MaterialInput(title="通用新流程", svg=template["svg"], enabled=True,
            parameterization=template["parameterization"])
        first = materials.save(owner, body)
        aid = "material." + first["id"]
        # New revision changes the controls; an in-flight old snapshot must
        # retain its original contract and SVG, not consult the current one.
        materials.save(owner, body.model_copy(update={"svg": TEMPLATES[1]["svg"],
            "parameterization": program(), "base_revision": 1}), asset_id=first["id"])
        with materials.owner_context(owner):
            spec = parameter_semantics(aid, 1)
            self.assertEqual(spec["left_text"]["role"], "text")
            drawing = materials.instantiate(aid, 1, {"left_text": "过滤", "right_text": "回收"})
            self.assertIn("过滤", drawing.intrinsic_marks)
            self.assertNotIn("过程一", drawing.intrinsic_marks)
            self.assertEqual(parameter_semantics(aid, 2), {})
            contract, brief, bundle, scene = fixture("biology.leaf")
            contract.public_question.stem = "过滤后回收。"
            contract.facts = [MaterialFact(id=key, entity_id=contract.entities[0].id, type="label",
                value=text, source_ref="stem", source_quote=text, predicate=key, display_policy="explicit")
                for key, text in (("left_text", "过滤"), ("right_text", "回收"))]
            brief.needs[0].fact_bindings = [f.id for f in contract.facts]
            node = scene.asset_instances[0]
            node.asset_id, node.version = aid, 1
            node.fact_bindings = {f.id: f.id for f in contract.facts}
            node.params, node.non_quantitative, node.scale = {}, [], 1
            card = materials.card({"id": first["id"], "revision": 1, "title": body.title})
            bundle.assets = [card]
            bundle.needs[0].candidate_ids = [aid]
            compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
            self.assertIn("过滤", compiled.illustration.svg)
            self.assertIn(aid, compiled.source.interface_hashes)
            self.assertEqual(compiled.source.resolved_parameters[node.instance_id]["right_text"], "回收")
            node.params["left_text"] = "蒸馏"
            with self.assertRaises(IllustrationError):
                _parameters(node, contract)

    def test_radius_control_preserves_inscribed_construction_across_interval(self):
        import math
        template = TEMPLATES[0]
        controls = program(template["parameterization"])
        controls.validate_template(template["svg"])
        inconsistent = json.loads(json.dumps(template["parameterization"]))
        inconsistent["bindings"][1]["offset"] += 40
        from app.diagrams.schema import DiagramError
        with self.assertRaises(DiagramError) as mismatch:
            program(inconsistent).validate_template(template["svg"])
        self.assertEqual(mismatch.exception.code, "material_parameter_default_mismatch")
        rounded = template["svg"].replace("441.244", "441.2")
        controls.validate_template(rounded)
        for radius in (80, 123, 160):
            svg, _ = controls.apply(template["svg"], {"radius": radius})
            root = ET.fromstring(svg)
            for line in root:
                if line.tag.endswith("line"):
                    for suffix in ("1", "2"):
                        self.assertAlmostEqual(math.hypot(float(line.get("x"+suffix))-320,
                            float(line.get("y"+suffix))-200), radius, places=3)

    def test_no_code_unknown_targets_duplicate_bindings_or_out_of_range(self):
        from app.diagrams.schema import DiagramError
        template = TEMPLATES[2]
        value = template["parameterization"]
        for attribute in ("onclick", "href", "d", "transform", "style"):
            bad = json.loads(json.dumps(value))
            bad["bindings"][0]["attribute"] = attribute
            with self.assertRaises(ValueError):
                program(bad).apply(template["svg"])
        bad = json.loads(json.dumps(value))
        bad["bindings"][0]["element_id"] = "nonexistent"
        with self.assertRaises(DiagramError):
            program(bad).apply(template["svg"])
        bad["bindings"].append(bad["bindings"][0])
        with self.assertRaises(ValueError): program(bad)
        controls = program(TEMPLATES[0]["parameterization"])
        for params in ({"radius": True}, {"radius": 1000}, {"radius": float("nan")}, {"fake": 1}):
            with self.assertRaises(DiagramError): controls.apply(TEMPLATES[0]["svg"], params)
        with self.assertRaises(DiagramError):
            program(value).apply(template["svg"], {"left_text": "<script>"})
