"""W3/D02 回合理解的有界会话上下文回归（docs/updatePlan.md §7.2 D02 / A10）。

此前 understand(msg, session, ...) 的 session 参数零引用：「B」「继续」这类
短确认在存在待答检测题/正在教学概念时被规则短路成 CHITCHAT，指代丢失；
LLM 理解也只输入当前文本。修复后：

- 显式选项点选（"B"/"选C。"/"答案是b"/选项原文）确定性解析为
  goal=answer_pending（判分仍在题卡 API，本轮不出新题）；
- 「继续/下一题」在有待答题或正在教学概念时保留指代，不落寒暄快道；
- 无上下文时行为与旧版逐例一致（寒暄快道保留）；
- LLM 路径携带【会话上下文】块；prompt understand_system@1.3.0 生效。

纯内存用例（不触任何存储根）。
"""
import asyncio
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.planner import make_plan  # noqa: E402
from app.agents.state import TaskType  # noqa: E402
from app.agents.task_understanding import (  # noqa: E402
    _session_context, understand)
from app.core.session import TutorSession  # noqa: E402
from app.prompts.registry import get as get_prompt  # noqa: E402


def _mc_set(questions=None):
    q = {"id": "q_abc12301_1", "type": "multiple_choice",
         "stem": "已知 P(A)=0.5，P(A∩B)=0.2，求 P(B|A)。",
         "options": {"A": "0.2", "B": "0.4", "C": "0.5", "D": "0.7"},
         "answer": "B",
         "explanation": "P(B|A)=P(A∩B)/P(A)。",
         "knowledge_point": "条件概率", "difficulty": 3}
    return {"topic": "条件概率", "grade": "本科", "difficulty": 3,
            "questions": questions if questions is not None else [q],
            "answer_verified": True,
            "verification": {"mode": "critic", "critic": "ok"}}


def _session_with_pending() -> TutorSession:
    s = TutorSession(session_id="sess_ctx", student_id="stu_ctx", title="t")
    s.quiz_history = [_mc_set()]
    return s


def _session_with_concept() -> TutorSession:
    s = TutorSession(session_id="sess_ctx2", student_id="stu_ctx", title="t")
    s.context_card = {"active_concepts": ["牛顿第二定律"],
                      "latest_verdicts": ["correct", "wrong"]}
    return s


class TestSessionContext(unittest.TestCase):

    def test_pending_question_projected_with_options(self):
        ctx = _session_context(_session_with_pending())
        self.assertEqual(ctx["pending_q_type"], "multiple_choice")
        self.assertIn("P(B|A)", ctx["pending_stem"])
        self.assertEqual(ctx["option_keys"], ["A", "B", "C", "D"])
        self.assertIn("0.4", ctx["option_values"])
        self.assertEqual(ctx["pending_knowledge_point"], "条件概率")

    def test_answered_sets_do_not_project(self):
        s = _session_with_pending()
        s.quiz_history[0]["questions"][0]["result"] = {"verdict": "correct"}
        ctx = _session_context(s)
        self.assertEqual(ctx["pending_stem"], "")
        self.assertEqual(ctx["option_keys"], [])

    def test_latest_set_with_unanswered_wins(self):
        s = _session_with_pending()
        s.quiz_history.append(_mc_set())  # 第二套未答
        s.quiz_history[0]["questions"][0]["result"] = {"verdict": "correct"}
        ctx = _session_context(s)
        self.assertEqual(ctx["option_keys"], ["A", "B", "C", "D"])

    def test_malformed_history_degrades_to_empty(self):
        s = TutorSession(session_id="sess_bad", student_id="stu", title="t")
        s.quiz_history = ["not-a-dict", {"questions": "junk"}, None]
        ctx = _session_context(s)
        self.assertEqual(ctx["pending_stem"], "")


