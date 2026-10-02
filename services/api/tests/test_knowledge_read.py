"""P9 R5：knowledge_read 按指针取原文工具。"""
import asyncio
import unittest

from tests.storage_sandbox import StorageSandboxTestCase
from app.core.knowledge_store import KnowledgeStore
from app.core.retriever import Chunk, tokenize
from app.tools.knowledge_read import KnowledgeReadTool


def _chunk(cid, index, text, *, file_id="f1", page=1, printed=None, lesson=None):
    metadata = {"printed_page": printed, "lesson": lesson,
                "section_path": [], "block_types": []}
    return Chunk(chunk_id=cid, source="物理必修一.pdf", text=text, index=index,
                 tokens=tokenize(text), file_id=file_id, page=page,
                 metadata=metadata)


class KnowledgeReadToolTest(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.store = KnowledgeStore(upload_dir=self.root / "uploads")
        self.store.chunks = [
            _chunk("f1#0", 0, "上一节：参考系与质点运动的描述。", page=10, printed=3),
            _chunk("f1#1", 1, "洛伦兹变换的定义：两个惯性参考系之间的坐标变换关系，"
                             "保持真空光速不变，式（8.23）给出完整表达式。", page=11, printed=4),
            _chunk("f1#2", 2, "下一节开始讨论相对论动量与能量的表述。", page=12, printed=5),
            _chunk("f2#1", 1, "另一本书的同序号片段，用于消歧测试。", file_id="f2", page=1),
        ]
        self.tool = KnowledgeReadTool(self.store)

    def _run(self, **kwargs):
        return asyncio.run(self.tool.run(**kwargs))

    def test_read_by_chunk_index_with_neighbors(self):
        result = self._run(chunk=1, file_id="f1", span="both")
        self.assertEqual(result.status, "success")
        self.assertIn("坐标变换关系", result.text)
        self.assertIn("参考系与质点", result.text, "prev 邻块头部应附带")
        self.assertIn("相对论动量", result.text, "next 邻块头部应附带")
        self.assertEqual(result.data["index"], 1)
        self.assertEqual(result.data["printed_page"], 4)
        self.assertEqual(result.data["lesson"], None)

    def test_read_current_only(self):
        result = self._run(chunk=1, file_id="f1", span="current")
        self.assertEqual(result.status, "success")
        self.assertNotIn("参考系与质点", result.text)

    def test_read_by_page(self):
        result = self._run(page=11, file_id="f1")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["page"], 11)

    def test_ambiguous_chunk_across_files_requires_file_id(self):
        result = self._run(chunk=1)
        self.assertEqual(result.status, "error")
        self.assertIn("file_id", result.text)

    def test_file_id_disambiguates(self):
        result = self._run(chunk=1, file_id="f2")
        self.assertEqual(result.status, "success")
        self.assertIn("另一本书", result.text)

    def test_missing_pointer_not_found(self):
        result = self._run(chunk=99)
        self.assertEqual(result.status, "error")

    def test_no_args_bad_request(self):
        result = self._run()
        self.assertEqual(result.status, "error")

    def test_chars_cap_respected(self):
        result = self._run(chunk=1, file_id="f1", chars=200)
        self.assertEqual(result.status, "success")
        self.assertLessEqual(result.data["chars"], 200)


if __name__ == "__main__":
    unittest.main()
