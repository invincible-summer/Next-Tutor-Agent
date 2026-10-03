"""P9 R6：触发统一——书名号信号、6 字门槛与 decision 门控。

取证样本3：「《湖畔夜色讲》」7 字过不了旧 ≥8 字符门槛，skill 决策又剪掉
检索技能 → 教材就在工作区却完全不触发（assistant 自曝「本轮没有可用的
教材检索工具」）。
"""
import unittest
from unittest.mock import Mock

from app.agents.material_signals import mentions_title
from app.agents.preresearch import is_content_question
from app.agents.skill_runtime.decision import build_task_frame


class TitleMarkSignalTest(unittest.TestCase):
    def test_book_title_mark_detected(self):
        self.assertTrue(mentions_title("《湖畔夜色讲》"))
        self.assertTrue(mentions_title("《沁园春·江畔》是什么"))
        self.assertFalse(mentions_title("湖畔夜色讲"))
        self.assertFalse(mentions_title("《》"))
        self.assertFalse(mentions_title(""))

    def test_seven_char_title_question_is_content(self):
        self.assertTrue(is_content_question("《湖畔夜色讲》"))

    def test_six_char_substantive_question_is_content(self):
        self.assertTrue(is_content_question("对数运算律什么"))

    def test_greetings_still_not_content(self):
        self.assertFalse(is_content_question("你好"))
        self.assertFalse(is_content_question("好的，继续"))
        self.assertFalse(is_content_question(""))


def _frame(message, *, concept="", materials=True):
    understanding = Mock()
    understanding.intent = Mock(value="explain")
    understanding.subject = "语文"
    understanding.concept = concept
    understanding.requires_tools = False
    understanding.confidence = 0.8
    snapshot = Mock()
    snapshot.grade = "高中"
    snapshot.has_materials = materials
    return build_task_frame(message, understanding, snapshot,
                            has_visible_materials=materials)


class SkillDecisionGateTest(unittest.TestCase):
    def test_title_mark_forces_material_grounding(self):
        frame = _frame("《湖畔夜色讲》", concept="湖畔夜色")
        self.assertTrue(frame.material_grounding_required,
                        "书名号命中时检索技能不得被剪（取证样本3 双重否决）")
        self.assertTrue(frame.references_materials)

    def test_no_materials_no_grounding(self):
        frame = _frame("《湖畔夜色讲》", concept="湖畔夜色", materials=False)
        self.assertFalse(frame.material_grounding_required)

    def test_plain_question_still_gated_by_content_rule(self):
        frame = _frame("帮我讲讲这道题怎么解", concept="解方程", materials=True)
        self.assertTrue(frame.material_grounding_required)


if __name__ == "__main__":
    unittest.main()
