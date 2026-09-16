"""P9 R4：上下文重建（课文合并/邻块扩展）与注入投影溢出指针。"""
import hashlib
import unittest

from app.agents.skill_runtime.runtime import SkillRuntime
from app.core.evidence_context import reconstruct_evidence
from app.core.tool_context import project_knowledge_evidence
from app.core.tool_protocol import ToolResult


def _item(index, excerpt, *, lesson=None, file_id="f1", title_match=0.0,
           printed=None, confidence=0.7, is_lesson=False, chunk_id=None):
    return {"index": index, "chunk_id": chunk_id or f"{file_id}#{index}",
            "file_id": file_id, "evidence_excerpt": excerpt, "lesson": lesson,
            "is_lesson": is_lesson, "title_match": title_match,
            "printed_page": printed, "confidence": confidence,
            "selection_reason": "exact_term+mmr", "text": excerpt,
            "source": "语文必修上册.pdf"}


class LessonGroupMergeTest(unittest.TestCase):
    def test_same_lesson_merges_into_single_entry(self):
        items = [
            _item(10, "这几天心里颇不宁静。", lesson="荷塘月色", printed=109),
            _item(11, "曲曲折折的荷塘上面，弥望的是田田的叶子。", lesson="荷塘月色", printed=109),
            _item(50, "单元学习任务：阅读与欣赏。", printed=129),
        ]
        merged = reconstruct_evidence(items)
        lessons = [m for m in merged if m.get("lesson_label") == "荷塘月色"]
        self.assertEqual(len(lessons), 1)
        self.assertIn("颇不宁静", lessons[0]["evidence_excerpt"])
        self.assertIn("田田的叶子", lessons[0]["evidence_excerpt"])
        self.assertIn("……", lessons[0]["evidence_excerpt"])
        self.assertEqual(lessons[0]["printed_page_range"], [109, 109])
        others = [m for m in merged if not m.get("lesson_label")]
        self.assertEqual(len(others), 1)

    def test_single_title_hit_upgrades_to_lesson_excerpt(self):
        items = [_item(10, "独立寒秋，湘江北去。", lesson="沁园春·长沙",
                       title_match=1.0, printed=2)]
        merged = reconstruct_evidence(items)
        self.assertEqual(merged[0].get("lesson_label"), "沁园春·长沙")

    def test_unrelated_items_untouched(self):
        items = [_item(1, "对数的运算性质。"), _item(2, "向量数量积。")]
        merged = reconstruct_evidence(items)
        self.assertEqual(len(merged), 2)
        self.assertFalse(any(m.get("lesson_label") for m in merged))


class NeighborExpansionTest(unittest.TestCase):
    def test_thin_excerpt_gets_neighbor_heads(self):
        chunks = {
            "f1#5": {"text": "前一页结尾的定理表述与符号说明。", "metadata": {}},
            "f1#6": {"text": "洛伦兹变换的定义很短。", "metadata": {"prev_id": "f1#5", "next_id": "f1#7"}},
            "f1#7": {"text": "后一页开始讨论其物理意义与应用场景。", "metadata": {}},
        }
        item = _item(6, "洛伦兹变换的定义很短。", chunk_id="f1#6")
        out = reconstruct_evidence([item], chunks.get)
        self.assertTrue(out[0].get("neighbor_expanded"))
        self.assertIn("前一页结尾", out[0]["evidence_excerpt"])
        self.assertIn("后一页开始", out[0]["evidence_excerpt"])

    def test_reconstructed_excerpt_rebinds_skill_context_hash(self):
        chunks = {
            "f1#5": {"text": "前文条件。", "metadata": {}},
            "f1#6": {"text": "核心结论。", "metadata": {"prev_id": "f1#5"}},
        }
        item = _item(6, "核心结论。", chunk_id="f1#6")
        item["source_visibility"] = "public"
        item["context_hash"] = hashlib.sha256(
            item["evidence_excerpt"].encode()).hexdigest()
        out = reconstruct_evidence([item], chunks.get)
        final_hash = hashlib.sha256(
            out[0]["evidence_excerpt"].encode()).hexdigest()
        self.assertEqual(out[0]["context_hash"], final_hash)

        result = ToolResult(
            tool="knowledge_search", status="success",
            data={"count": 1, "evidence_bundle": {
                "selected": out, "context_hashes": [final_hash],
            }},
            text=f"<material_excerpt>{out[0]['evidence_excerpt']}</material_excerpt>",
        )
        report = SkillRuntime([]).validate_result("knowledge_search", result)
        self.assertIsNotNone(report)
        self.assertTrue(report.valid)

    def test_rich_excerpt_not_expanded(self):
        chunks = {"f1#6": {"text": "x", "metadata": {}}}
        item = _item(6, "很长的摘录" * 100, chunk_id="f1#6")
        out = reconstruct_evidence([item], chunks.get)
        self.assertFalse(out[0].get("neighbor_expanded"))


class ProjectionOverflowTest(unittest.TestCase):
    def _result(self, rows, *, partial=False):
        return ToolResult(tool="knowledge_search", status="success",
                          data={"results": rows, "omitted_count": 40,
                                "partial": partial})

    def test_overflow_becomes_pointers_not_silent_drop(self):
        rows = [_item(i, f"证据正文内容{i}。" * 60) for i in range(6)]
        text = project_knowledge_evidence(self._result(rows), limit=1200)
        self.assertIn("[未展开]", text)
        self.assertIn("knowledge_read", text)
        self.assertIn("另有", text)
        # 前 1-2 条仍是完整 material_excerpt 块。
        self.assertIn("<material_excerpt>", text)

    def test_partial_headline(self):
        rows = [_item(0, "弱证据。")]
        text = project_knowledge_evidence(self._result(rows, partial=True), limit=2000)
        self.assertIn("低置信", text)

    def test_lesson_label_shown(self):
        rows = [_item(3, "课文正文。", lesson="荷塘月色", printed=109)]
        text = project_knowledge_evidence(self._result(rows), limit=2000)
        self.assertIn("课文《荷塘月色》节选", text)
        self.assertIn("教材第109页", text)


if __name__ == "__main__":
    unittest.main()
