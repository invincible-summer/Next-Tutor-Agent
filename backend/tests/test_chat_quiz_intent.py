"""模糊 Chat 出题语义必须进入结构化题卡的回归。"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.state import TaskPlan, TaskType, TaskUnderstanding
from app.agents.supervisor import _enforce_explicit_practice_plan
from app.agents.task_understanding import (
    is_new_question_request,
    llm_understand,
    new_question_request_score,
    rule_understand,
)
from app.core.session import TutorSession
from app.core.trace import Trace


class _IntentLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return json.dumps(self.payload, ensure_ascii=False), {}


class ChatQuizIntentTest(unittest.TestCase):
    def test_fuzzy_phrases_are_new_question_requests(self):
        for message in (
            "给我来个题", "考我一下", "想练练牛顿第二定律", "来一道函数题",
            "给点练习", "quiz me", "give me a problem",
        ):
            self.assertTrue(is_new_question_request(message), message)
            self.assertEqual(rule_understand(message).intent, TaskType.PRACTICE)
            self.assertTrue(rule_understand(message).structured_quiz_request)

    def test_existing_question_is_not_new_card(self):
        for message in ("这道题怎么做", "解释一下这道题", "帮我求解这题"):
            understanding = rule_understand(message)
            self.assertNotEqual(understanding.intent, TaskType.PRACTICE, message)
            self.assertFalse(understanding.structured_quiz_request, message)
            self.assertFalse(is_new_question_request(message), message)

    def test_llm_semantic_guard_overrides_wrong_explain(self):
        llm = _IntentLLM({
            "intent": "explain", "concept": "函数", "goal": "understand",
            "requires_tools": False,
        })
        understanding = asyncio.run(llm_understand("考我一下函数", llm))
        self.assertEqual(understanding.intent, TaskType.EXPLAIN)
        # The low-level parser preserves the provider payload; the public
        # understand() boundary applies the semantic guard.
        from app.agents.task_understanding import understand
        understanding = asyncio.run(understand(
            "考我一下函数", TutorSession(), llm, use_llm=True))
        self.assertEqual(understanding.intent, TaskType.PRACTICE)
        self.assertTrue(understanding.structured_quiz_request)
        self.assertTrue(understanding.requires_tools)
        self.assertEqual(understanding.source, "llm_semantic_guard")

    def test_llm_existing_question_guard_keeps_solve(self):
        llm = _IntentLLM({
            "intent": "practice", "concept": "函数", "goal": "practice",
            "requires_tools": True,
        })
        from app.agents.task_understanding import understand
        understanding = asyncio.run(understand(
            "这道题怎么做", TutorSession(), llm, use_llm=True))
        self.assertEqual(understanding.intent, TaskType.SOLVE)
        self.assertFalse(understanding.structured_quiz_request)

    def test_structured_flag_forces_auto_invoked_card(self):
        understanding = TaskUnderstanding(
            intent=TaskType.EXPLAIN, concept="函数", goal="understand",
            structured_quiz_request=True)
        plan = _enforce_explicit_practice_plan(
            TaskPlan(steps=[], source="rule"), understanding,
            "给我来个题", "高中", Trace())
        self.assertEqual(understanding.intent, TaskType.PRACTICE)
        self.assertEqual(len(plan.steps), 1)
        self.assertTrue(plan.steps[0].auto_invoke)
        self.assertEqual(plan.steps[0].suggested_tools, ["generate_quiz"])
        self.assertEqual(plan.steps[0].tool_args["generate_quiz"]["count"], 1)

    def test_score_is_bounded_by_existing_question_marker(self):
        self.assertLess(new_question_request_score("这道题怎么做"), 3)
        self.assertGreaterEqual(new_question_request_score("再来一道类似题"), 3)


if __name__ == "__main__":
    unittest.main()
