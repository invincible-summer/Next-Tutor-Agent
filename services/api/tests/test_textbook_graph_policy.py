"""Regression tests for per-volume graph policy, stable IDs and complete spec caches."""
from __future__ import annotations
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.agents.knowledge import custom_graph as cg
from app.agents.knowledge import store as kg_store
from app.agents.knowledge import textbook_builder as builder
from app.core import library as library_mod
from app.core import textbook as tb_store


class TestPerVolumePolicy(unittest.TestCase):
    def test_independent_budgets_and_repeated_membership(self):
        a = {"chapters": [{"name": "A", "chapter_key": "a", "metadata": {"file_id": "f1"},
                            "concepts": [{"name": "共享"}, {"name": "A独有"}]}]}
        b = {"chapters": [{"name": "B", "chapter_key": "b", "metadata": {"file_id": "f2"},
                            "concepts": [{"name": "共享"}, {"name": "B独有"}]}]}
        a, ca = builder._apply_volume_policy(a, {"max_chapters": None, "max_concepts": 2})
        b, cb = builder._apply_volume_policy(b, {"max_chapters": None, "max_concepts": 2})
        self.assertEqual((ca["included_concept_count"], cb["included_concept_count"]), (2, 2))
        data, _ = cg.spec_to_graph(
            {"subject": "物理", "level": "本科", "chapters": a["chapters"] + b["chapters"]},
            topic_key="tb", source="textbook:g", max_chapters=None, max_concepts=None,
            max_concepts_per_chapter=None, level="本科")
        concepts = {n["name"]: n for n in data["nodes"] if n["kind"] == "concept"}
        self.assertEqual(set(concepts), {"共享", "A独有", "B独有"})
        shared = concepts["共享"]
        memberships = [e for e in data["edges"] if e["type"] == "part_of" and e["source"] == shared["id"]]
        self.assertEqual(len(memberships), 2)
        self.assertEqual(set(shared["metadata"]["file_ids"]), {"f1", "f2"})

    def test_stable_concept_id_is_order_independent(self):
        def graph(names):
            return cg.spec_to_graph({"subject": "数学", "chapters": [{"name": "第一章",
                "concepts": [{"name": name} for name in names]}]}, topic_key="x",
                source="textbook:x", max_concepts=None)[0]
        ids1 = {n["name"]: n["id"] for n in graph(["向量", "矩阵"])["nodes"] if n["kind"] == "concept"}
        ids2 = {n["name"]: n["id"] for n in graph(["矩阵", "向量"])["nodes"] if n["kind"] == "concept"}
        self.assertEqual(ids1, ids2)


class TestVolumeCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(tb_store, "_LIBRARY_DIR", root / "library"),
                        patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
                        patch.object(kg_store, "_KG_DIR", root / "knowledge"),
                        patch.object(kg_store, "_CUSTOM_DIR", root / "knowledge" / "custom")]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.tmp.cleanup()

    def test_policy_remerge_uses_cache_without_llm(self):
        from app.core.library import load_library, save_library, library_data_dir
        lib = load_library("s")
        library_data_dir("s").mkdir(parents=True, exist_ok=True)
        for fid, filename, text in (("f1", "一.txt", "力学速度" * 20), ("f2", "二.txt", "热学温度" * 20)):
            (library_data_dir("s") / f"{fid}.txt").write_text(text, encoding="utf-8")
            lib.files.append({"id": fid, "filename": filename, "folder_id": "", "char_count": len(text),
                              "chunk_count": 0, "orig_ext": "", "kind": "textbook"})
        save_library(lib)
        group = tb_store.create_group("s", file_ids=["f1", "f2"], title="组")
        for fid, name in (("f1", "速度"), ("f2", "温度")):
            text = (library_data_dir("s") / f"{fid}.txt").read_text(encoding="utf-8")
            normalized = {"subject": "物理", "level": "本科", "chapters": [{"name": "第一章",
                "chapter_key": fid, "metadata": {"file_id": fid, "volume_id": fid},
                "concepts": [{"name": name}]}], "page_ranges": {}}
            kg_store.save_volume_spec("s", group["topic_key"], fid, {"file_id": fid,
                "text_sha256": builder._text_hash(text), "prompt_version": builder._prompt_fingerprint(),
                "schema_version": builder._VOLUME_SPEC_SCHEMA,
                "chapter_locator_version": builder._CHAPTER_LOCATOR_VERSION,
                "raw_spec": normalized, "normalized_spec": normalized})
        tb_store.update_textbook("s", group["id"], graph_policy={"default_max_chapters": None,
            "default_max_concepts": 1, "volume_overrides": {}})
        asyncio.run(builder.build_group_graph("s", group["id"], llm=None))
        out = tb_store.find_textbook("s", group["id"])
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["concept_count"], 2)

    def _base(self, text: str, name: str = "第一章") -> dict:
        return {"file_id": "f1", "text_sha256": builder._text_hash(text),
                "prompt_version": builder._prompt_fingerprint(),
                "schema_version": builder._VOLUME_SPEC_SCHEMA,
                "chapter_locator_version": builder._CHAPTER_LOCATOR_VERSION,
                "normalized_spec": {"chapters": [{"name": name}]}}

    def test_old_or_degraded_volume_cache_is_invalid(self):
        text = "教材正文"
        base = self._base(text)
        self.assertTrue(builder._valid_cached_spec(base, "f1", text))
        old = dict(base, schema_version="1")
        self.assertFalse(builder._valid_cached_spec(old, "f1", text))
        degraded = dict(base, normalized_spec={"chapters": [{"name": "全册"}],
            "chapter_detection": {"degraded": True}})
        self.assertFalse(builder._valid_cached_spec(degraded, "f1", text))

    def test_legacy_locator_cache_invalidated_but_good_cache_survives(self):
        short = "教材正文"
        good = self._base(short)
        self.assertTrue(builder._valid_cached_spec(good, "f1", short))
        # 旧版定位器缓存缺 chapter_locator_version → 定向失效重建
        legacy = dict(good)
        legacy.pop("chapter_locator_version")
        self.assertFalse(builder._valid_cached_spec(legacy, "f1", short))
        long_text = "长教材正文" * 12000
        bad = dict(good, text_sha256=builder._text_hash(long_text),
                   normalized_spec={"chapters": [{"name": "全册"}]})
        self.assertFalse(builder._valid_cached_spec(bad, "f1", long_text))

    def test_garbage_chapter_names_invalidate_cache(self):
        text = "教材正文"
        for bad_name in ("34172-0-示范高中教科书语文选择性必修上册_DJD",
                         "物质1 2.23小",
                         "示范高中教科书 物理 选择性必修第二册.pdf",
                         "示例科学技术出版社 物理 高中 选择性必修第一册"):
            with self.subTest(name=bad_name):
                cache = self._base(text, bad_name)
                self.assertFalse(builder._valid_cached_spec(cache, "f1", text))
        # 正常教学标题缓存不受影响
        for good_name in ("第12章 静电场", "第一单元 中国革命传统作品研习",
                          "UNIT 1 PEOPLE OF ACHIEVEMENT", "Sustainable Development",
                          "1-1 静电现象与电场强度"):
            with self.subTest(name=good_name):
                self.assertTrue(builder._valid_cached_spec(
                    self._base(text, good_name), "f1", text))

    def test_legacy_single_migrates_without_changing_identity(self):
        original = tb_store.create_textbook("s", file_id="legacy-file", title="旧教材")
        self.assertEqual(tb_store.migrate_legacy_single_to_groups(), 1)
        migrated = tb_store.find_textbook("s", original["id"])
        self.assertEqual(migrated["id"], original["id"])
        self.assertEqual(migrated["topic_key"], original["topic_key"])
        self.assertEqual(migrated["kind"], "group")
        self.assertEqual(migrated["file_ids"], ["legacy-file"])
        self.assertTrue(migrated["needs_reextract"])
        self.assertIsNone(migrated["graph_policy"]["default_max_concepts"])


if __name__ == "__main__": unittest.main()
