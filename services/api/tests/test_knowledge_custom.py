"""Unit tests for M5.7 custom knowledge graphs.

Covers the deterministic spec->graph conversion (id namespacing, prereq
resolution, cycle safety, strict seed anchors, caps), store path safety,
the per-student merged graph view, and delete semantics.

P6-A4: manual build/regenerate/rollback were removed (graphs come from
textbooks now); tests seed graphs directly via store.save_custom_graph.
All persistence is redirected to a temp dir by patching store._CUSTOM_DIR.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.agents.knowledge import KnowledgeGraph, KnowledgeService
from app.agents.knowledge import custom_graph as cg
from app.agents.knowledge import store as store_mod

STUDENT = "test_student_m57"

_VALID_SPEC = json.dumps({
    "subject": "嵌入式开发",
    "chapters": [
        {"name": "C语言基础", "concepts": [
            {"name": "指针", "difficulty": 3, "description": "内存地址操作",
             "aliases": ["pointer"], "prerequisites": [], "definition": "存储内存地址的变量",
             "example": "int *p = &x;"},
            {"name": "结构体", "difficulty": 3, "description": "复合数据类型",
             "prerequisites": ["指针"]},
        ]},
        {"name": "单片机", "concepts": [
            {"name": "GPIO", "difficulty": 2, "description": "通用输入输出",
             "prerequisites": ["结构体"], "related": ["指针"]},
            {"name": "中断", "difficulty": 4, "description": "异步事件处理",
             "prerequisites": ["GPIO", "不存在的前置"]},
        ]},
    ],
}, ensure_ascii=False)


def _tmp_store():
    """Redirect the custom-graph store into a temp dir (context manager)."""
    tmp = tempfile.TemporaryDirectory()
    p = mock.patch.object(store_mod, "_CUSTOM_DIR", Path(tmp.name))
    p.start()
    return tmp, p


class TestTopicKey(unittest.TestCase):
    def test_deterministic_and_insensitive(self):
        self.assertEqual(cg.topic_key("嵌入式开发"), cg.topic_key("嵌入式开发"))
        self.assertEqual(cg.topic_key(" 嵌入式开发 "), cg.topic_key("嵌入式开发"))
        self.assertEqual(cg.topic_key("Calculus"), cg.topic_key("calculus"))

    def test_distinct_topics_differ(self):
        self.assertNotEqual(cg.topic_key("嵌入式开发"), cg.topic_key("微积分"))

    def test_filesystem_safe(self):
        key = cg.topic_key("C++/嵌入式 (开发)!")
        self.assertNotIn("/", key)
        self.assertNotIn("..", key)
        self.assertTrue(key)


class TestSpecToGraph(unittest.TestCase):
    def test_ids_namespaced_and_kinds(self):
        spec = cg.parse_spec(_VALID_SPEC)
        data, _w = cg.spec_to_graph(spec, topic_key="k1.abc123", source="llm")
        ids = {n["id"] for n in data["nodes"]}
        self.assertTrue(all(i.startswith("custom.k1.abc123.") for i in ids))
        kinds = {n["id"]: n["kind"] for n in data["nodes"]}
        by_name = {n["name"]: n for n in data["nodes"]}
        self.assertEqual(by_name["C语言基础"]["kind"], "chapter")
        self.assertEqual(by_name["指针"]["kind"], "concept")
        self.assertIn(by_name["指针"]["id"], kinds)
        self.assertEqual(data["concept_count"], 4)
        self.assertTrue(all(n["level"] == cg.CUSTOM_LEVEL for n in data["nodes"]))
        self.assertTrue(all(n["origin"] == "llm" for n in data["nodes"]))

    def test_edges_prereq_partof_related(self):
        spec = cg.parse_spec(_VALID_SPEC)
        data, warnings = cg.spec_to_graph(spec, topic_key="k2.abc123", source="llm")
        edges = {(e["source"], e["target"], e["type"]) for e in data["edges"]}
        by_name = {n["name"]: n["id"] for n in data["nodes"]}
        c1, c2, c3, c4 = (by_name[n] for n in ("指针", "结构体", "GPIO", "中断"))
        self.assertIn((c1, c2, "prerequisite"), edges)
        self.assertIn((c2, c3, "prerequisite"), edges)
        self.assertIn((c3, by_name["单片机"], "part_of"), edges)
        self.assertIn((c3, c1, "related"), edges)
        self.assertFalse(any(src == c4 and dst.startswith("custom") and dst not in
                             {c1, c2, c3, by_name["单片机"]}
                             for src, dst, _ in edges))
        self.assertTrue(any("未知前置" in w for w in warnings))

    def test_cycle_edge_dropped(self):
        spec = {"chapters": [{"name": "x", "concepts": [
            {"name": "A", "prerequisites": ["B"]},
            {"name": "B", "prerequisites": ["A"]},
        ]}]}
        data, warnings = cg.spec_to_graph(spec, topic_key="k3.abc123", source="llm")
        prereqs = [(e["source"], e["target"]) for e in data["edges"]
                   if e["type"] == "prerequisite"]
        self.assertEqual(len(prereqs), 1)   # one direction accepted, other rejected
        self.assertTrue(any("环" in w for w in warnings))

    def test_anchor_to_seed_is_strict(self):
        base = KnowledgeGraph(nodes=[
            {"id": "math.calculus.derivative", "name": "导数", "subject": "数学"},
            {"id": "math.geometry_basics.angle", "name": "角", "subject": "数学"},
        ])
        spec = {"chapters": [{"name": "x", "concepts": [
            {"name": "导数"},                      # exact -> anchored
            {"name": "相似对角化"},                # loose-only -> NOT anchored
        ]}]}
        data, _w = cg.spec_to_graph(spec, topic_key="k4.abc123", source="llm",
                                    base_graph=base)
        anchors = [(e["source"], e["target"]) for e in data["edges"]
                   if e["type"] == "related" and not
                   e["target"].startswith("custom.")]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0][1], "math.calculus.derivative")

    def test_caps(self):
        concepts = [{"name": f"概念{i}"} for i in range(30)]
        spec = {"chapters": [{"name": f"章{j}", "concepts": concepts}
                             for j in range(20)]}
        data, warnings = cg.spec_to_graph(spec, topic_key="k5.abc123", source="llm")
        chapters = [n for n in data["nodes"] if n["kind"] == "chapter"]
        self.assertLessEqual(len(chapters), cg.MAX_CHAPTERS)
        self.assertLessEqual(data["concept_count"], cg.MAX_CONCEPTS)

    def test_contents_attached(self):
        spec = cg.parse_spec(_VALID_SPEC)
        data, _w = cg.spec_to_graph(spec, topic_key="k6.abc123", source="llm")
        by_id = {c["concept_id"]: c for c in data["contents"]}
        pointer_id = next(n["id"] for n in data["nodes"] if n.get("name") == "指针")
        self.assertIn(pointer_id, by_id)
        self.assertTrue(by_id[pointer_id]["definition"])

    def test_parse_spec_rejects_garbage(self):
        self.assertIsNone(cg.parse_spec("not json at all"))
        self.assertIsNone(cg.parse_spec('{"chapters": []}'))
        self.assertIsNone(cg.parse_spec('{"other": 1}'))


def _seed_payload(topic_key: str = "k1.abc123") -> dict:
    """Build a store payload directly (no LLM, no manager write surface)."""
    spec = cg.parse_spec(_VALID_SPEC)
    data, _w = cg.spec_to_graph(spec, topic_key=topic_key, source="llm")
    return {"version": 1, "topic": "嵌入式开发", "topic_key": topic_key,
            "subject": data["subject"], "level": data["level"], "source": "llm",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "nodes": data["nodes"], "edges": data["edges"],
            "contents": data["contents"]}


class TestDeleteCustom(unittest.TestCase):
    """delete_custom survives (cleanup of legacy graphs); seeded via store."""

    @classmethod
    def setUpClass(cls):
        cls.service = KnowledgeService()

    def setUp(self):
        self._tmp, self._patch = _tmp_store()
        store_mod.save_custom_graph(STUDENT, "k1.abc123", _seed_payload())
        self.key = "k1.abc123"

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_delete_archives_then_removes(self):
        r = self.service.delete_custom(student_id=STUDENT, topic_key=self.key)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(self.service.list_custom(student_id=STUDENT), [])
        r2 = self.service.delete_custom(student_id=STUDENT, topic_key=self.key)
        self.assertEqual(r2["status"], "not_found")
        # archive survives deletion
        self.assertEqual(store_mod.archive_count(STUDENT, self.key), 1)


class TestMergedView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = KnowledgeService()

    def setUp(self):
        self._tmp, self._patch = _tmp_store()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_graph_for_merges_custom_and_caches(self):
        base_size = self.service.graph.size
        self.assertIs(self.service.graph_for(STUDENT), self.service.graph)
        store_mod.save_custom_graph(STUDENT, "k1.abc123", _seed_payload())
        merged = self.service.graph_for(STUDENT)
        self.assertIsNot(merged, self.service.graph)
        self.assertEqual(merged.size[0], base_size[0] + 6)   # 4 concepts + 2 chapters
        cids = [i for i in merged.nodes if i.startswith("custom.")]
        self.assertEqual(len(cids), 6)
        # base graph untouched (no leakage into other students)
        self.assertFalse(any(i.startswith("custom.") for i in self.service.graph.nodes))
        self.assertIs(self.service.graph_for("other_student"), self.service.graph)
        # cache: same stamp returns the same object
        self.assertIs(self.service.graph_for(STUDENT), merged)

    def test_match_and_retrieve_see_custom_nodes(self):
        store_mod.save_custom_graph(STUDENT, "k1.abc123", _seed_payload())
        n = self.service.match_concept("中断", student_id=STUDENT)
        self.assertIsNotNone(n)
        self.assertTrue(n.id.startswith("custom."))
        self.assertIsNone(self.service.match_concept("中断",
                                                     student_id="other_student"))
        hits = self.service.retrieve("单片机 GPIO", student_id=STUDENT)
        self.assertTrue(any(h["concept_id"].startswith("custom.") for h in hits))

    def test_store_path_traversal_guarded(self):
        self.assertIsNone(store_mod.load_custom_graph("../evil", "x"))
        self.assertIsNone(store_mod.load_custom_graph(STUDENT, "../evil"))
        self.assertFalse(store_mod.save_custom_graph("..", "x", {"nodes": []}))
        self.assertEqual(store_mod.list_custom_graphs(".."), [])


if __name__ == "__main__":
    unittest.main()
