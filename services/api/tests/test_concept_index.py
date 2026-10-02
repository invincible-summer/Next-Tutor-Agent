"""P6-C2 概念级 RAG 预索引测试：

- 构建期：_save_concept_index 按章节页码范围产出概念→chunk_ids 倒排，
  存 store（.chunks.json），随图谱删除联动。
- 检索期：knowledge_search 命中教材图谱概念时，概念章节内的 chunks 优先返回
  （concept 标记），未选入教材/无索引时回落普通检索。
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

import app.core.library as lib_mod
import app.core.textbook as tb_mod
import app.agents.knowledge.store as kgs
from app.core.library import load_library, save_library
from app.core.knowledge_store import KnowledgeStore


class _TmpDirs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig = (lib_mod._LIBRARY_DIR, tb_mod._LIBRARY_DIR,
                      kgs._CUSTOM_DIR, kgs._KG_DIR)
        lib_mod._LIBRARY_DIR = root / "library"
        tb_mod._LIBRARY_DIR = root / "library"
        kgs._CUSTOM_DIR = root / "knowledge" / "custom"
        kgs._KG_DIR = root / "knowledge"

    def tearDown(self):
        (lib_mod._LIBRARY_DIR, tb_mod._LIBRARY_DIR,
         kgs._CUSTOM_DIR, kgs._KG_DIR) = self._orig
        self._tmp.cleanup()


SID = "stu_ci"


def _seed_book() -> tuple[dict, str]:
    """落一本两章教材：第1章（p1-2）讲导数，第2章（p3-4）讲积分。"""
    pages = ["第1章 导数。导数是瞬时变化率。导数的几何意义是切线斜率。",
             "导数的计算：幂函数求导法则。",
             "第2章 积分。积分是曲线下面积。",
             "定积分与不定积分的关系。"]
    text = "\f".join(pages)
    lib = load_library(SID)
    meta = lib.add_file("", "微积分.txt", text, file_id="fb1")
    meta["kind"] = "textbook"
    save_library(lib)
    tb = tb_mod.create_textbook(SID, file_id="fb1", title="微积分")
    return tb, text


def _build_index(tb):
    """经 builder 的 spec_to_graph + _save_concept_index 产出图谱与索引。"""
    from app.agents.knowledge import custom_graph as cg
    from app.agents.knowledge.textbook_builder import _save_concept_index
    spec = {
        "subject": "数学", "level": "本科",
        "chapters": [
            {"name": "第1章 导数", "concepts": [
                {"name": "导数", "difficulty": 3, "aliases": ["微商"]}]},
            {"name": "第2章 积分", "concepts": [
                {"name": "积分", "difficulty": 3}]},
        ],
        "page_ranges": {"第1章 导数": [1, 2], "第2章 积分": [3, 4]},
    }
    data, _w = cg.spec_to_graph(spec, topic_key=tb["topic_key"],
                                source=f"textbook:{tb['file_id']}",
                                level="本科")
    payload = {"topic": "微积分", "topic_key": tb["topic_key"],
               "subject": "数学", "level": "本科",
               "source": f"textbook:{tb['file_id']}",
               "nodes": data["nodes"], "edges": data["edges"],
               "contents": data["contents"]}
    kgs.save_custom_graph(SID, tb["topic_key"], payload)
    _save_concept_index(SID, tb, spec, payload)
    return payload


class TestConceptChunkIndex(_TmpDirs):
    def test_index_built_per_chapter_range(self):
        tb, _text = _seed_book()
        _build_index(tb)
        idx = kgs.load_concept_chunks(SID, tb["topic_key"])
        self.assertIsNotNone(idx)
        concepts = idx["concepts"]
        # 两个概念各有索引；导数的 chunks 只来自第 1-2 页（章范围限定）
        deriv = next(v for v in concepts.values() if v["name"] == "导数")
        integ = next(v for v in concepts.values() if v["name"] == "积分")
        self.assertEqual(deriv["pages"], [1, 2])
        self.assertTrue(deriv["chunk_ids"])
        lib = load_library(SID)
        page_of = {c.chunk_id: c.page for c in lib.chunks_for("fb1")}
        self.assertTrue(all(page_of[cid] <= 2 for cid in deriv["chunk_ids"]))
        self.assertTrue(all(page_of[cid] >= 3 for cid in integ["chunk_ids"]))

    def test_index_deleted_with_graph(self):
        tb, _text = _seed_book()
        _build_index(tb)
        self.assertIsNotNone(kgs.load_concept_chunks(SID, tb["topic_key"]))
        kgs.delete_custom_graph(SID, tb["topic_key"])
        self.assertIsNone(kgs.load_concept_chunks(SID, tb["topic_key"]))

    def test_boost_prefers_concept_chunks(self):
        from app.agents.knowledge import manager as kn_manager
        tb, _text = _seed_book()
        _build_index(tb)
        kn_manager._INSTANCE = None
        lib = load_library(SID)
        store = KnowledgeStore()
        store.chunks = lib.chunks_for("fb1")
        from app.tools.knowledge_search import KnowledgeSearchTool
        tool = KnowledgeSearchTool(store, student_id=SID)
        resp = asyncio.run(tool.run(query="导数", top_k=4))
        self.assertEqual(resp.status, "success")
        first = resp.data["results"][0]
        self.assertEqual(first.get("concept"), "导数")
        self.assertLessEqual(first.get("page") or 99, 2)  # 命中第 1 章范围

    def test_no_index_falls_back(self):
        tb, _text = _seed_book()  # 不建索引
        _ = tb
        lib = load_library(SID)
        store = KnowledgeStore()
        store.chunks = lib.chunks_for("fb1")
        from app.tools.knowledge_search import KnowledgeSearchTool
        tool = KnowledgeSearchTool(store, student_id=SID)
        resp = asyncio.run(tool.run(query="导数", top_k=2))
        self.assertEqual(resp.status, "success")
        self.assertFalse(any("concept" in r for r in resp.data["results"]))


if __name__ == "__main__":
    unittest.main()
