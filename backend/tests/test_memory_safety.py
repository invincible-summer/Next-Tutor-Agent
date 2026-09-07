import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core.memory_safety import memory_safe_text  # noqa: E402
from app.core.workspace_memory import _render_turn_for_memory  # noqa: E402


class TestMemorySafety(unittest.TestCase):
    def test_ocr_body_removed_but_instruction_kept(self):
        raw = "<ocr_material>秘密扫描正文\n公式很多</ocr_material>\n\n请讲解第二问"
        safe = memory_safe_text(raw)
        self.assertNotIn("秘密扫描正文", safe)
        self.assertIn("请讲解第二问", safe)

    def test_material_excerpt_removed_from_answer(self):
        safe = memory_safe_text("结论如下 <material_excerpt>教材大段原文</material_excerpt> 学生尚未掌握")
        self.assertNotIn("教材大段原文", safe)
        self.assertIn("学生尚未掌握", safe)

    def test_workspace_memory_render_uses_safe_projection(self):
        rendered = _render_turn_for_memory(
            "<ocr_material>不可跨会话的 OCR</ocr_material>\n\n帮我分析",
            "<material_excerpt>不可持久化的教材原文</material_excerpt>需要复习",
        )
        self.assertNotIn("不可跨会话的 OCR", rendered)
        self.assertNotIn("不可持久化的教材原文", rendered)
        self.assertIn("帮我分析", rendered)
        self.assertIn("需要复习", rendered)


if __name__ == "__main__":
    unittest.main()
