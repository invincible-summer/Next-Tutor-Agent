import asyncio
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase
from app.core.knowledge_store import KnowledgeStore  # noqa: E402
from app.tools.knowledge_search import KnowledgeSearchTool  # noqa: E402


class TestMaterialRetrieval(StorageSandboxTestCase):
    def test_results_cover_multiple_files_and_include_locations(self):
        store = KnowledgeStore()
        for fid, name, chapter in (
            ("v1", "大学物理上册.pdf", "第一章 质点运动"),
            ("v2", "大学物理下册.pdf", "第八章 电磁感应"),
            ("v3", "大学物理实验.pdf", "第三章 测量方法"),
        ):
            text = "\f".join(
                f"{chapter}\n共同概念 动量守恒 在本卷的说明 {i}。" * 5
                for i in range(1, 5))
            store.add_file(
                fid, name, text,
                metadata={"source_scope": "workspace_textbook",
                          "source_visibility": "public"})
        result = asyncio.run(KnowledgeSearchTool(store).run(query="动量守恒", top_k=3))
        self.assertEqual(result.status, "success")
        rows = result.data["results"]
        self.assertEqual(len({r["file_id"] for r in rows}), 3)
        for row in rows:
            self.assertTrue(row["filename"])
            self.assertTrue(row["page"])
            self.assertTrue(row["chapter"])
            self.assertEqual(row["source_scope"], "workspace_textbook")
            self.assertIn("location_label", row)

    def test_internal_file_scope_never_leaks_other_file(self):
        store = KnowledgeStore()
        store.add_file("a", "A.txt", "相同主题 A 私有内容 " * 30)
        store.add_file("b", "B.txt", "相同主题 B 指定内容 " * 30)
        result = asyncio.run(KnowledgeSearchTool(store).run(
            query="相同主题", top_k=4, file_ids=["b"]))
        self.assertEqual({r["file_id"] for r in result.data["results"]}, {"b"})
        self.assertNotIn("A 私有内容", result.text)


if __name__ == "__main__":
    unittest.main()
