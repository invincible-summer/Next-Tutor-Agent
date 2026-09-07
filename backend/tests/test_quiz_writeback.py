"""P0: quiz-answer write-back + next-turn visibility (作答回顾闭环).

Card grading (MC reveal / open-answer grading) happens outside the chat
stream. The quiz API writes the verdict back onto the matching question in
session.quiz_history, and the supervisor's status recap renders a one-line
「近期作答」 summary so "我的回答怎么样" can be answered truthfully.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.api.v1.quiz import _write_back_answer
from app.agents import supervisor
from app.core import session as session_mod
from app.core.session import TutorSession, load_session, save_session


def _session_with_quiz(sid: str) -> TutorSession:
    s = TutorSession(session_id=sid)
    s.quiz_history = [{
        "topic": "小数", "grade": "小学", "difficulty": "easy",
        "questions": [
            {"id": 1, "type": "multiple_choice",
             "stem": "0.25×0.4 的结果是（ ）", "answer": "B",
             "knowledge_point": "小数乘法"},
            {"id": 2, "type": "fill_blank",
             "stem": "将循环小数 0.6̄ 化为最简分数", "answer": "2/3",
             "knowledge_point": "小数与分数互化"},
        ],
    }]
    return s


class TestQuizWriteBack(unittest.TestCase):
    def setUp(self):
        self._orig = session_mod._SESSIONS_DIR
        session_mod._SESSIONS_DIR = Path(tempfile.mkdtemp(prefix="quiz_wb_"))

    def tearDown(self):
        session_mod._SESSIONS_DIR = self._orig

    def test_write_back_attaches_verdict_to_matching_question(self):
        save_session(_session_with_quiz("s_wb1"))
        _write_back_answer("s_wb1", stem="0.25×0.4 的结果是（ ）",
                           verdict="correct", student_answer="B")
        s = load_session("s_wb1")
        q1, q2 = s.quiz_history[0]["questions"]
        self.assertEqual(q1["result"]["verdict"], "correct")
        self.assertEqual(q1["result"]["student_answer"], "B")
        self.assertNotIn("result", q2)

    def test_write_back_skips_unknown_or_missing(self):
        save_session(_session_with_quiz("s_wb2"))
        _write_back_answer("s_wb2", stem="0.25×0.4 的结果是（ ）",
                           verdict="unknown", student_answer="A")
        _write_back_answer("s_wb2", stem="不存在的题干",
                           verdict="wrong", student_answer="x")
        _write_back_answer("", stem="0.25×0.4 的结果是（ ）",
                           verdict="wrong", student_answer="x")
        s = load_session("s_wb2")
        for q in s.quiz_history[0]["questions"]:
            self.assertNotIn("result", q)

    def test_write_back_newest_set_first(self):
        s = _session_with_quiz("s_wb3")
        s.quiz_history.append({
            "topic": "小数2", "questions": [
                {"id": 1, "stem": "0.25×0.4 的结果是（ ）",
                 "knowledge_point": "小数乘法"}]})
        save_session(s)
        _write_back_answer("s_wb3", stem="0.25×0.4 的结果是（ ）",
                           verdict="wrong", student_answer="A")
        s2 = load_session("s_wb3")
        self.assertNotIn("result", s2.quiz_history[0]["questions"][0])
        self.assertEqual(s2.quiz_history[1]["questions"][0]["result"]["verdict"],
                         "wrong")

    def test_recent_quiz_results_renders_marks(self):
        s = _session_with_quiz("s_wb4")
        s.quiz_history[0]["questions"][0]["result"] = {
            "verdict": "correct", "student_answer": "B"}
        s.quiz_history[0]["questions"][1]["result"] = {
            "verdict": "wrong", "student_answer": "3/2"}
        line = supervisor._recent_quiz_results(s)
        self.assertIn("近期作答", line)
        self.assertIn("第1题对（小数乘法）", line)
        self.assertIn("第2题错（小数与分数互化）", line)

    def test_recent_quiz_results_empty_when_unanswered(self):
        s = _session_with_quiz("s_wb5")
        self.assertEqual(supervisor._recent_quiz_results(s), "")

    def test_status_recap_includes_recent_answers(self):
        s = _session_with_quiz("s_wb6")
        s.quiz_history[0]["questions"][1]["result"] = {
            "verdict": "partial", "student_answer": "0.66"}
        recap = supervisor._status_recap(s)
        self.assertIn("已出1套题", recap)
        self.assertIn("第2题部分对", recap)


if __name__ == "__main__":
    unittest.main()
