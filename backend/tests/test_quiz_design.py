"""出题两轮化（命题蓝图轮）与 critic 深度拦截的回归测试。

覆盖：
  - core.quiz_design：two_pass 渲染注入块 / single 跳过 LLM / 垃圾输出与
    异常的 fail-open 回退 / 蓝图 prompt 携带学段锚点、focus 与避让题干；
  - tools/quiz.py 接线：蓝图注入生成 prompt、蓝图失败降级、single 模式无蓝图轮；
  - core.quiz_verify critic：too_shallow 判定丢弃、难度行注入、meta 计数。

全部走 mock LLM，不触碰任何存储根。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from app.core.config import settings


class QueueLLM:
    """complete() serves queued responses in order (blueprint, generation, critic, ...)."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        self.calls.append(messages)
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def _q(qid, *, answer="B"):
    return {"id": qid, "type": "multiple_choice", "stem": "题干",
            "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            "answer": answer,
            "explanation": "这是足够长的解析内容，超过十五个字的下限。",
            "knowledge_point": "浮力", "difficulty": "medium"}


def _gen_json(questions) -> str:
    return json.dumps({"questions": questions}, ensure_ascii=False)


def _critic_json(pairs) -> str:
    verdicts = [{"id": i, "verdict": v, "reason": "r"} for i, v in pairs]
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


def _blueprint_json(n: int = 1) -> str:
    return json.dumps({
        "angles_considered": ["概念辨析", "迁移应用"],
        "blueprint": [{"id": i, "angle": "概念辨析", "bloom": "analyze",
                       "q_type": "short_answer", "trap": "常见误区",
                       "idea": f"换情境考查第{i}题"} for i in range(1, n + 1)],
    }, ensure_ascii=False)


class TestDesignBlueprint(unittest.TestCase):
    def test_two_pass_renders_injection_block(self):
        from app.core.quiz_design import design_blueprint
        llm = QueueLLM([_blueprint_json(2)])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            block, status = asyncio.run(design_blueprint(
                llm, topic="牛顿第二定律", grade="高中", difficulty="hard", count=2))
        self.assertEqual(status, "two_pass")
        self.assertIn("命题蓝图", block)
        self.assertIn("第 1 题", block)
        self.assertIn("第 2 题", block)
        self.assertIn("概念辨析", block)
        self.assertEqual(len(llm.calls), 1)

    def test_single_mode_skips_llm(self):
        from app.core.quiz_design import design_blueprint
        llm = QueueLLM([_blueprint_json(1)])
        with mock.patch.object(settings, "quiz_design_mode", "single"):
            block, status = asyncio.run(design_blueprint(
                llm, topic="浮力", grade="初中", difficulty="easy", count=1))
        self.assertEqual(block, "")
        self.assertEqual(status, "single")
        self.assertEqual(llm.calls, [])

    def test_garbage_blueprint_falls_back(self):
        from app.core.quiz_design import design_blueprint
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            block, status = asyncio.run(design_blueprint(
                QueueLLM(["not json at all"]), topic="t", grade="高中",
                difficulty="medium", count=1))
        self.assertEqual((block, status), ("", "fallback"))

    def test_exception_falls_back(self):
        from app.core.quiz_design import design_blueprint
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            block, status = asyncio.run(design_blueprint(
                QueueLLM([RuntimeError("boom")]), topic="t", grade="高中",
                difficulty="medium", count=1))
        self.assertEqual((block, status), ("", "fallback"))

    def test_prompt_carries_anchor_focus_and_avoid_stems(self):
        from app.core.quiz_design import design_blueprint
        llm = QueueLLM([_blueprint_json(1)])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            asyncio.run(design_blueprint(
                llm, topic="浮力", grade="高中", difficulty="medium", count=1,
                focus="方向判断", avoid_stems=["旧题干X"]))
        prompt = llm.calls[0][0]["content"]
        self.assertIn("难度锚点", prompt)       # 显式学段注入锚点
        self.assertIn("方向判断", prompt)       # focus 进蓝图
        self.assertIn("旧题干X", prompt)        # 避让题干进蓝图
        self.assertIn("好题标准", prompt)

    def test_auto_grade_uses_auto_anchor(self):
        from app.core.quiz_design import design_blueprint
        llm = QueueLLM([_blueprint_json(1)])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            asyncio.run(design_blueprint(
                llm, topic="浮力", grade="", difficulty="medium", count=1))
        prompt = llm.calls[0][0]["content"]
        self.assertIn("按知识点本身标定", prompt)


