"""Real catalog, scientific computations, bounded compilation and delivery."""
import asyncio
import copy
import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.core.quiz_illustration import QuestionIllustration, normalize_illustration
from app.diagrams.catalog import CandidateBundle, catalog, retrieve, search
from app.diagrams.compiler import compile_scene, preview_asset
from app.diagrams.pipeline import (compile_questions, declare_and_retrieve, fallback_requirements,
                                   retrieve_declaration, scene_geometry, scene_condition_issues,
                                   _wire_topology_issues)
from app.diagrams.schema import DiagramError, VisualRequirements
from tests.support.storage_sandbox import StorageSandboxTestCase


REQUIREMENTS = {"requirements": [{"question_slot": "q1", "illustration_needed": True,
    "scene_brief": "水平面滑块", "needs": [{"key": "scene", "name": "水平面滑块"}]}]}
SCENE = {"nodes": [{"id": "scene", "asset_id": "template.horizontal_block", "x": 16,
                   "y": 16, "scale": .9}], "alt": "水平面上的物体"}
QUESTION = {"id": "1", "type": "multiple_choice", "stem": "已知水平面上物体质量2kg，合力6N，求加速度。",
    "options": {"A": "3m/s²", "B": "6m/s²"}, "answer": "A",
    "explanation": "由牛顿第二定律a=F/m，代入题中已知条件得到加速度为3m/s²。", "diagram_scene": SCENE}
AUDIT = {"items": [{"question_ref": "1", "proposed_status": "passed", "illustration_check": "passed"}]}


class FakeLLM:
    supports_images = True

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.responses.pop(0), ensure_ascii=False), {}


