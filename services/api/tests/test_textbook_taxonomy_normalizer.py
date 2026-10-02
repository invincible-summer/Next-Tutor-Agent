from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.knowledge import custom_graph
from app.agents.knowledge import store
from app.agents.knowledge.taxonomy_normalizer import (
    graph_quality,
    normalize_chapter_name,
    normalize_textbook_spec,
)


class TestTextbookTaxonomyNormalizer(unittest.TestCase):
    def test_removes_legacy_filename_prefixes(self):
        cases = [
            ("3 C#.pdf·准备工作", ".Net 部分", "3 C#.pdf", "准备工作"),
            ("6 Avalonia_UI_入门_学生讲义（更新版）.pdf·第一部分 基础",
             ".Net 部分", "6 Avalonia_UI_入门_学生讲义（更新版）.pdf",
             "第一部分 基础"),
            ("大学物理学.pdf·第一章 质点运动学", "大学物理学",
             "大学物理学.pdf", "第一章 质点运动学"),
        ]
        for raw, title, volume, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_chapter_name(
                    raw, textbook_title=title, volume_title=volume), expected)

    def test_volume_identity_is_metadata_not_display_name(self):
        raw = {"subject": "物理", "chapters": [{
            "name": "力学.pdf·第一章 运动",
            "concepts": [{"name": "速度", "difficulty": 2}],
        }]}
        spec, warnings = normalize_textbook_spec(
            raw, textbook_title="大学物理", volume_id="file-a",
            volume_title="力学.pdf")
        self.assertEqual(warnings, [])
        chapter = spec["chapters"][0]
        self.assertEqual(chapter["name"], "第一章 运动")
        self.assertEqual(chapter["metadata"]["volume_id"], "file-a")
        data, _ = custom_graph.spec_to_graph(
            spec, topic_key="tb-demo", source="textbook:file-a", base_graph=None)
        chapter_node = next(n for n in data["nodes"] if n["kind"] == "chapter")
        self.assertEqual(chapter_node["name"], "第一章 运动")
        self.assertEqual(chapter_node["metadata"]["file_id"], "file-a")
        payload = {"nodes": data["nodes"], "edges": data["edges"]}
        self.assertTrue(graph_quality(payload, textbook_title="大学物理")["ok"])

    def test_same_heading_across_volumes_has_distinct_internal_ids(self):
        ids = []
        for volume in ("a", "b"):
            spec, _ = normalize_textbook_spec(
                {"chapters": [{"name": "第一章 绪论",
                                "concepts": [{"name": f"概念{volume}"}]}]},
                textbook_title="课程", volume_id=volume,
                volume_title=f"第{volume}卷.pdf")
            data, _ = custom_graph.spec_to_graph(
                spec, topic_key="tb-course", source=f"textbook:{volume}", base_graph=None)
            ids.append(next(n["id"] for n in data["nodes"] if n["kind"] == "chapter"))
        self.assertNotEqual(ids[0], ids[1])

    def test_all_polluted_headings_fall_back_to_whole_book(self):
        spec, warnings = normalize_textbook_spec(
            {"chapters": [{"name": "大学物理.pdf·",
                            "concepts": [{"name": "速度"}]}]},
            textbook_title="大学物理", volume_id="f1",
            volume_title="大学物理.pdf")
        self.assertEqual(spec["chapters"][0]["name"], "全书")
        self.assertTrue(spec["chapters"][0]["metadata"]["normalization_fallback"])
        self.assertTrue(any("确定性兜底" in w for w in warnings))


class TestLegacyArchiveCleanup(unittest.TestCase):
    def test_cleanup_is_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "custom"
            active = root / "u1" / "topic.json"
            old = root / "u1" / "archive" / "topic.v1.json"
            old.parent.mkdir(parents=True)
            active.write_text('{"nodes":[1]}', encoding="utf-8")
            old.write_text('{"nodes":[1]}', encoding="utf-8")
            with patch.object(store, "_CUSTOM_DIR", root):
                first = store.cleanup_legacy_graph_archives()
                second = store.cleanup_legacy_graph_archives()
            self.assertEqual(first["removed"], 1)
            self.assertEqual(second["removed"], 1)
            self.assertTrue(active.exists())
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
