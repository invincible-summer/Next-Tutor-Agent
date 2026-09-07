"""Learning-gain computation: the mastery delta produced by teaching.

Given a "before" mastery (captured at turn start) and an "after" mastery (read
after the turn's events are recorded), compute how much the student's P(know)
moved. This is M7's measure of teaching EFFECTIVENESS -- not "was the answer
right" (that's M4) but "did understanding actually improve."

A pure teaching turn with no quiz has before == after (gain = 0, not
measurable). Gain is only meaningful when assessment happened (quiz_graded
event moved the BKT posterior).

Pure functions; deterministic; zero LLM.
"""
from __future__ import annotations

from typing import Any

from .schema import LearningGain


def compute_gain(before: float | None, after: float | None, *,
                 concept: str = "", subject: str = "",
                 n_questions: int = 0, trace_id: str = "") -> LearningGain | None:
    """Compute a LearningGain from before/after mastery.

    Returns None when neither before nor after is available (nothing to
    measure). When only one side is known, the gain defaults to 0 with the
    available side filled in.
    """
    if before is None and after is None:
        return None
    b = float(before) if before is not None else float(after or 0.0)
    a = float(after) if after is not None else float(before or 0.0)
    gain = max(-1.0, min(1.0, a - b))
    return LearningGain(
        concept=concept, subject=subject,
        before=round(b, 4), after=round(a, 4),
        gain=round(gain, 4), n_questions=n_questions, trace_id=trace_id,
    )


def classify_effectiveness(gain: float | None, *,
                           n_questions: int = 0) -> str:
    """Map a learning gain onto a coarse effectiveness label.

    Used by the strategy analyzer and the evaluation directive to turn a raw
    number into an actionable signal. Returns one of:
    high / moderate / low / none / unmeasured.
    """
    if gain is None or n_questions == 0:
        return "unmeasured"
    if gain >= 0.3:
        return "high"
    if gain >= 0.1:
        return "moderate"
    if gain > 0.0:
        return "low"
    if gain == 0.0:
        return "none"
    return "low"  # negative gain: still "low" (regression), flagged elsewhere


def aggregate_gain(gains: list[LearningGain]) -> dict[str, float]:
    """Aggregate a list of LearningGains into summary stats.

    Returns {avg_gain, total, measured, avg_questions}. Pure function.
    """
    if not gains:
        return {"avg_gain": 0.0, "total": 0, "measured": 0, "avg_questions": 0.0}
    measured = [g for g in gains if g.n_questions > 0]
    total = len(gains)
    avg_gain = sum(g.gain for g in measured) / len(measured) if measured else 0.0
    avg_q = (sum(g.n_questions for g in measured) / len(measured)
             if measured else 0.0)
    return {
        "avg_gain": round(avg_gain, 4),
        "total": total,
        "measured": len(measured),
        "avg_questions": round(avg_q, 2),
    }
