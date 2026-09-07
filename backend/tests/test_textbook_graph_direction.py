"""知识谱系方向守卫与覆盖保障回归测试。

验收：
- spec_to_graph 方向守卫：双信号（更难 + 更靠后）可疑前置边反转；反转会成环
  则移除；同章/同难度/更早的难前置边不动（「用难工具定义简单概念」合法）。
- 覆盖：单章概念抽取失败重试后仍保留章结构（含节），不整章丢弃；
  _apply_volume_policy 保留无概念章；graph_quality 只计数不阻塞。
- 伪章：本章小结/习题/参考答案/趣闻等后置事项在归一化与 Tier1 质检两侧
  都被剔除；真实教学章名不受影响。
- 缓存：prompt 版本号变化使卷 spec 缓存失效（rag_graph 模式自动重抽）。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.agents.knowledge import custom_graph as cg
from app.agents.knowledge import textbook_builder as builder
from app.agents.knowledge.taxonomy_normalizer import (
    graph_quality, is_back_matter, is_teaching_chapter, normalize_textbook_spec,
)
from tests.storage_sandbox import StorageSandboxTestCase


def _edges(data: dict) -> list[tuple[str, str]]:
    return [(e["source"], e["target"]) for e in data["edges"]
            if e["type"] == "prerequisite"]


def _ids_by_name(data: dict) -> dict[str, str]:
    return {n["name"]: n["id"] for n in data["nodes"] if n["kind"] == "concept"}


class TestDirectionRepair(unittest.TestCase):
    """spec_to_graph 的前置边方向守卫（纯函数，无存储）。"""

    @staticmethod
    def _spec(chapters: list[dict]) -> dict:
        return {"subject": "数学", "level": "本科", "chapters": chapters}

    def test_suspicious_edge_is_reversed(self):
        # 第2章的难概念「进阶」被写成第1章「基础」的前置 → 反转为 基础→进阶。
        spec = self._spec([
            {"name": "第1章", "concepts": [
                {"name": "基础", "difficulty": 1, "prerequisites": ["进阶"]}]},
            {"name": "第2章", "concepts": [
                {"name": "进阶", "difficulty": 3, "prerequisites": []}]},
        ])
        data, warnings = cg.spec_to_graph(spec, topic_key="dir1", source="llm")
        ids = _ids_by_name(data)
        self.assertIn((ids["基础"], ids["进阶"]), _edges(data))
        self.assertNotIn((ids["进阶"], ids["基础"]), _edges(data))
        self.assertTrue(any("反转为" in w for w in warnings))

    def test_same_chapter_harder_prereq_kept(self):
        # 同章「难工具(d4)→简单对象(d2)」：位置相同，不触发守卫。
        spec = self._spec([
            {"name": "第1章", "concepts": [
                {"name": "难工具", "difficulty": 4},
                {"name": "简单对象", "difficulty": 2, "prerequisites": ["难工具"]}]},
        ])
        data, warnings = cg.spec_to_graph(spec, topic_key="dir2", source="llm")
        ids = _ids_by_name(data)
        self.assertIn((ids["难工具"], ids["简单对象"]), _edges(data))
        self.assertFalse(any("反转" in w for w in warnings))

    def test_earlier_harder_prereq_kept(self):
        # 跨章但 source 更早（第1章难工具 → 第2章简单对象）：合法，保留。
        spec = self._spec([
            {"name": "第1章", "concepts": [
                {"name": "难工具", "difficulty": 4}]},
            {"name": "第2章", "concepts": [
                {"name": "简单对象", "difficulty": 2, "prerequisites": ["难工具"]}]},
        ])
        data, _ = cg.spec_to_graph(spec, topic_key="dir3", source="llm")
        ids = _ids_by_name(data)
        self.assertIn((ids["难工具"], ids["简单对象"]), _edges(data))

    def test_equal_difficulty_later_prereq_kept(self):
        # 更靠后但同难度：单信号不处理。
        spec = self._spec([
            {"name": "第1章", "concepts": [
                {"name": "甲", "difficulty": 2, "prerequisites": ["乙"]}]},
            {"name": "第2章", "concepts": [
                {"name": "乙", "difficulty": 2}]},
        ])
        data, _ = cg.spec_to_graph(spec, topic_key="dir4", source="llm")
        ids = _ids_by_name(data)
        self.assertIn((ids["乙"], ids["甲"]), _edges(data))

    def test_flip_cycle_drops_edge(self):
        # S(d4,第2章)→T(d1,第1章) 可疑；另有 S→X→T 间接链，反转为 T→S 会
        # 成环 → 移除直连边，保留间接链（X 同难度不触发守卫）。
        spec = self._spec([
            {"name": "第1章", "concepts": [
                {"name": "T", "difficulty": 1, "prerequisites": ["S", "X"]}]},
            {"name": "第2章", "concepts": [
                {"name": "S", "difficulty": 4},
                {"name": "X", "difficulty": 1, "prerequisites": ["S"]}]},
        ])
        data, warnings = cg.spec_to_graph(spec, topic_key="dir5", source="llm")
        ids = _ids_by_name(data)
        edges = _edges(data)
        self.assertNotIn((ids["S"], ids["T"]), edges)
        self.assertIn((ids["S"], ids["X"]), edges)
        self.assertIn((ids["X"], ids["T"]), edges)
        self.assertTrue(any("成环，已移除" in w for w in warnings))


class TestFullSpecKeepsFailedChapter(StorageSandboxTestCase):
    """单章概念抽取失败：重试一次仍失败也保留章与节，不整章丢弃。"""

    def test_failed_chapter_stays_with_sections(self):
        from app.core import textbook as tb
        rec = tb.create_textbook("stu_dir", file_id="f1", title="覆盖教材")
        slices = [
            ("第1章", "第1章正文。基础概念。", (1, 2),
             [{"name": "1.1 节", "page_range": [1, 1]}]),
            ("第2章", "第2章正文。进阶内容。", (3, 4),
             [{"name": "2.1 节", "page_range": [3, 3]}]),
            ("第3章", "第3章正文。收尾。", (5, 6),
             [{"name": "3.1 节", "page_range": [5, 5]}]),
        ]

        class FlakyLLM:
            def __init__(self):
                self.chapter_calls = 0

            async def complete(self, messages, **kw):
                prompt = messages[0]["content"]
                if "核心知识点" in prompt:
                    self.chapter_calls += 1
                    if "第2章" in prompt:
                        return "不是 JSON", {}   # 第2章永远失败
                    return ('{"concepts": [{"name": "概念", "difficulty": 2}]}', {})
                if '"subject"' in prompt and '"level"' in prompt:
                    return '{"subject": "数学", "level": "本科"}', {}
                return "{}", {}

        async def fake_extract(text, raw, llm, volume_hint=""):
            return slices, "目录文本"

        llm = FlakyLLM()
        warnings: list[str] = []
        async def drive():
            with patch.object(builder, "extract_chapters",
                              side_effect=fake_extract):
                return await builder._full_path_spec(
                    "stu_dir", rec["id"], "正文" * 30, None, "书.pdf", llm, warnings)
        spec = asyncio.run(drive())
        self.assertIsNotNone(spec)
        self.assertEqual([c["name"] for c in spec["chapters"]],
                         ["第1章", "第2章", "第3章"])
        failed = spec["chapters"][1]
        self.assertEqual(failed["concepts"], [])
        self.assertEqual([s["name"] for s in failed["sections"]], ["2.1 节"])
        self.assertTrue(any("已保留章节结构" in w for w in warnings))
        # 失败章重试了一次：3 章初始调用 + 第2章重试 1 次。
        self.assertEqual(llm.chapter_calls, 4)


class TestVolumePolicyAndQuality(unittest.TestCase):
    """无概念章保留 + graph_quality 只计数不阻塞（纯函数）。"""

    def test_policy_keeps_empty_chapter(self):
        spec = {"chapters": [
            {"name": "第1章", "chapter_key": "k1", "concepts": []},
            {"name": "第2章", "chapter_key": "k2",
             "concepts": [{"name": "概念A"}]},
        ]}
        out, coverage = builder._apply_volume_policy(
            spec, {"max_chapters": None, "max_concepts": None})
        self.assertEqual([c["name"] for c in out["chapters"]], ["第1章", "第2章"])
        self.assertEqual(coverage["empty_concept_chapters"], 1)

    def test_quality_counts_empty_chapter_without_error(self):
        payload = {
            "nodes": [
                {"id": "ch1", "name": "第1章 集合", "kind": "chapter"},
                {"id": "ch2", "name": "第2章 函数", "kind": "chapter"},
                {"id": "c1", "name": "函数", "kind": "concept"},
            ],
            "edges": [
                {"source": "c1", "target": "ch2", "type": "part_of"},
            ],
        }
        quality = graph_quality(payload, textbook_title="教材")
        self.assertTrue(quality["ok"])
        self.assertEqual(quality["empty_concept_chapters"], 1)

    def test_quality_still_rejects_conceptless_graph(self):
        payload = {
            "nodes": [{"id": "ch1", "name": "第1章 集合", "kind": "chapter"}],
            "edges": [],
        }
        self.assertFalse(graph_quality(payload)["ok"])


class TestBackMatterFilter(unittest.TestCase):
    """后置事项伪章（小结/习题/答案/趣闻）双侧剔除；真实章名不受影响。"""

    def test_back_matter_detected(self):
        for name in ("本章小结", "小结", "习题", "习题一", "习题参考答案",
                     "参考答案", "答案", "书名页", "标题", "结语", "后记",
                     "今日物理趣闻G", "第十章小结"):
            with self.subTest(name=name):
                self.assertTrue(is_back_matter(name), name)
                self.assertFalse(is_teaching_chapter(name), name)
                self.assertTrue(builder._garbage_outline_title(name, ""), name)

    def test_teaching_chapters_survive(self):
        for name in ("第1章 集合的概念", "第一单元 青春的价值",
                     "UNIT 1 PEOPLE OF ACHIEVEMENT", "小结与反思提升",
                     "第12章 静电场及其应用"):
            with self.subTest(name=name):
                self.assertFalse(is_back_matter(name), name)
                self.assertTrue(is_teaching_chapter(name), name)
                self.assertFalse(builder._garbage_outline_title(name, ""), name)

    def test_normalize_drops_back_matter_chapters(self):
        spec = {"chapters": [
            {"name": "第1章 函数", "concepts": [{"name": "函数"}]},
            {"name": "本章小结", "concepts": [{"name": "杂项"}]},
            {"name": "习题参考答案", "concepts": []},
        ], "page_ranges": {}}
        out, warnings = normalize_textbook_spec(
            spec, textbook_title="教材", volume_id="f1", volume_title="卷一")
        self.assertEqual([c["name"] for c in out["chapters"]], ["第1章 函数"])
        self.assertTrue(any("已过滤" in w for w in warnings))

    def test_all_back_matter_falls_back_to_whole_book(self):
        spec = {"chapters": [
            {"name": "本章小结", "concepts": [{"name": "仅存概念"}]},
        ], "page_ranges": {}}
        out, warnings = normalize_textbook_spec(
            spec, textbook_title="教材", volume_id="f1", volume_title="卷一")
        self.assertEqual([c["name"] for c in out["chapters"]], ["全书"])
        self.assertTrue(any("全书" in w for w in warnings))


class TestPromptVersionInvalidatesCache(unittest.TestCase):
    """prompt 版本号 bump 后，旧指纹的卷 spec 缓存必须失效。"""

    def test_old_fingerprint_is_invalid(self):
        text = "教材正文"
        current = {
            "file_id": "f1", "text_sha256": builder._text_hash(text),
            "prompt_version": builder._prompt_fingerprint(),
            "schema_version": builder._VOLUME_SPEC_SCHEMA,
            "chapter_locator_version": builder._CHAPTER_LOCATOR_VERSION,
            "normalized_spec": {"chapters": [{"name": "第1章 函数"}]},
        }
        self.assertTrue(builder._valid_cached_spec(current, "f1", text))
        stale = dict(current, prompt_version="knowledge_graph_build@2.0.0+"
                     "textbook_toc_extract@2.2.0+textbook_skeleton@2.0.0+"
                     "textbook_chapter_concepts@2.2.0+textbook_graph_design@1.0.0")
        self.assertFalse(builder._valid_cached_spec(stale, "f1", text))


if __name__ == "__main__":
    unittest.main()
