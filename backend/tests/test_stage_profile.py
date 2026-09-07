"""P1 学段适配体系测试：stage_profile 数据完整性、prompt 注入、种子包、policy 分档。"""
import unittest


class TestStageProfile(unittest.TestCase):
    def test_all_stages_all_dimensions_nonempty(self):
        from app.agents.teaching_engine.stage_profile import (
            VALID_STAGES, stage_profile)
        dims = ("language", "abstraction", "examples", "structure",
                "encouragement", "anchor", "mistakes")
        self.assertEqual(set(VALID_STAGES), {"小学", "初中", "高中", "本科"})
        for stage in VALID_STAGES:
            p = stage_profile(stage)
            for d in dims:
                self.assertTrue(p[d].strip(), f"{stage}.{d} 为空")

    def test_unknown_grade_falls_back(self):
        from app.agents.teaching_engine.stage_profile import stage_profile
        # 未知学段回退到产品默认学段（本科）。
        self.assertEqual(stage_profile("大学"), stage_profile("本科"))
        self.assertEqual(stage_profile(""), stage_profile("本科"))

    def test_brief_and_anchor_render(self):
        from app.agents.teaching_engine.stage_profile import (
            difficulty_anchor, stage_brief)
        brief = stage_brief("小学")
        self.assertIn("学段教学细则·小学", brief)
        self.assertIn("难度锚点", brief)
        self.assertIn("高考", difficulty_anchor("高中"))
        self.assertIn("考研", difficulty_anchor("本科"))
        self.assertNotIn("高考", difficulty_anchor("小学"))


class TestGradePreambleInjection(unittest.TestCase):
    def test_preamble_carries_stage_brief(self):
        from app.prompts.tutor import grade_preamble
        p = grade_preamble("小学", False)
        self.assertIn("学段教学细则·小学", p)
        self.assertIn("生活场景", p)
        p2 = grade_preamble("本科", False)
        self.assertIn("定义-定理-证明", p2)


class TestQuizPromptAnchor(unittest.TestCase):
    def test_generate_quiz_prompt_has_stage_anchor(self):
        import asyncio
        from unittest import mock
        from app.core.config import settings
        from app.tools.quiz import GenerateQuizTool

        class LLM:
            def __init__(self): self.calls = []
            async def complete(self, messages, **kw):
                self.calls.append(messages[0]["content"])
                return ('{"questions": []}', {})

        llm = LLM()
        # single 模式：calls[0] 即生成 prompt（two_pass 下 calls[0] 是蓝图轮，
        # 蓝图注入的锚点语义由 tests/test_quiz_design.py 覆盖）。
        with mock.patch.object(settings, "quiz_design_mode", "single"):
            asyncio.run(GenerateQuizTool(llm).run(topic="浮力", grade="小学"))
        self.assertTrue(llm.calls)
        self.assertIn("难度锚点", llm.calls[0])
        self.assertIn("课内变式", llm.calls[0])      # 小学锚点
        self.assertNotIn("高考压轴", llm.calls[0])   # 不再是 K12 高中锚点

    def test_fit_quiz_prompt_has_stage_anchor(self):
        from app.tools.fit_quiz import _FIT_PROMPT
        from app.agents.teaching_engine.stage_profile import difficulty_anchor
        text = _FIT_PROMPT.format(reference="参考题", grade="本科", count=2,
                                  difficulty="hard", difficulty_zh="挑战",
                                  anchor=difficulty_anchor("本科"))
        self.assertIn("考研", text)

    def test_grade_prompt_checks_stage_mistakes(self):
        from app.agents.assessment.evaluator import grade_open_prompt
        p = grade_open_prompt(stem="1+1=?", q_type="fill_blank",
                              correct_answer="2", explanation="...",
                              student_answer="3", grade="小学")
        self.assertIn("典型错因", p)
        self.assertIn("单位", p)  # 小学典型错因


class TestPolicyStageBranches(unittest.TestCase):
    def _ctx(self, grade):
        from app.agents.teaching_engine import TeachingContext
        return TeachingContext(concept="极限", grade=grade, mastery=0.1,
                               turns_on_concept=0)

    def test_primary_introduction_recipe(self):
        from app.agents.teaching_engine import adapt_from_context
        s = adapt_from_context(self._ctx("小学"))
        self.assertTrue(any("互动" in f or "提一个小问题" in f for f in s.focus),
                        s.focus)
        self.assertTrue(any("长段抽象" in a for a in s.avoid), s.avoid)

    def test_undergrad_introduction_recipe(self):
        from app.agents.teaching_engine import adapt_from_context
        s = adapt_from_context(self._ctx("本科"))
        self.assertTrue(any("证明" in f for f in s.focus), s.focus)
        self.assertTrue(any("经验" in a for a in s.avoid), s.avoid)


if __name__ == "__main__":
    unittest.main()
