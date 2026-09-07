"""Scoped textbook graph API: group/volume/overview/chapter/search and 404 isolation."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from app.api.v1 import knowledge as api
from app.agents.knowledge import store as kg_store
from app.core import textbook as tb_store


class TestKnowledgeGraphViews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
                        patch.object(kg_store, "_KG_DIR", root / "knowledge"),
                        patch.object(kg_store, "_CUSTOM_DIR", root / "knowledge" / "custom"),
                        patch.object(api._kn, "is_enabled", return_value=True),
                        patch.object(api, "_mastery_overlay", return_value={})]
        for item in self.patches: item.start()
        self.group = tb_store.create_group("s", file_ids=["f1", "f2"], title="大学物理", subject="物理", level="本科")
        topic = self.group["topic_key"]
        nodes = [
            {"id": f"custom.{topic}.ch.a", "name": "运动学", "subject": "物理", "level": "本科", "kind": "chapter", "metadata": {"file_id": "f1", "chapter_key": "a"}},
            {"id": f"custom.{topic}.ch.b", "name": "热学", "subject": "物理", "level": "本科", "kind": "chapter", "metadata": {"file_id": "f2", "chapter_key": "b"}},
            {"id": f"custom.{topic}.c.shared", "name": "能量", "subject": "物理", "level": "本科", "kind": "concept", "aliases": [], "metadata": {"file_ids": ["f1", "f2"]}},
            {"id": f"custom.{topic}.c.temp", "name": "温度", "subject": "物理", "level": "本科", "kind": "concept", "aliases": []},
        ]
        edges = [
            {"source": nodes[2]["id"], "target": nodes[0]["id"], "type": "part_of"},
            {"source": nodes[2]["id"], "target": nodes[1]["id"], "type": "part_of"},
            {"source": nodes[3]["id"], "target": nodes[1]["id"], "type": "part_of"},
        ]
        kg_store.save_custom_graph("s", topic, {"topic": "大学物理", "topic_key": topic,
            "nodes": nodes, "edges": edges, "contents": [], "coverage": [
                {"file_id": "f1", "status": "included"}, {"file_id": "f2", "status": "included"}]})
        self.nodes = nodes

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.tmp.cleanup()

    def call(self, **kw):
        defaults = dict(textbook_id=self.group["id"], file_id="", level="", subject="",
                        view="full", chapter_id="", q="", student_id="s")
        defaults.update(kw)
        return api.knowledge_graph(**defaults)

    def test_volume_isolated_and_shared_concept_retained(self):
        out = self.call(file_id="f1")
        self.assertEqual({n["name"] for n in out["nodes"]}, {"运动学", "能量"})
        self.assertEqual([x["file_id"] for x in out["coverage"]], ["f1"])

    def test_overview_chapter_and_search(self):
        overview = self.call(view="overview")
        self.assertEqual({n["name"] for n in overview["nodes"]}, {"运动学", "热学"})
        self.assertEqual(overview["edges"], [])
        chapter = self.call(view="chapter", chapter_id=self.nodes[1]["id"])
        self.assertEqual({n["name"] for n in chapter["nodes"]}, {"热学", "能量", "温度"})
        search = self.call(view="search", q="温度")
        self.assertEqual({n["name"] for n in search["nodes"]}, {"热学", "温度"})

    def test_invalid_volume_and_chapter_are_404(self):
        with self.assertRaises(HTTPException) as cm:
            self.call(file_id="foreign")
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(HTTPException) as cm:
            self.call(view="chapter", chapter_id="missing")
        self.assertEqual(cm.exception.status_code, 404)

    def test_global_view_contains_multiple_textbooks_and_subject_filters(self):
        from app.agents.knowledge.schema import KnowledgeNode, KnowledgeEdge, EdgeType
        math_group = tb_store.create_group(
            "s", file_ids=["m1"], title="线性代数", subject="数学", level="本科")
        physics_prefix = f"custom.{self.group['topic_key']}."
        math_prefix = f"custom.{math_group['topic_key']}."
        physics = KnowledgeNode(
            id=physics_prefix + "c.motion", name="运动", subject="物理", level="本科")
        math = KnowledgeNode(
            id=math_prefix + "c.matrix", name="矩阵", subject="数学", level="本科")
        graph = SimpleNamespace(
            nodes={physics.id: physics, math.id: math},
            edges=[KnowledgeEdge(source=math.id, target=physics.id,
                                 type=EdgeType.RELATED, provenance="test")],
        )
        service = SimpleNamespace(graph_for=lambda _sid: graph)
        with patch.object(api._kn, "get_knowledge_service", return_value=service):
            global_out = api.knowledge_graph(
                textbook_id="", file_id="", level="", subject="", view="full",
                chapter_id="", q="", student_id="s")
            math_out = api.knowledge_graph(
                textbook_id="", file_id="", level="本科", subject="数学", view="full",
                chapter_id="", q="", student_id="s")
            physics_out = api.knowledge_graph(
                textbook_id="", file_id="", level="本科", subject="物理", view="full",
                chapter_id="", q="", student_id="s")
        self.assertEqual({n["name"] for n in global_out["nodes"]}, {"运动", "矩阵"})
        self.assertEqual({n["name"] for n in math_out["nodes"]}, {"矩阵"})
        self.assertEqual({n["name"] for n in physics_out["nodes"]}, {"运动"})


if __name__ == "__main__": unittest.main()
