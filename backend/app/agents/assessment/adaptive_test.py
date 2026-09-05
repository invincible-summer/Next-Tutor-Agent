"""Computerized Adaptive Testing (M4 Phase 3): the "just hard enough" quiz loop.

A fixed 10-question quiz wastes the student's time on questions too easy or too
hard. A CAT instead picks each next question's difficulty from how the previous
answers went: answer right -> step up, answer wrong -> step down, stop when the
system is confident the student is either solid or genuinely stuck.

This module holds the PURE decision functions (stop rules + difficulty step) and
the AssessmentSession data structure. The LLM-driven question generation lives
in generator.py; this module never calls an LLM. Deterministic, testable, zero
latency -- the same "rules before LLM" stance as M3's strategy/difficulty.

Difficulty stepping MIRRORS the thresholds of
teaching_engine.difficulty.compute_difficulty (>=80% up, <=40% down, PARTIAL
half, clamp [1,5]) so the two difficulty systems agree.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .question import Question
from .state import AssessmentContext, AssessmentGoal, AssessmentResult, ScoreLevel

_MIN_D = 1
_MAX_D = 5
_WINDOW = 5
_ACC_UP = 0.8
_ACC_DOWN = 0.4

STOP_MASTERED = "mastered"
STOP_CONFIRMED_GAP = "confirmed_gap"
STOP_MAX = "max_reached"
STOP_OSCILLATING = "oscillating"


def new_assessment_id() -> str:
    """A fresh independent assessment id (W2/A03: the CAT lifecycle key).

    ``session_id`` on the session predates this and was never populated (the
    API even echoed the *student* id under that name); assessment_id is the
    real per-run identity that survives refresh and appears in responses,
    the ledger and M2 events."""
    return "asmt_" + uuid.uuid4().hex[:16]


@dataclass
class AssessmentSession:
    """The cross-question state of one adaptive test.

    Held in memory during a turn and persisted between turns
    (students/<id>.assessment.json) so a CAT can resume across messages.
    Terminal states (mastered/stopped/abandoned) also stay persisted — the
    report and audit read them; only a new start replaces the slot.
    """
    session_id: str = ""
    assessment_id: str = ""
    student_id: str = ""
    goal: AssessmentGoal = field(default_factory=AssessmentGoal)
    ctx: AssessmentContext = field(default_factory=AssessmentContext)
    questions: list[Question] = field(default_factory=list)
    results: list[AssessmentResult] = field(default_factory=list)
    current_difficulty: int = 2
    status: str = "active"   # active | mastered | stuck | stopped | abandoned
    stop_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "assessment_id": self.assessment_id,
            "student_id": self.student_id,
            "goal": self.goal.to_dict(), "ctx": self.ctx.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "results": [r.to_dict() for r in self.results],
            "current_difficulty": self.current_difficulty,
            "status": self.status, "stop_reason": self.stop_reason,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @property
    def answered_count(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)


def next_difficulty(session: AssessmentSession) -> int:
    """Pick the difficulty for the NEXT question from recent answers.

    Mirrors teaching_engine.compute_difficulty thresholds: >=80% recent correct
    steps up, <=40% steps down, PARTIAL counts half. Clamped to [1, 5].
    """
    recent = session.results[-_WINDOW:]
    if not recent:
        return max(_MIN_D, min(_MAX_D, session.current_difficulty))
    acc = sum(r.score for r in recent) / len(recent)
    base = session.current_difficulty
    if acc >= _ACC_UP:
        return min(_MAX_D, base + 1)
    if acc <= _ACC_DOWN:
        return max(_MIN_D, base - 1)
    return base


def _recent_verdicts(session: AssessmentSession, n: int) -> list[str]:
    return [r.verdict for r in session.results[-n:]]


def should_stop(session: AssessmentSession) -> str:
    """Decide whether the CAT should stop. Returns "" to continue, else a reason.

    Four rules (first match wins), all pure functions of the session:
      mastered       : last 2 answers correct AND difficulty >= 3 (solid).
      confirmed_gap  : last 2 answers wrong AND difficulty at the floor.
      max_reached    : answered the goal.count cap.
      oscillating    : >=4 answers alternating right/wrong -- enough signal.
    """
    n = session.answered_count
    if n >= max(1, int(session.goal.count or 99)):
        return STOP_MAX
    recent = _recent_verdicts(session, 4)
    if n >= 2 and recent[-2:] == ["correct", "correct"] and session.current_difficulty >= 3:
        return STOP_MASTERED
    if n >= 2 and recent[-2:] == ["wrong", "wrong"] and session.current_difficulty <= _MIN_D:
        return STOP_CONFIRMED_GAP
    if n >= 4:
        tail = recent[-4:]
        if all(tail[i] != tail[i + 1] for i in range(len(tail) - 1)):
            return STOP_OSCILLATING
    return ""


def summary(session: AssessmentSession) -> dict[str, Any]:
    """A compact report of a finished/ongoing CAT.

    bloom: per-cognitive-level breakdown (question tag joined with its graded
    verdict) for the summary card's level distribution; untagged questions
    are skipped there — never a gate, purely reporting."""
    correct = sum(1 for r in session.results if r.verdict == "correct")
    wrong = sum(1 for r in session.results if r.verdict == "wrong")
    partial = sum(1 for r in session.results if r.verdict == "partial")
    bloom_breakdown: dict[str, dict[str, int]] = {}
    try:
        q_by_id = {q.id: q for q in session.questions}
        for r in session.results:
            q = q_by_id.get(r.question_id)
            lv = getattr(q, "bloom_level", "") if q else ""
            if not lv:
                continue
            b = bloom_breakdown.setdefault(
                lv, {"asked": 0, "correct": 0, "partial": 0, "wrong": 0})
            b["asked"] += 1
            if r.verdict in ("correct", "partial", "wrong"):
                b[r.verdict] += 1
    except Exception:
        bloom_breakdown = {}
    return {
        "concept": session.goal.concept or session.ctx.concept,
        "assessment_id": session.assessment_id,
        "answered": session.answered_count,
        "correct": correct, "wrong": wrong, "partial": partial,
        "accuracy": round(session.accuracy, 2),
        "final_difficulty": session.current_difficulty,
        "status": session.status, "stop_reason": session.stop_reason,
        "bloom": bloom_breakdown,
    }
