# -*- coding: utf-8 -*-
"""章→节(课/篇目)→关键概念 三级图谱结构回归。

覆盖 2026-08-23「语文必修搜不到沁园春江畔/我与小城」缺陷的修复面：
  1. locate_sections：压缩归一（去间隔号/编号）+ 标题位优先定位篇目锚点。
  2. spec_to_graph：节节点（kind=section）+ PART_OF 概念→节→章 链。
  3. normalize_textbook_spec：节名保留间隔号 + 概念 section 引用改写。
  4. graph_quality：概念挂节不再误判「指向不存在章节」。
  5. 证据门：标题类查询（含间隔号差异/口语修饰）不再被整批清零。
  6. 概念索引：节条目（kind=section）按压缩匹配落 chunk_ids。
  7. 教材 TOC 嵌套解析（_llm_extract_toc）。
"""
from __future__ import annotations
import asyncio
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.agents.knowledge import custom_graph as cg
from app.agents.knowledge import textbook_builder as tb
from app.agents.knowledge.taxonomy_normalizer import (
    graph_quality, normalize_section_name, normalize_textbook_spec,
)


CHINESE_TEXT = (
    "目  录\n第一单元 青春意象\n1 沁园春·江畔 2\n2 我与小城（节选） 8\n"
    "\f第一单元 青春意象\n单元导语：本单元学习沁园春江畔等作品。\n"
    "\f1 沁园春·江畔\n独立寒秋，湘江北去，沙洲头。\n曾记否，到中流击水，浪遏飞舟？\n"
    "\f2 我与小城（节选）\n我在好几篇小说中都提到过一座建筑：地坛。"
)


class TestSectionHelpers(unittest.TestCase):
    def test_compact_title_strips_interpunct_and_numbering(self):
        self.assertEqual(cg.compact_title("1 沁园春·江畔"), "沁园春江畔")
        self.assertEqual(cg.compact_title("第1课 沁园春·江畔"), "沁园春江畔")
        self.assertEqual(cg.compact_title("沁园春·江畔"), "沁园春江畔")

    def test_section_aliases_include_compact_form(self):
        aliases = cg.section_aliases("1 沁园春·江畔")
        self.assertIn("沁园春江畔", aliases)

    def test_normalize_section_name_keeps_interpunct(self):
        # 章名归一化按 · 切分取末段（毁篇目名）；节名必须保留完整间隔号。
        self.assertEqual(normalize_section_name("1 沁园春·江畔 3"), "1 沁园春·江畔")

    def test_locate_sections_prefers_heading_position(self):
        # 导语中行提及「沁园春江畔」不能抢锚：标题位（编号开头行）优先。
        slices = tb.locate_chapters(
            CHINESE_TEXT, ["第一单元 青春意象"],
            {"第一单元 青春意象": ["1 沁园春·江畔", "2 我与小城（节选）"]})
        self.assertEqual(len(slices), 1)
        secs = slices[0][3]
        self.assertEqual([s["name"] for s in secs],
                         ["1 沁园春·江畔", "2 我与小城（节选）"])
        # 第3页起（\f 分页：p1 目录、p2 单元导语、p3 沁园春、p4 我与小城）
        self.assertEqual(secs[0]["page_range"][0], 3)

    def test_attach_concepts_to_sections_unique_hit(self):
        slices = tb.locate_chapters(
            CHINESE_TEXT, ["第一单元 青春意象"],
            {"第一单元 青春意象": ["1 沁园春·江畔", "2 我与小城（节选）"]})
        secs = slices[0][3]
        concepts = [{"name": "地坛"}, {"name": "意象"}]  # 意象两节都有 → 留章级
        tb._attach_concepts_to_sections(concepts, slices[0][1], secs)
        self.assertEqual(concepts[0].get("section"), "2 我与小城（节选）")
        self.assertNotIn("section", concepts[1])


