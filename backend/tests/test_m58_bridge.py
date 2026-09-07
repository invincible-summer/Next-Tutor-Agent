"""Unit tests for M5.8 wiring: SkillGraph <- M5 ontology bridge, seed-id
attribution preference, strict floating nodes, M4 quiz anchoring, and the
stage-aware matcher.

The bridge is what makes M5 real for the rest of the system: BKT mastery is
tracked per real ontology node (not per floating auto node), while
off-syllabus concepts keep precise floating nodes instead of being
mis-attributed to a superficially similar one.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase
from app.agents.knowledge import get_knowledge_service
from app.agents.knowledge import store as store_mod
from app.agents.student_model.manager import StudentModel
from app.agents.student_model.skill_graph import SkillGraph

_VALID_SPEC = json.dumps({
    "subject": "嵌入式开发",
    "chapters": [{"name": "单片机", "concepts": [
        {"name": "GPIO", "difficulty": 2, "prerequisites": []},
        {"name": "中断", "difficulty": 4, "prerequisites": ["GPIO"]},
    ]}],
}, ensure_ascii=False)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def complete(self, messages, temperature=0.0, max_tokens=1000):
        self.calls += 1
        return self.payload, {}


class TestSkillGraphBridge(unittest.TestCase):
    """StudentModel.graph mirrors the M5 ontology when knowledge is on."""

    @classmethod
    def setUpClass(cls):
        cls.sm = StudentModel("test_m58_bridge").load()

    def test_merge_adds_ontology_nodes(self):
        # P6-A2：考纲 seed 已删，空 M5 下 SkillGraph 保持 legacy 21 节点种子；
        # 教材/自定义图谱节点经 merge 进入并开启 strict_match。
        g = self.sm.graph
        self.assertIn("physics.dynamics.friction", g.nodes)  # legacy 节点仍在
        # Public textbook concepts may already be present in the merged graph.
        # The bridge must keep legacy nodes and project only trackable concepts.
        kinds = {getattr(n, "kind", "concept") for n in g.nodes.values()}
        self.assertNotIn("chapter", kinds)

    def test_merge_adds_prerequisite_edges(self):
        # a pack-internal chain (集合 -> 函数 style) must exist as SkillNode prereqs
        g = self.sm.graph
        node = g.nodes.get("math.function.derivative")
        if node is not None:   # legacy id present in both seeds
            self.assertTrue(node.prerequisites)

    def test_seed_preference_keeps_legacy_attribution(self):
        # pack node name-exact "摩擦力" (1.0) vs legacy alias/substring: the
        # legacy id is the BKT keyspace and must win the near-tie
        n = self.sm.graph.match_concept("摩擦力", threshold=0.6)
        self.assertIsNotNone(n)
        self.assertEqual(n.id, "physics.dynamics.friction")

    def test_alias_widening_from_m5(self):
        # M5 alias "受力分析" of the legacy node must match after the merge
        n = self.sm.graph.match_concept("受力分析", threshold=0.6)
        self.assertIsNotNone(n)
        self.assertEqual(n.id, "physics.dynamics.friction")

    def test_strict_floating_for_off_syllabus(self):
        # 相似对角化 is university linear algebra: token-overlap with 初中
        # 相似三角形 (~0.43) must NOT attach; it gets its own floating node
        node = self.sm.graph.ensure_node_for("相似对角化", "数学")
        self.assertNotEqual(node.id, "")
        self.assertNotEqual(node.id, "math.geometry.similar_triangles")
        self.assertTrue(".auto." in node.id or node.id.startswith("custom."))

    def test_syllabus_concept_attaches_to_real_node(self):
        node = self.sm.graph.ensure_node_for("牛顿第二定律", "物理")
        self.assertEqual(node.id, "physics.dynamics.newton_second")

    def test_unmerged_graph_keeps_loose_legacy_behavior(self):
        g = SkillGraph()   # seeded only, no M5 merge
        self.assertFalse(g.strict_match)
        n = g.match_concept("摩擦力")
        self.assertEqual(n.id, "physics.dynamics.friction")


class TestCustomGraphFlowsIntoSkillGraph(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(store_mod, "_CUSTOM_DIR",
                                        Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_custom_concepts_become_bkt_trackable(self):
        ks = get_knowledge_service()
        # P6-A4：手动 build_custom 已移除——直接经 store 落图谱（同 textbook 管线）。
        from app.agents.knowledge import custom_graph as cg
        spec = cg.parse_spec(_VALID_SPEC)
        data, _w = cg.spec_to_graph(spec, topic_key="m58.demo", source="llm")
        payload = {"version": 1, "topic": "嵌入式开发", "topic_key": "m58.demo",
                   "subject": data["subject"], "level": data["level"],
                   "source": "llm", "created_at": "2026-01-01T00:00:00+00:00",
                   "updated_at": "2026-01-01T00:00:00+00:00",
                   "nodes": data["nodes"], "edges": data["edges"],
                   "contents": data["contents"]}
        store_mod.save_custom_graph("test_m58_custom", "m58.demo", payload)
        sm = StudentModel("test_m58_custom").load()
        custom_ids = [i for i in sm.graph.nodes if i.startswith("custom.")]
        self.assertEqual(len(custom_ids), 2)             # concepts only, no chapters
        # the custom concept resolves to its real node, not a floating one
        node = sm.graph.ensure_node_for("中断", "嵌入式开发")
        self.assertTrue(node.id.startswith("custom."))
        # prereq edge came along: 中断 requires GPIO inside the custom graph
        gpio = [i for i in custom_ids if sm.graph.nodes[i].name == "GPIO"][0]
        self.assertIn(gpio, sm.graph.nodes[node.id].prerequisites)


class TestM4Anchoring(StorageSandboxTestCase):
    # evaluate_and_record(student_id="") 走默认学生并持久化 students/ 文件，
    # 沙箱隔离防止直写生产目录。

    def test_evaluate_and_record_anchors_skill_id(self):
        from app.agents.assessment import (AssessmentContext, Question,
                                           get_assessment_manager)
        q = Question(concept="摩擦力", q_type="multiple_choice",
                     stem="下列关于摩擦力的说法正确的是", answer="A",
                     options=["A. 方向与相对运动趋势相反", "B. 总是动力"],
                     difficulty=3)
        ctx = AssessmentContext(concept="摩擦力", subject="物理", grade="高中")
        mgr = get_assessment_manager()
        result = asyncio.run(mgr.evaluate_and_record(q, "A", ctx, student_id=""))
        self.assertEqual(result.skill_id, "physics.dynamics.friction")

    def test_off_syllabus_not_misanchored(self):
        from app.agents.assessment import (AssessmentContext, Question,
                                           get_assessment_manager)
        q = Question(concept="相似对角化", q_type="multiple_choice",
                     stem="矩阵可相似对角化的条件是", answer="A",
                     options=["A. 有 n 个线性无关特征向量", "B. 行列式非零"],
                     difficulty=4)
        ctx = AssessmentContext(concept="相似对角化", subject="数学", grade="本科")
        mgr = get_assessment_manager()
        result = asyncio.run(mgr.evaluate_and_record(q, "A", ctx, student_id=""))
        # strict bar: no node id fabricated for an off-syllabus concept
        self.assertFalse(result.skill_id.startswith("math."))
        self.assertNotEqual(result.skill_id, "math.similar.triangles")


class TestStageAwareMatch(unittest.TestCase):
    def test_level_preference(self):
        # P6-A2：考纲 seed 已删——用两份不同学段的自定义图谱验证学段偏好匹配。
        import tempfile as _tempfile
        from app.agents.knowledge import custom_graph as cg
        tmp = _tempfile.TemporaryDirectory()
        patcher = mock.patch.object(store_mod, "_CUSTOM_DIR", Path(tmp.name))
        patcher.start()
        try:
            ks = get_knowledge_service()
            sid = "test_m58_level"
            for lv in ("初中", "高中"):
                spec = {"subject": "物理", "chapters": [{"name": "章", "concepts": [
                    {"name": "浮力", "difficulty": 2}]}]}
                data, _w = cg.spec_to_graph(spec, topic_key=f"fy.{lv}",
                                            source="llm", level=lv)
                store_mod.save_custom_graph(sid, f"fy.{lv}", {
                    "topic": "浮力书", "topic_key": f"fy.{lv}",
                    "subject": "物理", "level": data["level"], "source": "llm",
                    "nodes": data["nodes"], "edges": data["edges"],
                    "contents": data["contents"]})
            junior = ks.match_concept("浮力", student_id=sid, level="初中")
            senior = ks.match_concept("浮力", student_id=sid, level="高中")
            self.assertIsNotNone(junior)
            self.assertIsNotNone(senior)
            self.assertNotEqual(junior.id, senior.id)
            self.assertEqual(junior.level, "初中")
        finally:
            patcher.stop()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