class DiagramLibraryTest(StorageSandboxTestCase):
    def test_every_asset_is_real_safe_and_portable_in_both_profiles(self):
        assets = catalog()[1]
        self.assertGreater(len(assets), 500)
        for asset in assets.values():
            for profile in ["textbook", "monochrome"]:
                with self.subTest(asset=asset.id, profile=profile):
                    picture = preview_asset(asset.id, profile=profile)
                    self.assertEqual(normalize_illustration(picture.model_dump()), picture)
                    self.assertNotIn("<use", picture.svg)
                    self.assertNotIn("foreignObject", picture.svg)
                    self.assertGreater(len(asset.draw(preview=True).parts), 0)

    def test_fuzzy_names_and_hard_functional_constraints(self):
        self.assertEqual(search("beakr")[0].id, "vessel.beaker")
        self.assertEqual(search("量筒", features=["刻度"])[0].id, "vessel.cylinder")
        self.assertEqual(search("烧杯", features=["带侧管"]), [])
        self.assertEqual(search("不存在的星际仪器"), [])
        self.assertEqual(search(" "), [])

    def test_gallery_fuzzy_tolerates_typos_but_agent_gate_stays_strict(self):
        gallery = [a.id for a in search("烧被", gallery=True, top_k=20)]
        self.assertIn("vessel.beaker", gallery)
        self.assertEqual(search("beakr", gallery=True)[0].id, "vessel.beaker")
        self.assertEqual(search("烧被"), [])
        self.assertEqual(search("bk"), [])

    def test_runtime_charts_require_real_data_and_compute_geometry(self):
        assets = catalog()[1]
        with self.assertRaisesRegex(DiagramError, "data_required"):
            assets["chart.pie"].draw()
        pie = assets["chart.pie"].draw({"values": [1, 2, 3]})
        for actual, expected in zip(pie.facts["sector_angles"], [60, 120, 180]):
            self.assertAlmostEqual(actual, expected)
        with self.assertRaisesRegex(DiagramError, "unequal_bins_need_density"):
            assets["chart.histogram"].draw({"values": [2, 4], "bin_edges": [0, 1, 3]})
        hist = assets["chart.histogram"].draw({"values": [2, 4], "bin_edges": [0, 1, 3], "density": True})
        self.assertEqual(hist.facts["bar_heights"], [2, 2])
        for face in range(1, 7):
            die = assets["geometry.dice"].draw({"face": face})
            self.assertEqual(sum(e.tag.endswith("circle") for e in die.parts), face)
        formula = assets["chemistry.ethanol"].draw().facts["atoms"]
        self.assertEqual([formula.count(v) for v in ["C", "H", "O"]], [2, 6, 1])

    def test_missing_chart_data_never_uses_gallery_or_renderer_defaults(self):
        requirements = [VisualRequirements.model_validate({
            "question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "chart", "name": "柱状图"}],
        })]
        bundle = retrieve(requirements)
        result = compile_questions([{"diagram_scene": {"alt": "题目数据",
            "nodes": [{"id": "chart", "asset_id": "chart.bar", "x": 20, "y": 20}]}}],
            bundle, "required")[0]
        self.assertEqual(result["_diagram_error"], "diagram_missing_fact_binding")
        self.assertIsNone(result["illustration"])
        self.assertNotIn("diagram_recovery", result)

    def test_requirements_recovery_cannot_supply_default_template_conditions(self):
        declaration = fallback_requirements("用酒精灯给烧杯加热", policy="required")
        bundle = retrieve_declaration(declaration, policy="required")
        self.assertTrue(any(need["name"] == "烧杯" for need in bundle.needs))
        result = compile_questions([{"diagram_scene": {"alt": "加热烧杯",
            "nodes": [{"id": "scene", "asset_id": "template.heating_beaker",
                       "x": 16, "y": 16, "scale": .9}]}}], bundle, "required")[0]
        self.assertEqual(result["_diagram_error"], "diagram_missing_fact_binding")
        self.assertIsNone(result["illustration"])

    def test_missing_scene_cannot_turn_retrieved_components_into_a_layout(self):
        declaration = fallback_requirements("小车沿水平直线向右运动", policy="required")
        bundle = retrieve_declaration(declaration, policy="required")
        result = compile_questions([{"diagram_scene": None}], bundle, "required")[0]
        self.assertEqual(result["_diagram_error"], "illustration_required_missing")
        self.assertIsNone(result["illustration"])
        self.assertNotIn("diagram_source", result)

    def test_display_overflow_fits_the_original_scene_without_substitution(self):
        bundle = retrieve_declaration(REQUIREMENTS, policy="required")
        raw = copy.deepcopy(QUESTION)
        raw["diagram_scene"]["nodes"][0]["x"] = 0
        original = copy.deepcopy(raw)
        result = compile_questions([raw], bundle, "required")[0]
        self.assertNotIn("_diagram_error", result)
        self.assertIsNotNone(result["illustration"])
        self.assertEqual(raw, original)
        actual = result["diagram_source"]["scene"]["nodes"][0]
        expected = raw["diagram_scene"]["nodes"][0]
        self.assertEqual(actual["asset_id"], expected["asset_id"])
        self.assertEqual(actual["params"], expected.get("params", {}))
        self.assertGreaterEqual(actual["x"], 4)
        self.assertGreaterEqual(actual["y"], 4)
        self.assertNotIn("diagram_recovery", result)

    def test_requirements_fallback_reads_question_text_without_json_field_names(self):
        context = "冻结文字题：" + json.dumps({"question_id": "q_question", "question_revision": 1,
            "stem": "用烧杯盛水观察。", "options": None})
        declaration = fallback_requirements(context, policy="required")
        names = [need["name"] for need in declaration["requirements"][0]["needs"]]
        self.assertIn("烧杯", names)
        self.assertNotIn("stem", names)
        self.assertNotIn("ion", names)

    def test_empty_feature_match_is_corrected_using_real_name_candidates(self):
        rejected = {"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "function", "name": "二次函数", "features": ["刻度"]}]}]}
        corrected = copy.deepcopy(rejected)
        corrected["requirements"][0]["needs"][0]["features"] = []
        model = FakeLLM(rejected, corrected)
        budget = GenerationBudget(max_calls=2)
        bundle = asyncio.run(declare_and_retrieve(BudgetedLLM(model, budget),
            context="已知二次函数y=x²，画出函数图像。", policy="required", count=1))
        self.assertIn("function.quadratic", bundle.assets)
        self.assertEqual(budget.calls, 2)
        first_context = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(first_context["question_slots"], ["q1"])
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertTrue(feedback["declaration_repair"])
        self.assertEqual(feedback["machine_feedback"]["missing_needs"][0]["name_matches"][0]["features"], [])

    def test_public_control_filter_recovers_partially_missing_need(self):
        rejected = {"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "power", "name": "电池"},
                      {"key": "meter", "name": "电流表", "features": ["刻度"]}]}]}
        corrected = copy.deepcopy(rejected)
        corrected["requirements"][0]["needs"][1].update(name="电流表符号", features=[])
        model = FakeLLM(rejected, corrected)
        public = "电池和电流表组成闭合回路，讨论连接方式。"
        budget = GenerationBudget(max_calls=2)
        bundle = asyncio.run(declare_and_retrieve(BudgetedLLM(model, budget), context=public,
            public_source=public, policy="required", count=1))
        self.assertEqual(budget.calls, 2)
        self.assertIn("circuit.ammeter", bundle.assets)
        self.assertNotIn("measurement.ammeter_real", bundle.assets)
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        rows = feedback["machine_feedback"]["missing_needs"][1]["name_matches"]
        self.assertTrue(any("reading" in row["unsupported_quantity_controls"] for row in rows))

    def test_numeric_controls_require_matching_unit_and_role(self):
        bundle = retrieve_declaration({"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "measure", "name": "温度计"}]}]}, policy="required")
        scene = {"alt": "测量器示意", "nodes": [{"id": "measure", "asset_id": "apparatus.thermometer",
            "version": catalog()[1]["apparatus.thermometer"].version, "x": 120, "y": 60,
            "params": {"reading": 45, "scale_labels": True}}]}
        geometry = scene_geometry(scene, bundle)
        for public in ("温度计固定在45厘米长的支架上，观察温度变化。",
                       "环境温度45℃。用温度计测量正在加热的水温。"):
            with self.subTest(public=public):
                issues = scene_condition_issues(geometry, public)
                self.assertIn("unsupported_reading", [issue["code"] for issue in issues])
        self.assertEqual(scene_condition_issues(geometry, "温度计示数为45 °C。"), [])

    def test_range_and_reading_cannot_borrow_each_others_public_values(self):
        bundle = retrieve_declaration({"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "measure", "name": "电流表"}]}]}, policy="required")
        scene = {"alt": "定量仪表", "nodes": [{"id": "measure", "asset_id": "measurement.ammeter_real",
            "x": 120, "y": 60, "params": {"reading": 2, "maximum": 5, "scale_labels": True}}]}
        public = "电流表量程为5 A，示数为2 A。"
        self.assertEqual(scene_condition_issues(scene_geometry(scene, bundle), public), [])
        for params, parameter in (({"reading": 5, "maximum": 5}, "reading"),
                                  ({"reading": 2, "maximum": 2}, "maximum")):
            with self.subTest(parameter=parameter):
                scene["nodes"][0]["params"].update(params)
                issues = scene_condition_issues(scene_geometry(scene, bundle), public)
                self.assertEqual({issue["parameter"] for issue in issues}, {parameter})

    def test_wires_cannot_merge_distinct_terminal_networks_on_the_canvas(self):
        first = {"kind": "wire", "start_node": "a", "start_anchor": "terminal_left",
            "end_node": "b", "end_anchor": "terminal_left", "points": [[10, 10], [30, 10]]}
        second = {"kind": "wire", "start_node": "c", "start_anchor": "terminal_left",
            "end_node": "d", "end_anchor": "terminal_left"}
        for points in ([[20, 0], [20, 20]], [[20, 10], [40, 10]], [[30, 10], [40, 20]]):
            with self.subTest(points=points):
                issues = _wire_topology_issues([first, {**second, "points": points}])
                self.assertEqual(issues, [{"code": "connection_crosses_connection", "connections": [0, 1]}])
        self.assertEqual(_wire_topology_issues([first, {**second, "points": [[10, 20], [30, 20]]}]), [])

    def test_wires_in_one_explicit_terminal_network_may_share_a_branch(self):
        first = {"kind": "wire", "start_node": "a", "start_anchor": "terminal_left",
            "end_node": "b", "end_anchor": "terminal_left", "points": [[10, 10], [30, 10]]}
        branch = {"kind": "wire", "start_node": "b", "start_anchor": "terminal_left",
            "end_node": "c", "end_anchor": "terminal_left", "points": [[30, 10], [40, 10]]}
        continuation = {"kind": "wire", "start_node": "c", "start_anchor": "terminal_left",
            "end_node": "d", "end_anchor": "terminal_left", "points": [[40, 10], [20, 10], [20, 20]]}
        self.assertEqual(_wire_topology_issues([first, branch, continuation]), [])

    def test_support_uses_physical_contact_ports_instead_of_padded_body(self):
        public = "烧杯放在支撑板上。"
        bundle = retrieve_declaration({"requirements": [{"question_slot": "q1", "illustration_needed": True,
            "needs": [{"key": "a", "name": "烧杯"}, {"key": "b", "name": "石棉网"}]}]}, policy="required")
        scene = {"alt": "主体得到支撑", "nodes": [
            {"id": "a", "asset_id": "vessel.beaker", "x": 200, "y": 55, "params": {"fill": .5}},
            {"id": "b", "asset_id": "apparatus.wire_mesh", "x": 200, "y": 140}],
            "layout_relations": [{"type": "supported_by", "source": {"node": "a", "region": "body"},
                "target": {"node": "b", "region": "body"}, "source_quote": "烧杯放在支撑板上"}]}
        issues = scene_condition_issues(scene_geometry(scene, bundle), public)
        contacts = [issue for issue in issues if issue["code"] == "relation_contact_port_required"]
        self.assertEqual(len(contacts), 2)
        self.assertEqual(set(contacts[0]["allowed_anchors"]), {"support_bottom"})
        self.assertEqual(set(contacts[1]["allowed_anchors"]), {"support_top"})
        scene["layout_relations"][0].update(source={"node": "a", "anchor": "support_bottom"},
                                            target={"node": "b", "anchor": "support_top"})
        self.assertEqual(scene_condition_issues(scene_geometry(scene, bundle), public), [])

    def test_new_scenes_must_explicitly_choose_condition_parameters(self):
        assets = catalog()[1]
        choices = {
            "measurement.dynamometer": {"reading": 3, "maximum": 5, "scale_labels": False},
            "circuit.switch": {"closed": True},
            "mechanics.incline": {"angle": 20},
            "vessel.beaker": {"fill": .5, "show_scale": False},
            "geometry.dice": {"face": 3},
        }
        for asset_id, parameters in choices.items():
            with self.subTest(asset=asset_id):
                bundle = retrieve_declaration({"requirements": [{
                    "question_slot": "q1", "illustration_needed": True,
                    "needs": [{"key": "object", "name": assets[asset_id].title}],
                }]}, policy="required")
                scene = {"alt": "题目已知条件", "nodes": [{"id": "object",
                    "asset_id": asset_id, "x": 20, "y": 20}]}
                missing = compile_questions([{"diagram_scene": scene}], bundle, "required")[0]
                self.assertEqual(missing["_diagram_error"], "diagram_missing_fact_binding")
                scene["nodes"][0]["params"] = parameters
                explicit = compile_questions([{"diagram_scene": scene}], bundle, "required")[0]
                self.assertNotIn("_diagram_error", explicit)
                actual = explicit["diagram_facts"][0]["parameters"]
                for key, value in parameters.items():
                    self.assertEqual(actual[key], value)

    def test_fixed_readout_templates_are_rejected_when_legacy_schema_cannot_bind_state(self):
        # This gate is independent of catalog review/retrieval: even an
        # authorized historical template cannot supply unexposed state.
        assets = catalog()[1]
        for asset_id, parameters in {
            "template.buoyancy": {"fill": .65},
            "template.overflow": {"fill": .2},
            "template.thermal": {},
        }.items():
            with self.subTest(asset=asset_id):
                requirement = VisualRequirements.model_validate({"question_slot": "q1",
                    "illustration_needed": True, "needs": [{"key": "scene", "name": assets[asset_id].title}]})
                bundle = CandidateBundle([requirement], {asset_id: assets[asset_id]}, [{
                    "question_slot": "q1", "key": "scene", "candidates": [asset_id]}])
                result = compile_questions([{"diagram_scene": {"alt": "已有条件",
                    "nodes": [{"id": "scene", "asset_id": asset_id, "x": 16, "y": 16,
                               "scale": .9, "params": parameters}]}}], bundle, "required")[0]
                self.assertEqual(result["_diagram_error"], "diagram_missing_fact_binding")
                self.assertIsNone(result["illustration"])
                self.assertNotIn("diagram_source", result)

    def test_invalid_scientific_data_is_rejected_before_delivery(self):
        assets = catalog()[1]
        cases = [("chart.scatter", {"values": [1], "points": [[1]]}),
                 ("chart.stem_leaf", {"values": [1.5, 2.7]}),
                 ("chart.pie", {"values": [1, 2], "labels": ["过长的图例会挤出画布边界", "B"]}),
                 ("function.vector", {"vector": [100, 100]}),
                 ("function.tangent_line", {"interval": [0, 100]}),
                 ("measurement.dynamometer", {"reading": 6, "maximum": 5}),
                 ("apparatus.thermometer", {"reading": 100}),
                 ("geometry.fraction_bar", {"count": 2, "filled": 3}),
                 ("graph.stack", {"items": list(range(15))})]
        for asset_id, params in cases:
            with self.subTest(asset=asset_id), self.assertRaises(DiagramError):
                assets[asset_id].draw(params)
        # Fixed instruments do not advertise unrelated adjustable readings.
        self.assertEqual(assets["measurement.micrometer"].parameter_schema(), {})
        self.assertEqual(assets["apparatus.glass_rod"].parameter_schema(), {})

    def test_subset_repair_keeps_original_question_candidate_slot(self):
        rows = [VisualRequirements.model_validate({"question_slot": slot,
            "illustration_needed": True, "needs": [{"key": "object", "name": name}]})
            for slot, name in [("q1", "烧杯"), ("q2", "骰子")]]
        bundle = retrieve(rows)
        question = {"id": "second", "diagram_source": {"forged": True},
                    "diagram_scene": {"alt": "已知骰子形态", "nodes": [{"id": "die",
                        "asset_id": "geometry.dice", "x": 40, "y": 40, "params": {"face": 3}}]}}
        compiled = compile_questions([question], bundle, "required", question_slots={"second": "q2"})[0]
        self.assertNotIn("_diagram_error", compiled)
        self.assertEqual(compiled["visual_requirements"]["question_slot"], "q2")
        self.assertEqual(compiled["diagram_source"]["asset_versions"], {"geometry.dice": 1})

    def test_compiler_freezes_sources_and_only_uses_authorized_assets(self):
        result = compile_scene(SCENE, allowed_assets={"template.horizontal_block"})
        self.assertEqual(result.illustration.sanitizer_version, 3)
        self.assertEqual(result.source.asset_versions, {"template.horizontal_block": 1})
        self.assertEqual(QuestionIllustration.model_validate(result.illustration.model_dump()), result.illustration)
        with self.assertRaisesRegex(DiagramError, "not_retrieved"):
            compile_scene(SCENE, allowed_assets=set())
        for updates in [{"x": 0}, {"scale": 6}, {"version": 2}, {"params": {"script": "x"}}, {"rotation": 90}, {"label": "过长的标签"*12}]:
            bad = copy.deepcopy(SCENE)
            bad["nodes"][0].update(updates)
            with self.subTest(updates=updates), self.assertRaises(DiagramError):
                compile_scene(bad)

    def test_custom_svg_fragments_are_rejected(self):
        bundle = retrieve([VisualRequirements.model_validate(REQUIREMENTS["requirements"][0])])
        raw = copy.deepcopy(QUESTION)
        raw["diagram_scene"]["fragments"] = [{"svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><circle cx="60" cy="60" r="20"/></svg>', "x": 20, "y": 20}]
        self.assertEqual(compile_questions([raw], bundle, "required")[0]["_diagram_error"], "diagram_invalid_scene")
        raw["diagram_scene"] = {"alt": "可见结构", "fragments": [{"svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><script>bad</script></svg>'}]}
        with self.assertRaises(ValueError):
            compile_scene(raw["diagram_scene"])

    def test_model_authored_svg_is_rejected_even_with_a_component_scene(self):
        bundle = retrieve([VisualRequirements.model_validate(REQUIREMENTS["requirements"][0])])
        raw = copy.deepcopy(QUESTION)
        raw["illustration"] = {"kind": "svg", "alt": "外部图", "svg": "<svg/>"}
        self.assertEqual(compile_questions([raw], bundle, "required")[0]["_diagram_error"],
                         "diagram_model_svg_forbidden")

    def test_requirements_retrieval_generation_and_audit_share_budget(self):
        from app.core.quiz_verify import generate_verified_questions
        llm = FakeLLM(REQUIREMENTS, {"questions": [QUESTION]}, AUDIT)
        budget = GenerationBudget(max_calls=3)
        with patch.object(settings, "quiz_diagram_mode", "components"):
            questions, meta = asyncio.run(generate_verified_questions(BudgetedLLM(llm, budget),
                make_prompt=lambda: "一道带图的牛顿第二定律选择题", parse=lambda text: json.loads(text)["questions"],
                topic="牛顿第二定律", grade="高中", temperature=.2, max_tokens=4000,
                illustration_policy="required", verify_mode="critic"))
        self.assertEqual(len(questions), 1)
        self.assertEqual(meta["generation_calls"], 3)
        self.assertEqual(questions[0]["verification"]["illustration_check"], "passed")
        self.assertIn("diagram_source", questions[0])
        self.assertNotIn("vessel.beaker", llm.calls[0]["messages"][0]["content"])
        self.assertIn("template.horizontal_block", llm.calls[1]["messages"][0]["content"])
        self.assertIn("项目计算的构图事实", llm.calls[2]["messages"][1]["content"])

    def test_cat_component_enrichment_never_changes_frozen_material(self):
        from app.core import quiz_illustration_enrichment as enrichment
        from app.core import quiz_illustration_policy as policy
        from tests.illustration.test_quiz_illustration_enrichment import _task
        task = _task("q_component")
        before = task.model_dump_json()
        llm = FakeLLM(REQUIREMENTS, {"questions": [{"diagram_scene": SCENE}]}, {"status": "passed", "issues": []})
        with patch.object(settings, "quiz_diagram_mode", "components"), patch.object(policy, "account_allows_illustration_review", return_value=True):
            result = asyncio.run(enrichment.generate_assessment_illustration(student_id="usr_component", task=task, policy="required", llm=llm))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(task.model_dump_json(), before)
        self.assertEqual(enrichment.get_cached_assessment_illustration("usr_component", task.question_id, 1)["status"], "ready")

    def test_catalog_api_pagination_filter_and_preview_validation(self):
        from app.api.v1.diagram_library import router
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            first = client.get("/diagram-assets?per=12").json()
            self.assertEqual(len(first["items"]), 12)
            second = client.get("/diagram-assets?page=1&per=12").json()
            self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
            matched = client.get("/diagram-assets?q=beakr&category=chemistry").json()
            self.assertEqual(matched["items"][0]["id"], "vessel.beaker")
            detail = client.get("/diagram-assets/vessel.beaker").json()
            self.assertIn("fill", detail["parameters"])
            self.assertEqual(client.post("/diagram-assets/vessel.beaker/preview", json={"params": {"fill": .7}}).status_code, 200)
            self.assertEqual(client.post("/diagram-assets/vessel.beaker/preview", json={"params": {"fill": 2}}).status_code, 422)
            self.assertEqual(client.post("/diagram-assets/chart.scatter/preview", json={"params": {"values": [1], "points": [[1]]}}).status_code, 422)
            self.assertEqual(client.get("/diagram-assets/missing.asset").status_code, 404)

    def test_library_routes_require_existing_workspace_access(self):
        from fastapi import APIRouter, Depends
        from app.api.v1.diagram_library import router
        from app.identity.access import require_api_access
        from tests.support.storage_sandbox import authenticated_client
        app = FastAPI()
        gated = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_access)])
        gated.include_router(router)
        app.include_router(gated)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/diagram-assets").status_code, 401)
            self.assertEqual(client.post("/api/v1/diagram-assets/vessel.beaker/preview", json={}).status_code, 401)
        with authenticated_client(app, "usr_diagram_owner") as client:
            self.assertEqual(client.get("/api/v1/diagram-assets/vessel.beaker").status_code, 200)