class TestSpecToGraphSections(unittest.TestCase):
    def _spec(self):
        return {"subject": "语文", "level": "高中", "chapters": [{
            "name": "第一单元 青春意象", "chapter_key": "k1",
            "metadata": {"volume_id": "f1", "file_id": "f1", "chapter_order": 1},
            "sections": [
                {"name": "1 沁园春·江畔", "page_range": [3, 4], "section_key": "s1"},
                {"name": "2 我与小城（节选）", "page_range": [5, 6], "section_key": "s2"},
            ],
            "concepts": [
                {"name": "词牌", "section": "1 沁园春·江畔"},
                {"name": "意象"},
            ],
        }], "page_ranges": {"k1": [2, 10]}}

    def test_section_nodes_and_part_of_chain(self):
        data, warnings = cg.spec_to_graph(
            self._spec(), topic_key="tb-x", source="textbook:tb-x",
            max_chapters=None, max_concepts=None, max_concepts_per_chapter=None,
            level="高中")
        self.assertEqual(warnings, [])
        sections = [n for n in data["nodes"] if n["kind"] == "section"]
        self.assertEqual(len(sections), 2)
        sec = next(n for n in sections if "沁园春" in n["name"])
        self.assertIn("沁园春江畔", sec["aliases"])  # 无间隔号查询可精确命中
        self.assertEqual(sec["metadata"]["page_range"], [3, 4])
        part_of = {e["source"]: e["target"] for e in data["edges"]
                   if e["type"] == "part_of"}
        # 词牌→沁园春·江畔（节）→第一单元（章）；意象直接挂章。
        cid = next(n["id"] for n in data["nodes"] if n["name"] == "词牌")
        self.assertEqual(part_of[cid], sec["id"])
        self.assertEqual(part_of[sec["id"]].rsplit(".", 1)[-1], "k1")

    def test_graph_quality_accepts_section_layer(self):
        data, _ = cg.spec_to_graph(
            self._spec(), topic_key="tb-x", source="textbook:tb-x",
            max_chapters=None, max_concepts=None, max_concepts_per_chapter=None,
            level="高中")
        quality = graph_quality({"nodes": data["nodes"], "edges": data["edges"]})
        self.assertTrue(quality["ok"], quality["errors"])
        self.assertEqual(quality["section_count"], 2)

    def test_normalize_carries_sections_and_remaps_concepts(self):
        raw = {"subject": "语文", "level": "高中", "chapters": [{
            "name": "第一单元 青春意象",
            "sections": [{"name": "1 沁园春·江畔 3", "page_range": [3, 4]},
                         {"name": "2 我与小城（节选）", "page_range": [5, 6]}],
            # 概念挂节引用的是抽取期原始名（含目录页码尾巴），归一化后须改写
            "concepts": [{"name": "词牌", "section": "1 沁园春·江畔 3"}]}],
            "page_ranges": {"第一单元 青春意象": [2, 10]}}
        norm, warnings = normalize_textbook_spec(
            raw, textbook_title="示例语文必修", volume_id="f1",
            volume_title="语文必修上.pdf")
        self.assertEqual(warnings, [])
        ch = norm["chapters"][0]
        self.assertEqual(ch["metadata"]["page_range"], [2, 10])
        # NFKC 把全角括号归一为半角；间隔号保留（篇目名完整性）
        self.assertEqual([s["name"] for s in ch["sections"]],
                         ["1 沁园春·江畔", "2 我与小城(节选)"])
        self.assertIn("·", ch["sections"][0]["name"])
        self.assertEqual(ch["sections"][0]["page_range"], [3, 4])
        self.assertTrue(all(s.get("section_key") for s in ch["sections"]))
        self.assertIn(ch["sections"][0]["section_key"], norm["section_ranges"])
        # 概念的节引用改写到归一化后的显示名
        self.assertEqual(ch["concepts"][0]["section"], "1 沁园春·江畔")


class TestTocNestedParse(unittest.TestCase):
    def test_llm_extract_toc_parses_nested_chapters(self):
        class TocLLM:
            async def complete(self, messages, **kwargs):
                return ('{"chapters":[{"name":"第一单元 青春意象",'
                        '"sections":["1 沁园春·江畔","2 我与小城（节选）"]},'
                        '{"name":"第二单元 劳动价值","sections":[]}]}', None)

        entries = asyncio.run(tb._llm_extract_toc("...", TocLLM()))
        self.assertEqual(entries[0]["name"], "第一单元 青春意象")
        self.assertEqual(entries[0]["sections"][0], "1 沁园春·江畔")
        self.assertEqual(entries[1]["sections"], [])

    def test_llm_extract_toc_accepts_legacy_flat_list(self):
        class FlatLLM:
            async def complete(self, messages, **kwargs):
                return '{"chapters":["第一章 导数","第二章 积分"]}', None

        entries = asyncio.run(tb._llm_extract_toc("...", FlatLLM()))
        self.assertEqual([e["name"] for e in entries], ["第一章 导数", "第二章 积分"])
        self.assertEqual(entries[0]["sections"], [])


