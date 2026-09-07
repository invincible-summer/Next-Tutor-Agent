"""M9 quiz-evidence feed regressions (W4/A08, updatePlan.md §12.2 W4 行).

Pins the committed-event contract: quiz verdicts graded on /quiz/record,
/quiz/grade and /assessment/answer (all outside chat turns) reach M9 via
record_quiz_evidence — SRS quality updates, attempt-idempotency (a replayed
submission never grows the interval twice), the positive quiz_evidence
orchestration event, and unknown staying exposure-only (A07). Also pins the
M2 event's additive session_id key (previously dropped by the facade).

All storage goes through StorageSandboxTestCase — no production root is ever
touched.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from app.agents.assessment import get_assessment_manager  # noqa: E402
from app.agents.assessment.adaptive_test import AssessmentSession  # noqa: E402
from app.agents.assessment.question import Question, QuestionType  # noqa: E402
from app.agents.assessment.state import (AssessmentContext,  # noqa: E402
                                         AssessmentGoal, AssessmentResult)
from app.agents.assessment import session_store  # noqa: E402
from app.agents.learning_orchestration import (  # noqa: E402
    get_orchestration_service)
from app.agents.learning_orchestration import store as orch_store  # noqa: E402
from app.core.quiz_attempts import record_quiz_attempt  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class TestQuizAttemptFanout(StorageSandboxTestCase):
    """The /quiz/* grading bridge feeds M9 (manager-level, no HTTP)."""

    def test_record_quiz_attempt_feeds_m9_srs_and_event(self):
        # A08：判分在 /quiz/* 轮外发生——此前该路径对 M9 零输出。
        record_quiz_attempt(
            "sess_feed", stem="1+1=?", verdict="correct", student_answer="2",
            concept="加法", student_id="st_feed", attempt_id="att_feed1")
        card = get_orchestration_service().summary("st_feed")[
            "review_queue"].get("加法")
        self.assertIsNotNone(card)
        self.assertEqual(card["repetitions"], 1)
        self.assertEqual(card["interval"], 1)
        events = [e for e in orch_store.read_events("st_feed")
                  if e.type == "quiz_evidence"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload.get("attempt_id"), "att_feed1")
        self.assertEqual(events[0].payload.get("session_id"), "sess_feed")

    def test_record_quiz_attempt_idempotent_per_attempt(self):
        for _ in range(2):
            record_quiz_attempt(
                "sess_feed", stem="1+1=?", verdict="correct", student_answer="2",
                concept="加法", student_id="st_feed", attempt_id="att_feed2")
        card = get_orchestration_service().summary("st_feed")[
            "review_queue"]["加法"]
        self.assertEqual(card["repetitions"], 1)
        self.assertEqual(len([e for e in orch_store.read_events("st_feed")
                              if e.type == "quiz_evidence"]), 1)

    def test_record_quiz_attempt_unknown_zero_m9_writes(self):
        # unknown：无有效召回证据，M9 零写入（A07）。
        record_quiz_attempt(
            "sess_feed", stem="1+1=?", verdict="unknown", student_answer="?",
            concept="加法", student_id="st_feed", attempt_id="att_feed3")
        summary = get_orchestration_service().summary("st_feed")
        self.assertEqual(summary["review_queue"], {})
        self.assertEqual([e for e in orch_store.read_events("st_feed")
                          if e.type == "quiz_evidence"], [])

    def test_record_quiz_attempt_orchestration_off_is_safe(self):
        with patch.dict("os.environ", {"ORCHESTRATION_MODE": "0"}):
            record_quiz_attempt(
                "sess_feed", stem="1+1=?", verdict="correct", student_answer="2",
                concept="加法", student_id="st_feed", attempt_id="att_feed4")
        summary = get_orchestration_service().summary("st_feed")
        self.assertEqual(summary["review_queue"], {})


class TestCatAnswerFeed(StorageSandboxTestCase):
    """The CAT grading path feeds M9 the same way (manager-level, no LLM)."""

    def setUp(self) -> None:
        super().setUp()
        self.am = get_assessment_manager()
        self.sid = "st_catfeed"

    def _seed_active_session(self) -> None:
        q = Question(
            id="q_feed_1", concept="条件概率", knowledge_points=["条件概率"],
            difficulty=3, q_type=QuestionType.MULTIPLE_CHOICE,
            stem="题干", options={"A": "甲", "B": "乙"}, answer="A",
            explanation="解析")
        session = AssessmentSession(
            student_id=self.sid, assessment_id="asmt_feed",
            goal=AssessmentGoal(concept="条件概率", purpose="adaptive",
                                count=3, difficulty=3),
            ctx=AssessmentContext(concept="条件概率", subject="数学"))
        session.questions = [q]
        session_store.save_session(self.sid, session.to_dict())

    def test_cat_answer_feeds_m9(self):
        self._seed_active_session()

        async def fake_eval(question, answer, ctx, **kwargs):
            return AssessmentResult(question_id=question.id,
                                    concept="条件概率", verdict="correct",
                                    score=1.0)

        with patch.object(self.am, "evaluate_and_record", fake_eval):
            result = _run(self.am.record_cat_answer(self.sid, answer="A"))
        self.assertEqual(result.verdict, "correct")
        card = get_orchestration_service().summary(self.sid)[
            "review_queue"].get("条件概率")
        self.assertIsNotNone(card)
        self.assertEqual(card["repetitions"], 1)
        events = [e for e in orch_store.read_events(self.sid)
                  if e.type == "quiz_evidence"]
        self.assertEqual(len(events), 1)
        self.assertTrue(str(events[0].payload.get("session_id") or "")
                        .startswith("assessment:"))


class TestM2EventSessionId(StorageSandboxTestCase):
    """W4/A08: the M2 event carries the source session (additive key)."""

    def test_event_carries_session_id(self):
        from app.agents.student_model import record_quiz_result
        from app.agents.student_model import store as sm_store
        record_quiz_result(
            concept="数列", correct=True, session_id="sess_m2feed",
            knowledge_point="数列", student_id="st_m2feed",
            verdict="correct", attempt_id="att_m2feed")
        events = [e for e in sm_store.read_events("st_m2feed")
                  if e.payload.get("attempt_id") == "att_m2feed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload.get("session_id"), "sess_m2feed")

    def test_event_omits_blank_session_id(self):
        from app.agents.student_model import record_quiz_result
        from app.agents.student_model import store as sm_store
        record_quiz_result(
            concept="数列", correct=False, student_id="st_m2feed",
            verdict="wrong", attempt_id="att_m2feed2")
        events = [e for e in sm_store.read_events("st_m2feed")
                  if e.payload.get("attempt_id") == "att_m2feed2"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("session_id", events[0].payload)


if __name__ == "__main__":
    unittest.main()
