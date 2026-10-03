"""Scoped prompt loading, phase separation and bounded inventory selection."""
import json
from app.diagrams.guidance import GUIDE_DIR, records, for_asset, bundle_view
from app.diagrams.semantics import RECIPES, asset_card
from app.diagrams.catalog import catalog
from app.illustration.contracts import CandidateBundleV2
from app.illustration.requirements import capability_guide
from app.prompts.registry import get
from tests.storage_sandbox import StorageSandboxTestCase


class DiagramGuidanceTest(StorageSandboxTestCase):
    def test_literal_chinese_counts_do_not_infer_science_facts(self):
        from app.illustration.contracts import literal_number_supported
        for value, quote in [(2, "两个子代双链"), (1, "一条旧链"), (23, "二十三个球"), (10, "十次实验")]:
            self.assertTrue(literal_number_supported(value, quote))
        for value, quote in [(2, "二氧化碳"), (1, "单位圆"), (2, "二百个粒子"), (20, "一百二十次"), (2, "-2"), (3, "0.3")]:
            self.assertFalse(literal_number_supported(value, quote))

    def test_complete_material_policy_and_fixed_marks_are_selected_only(self):
        guide = capability_guide("闭端气柱位移模态 mode=2")
        row = next(row for row in guide["relevant_materials"] if row["name"] == "闭端气柱位移模态")
        self.assertEqual(row["entity_policy"], "single_construction")
        self.assertEqual(set(row["fixed_marks"]), {"闭端", "开端", "位移包络"})
        unrelated = capability_guide("烧杯水中的温度计")
        self.assertNotIn("位移包络", json.dumps(unrelated, ensure_ascii=False))

    def test_pending_material_guide_is_not_loaded_for_authoring(self):
        from unittest.mock import patch
        asset = catalog()[1]["physics_extended.venturi"]
        with patch.dict(asset.review, {"status": "pending"}):
            guide = capability_guide("文丘里管与测压管")
            self.assertNotIn(asset.title, [row["name"] for row in guide["relevant_materials"]])

    def test_non_quantitative_liquid_appearance_never_erases_quantitative_material(self):
        from app.illustration.contracts import material_contract
        question = {"stem": "烧杯中有水。", "material_contract": {
            "visual_role": "essential", "entities": [{"id": "beaker", "name": "烧杯"}],
            "facts": [{"id": "height", "entity_id": "beaker", "type": "scalar", "value": .65,
                "unit": "height_fraction", "source_ref": "blueprint", "display_policy": "depict_only",
                "source_quote": "液面高度按非定量示意绘制"}]}}
        self.assertEqual(material_contract(question, question_ref="synthetic").facts, [])
        question["material_contract"]["facts"][0]["source_quote"] = ""
        self.assertEqual(material_contract(question, question_ref="synthetic").facts[0].value, .65)
        question["material_contract"]["facts"][0]["unit"] = "cm"
        question["material_contract"]["facts"][0]["source_quote"] = "非定量示意"
        self.assertEqual(material_contract(question, question_ref="synthetic").facts[0].unit, "cm")

    def test_guidance_is_versioned_registered_and_bound_to_existing_assets(self):
        assets = {*catalog()[1], *RECIPES, "material.static"}
        for asset, row in records().items():
            self.assertIn(asset, assets)
            for phase, hint in row["hints"].items():
                self.assertLessEqual(len(hint), 600)
                self.assertEqual(get(f"diagram_asset.{asset}.{phase}", row["version"]).text, hint)

    def test_every_builtin_svg_has_its_own_metadata_and_guidance(self):
        import hashlib
        for aid in {*catalog()[1], *RECIPES}:
            directory = GUIDE_DIR / aid
            metadata = json.loads((directory / "material.json").read_text())
            self.assertEqual(metadata["id"], aid)
            svg = (directory / "asset.svg").read_text().rstrip("\n")
            self.assertEqual(metadata["svg_hash"], "sha256:"+hashlib.sha256(svg.encode()).hexdigest())
            self.assertEqual(metadata["guidance_file"], "usage_guide.json")
            self.assertNotIn("assets", json.loads((directory / "usage_guide.json").read_text()))

    def test_no_full_inventory_hints_or_unrelated_calibration_in_main_prompts(self):
        guide = capability_guide()
        self.assertEqual(guide["relevant_materials"], [])
        for name in ["quiz_illustration_composer", "quiz_illustration_requirements", "quiz_illustration_review"]:
            text = get(name, "2.2.0" if name == "quiz_illustration_requirements" else "2.1.0").text
            for forbidden in ["5°C", "35°C", "漏斗", "recipe.thermal", "maximum/10"]:
                self.assertNotIn(forbidden, text)
        related = capability_guide("烧杯水中的温度计读取温度")
        self.assertLessEqual(len(related["relevant_materials"]), 12)
        payload = json.dumps(related, ensure_ascii=False)
        self.assertIn("5°C", payload)
        self.assertNotIn("导管末端", payload)
        self.assertNotIn("串联回路", payload)

    def test_candidate_payload_includes_only_current_phase_and_selected_assets(self):
        bundle = CandidateBundleV2(catalog_version="test", metadata_version="test",
            retrieval_trace={}, needs=[], assets=[asset_card("recipe.thermal")])
        view = bundle_view(bundle, "compose")
        self.assertEqual(len(view["assets"]), 1)
        hint = view["assets"][0]["usage_guidance"]
        self.assertEqual(set(hint), {"version", "text"})
        self.assertEqual(hint["text"], for_asset("recipe.thermal")["hints"]["compose"])
        self.assertNotIn("maximum/10", json.dumps(view))
        self.assertNotIn("authoring", hint)

    def test_local_match_terms_select_relevant_protocol_without_inventory_hints(self):
        cases = [
            ("正三角形内接于圆", "circle", "maximum/10"),
            ("y=x**2函数图像", "函数表达式", "导管末端"),
            ("电池开关闭合组成串联回路", "battery", "5°C"),
        ]
        for context, expected, unrelated in cases:
            with self.subTest(context=context):
                rows = capability_guide(context)["relevant_materials"]
                self.assertLessEqual(len(rows), 12)
                payload = json.dumps(rows, ensure_ascii=False)
                self.assertIn(expected, payload)
                self.assertNotIn(unrelated, payload)
                for row in rows:
                    self.assertEqual(set(row["usage_guidance"]), {"version", "text"})
                    self.assertNotIn("match_terms", row)

    def test_unique_parameter_predicate_preserves_hidden_binding_and_owner(self):
        from app.illustration.composition import validate_scene
        from tests.test_illustration_v2 import fixture
        contract, brief, bundle, scene = fixture("measurement.dynamometer",
            parameters={"reading": (3, "N"), "maximum": (5, "N")}, essential=True)
        node = scene.asset_instances[0]
        node.fact_bindings.pop("reading")
        contract.facts[0].predicate = "reading"
        validate_scene(scene, contract, brief, bundle)
        self.assertEqual(node.fact_bindings["reading"], "f_reading")
        node.fact_bindings.pop("reading")
        contract.facts[0].predicate = ""
        validate_scene(scene, contract, brief, bundle)
        self.assertNotIn("reading", node.fact_bindings)
        contract.facts[0].predicate = "reading"
        contract.facts[0].entity_id = "other"
        validate_scene(scene, contract, brief, bundle)
        self.assertNotIn("reading", node.fact_bindings)

    def test_hidden_model_placeholder_cannot_change_server_reading(self):
        from app.illustration.composition import validate_scene
        from app.illustration.layout import _parameters
        from tests.test_illustration_v2 import fixture
        contract, brief, bundle, scene = fixture("measurement.dynamometer",
            parameters={"reading": (3, "N"), "maximum": (5, "N")}, essential=True)
        node = scene.asset_instances[0]
        node.params["reading"] = 999
        validate_scene(scene, contract, brief, bundle)
        self.assertNotIn("reading", node.params)
        self.assertEqual(_parameters(node, contract)[0]["reading"], 3)
        node.params["maximum"] = 99
        from app.illustration.contracts import IllustrationError
        with self.assertRaises(IllustrationError):
            _parameters(node, contract)
