"""W3/D11 课堂小结与恢复锚点回归（updatePlan.md §7.2 D11）。

- 确定性骨架：只引用 quiz_history 已接受判定（demonstrated）；
  无测评 → 「已讲解，待验证」；待答计数、开放问题、恢复锚点；
- 恢复注入带空闲阈值（30 分钟内连续提问不打断）；
- active 润色只改两行文案、失败保留骨架；TutorSession 往返持久化。

纯内存用例（不触任何存储根）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.teaching_engine.session_summary import (  # noqa: E402
    RESUME_IDLE_SECONDS, build_session_summary, polish_summary,
    resume_preamble_line)
from app.core.session import TutorSession  # noqa: E402


def _quiz_set(questions):
    return {"topic": "条件概率", "grade": "本科", "difficulty": 3,
            "questions": questions, "answer_verified": True,
            "verification": {"mode": "critic", "critic": "ok"}}


def _q(stem, result=None):
    q = {"id": "q_s1_1", "type": "short_answer", "stem": stem,
         "answer": "0.4", "explanation": "解析",
         "knowledge_point": "条件概率", "difficulty": 3}
    if result is not None:
        q["result"] = result
    return q


class _QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, messages, **kw):
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item, {"total_tokens": 1}


class TestSkeleton(unittest.TestCase):

    def test_demonstrated_only_from_accepted_verdicts(self):
        s = TutorSession(session_id="s1", student_id="u1", title="t")
        s.quiz_history = [_quiz_set([
            _q("求 P(B|A)。", {"verdict": "correct", "attempt_id": "a1"}),
            _q("另一题。", {"verdict": "wrong", "attempt_id": "a2"}),
            _q("待答的题。"),
        ])]
        s.context_card = {"active_concepts": ["条件概率"]}
        summary = build_session_summary(s)
        self.assertEqual(len(summary["demonstrated"]), 1)
        self.assertIn("P(B|A)", summary["demonstrated"][0]["stem"])
        self.assertEqual(len(summary["open_items"]), 1)
        self.assertEqual(summary["pending_questions"], 1)
        self.assertEqual(summary["resume_anchor"]["pending"], 1)
        self.assertIn("待答", summary["resume_line"])

    def test_no_assessment_says_taught_not_verified(self):
        s = TutorSession(session_id="s2", student_id="u1", title="t")
        s.context_card = {"active_concepts": ["牛顿第二定律"]}
        summary = build_session_summary(s)
        self.assertEqual(summary["coverage_note"], "已讲解，待验证")
        self.assertIn("待验证", summary["summary_line"])
        self.assertNotIn("掌握", summary["summary_line"])

    def test_empty_session_returns_none(self):
        s = TutorSession(session_id="s3", student_id="u1", title="t")
        self.assertIsNone(build_session_summary(s))


class TestResumeInjection(unittest.TestCase):

    def _session_with_summary(self, built_at):
        s = TutorSession(session_id="s4", student_id="u1", title="t")
        s.context_card = {"active_concepts": ["条件概率"]}
        s.learning_summary = build_session_summary(s, now=built_at)
        return s

    def test_fresh_summary_not_injected(self):
        s = self._session_with_summary(built_at=time.time() - 60)
        self.assertEqual(resume_preamble_line(s), "")

    def test_idle_summary_injected_with_anchor(self):
        s = self._session_with_summary(
            built_at=time.time() - RESUME_IDLE_SECONDS - 10)
        line = resume_preamble_line(s)
        self.assertIn("[上次学习小结]", line)
        self.assertIn("恢复点：", line)

    def test_missing_summary_is_empty(self):
        s = TutorSession(session_id="s5", student_id="u1", title="t")
        self.assertEqual(resume_preamble_line(s), "")


class TestPolish(unittest.TestCase):

    def test_polish_replaces_lines(self):
        s = TutorSession(session_id="s6", student_id="u1", title="t")
        s.context_card = {"active_concepts": ["条件概率"]}
        summary = build_session_summary(s)
        llm = _QueueLLM([json.dumps(
            {"summary_line": "条件概率：能算但分母还差一步。",
             "resume_line": "下次先做 1 道不带提示的判断题。"},
            ensure_ascii=False)])
        polished = asyncio.run(polish_summary(summary, llm))
        self.assertIsNotNone(polished)
        self.assertIn("分母", polished[0])

    def test_polish_failure_keeps_skeleton(self):
        s = TutorSession(session_id="s7", student_id="u1", title="t")
        s.context_card = {"active_concepts": ["条件概率"]}
        summary = build_session_summary(s)
        self.assertIsNone(asyncio.run(polish_summary(
            summary, _QueueLLM(["garbage"]))))
        self.assertIsNone(asyncio.run(polish_summary(
            summary, _QueueLLM([RuntimeError("boom")]))))


class TestSessionRoundtrip(unittest.TestCase):

    def test_learning_summary_survives_save_load(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import app.core.session as sess_mod
        from app.core.session import load_session, save_session
        with tempfile.TemporaryDirectory() as td:
            s = TutorSession(session_id="s8", student_id="u1", title="t")
            s.context_card = {"active_concepts": ["条件概率"]}
            s.learning_summary = build_session_summary(s)
            with patch.object(sess_mod, "_SESSIONS_DIR", Path(td)):
                save_session(s)
                loaded = load_session("s8")
            self.assertIsNotNone(loaded.learning_summary)
            self.assertEqual(loaded.learning_summary["schema"],
                             "session_summary.v1")


if __name__ == "__main__":
    unittest.main()
