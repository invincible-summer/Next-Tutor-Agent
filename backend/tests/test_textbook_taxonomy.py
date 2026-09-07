"""M5.9 taxonomy projection regression tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.v1 import knowledge as knowledge_api  # noqa: E402
from app.core import textbook as tb_store  # noqa: E402


class _FakeService:
    def __init__(self, topic_key):
        prefix = f"custom.{topic_key}."
        self.nodes = {
            prefix + "ch1": SimpleNamespace(id=prefix + "ch1", subject="物理"),
            prefix + "c1": SimpleNamespace(id=prefix + "c1", subject="物理"),
        }

    def graph_for(self, _student_id):
        return SimpleNamespace(nodes=self.nodes)


class TestTextbookTaxonomy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = tb_store._LIBRARY_DIR
        tb_store._LIBRARY_DIR = Path(self.tmp.name) / "library"

    def tearDown(self):
        tb_store._LIBRARY_DIR = self.orig
        self.tmp.cleanup()

    def test_three_level_projection_uses_metadata_and_stable_topic_key(self):
        rec = tb_store.create_group(
            "stu1", file_ids=["f1"], title="示例物理学（示例作者）",
            subject="物理", level="本科", group_note="相对论章节",
        )
        before_topic = rec["topic_key"]
        with patch.object(knowledge_api._kn, "is_enabled", return_value=True), \
             patch.object(knowledge_api._kn, "get_knowledge_service", return_value=_FakeService(rec["topic_key"])):
            first = knowledge_api.knowledge_taxonomy("stu1")
        group = first["levels"][0]["subjects"][0]["groups"][0]
        self.assertEqual(first["levels"][0]["name"], "本科")
        self.assertEqual(first["levels"][0]["subjects"][0]["name"], "物理")
        self.assertEqual(group["name"], "示例物理学（示例作者）")
        self.assertEqual(group["note"], "相对论章节")
        self.assertEqual(group["node_prefix"], "custom." + before_topic + ".")

        updated = tb_store.update_textbook(
            "stu1", rec["id"], title="大学物理学新版", subject="数学", level="高中",
        )
        self.assertEqual(updated["topic_key"], before_topic)
        with patch.object(knowledge_api._kn, "is_enabled", return_value=True), \
             patch.object(knowledge_api._kn, "get_knowledge_service", return_value=_FakeService(rec["topic_key"])):
            second = knowledge_api.knowledge_taxonomy("stu1")
        self.assertEqual(second["levels"][0]["name"], "高中")
        self.assertEqual(second["levels"][0]["subjects"][0]["name"], "数学")
        group2 = second["levels"][0]["subjects"][0]["groups"][0]
        self.assertEqual(group2["name"], "大学物理学新版")
        self.assertEqual(group2["topic_key"], before_topic)


if __name__ == "__main__":
    unittest.main()
