"""聊天题卡身份、确定性出题与习题历史回归。"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.assessment import manager as assessment_manager
from app.agents.assessment.adaptive_test import CatInstance
from app.agents.student_model.evaluation import schema as evaluation_schema
from app.agents.state import TaskPlan, TaskType, TaskUnderstanding
from app.agents.supervisor import (_enforce_explicit_practice_plan,
                                   _explicit_quiz_count,
                                   _explicit_quiz_type)
from app.api.v1.assessment import CatStartRequest
from app.api.v1.quiz import _write_back_result
from app.core import learner_runtime
from app.core.session import TutorSession, load_session, save_session
from app.core.tool_base import Tool
from app.core.tool_protocol import ok
from app.core.trace import Trace
from app.identity import store as id_store
from app.identity.security import create_token, hash_password
from app.main import create_app
from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_unified_submission import FakeRunner


def _quiz_payload() -> dict:
    return {
        "topic": "条件概率",
        "questions": [{
            "id": 1,
            "type": "multiple_choice",
            "stem": "已知事件 A，条件概率的定义式是哪一个？",
            "options": {"A": "P(A)", "B": "P(A∩B)/P(A)"},
            "answer": "B",
            "explanation": "条件概率以事件 A 已发生为条件，定义为交集概率除以 A 的概率。",
            "knowledge_point": "条件概率",
            "difficulty": "easy",
            "verification": {"answer_verified": True},
        }],
    }


class _NoToolThenAnswerLLM:
    """模型故意漏调工具；executor 必须按 auto_invoke 补执行。"""

    def __init__(self) -> None:
        self.stream_calls = 0

    async def stream(self, messages, tools=None, **kwargs):
        self.stream_calls += 1
        yield {"kind": "answer", "delta": ("我先直接写一道文字题。" if
                                             self.stream_calls == 1 else
                                             "请在题卡中作答。")}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"total_tokens": 1}}

    async def complete(self, messages, **kwargs):
        return "", {"total_tokens": 0}


class _ToolCallingLLM(_NoToolThenAnswerLLM):
    async def stream(self, messages, tools=None, **kwargs):
        self.stream_calls += 1
        if self.stream_calls == 1:
            yield {"kind": "tool_calls", "calls": [{
                "id": "call_quiz", "name": "generate_quiz",
                "args": {"topic": "条件概率", "count": 1},
            }]}
        else:
            yield {"kind": "answer", "delta": "请在题卡中作答。"}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"total_tokens": 1}}


class _QuizTool(Tool):
    name = "generate_quiz"
    description = "生成题目"
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "grade": {"type": "string"},
            "difficulty": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["topic"],
    }

    async def run(self, **kwargs):
        return ok(self.name, _quiz_payload(), "已生成题卡")


async def _collect(gen) -> list[dict]:
    return [event async for event in gen]


class PracticePlanContractTest(unittest.TestCase):
    def test_explicit_count_and_default(self):
        self.assertEqual(_explicit_quiz_count("出 5 道题"), 5)
        self.assertEqual(_explicit_quiz_count("给我两道题"), 2)
        self.assertEqual(_explicit_quiz_count("考考我"), 1)

    def test_explicit_question_type(self):
        self.assertEqual(_explicit_quiz_type("出一道选择题"), "multiple_choice")
        self.assertEqual(_explicit_quiz_type("来个填空题"), "fill_blank")
        self.assertEqual(_explicit_quiz_type("请出简答题"), "short_answer")
        self.assertEqual(_explicit_quiz_type("考考我"), "")

    def test_practice_plan_is_auto_invoked(self):
        understanding = TaskUnderstanding(
            intent=TaskType.PRACTICE, concept="条件概率", goal="practice")
        plan = _enforce_explicit_practice_plan(
            TaskPlan(steps=[], source="rule"), understanding,
            "请出 3 道条件概率选择题", "高中", Trace())
        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertTrue(step.auto_invoke)
        self.assertEqual(step.suggested_tools, ["generate_quiz"])
        self.assertEqual(step.tool_args["generate_quiz"]["count"], 3)
        self.assertEqual(step.tool_args["generate_quiz"]["q_type"],
                         "multiple_choice")

    def test_assessment_defaults_to_one(self):
        self.assertEqual(CatStartRequest(concept_keys=["c"]).count, 1)
        self.assertEqual(CatInstance.from_detail({
            "assessment_id": "a"}).count_limit, 1)


class QuizCardPersistenceTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        learner_runtime.set_evaluation_runner(FakeRunner([]))
        id_store.create_user("cards@example.com", "Cards",
                             hash_password("pw123456"), user_id="usr_cards")
        self.token = create_token("usr_cards")
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def test_same_display_id_gets_unique_question_identity(self):
        payload_a = _quiz_payload()
        payload_b = _quiz_payload()
        assessment_manager.register_quiz_payload(
            student_id="usr_cards", workspace_id="", session_id="session_a",
            quiz_data=payload_a)
        assessment_manager.register_quiz_payload(
            student_id="usr_cards", workspace_id="", session_id="session_b",
            quiz_data=payload_b)
        qa = payload_a["questions"][0]
        qb = payload_b["questions"][0]
        self.assertEqual(qa["id"], qb["id"])
        self.assertNotEqual(qa["question_id"], qb["question_id"])

    def test_repeated_identical_sets_in_one_session_keep_distinct_identity(self):
        payload_a = _quiz_payload()
        payload_b = _quiz_payload()
        assessment_manager.register_quiz_payload(
            student_id="usr_cards", workspace_id="",
            session_id="session_repeat", quiz_data=payload_a)
        assessment_manager.register_quiz_payload(
            student_id="usr_cards", workspace_id="",
            session_id="session_repeat", quiz_data=payload_b)
        self.assertNotEqual(payload_a["quiz_set_id"], payload_b["quiz_set_id"])
        self.assertNotEqual(payload_a["questions"][0]["question_id"],
                            payload_b["questions"][0]["question_id"])

        original_id = payload_a["questions"][0]["question_id"]
        assessment_manager.register_quiz_payload(
            student_id="usr_cards", workspace_id="",
            session_id="session_repeat", quiz_data=payload_a)
        self.assertEqual(payload_a["questions"][0]["question_id"], original_id)

    def test_unanswered_then_answered_question_loads_from_history(self):
        session = TutorSession(session_id="session_history",
                               student_id="usr_cards", title="练习")
        payload = _quiz_payload()
        assessment_manager.register_quiz_payload(
            student_id="usr_cards", workspace_id="",
            session_id=session.session_id, quiz_data=payload)
        session.quiz_history = [payload]
        save_session(session)

        first = self.client.get("/api/v1/quiz/recent",
                                headers=self._headers())
        self.assertEqual(first.status_code, 200, first.text)
        rows = first.json()["questions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evaluation_status"], "unanswered")

        question = payload["questions"][0]
        submitted = self.client.post("/api/v1/quiz/record", json={
            "question_id": question["question_id"],
            "question_revision": 1,
            "student_answer": "B",
            "session_id": session.session_id,
        }, headers=self._headers())
        self.assertEqual(submitted.status_code, 202, submitted.text)
        second = self.client.get("/api/v1/quiz/recent",
                                 headers=self._headers()).json()["questions"]
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["verdict"], "correct")
        restored = load_session(session.session_id)
        self.assertEqual(
            restored.quiz_history[0]["questions"][0]["result"]["verdict"],
            "correct")

    def test_writeback_matches_question_id_when_display_id_is_numeric(self):
        session = TutorSession(session_id="session_writeback",
                               student_id="usr_cards")
        q = _quiz_payload()["questions"][0]
        q["question_id"] = "q_stable"
        session.quiz_history = [{"questions": [dict(q)]}]
        session.messages = [{
            "role": "assistant", "content": "请作答",
            "toolCalls": [{"name": "generate_quiz", "result": {
                "data": {"questions": [dict(q)]}}}],
        }]
        save_session(session)
        _write_back_result(session.session_id, "q_stable", "B", "correct",
                           "att_stable")
        restored = load_session(session.session_id)
        self.assertEqual(
            restored.quiz_history[0]["questions"][0]["result"]["attempt_id"],
            "att_stable")

    def test_v2_model_omission_still_emits_registered_card(self):
        from app.agents.supervisor import run
        session = TutorSession(session_id="session_auto", grade="高中",
                               student_id="usr_cards")
        env = {"SUPERVISOR_LLM_UNDERSTAND": "0",
               "SUPERVISOR_LLM_PLAN": "0",
               "SKILL_RUNTIME_MODE": "off"}
        with patch.dict(os.environ, env):
            events = asyncio.run(_collect(run(
                "请出一道条件概率题考我", session, [_QuizTool()],
                llm=_NoToolThenAnswerLLM(), student_id="usr_cards")))
        results = [e["result"] for e in events if e.get("type") == "tool_result"]
        self.assertEqual(len(results), 1)
        visible = "".join(str(e.get("content") or "") for e in events
                          if e.get("type") == "answer")
        self.assertNotIn("文字题", visible)
        self.assertIn("题卡", visible)
        question = results[0]["data"]["questions"][0]
        self.assertTrue(question["question_id"].startswith("q_"))
        self.assertEqual(len(session.quiz_history), 1)

    def test_legacy_tool_path_uses_same_registered_identity(self):
        from app.agents.chat_agent import chat_turn
        session = TutorSession(session_id="session_legacy", grade="高中",
                               student_id="usr_cards")
        events = asyncio.run(_collect(chat_turn(
            "请出一道条件概率题", session, [_QuizTool()],
            llm=_ToolCallingLLM())))
        results = [e["result"] for e in events if e.get("type") == "tool_result"]
        self.assertEqual(len(results), 1)
        question = results[0]["data"]["questions"][0]
        self.assertTrue(question["question_id"].startswith("q_"))
        task = assessment_manager.load_task_snapshot(
            "usr_cards", evaluation_schema.QuestionRef(
                question_id=question["question_id"], question_revision=1))
        self.assertEqual(task.source_session_ref, session.session_id)


if __name__ == "__main__":
    unittest.main()