class TestOptionPickResolution(unittest.TestCase):

    def test_bare_letter_resolves_to_pending_question(self):
        u = asyncio.run(understand("B", _session_with_pending(), None,
                                   use_llm=False))
        self.assertEqual(u.intent, TaskType.PRACTICE)
        self.assertEqual(u.goal, "answer_pending")
        self.assertEqual(u.source, "rule_option")
        self.assertEqual(u.concept, "条件概率")
        self.assertFalse(u.allow_followup_assessment)

    def test_prefixed_and_punctuated_variants(self):
        for msg in ("选C。", "答案是b", "我选 D", "A"):
            u = asyncio.run(understand(msg, _session_with_pending(), None,
                                       use_llm=False))
            self.assertEqual(u.goal, "answer_pending", msg)

    def test_option_value_text_matches(self):
        u = asyncio.run(understand("0.4", _session_with_pending(), None,
                                   use_llm=False))
        self.assertEqual(u.goal, "answer_pending")

    def test_non_option_short_text_stays_chitchat(self):
        # "E" 不是候选选项、"嗯" 是问候：无匹配时保持旧寒暄行为。
        for msg in ("E", "嗯", "好的"):
            u = asyncio.run(understand(msg, _session_with_pending(), None,
                                       use_llm=False))
            self.assertEqual(u.intent, TaskType.CHITCHAT, msg)

    def test_without_pending_question_b_is_chitchat(self):
        u = asyncio.run(understand("B", _session_with_concept(), None,
                                   use_llm=False))
        self.assertEqual(u.intent, TaskType.CHITCHAT)


class TestContinueResolution(unittest.TestCase):

    def test_continue_with_pending_question_engages_exercise(self):
        u = asyncio.run(understand("继续", _session_with_pending(), None,
                                   use_llm=False))
        self.assertEqual(u.goal, "answer_pending")
        self.assertEqual(u.source, "rule_continue")

    def test_continue_with_active_concept_continues_teaching(self):
        u = asyncio.run(understand("继续", _session_with_concept(), None,
                                   use_llm=False))
        self.assertEqual(u.intent, TaskType.EXPLAIN)
        self.assertEqual(u.concept, "牛顿第二定律")
        self.assertEqual(u.source, "rule_continue")

    def test_continue_without_context_stays_chitchat(self):
        s = TutorSession(session_id="sess_empty", student_id="stu", title="t")
        u = asyncio.run(understand("继续", s, None, use_llm=False))
        self.assertEqual(u.intent, TaskType.CHITCHAT)

    def test_greeting_unchanged_with_context(self):
        u = asyncio.run(understand("你好", _session_with_pending(), None,
                                   use_llm=False))
        self.assertEqual(u.intent, TaskType.CHITCHAT)
        self.assertEqual(u.source, "rule")


class _CaptureLLM:
    """Offline stand-in that records prompts and returns a fixed JSON."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        import json
        return json.dumps(self._payload), {}


class TestLLMContextBlock(unittest.TestCase):

    def test_context_block_rides_with_user_message(self):
        llm = _CaptureLLM({"intent": "practice", "concept": "条件概率",
                           "goal": "practice", "requires_tools": False,
                           "search_queries": []})
        u = asyncio.run(understand("这道题选哪个？为什么", _session_with_pending(),
                                   llm, use_llm=True))
        self.assertEqual(u.source, "llm")
        user_msg = llm.calls[0][1]["content"]
        self.assertIn("【会话上下文】", user_msg)
        self.assertIn("存在待答题目", user_msg)
        self.assertIn("条件概率", user_msg)

    def test_no_context_block_without_pending(self):
        llm = _CaptureLLM({"intent": "explain", "concept": "惯性",
                           "search_queries": []})
        asyncio.run(understand("讲一下惯性是什么", _session_with_concept(),
                               llm, use_llm=True))
        user_msg = llm.calls[0][1]["content"]
        self.assertIn("正在教学的概念", user_msg)

    def test_prompt_registry_has_130_active(self):
        p = get_prompt("understand_system")
        self.assertEqual(p.version, "1.3.0")
        self.assertIn("【会话上下文】", p.text)
        self.assertIn("引述", p.text)


class TestAnswerPendingPlan(unittest.TestCase):

    def test_answer_pending_plan_is_empty_no_quiz(self):
        from app.agents.state import TaskUnderstanding
        u = TaskUnderstanding(intent=TaskType.PRACTICE, concept="条件概率",
                              goal="answer_pending", source="rule_option")
        plan, goal = asyncio.run(make_plan(u, None, None, None, use_llm=False))
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.source, "rule")
        self.assertIn("练习", goal)


if __name__ == "__main__":
    unittest.main()