class TestEvidenceGateTitleQueries(unittest.TestCase):
    """标题类查询不再被间隔号/修饰词击穿（对话检索核心回归）。"""

    def _candidates(self):
        return [
            {"chunk_id": "f1#0", "file_id": "f1", "source": "语文必修上.pdf",
             "index": 0, "page": 3, "bm25_score": 3.0,
             "text": "1 沁园春·江畔\n独立寒秋，湘江北去，沙洲头。"},
            {"chunk_id": "f1#1", "file_id": "f1", "source": "语文必修上.pdf",
             "index": 1, "page": 9, "bm25_score": 2.0,
             "text": "2 我与小城（节选）\n我在好几篇小说中都提到过一座建筑：地坛。"},
        ]

    def test_exact_title_without_interpunct_matches(self):
        gate = tb_apply("沁园春江畔", self._candidates())
        self.assertFalse(gate.no_hit)
        self.assertTrue(any("沁园春" in r["text"] for r in gate.selected))

    def test_second_title_matches(self):
        gate = tb_apply("我与小城", self._candidates())
        self.assertFalse(gate.no_hit)
        self.assertEqual(len(gate.selected), 1)

    def test_decorated_title_query_not_zeroed(self):
        # 修复前：噪声词（教材/哪一页）稀释词项覆盖 → weak_term_coverage 清零。
        gate = tb_apply("沁园春江畔在教材哪一页", self._candidates())
        self.assertFalse(gate.no_hit, gate.drop_reasons)

    def test_colloquial_title_query_not_zeroed(self):
        gate = tb_apply("帮我讲讲课文沁园春江畔", self._candidates())
        self.assertFalse(gate.no_hit, gate.drop_reasons)

    def test_irrelevant_query_still_rejected(self):
        gate = tb_apply("量子纠缠的实验验证", self._candidates())
        self.assertTrue(gate.no_hit)


def tb_apply(query, candidates):
    from app.core.evidence_gate import apply_evidence_gate
    return apply_evidence_gate(query, candidates, 4)


class TestChunkerSectionBoundaries(unittest.TestCase):
    """语文课目行是硬边界：块不跨课，section_path 携带 单元→课 层级。"""

    def test_lesson_heading_is_hard_boundary(self):
        from app.core.structured_chunker import chunk_text_v2, _is_heading
        self.assertTrue(_is_heading("第一单元 青春意象"))
        self.assertTrue(_is_heading("1 沁园春·江畔"))
        self.assertTrue(_is_heading("第3课 湖畔夜色"))
        self.assertFalse(_is_heading("独立寒秋，湘江北去。"))
        text = ("第一单元 青春意象\n"
                "1 沁园春·江畔\n独立寒秋，湘江北去，沙洲头。" + "漫江碧流。".ljust(900)
                + "\n第2课 我与小城（节选）\n我在好几篇小说中都提到过一座建筑：地坛。" + "地坛的景物描写。".ljust(900))
        chunks = chunk_text_v2(text, source="语文必修上.pdf", file_id="f1")
        # 沁园春与地坛的正文绝不混入同一块
        by_text = [(c.text, c) for c in chunks]
        for text_body, c in by_text:
            if "独立寒秋" in text_body:
                self.assertNotIn("我与小城", text_body)
            if "我在好几篇小说" in text_body:
                self.assertNotIn("沁园春", text_body)
        # section_path 含课层
        qin = next(c for c in chunks if "独立寒秋" in c.text)
        self.assertTrue(any("沁园春" in str(p) for p in qin.metadata.get("section_path", [])))


class TestSectionConceptIndex(unittest.TestCase):
    """概念索引含节条目（kind=section）：篇目查询经 _concept_boost 直达。"""

    def test_section_index_entry_matching(self):
        from app.core.structured_chunker import chunk_text_v2
        # \f 分页：物理页 1 = 沁园春、页 2 = 我与小城；[页码=N] 提供印刷页码
        text = ("[页码=3]\n1 沁园春·江畔\n独立寒秋，湘江北去，沙洲头。看万山红遍。\n"
                "\f[页码=9]\n2 我与小城（节选）\n我在好几篇小说中都提到过一座建筑：地坛。")
        chunks = chunk_text_v2(text, source="语文必修上.pdf", file_id="lib1")
        pool = [c for c in chunks if c.page in (1, 2)]
        self.assertTrue(pool)
        hits = tb._pool_section_hits(pool, tb._section_match_terms("1 沁园春·江畔"))
        self.assertTrue(hits, "篇目压缩匹配应命中其正文 chunk")
        hit_texts = [c.text for c in pool if c.chunk_id in hits]
        self.assertTrue(all("独立寒秋" in t or "沁园春" in t for t in hit_texts))


if __name__ == "__main__":
    unittest.main()