class TestTwoPassWiring(unittest.TestCase):
    def test_generate_quiz_injects_blueprint_into_generation_prompt(self):
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM([_blueprint_json(1), _gen_json([_q(1)]),
                        _critic_json([(1, "correct")])])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            result = asyncio.run(GenerateQuizTool(llm).run(
                topic="浮力", grade="初中", count=1))
        self.assertEqual(result.status, "success")
        self.assertIn("命题蓝图", llm.calls[1][0]["content"])
        self.assertEqual(result.data["verification"]["design"], "two_pass")

    def test_generate_quiz_blueprint_failure_degrades_to_single_pass(self):
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM(["garbage", _gen_json([_q(1)]),
                        _critic_json([(1, "correct")])])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            result = asyncio.run(GenerateQuizTool(llm).run(
                topic="浮力", grade="初中", count=1))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["verification"]["design"], "fallback")
        self.assertNotIn("命题蓝图", llm.calls[1][0]["content"])

    def test_single_mode_legacy_call_order(self):
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM([_gen_json([_q(1)]), _critic_json([(1, "correct")])])
        with mock.patch.object(settings, "quiz_design_mode", "single"):
            result = asyncio.run(GenerateQuizTool(llm).run(
                topic="浮力", grade="初中", count=1))
        self.assertEqual(result.status, "success")
        self.assertEqual(len(llm.calls), 2)  # 生成 + critic，无蓝图轮
        self.assertEqual(result.data["verification"]["design"], "single")

    def test_assessment_generator_runs_blueprint_first(self):
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal
        ctx = AssessmentContext(concept="浮力", grade="初中", base_difficulty=2)
        llm = QueueLLM([_blueprint_json(1), _gen_json([_q(1)]),
                        _critic_json([(1, "correct")])])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            q = asyncio.run(generate_question(
                AssessmentGoal(purpose="check"), ctx, llm=llm))
        self.assertIsNotNone(q)
        self.assertIn("命题蓝图", llm.calls[1][0]["content"])


class TestCriticDepthGate(unittest.TestCase):
    def test_too_shallow_drops_question(self):
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([_critic_json([(1, "correct"), (2, "too_shallow")])])
        kept, dropped, ok = asyncio.run(verify_questions(
            llm, [_q(1), _q(2)], topic="浮力", grade="高中", difficulty="hard"))
        self.assertTrue(ok)
        self.assertEqual([q["id"] for q in kept], [1])
        self.assertEqual(dropped[0]["_verdict"], "too_shallow")

    def test_critic_prompt_carries_target_difficulty(self):
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([_critic_json([(1, "correct")])])
        asyncio.run(verify_questions(llm, [_q(1)], topic="t", grade="g",
                                     difficulty="hard"))
        self.assertIn("目标难度：挑战（hard）", llm.calls[0][0]["content"])
        llm2 = QueueLLM([_critic_json([(1, "correct")])])
        asyncio.run(verify_questions(llm2, [_q(1)], topic="t", grade="g"))
        self.assertNotIn("目标难度：", llm2.calls[0][0]["content"])

    def test_meta_counts_shallow_drops(self):
        from app.core.quiz_verify import generate_verified_questions
        llm = QueueLLM([_gen_json([_q(1), _q(2)]),
                        _critic_json([(1, "correct"), (2, "too_shallow")])])
        questions, meta = asyncio.run(generate_verified_questions(
            llm, make_prompt=lambda: "p",
            parse=lambda raw: json.loads(raw)["questions"],
            topic="浮力", grade="高中", difficulty="hard",
            temperature=0.4, max_tokens=1000))
        self.assertEqual(len(questions), 1)
        self.assertEqual(meta["dropped_shallow"], 1)
        self.assertEqual(meta["dropped_by_critic"], 1)
        self.assertEqual(meta["critic_flags"][0]["verdict"], "too_shallow")


if __name__ == "__main__":
    unittest.main()
