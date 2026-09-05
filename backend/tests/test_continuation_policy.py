"""W3/D10 CAT continuation 策略回归（docs/updatePlan.md §7.2 D10、§9.3）。

硬上限（should_stop 纯规则，未改动）永远优先；仅当硬规则放行时，
STRUCTURED_ASSESSMENT_MODE=active 才让结构化分析的 continue/probe/finish
建议生效（finish 映射 sufficient_evidence / insufficient_evidence，与作答
同一次落盘）；shadow 只落盘 continuation_shadow 对照、绝不改变走向；
off 与旧路径逐例一致。

全部用例继承 StorageSandboxTestCase。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.agents.assessment import get_assessment_manager, session_store  # noqa: E402
from app.agents.assessment.adaptive_test import AssessmentSession  # noqa: E402
from app.agents.assessment.question import Question, QuestionType  # noqa: E402
from app.agents.assessment.state import AssessmentContext, AssessmentGoal  # noqa: E402
from app.core.config import settings  # noqa: E402


def _rubric_question(qid: str = "q_cont_1", stem: str = "开放题：求 P(B|A)。",
                     difficulty: int = 3) -> Question:
    return Question(
        id=qid, concept="条件概率", difficulty=difficulty,
        q_type=QuestionType.SHORT_ANSWER, stem=stem, answer="0.4",
        explanation="P(B|A)=P(A∩B)/P(A)=0.4。",
        rubric={
            "rubric_id": qid, "version": 1,
            "criteria": [
                {"id": "c1", "description": "正确写出归一化分母 P(A)",
                 "weight": 1.0, "critical": True},
                {"id": "c2", "description": "完成除法计算", "weight": 1.0},
            ],
            "equivalent_solutions": [], "frozen_at": 1757000000.0,
        },
        verification={"mode": "critic", "critic": "ok"})


def _structured(*, results, action="finish", score=1.0, hypotheses=()):
    return {
        "criterion_results": [
            {"criterion_id": cid, "result": r, "evidence_quote": ""}
            for cid, r in results],
        "hypotheses": [{"kind": "concept_relation", "statement": h}
                       for h in hypotheses],
        "score": score, "verdict": "correct" if score == 1.0 else "partial",
        "rubric_id": "q_cont_1", "rubric_version": 1,
        "feedback": {"strength": "方向正确", "next_step": "说明分母含义"},
        "continuation": {"action": action, "reason": "测试"},
    }


def _session(sid: str, questions, results=(), count: int = 6) -> AssessmentSession:
    """Persisted session (sandboxed tests only)."""
    s = _pure_session(questions, results=results, count=count)
    s.student_id = sid
    session_store.save_session(sid, s.to_dict())
    return s


def _pure_session(questions, results=(), count: int = 6) -> AssessmentSession:
    """In-memory session (no disk) for pure decision-function tests."""
    return AssessmentSession(assessment_id="asmt_cont", student_id="",
                             goal=AssessmentGoal(concept="条件概率",
                                                 purpose="adaptive", count=count),
                             ctx=AssessmentContext(concept="条件概率",
                                                   subject="数学", grade="高中"),
                             questions=list(questions), results=list(results),
                             current_difficulty=3)


class TestDecisionFunction(unittest.TestCase):
    """Pure-function tests — no session is ever written to disk."""

    def test_hard_stop_wins_over_llm_finish(self):
        from app.agents.assessment.continuation_policy import decide_continuation
        s = AssessmentSession(current_difficulty=3)
        d = decide_continuation(s, _structured(results=[("c1", "met")],
                                               action="finish"),
                                hard_stop="mastered")
        self.assertEqual(d.source, "hard_rule")
        self.assertEqual(d.stop_reason, "mastered")

    def test_finish_solid_maps_sufficient(self):
        from app.agents.assessment.continuation_policy import decide_continuation
        s = _pure_session([_rubric_question()])
        d = decide_continuation(
            s, _structured(results=[("c1", "met"), ("c2", "met")],
                           action="finish", score=1.0))
        self.assertEqual(d.stop_reason, "sufficient_evidence")

    def test_finish_with_critical_unmet_maps_insufficient(self):
        from app.agents.assessment.continuation_policy import decide_continuation
        s = _pure_session([_rubric_question()])
        d = decide_continuation(
            s, _structured(results=[("c1", "not_met"), ("c2", "met")],
                           action="finish", score=0.5))
        self.assertEqual(d.stop_reason, "insufficient_evidence")

    def test_probe_targets_unmet_critical_then_hypothesis(self):
        from app.agents.assessment.continuation_policy import decide_continuation
        s = _pure_session([_rubric_question()])
        d = decide_continuation(
            s, _structured(results=[("c1", "partial"), ("c2", "met")],
                           action="probe", score=0.75,
                           hypotheses=["未把条件事件当新样本空间"]))
        self.assertEqual(d.action, "probe")
        self.assertEqual(d.probe_assesses,
                         ["正确写出归一化分母 P(A)",
                          "未把条件事件当新样本空间"])

    def test_no_structured_is_default_continue(self):
        from app.agents.assessment.continuation_policy import decide_continuation
        d = decide_continuation(AssessmentSession(), None)
        self.assertEqual(d.action, "continue")
        self.assertEqual(d.source, "default")


class _GenCapture:
    """Scripted generator capturing the goal it was asked to generate for."""

    def __init__(self, question: Question | None = None) -> None:
        self.question = question
        self.goals: list[AssessmentGoal] = []

    async def __call__(self, session, llm):
        self.goals.append(session.goal)
        # W3/D10 注入在 _gen_for_session 内部构造的临时 goal 上，捕捉方式：
        # 记录 session.probe_assesses 消费前的状态即可（下面用 disk 断言）。
        return self.question


class TestManagerWiring(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.am = get_assessment_manager()

    def _answer_with_structured(self, sid, structured, answer: str = "0.4"):
        """record_cat_answer with evaluate_and_record patched to return a
        result carrying the given structured payload; the mode is patched by
        the caller so each test states it explicitly."""
        from app.agents.assessment.state import AssessmentResult
        q = _rubric_question()
        _session(sid, [q])

        async def fake_eval(self, question, student_answer, ctx, **kw):
            r = AssessmentResult(question_id=q.id, concept="条件概率",
                                 verdict="partial" if structured["score"] < 1
                                 else "correct",
                                 score=structured["score"], feedback="ok")
            r.structured = structured
            return r

        with patch.object(type(self.am), "evaluate_and_record", fake_eval):
            result = asyncio.run(self.am.record_cat_answer(sid, answer=answer))
        return result

    def test_active_finish_persists_sufficient_in_same_save(self):
        sid = "st_cont_fin"
        with patch.object(settings, "structured_assessment_mode", "active"):
            self._answer_with_structured(
                sid, _structured(results=[("c1", "met"), ("c2", "met")],
                                 action="finish", score=1.0))
        disk = json.loads(session_store.session_path(sid).read_text())
        self.assertEqual(disk["status"], "stopped")
        self.assertEqual(disk["stop_reason"], "sufficient_evidence")

    def test_active_probe_directs_next_question_and_consumes(self):
        sid = "st_cont_probe"
        with patch.object(settings, "structured_assessment_mode", "active"):
            self._answer_with_structured(
                sid, _structured(results=[("c1", "partial"), ("c2", "met")],
                                 action="probe", score=0.75))
        disk = json.loads(session_store.session_path(sid).read_text())
        self.assertEqual(disk["status"], "active")   # probe 不终止
        self.assertEqual(disk["probe_assesses"][0], "正确写出归一化分母 P(A)")

        captured = {}

        async def gen(session, llm):
            captured["probe"] = list(session.probe_assesses)
            return _rubric_question("q_cont_2", "开放题二")

        with patch.object(self.am, "_gen_for_session", gen):
            sess, q, stop = asyncio.run(self.am.next_question(sid, llm=None))
        self.assertEqual(stop, "")
        self.assertIsNotNone(q)
        self.assertIn("正确写出归一化分母 P(A)", captured["probe"])
        # 生成后指令被消费
        disk = json.loads(session_store.session_path(sid).read_text())
        self.assertEqual(disk["probe_assesses"], [])

    def test_shadow_records_but_never_applies(self):
        sid = "st_cont_shadow"
        with patch.object(settings, "structured_assessment_mode", "shadow"):
            self._answer_with_structured(
                sid, _structured(results=[("c1", "met"), ("c2", "met")],
                                 action="finish", score=1.0))
        disk = json.loads(session_store.session_path(sid).read_text())
        self.assertEqual(disk["status"], "active")  # 判定不被 shadow 改变
        self.assertEqual(disk["stop_reason"], "")
        self.assertEqual(disk["continuation_shadow"]["action"], "finish")
        self.assertEqual(disk["continuation_shadow"]["stop_reason"],
                         "sufficient_evidence")

    def test_off_mode_leaves_no_continuation_keys(self):
        sid = "st_cont_off"
        with patch.object(settings, "structured_assessment_mode", "off"):
            self._answer_with_structured(
                sid, _structured(results=[("c1", "met"), ("c2", "met")],
                                 action="finish", score=1.0))
        disk = json.loads(session_store.session_path(sid).read_text())
        self.assertEqual(disk["status"], "active")
        self.assertEqual(disk["stop_reason"], "")
        self.assertEqual(disk.get("continuation_shadow"), {})

    def test_hard_rule_mastered_still_wins_in_active(self):
        from app.agents.assessment.state import AssessmentResult
        sid = "st_cont_hard"
        q1 = _rubric_question("q_h1", "开放一", difficulty=3)
        q2 = _rubric_question("q_h2", "开放二", difficulty=3)
        _session(sid, [q1, q2])

        async def fake_eval(self, question, student_answer, ctx, **kw):
            r = AssessmentResult(question_id=question.id, concept="条件概率",
                                 verdict="correct", score=1.0, feedback="ok")
            r.structured = _structured(results=[("c1", "met"), ("c2", "met")],
                                       action="continue", score=1.0)
            return r

        with patch.object(type(self.am), "evaluate_and_record", fake_eval):
            asyncio.run(self.am.record_cat_answer(sid, answer="0.4"))
            result2 = asyncio.run(self.am.record_cat_answer(sid, answer="0.5"))
        # 两题连对且难度>=3 → 硬规则 mastered 优先（LLM 建议 continue 无效）
        disk = json.loads(session_store.session_path(sid).read_text())
        self.assertEqual(disk["status"], "mastered")
        self.assertEqual(disk["stop_reason"], "mastered")
        self.assertIsNotNone(result2)


if __name__ == "__main__":
    unittest.main()
