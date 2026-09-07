"""Rule-based trace analyzer: diagnose WHERE a teaching turn failed.

This is M7's per-turn diagnostic engine (zero LLM, deterministic). Given the
signals captured in a TurnTrace (mode, outcome, tool calls, mastery before/
after, mistakes, misconceptions), it classifies the failure locus and produces
a cause + recommendation.

The classification is a priority cascade -- first match wins -- so each turn
gets exactly one diagnosis. This is the difference between a scoreboard
("wrong answer") and a feedback instrument ("the explanation was too deep;
the student is missing a prerequisite").

Priority order:
  1. RETRIEVAL_MISS    : knowledge_search was called but the turn failed
  2. PREREQUISITE_MISSING : outcome wrong AND unmet prerequisites were flagged
  3. TEACHING_DEPTH_MISMATCH : outcome wrong, low mastery, no remediation used
  4. STRATEGY_MISMATCH : wrong outcome with a repeated mode (didn't advance)
  5. ASSESSMENT_TOO_HARD : quiz answered wrong, difficulty was at the top band
  6. NO_ASSESSMENT     : taught but never assessed (can't measure gain)
  7. NONE              : outcome was correct/engaged (success)
"""
from __future__ import annotations

from typing import Any

from .schema import FailureType


def diagnose(*, mode: str = "", outcome: str = "unknown",
             tool_calls: list[str] | None = None,
             before_mastery: float | None = None,
             after_mastery: float | None = None,
             learning_gain: float | None = None,
             unmet_prereqs: list[str] | None = None,
             misconceptions: list[str] | None = None,
             quiz_difficulty: str = "",
             had_assessment: bool = False,
             ) -> tuple[FailureType, str, str]:
    """Diagnose a completed teaching turn.

    Returns (failure_type, cause, recommendation). cause is a short factual
    explanation; recommendation is an actionable next-step string. Both are ''
    when failure_type is NONE.

    Pure function of the inputs; deterministic; zero LLM.
    """
    tools = [str(t).lower() for t in (tool_calls or [])]
    outcome_l = str(outcome).lower()
    unmet = unmet_prereqs or []
    miscon = misconceptions or []

    # a "wrong" outcome is the primary failure signal
    is_wrong = outcome_l in ("wrong", "partial") or outcome_l == "\u9519"
    is_success = outcome_l in ("correct", "engaged") or outcome_l == "\u5bf9"

    # 1. retrieval miss: knowledge_search ran but the turn still failed
    if "knowledge_search" in tools and is_wrong:
        return (
            FailureType.RETRIEVAL_MISS,
            "knowledge_search returned results but teaching still failed; "
            "the retrieved material may not match the student's gap.",
            "Re-query with more specific keywords, or teach from the LLM's "
            "own knowledge and verify understanding with a check question.",
        )

    # 2. prerequisite missing: wrong outcome with flagged unmet prerequisites
    if is_wrong and unmet:
        names = "\u3001".join(unmet[:3])
        return (
            FailureType.PREREQUISITE_MISSING,
            f"student prerequisite missing ({names}); the concept was taught "
            "before the foundation was solid.",
            f"Insert a brief prerequisite review of {names} before "
            "re-explaining the target concept.",
        )

    # 3. teaching depth mismatch: wrong outcome, low mastery, not in remediation
    bm = before_mastery if before_mastery is not None else 0.0
    if is_wrong and before_mastery is not None and bm < 0.3 and mode \
           and mode.lower() not in ("remediation", "practice", "review"):
       return (
            FailureType.TEACHING_DEPTH_MISMATCH,
            f"explanation level too high for current mastery ({bm:.2f}); "
            "the student was at novice level but received a full explanation.",
            "Drop to INTRODUCTION mode: start with intuition and analogies, "
            "defer formal derivation until mastery rises above 0.3.",
        )

    # 4. strategy mismatch: wrong outcome with remediation already used (a
    #    repeated failure even after error correction signals the mode itself
    #    doesn't fit this student)
    if is_wrong and mode and mode.lower() == "remediation" and miscon:
        return (
            FailureType.STRATEGY_MISMATCH,
            "remediation was already attempted but the misconception persists; "
            "the current correction approach does not resonate with this student.",
            "Switch to a different teaching style (e.g. visual analogy or "
            "concrete worked example) rather than repeating the same correction.",
        )

    # 5. assessment too hard: quiz answered wrong, difficulty at the top
    diff = str(quiz_difficulty).lower()
    if is_wrong and diff in ("hard", "5") and had_assessment:
        return (
            FailureType.ASSESSMENT_TOO_HARD,
            "the quiz difficulty was set too high relative to the student's "
            "current level; the wrong answer reflects difficulty, not "
            "misunderstanding.",
            "Lower the next quiz difficulty by one level to confirm whether "
            "the concept itself is understood.",
        )

    # 6. no assessment: taught but never measured (can't verify learning)
    if mode and mode.lower() not in ("practice",) and not had_assessment \
            and outcome_l in ("engaged", "unknown", ""):
        return (
            FailureType.NO_ASSESSMENT,
            "the concept was taught but no assessment followed, so learning "
            "cannot be verified.",
            "Add a short check question at the end to confirm understanding.",
        )

    # 7. none: success path
    if is_success:
        return (FailureType.NONE, "", "")

    # fallback: wrong outcome with no specific signal
    if is_wrong:
        return (
            FailureType.STRATEGY_MISMATCH,
            "the teaching approach produced a wrong outcome; no specific root "
            "cause was identifiable from available signals.",
            "Review the student's recent mistakes and try an alternative "
            "explanation angle.",
        )

    return (FailureType.NONE, "", "")


def apply_diagnosis(trace, *, unmet_prereqs: list[str] | None = None,
                    misconceptions: list[str] | None = None,
                    quiz_difficulty: str = "",
                    had_assessment: bool = False) -> None:
    """Mutate a TurnTrace in-place: set failure_type/cause/recommendation.

    Convenience wrapper around diagnose() that writes the result back onto the
    trace dataclass. Never raises.
    """
    try:
        ft, cause, rec = diagnose(
            mode=trace.mode, outcome=trace.outcome,
            tool_calls=trace.tool_calls,
            before_mastery=trace.before_mastery,
            after_mastery=trace.after_mastery,
            learning_gain=trace.learning_gain,
            unmet_prereqs=unmet_prereqs,
            misconceptions=misconceptions,
            quiz_difficulty=quiz_difficulty,
            had_assessment=had_assessment,
        )
        trace.failure_type = ft.value
        trace.failure_cause = cause
        trace.recommendation = rec
    except Exception:
        pass


def recurring_failure_pattern(traces: list, *, concept: str = "",
                              subject: str = "", window: int = 10) -> dict[str, Any] | None:
    """Detect a recurring failure pattern in recent traces.

    Scans the last `window` traces for the same concept/subject. If the same
    FailureType appears >= 2 times, returns a summary dict (failure_type,
    count, last_recommendation, sample_concept) for the evaluation directive.
    Returns None when no pattern is strong enough to act on.

    Pure function; deterministic.
    """
    if not traces:
        return None
    # filter to concept/subject if specified
    pool = []
    for t in reversed(traces[-window:]):
        if concept and t.concept and concept.lower() not in t.concept.lower() \
                and t.concept.lower() not in concept.lower():
            continue
        if subject and t.subject and subject.lower() not in t.subject.lower():
            continue
        pool.append(t)
    if len(pool) < 2:
        return None
    counts: dict[str, int] = {}
    last_rec: dict[str, str] = {}
    last_concept: dict[str, str] = {}
    for t in pool:
        ft = t.failure_type or FailureType.NONE.value
        if ft == FailureType.NONE.value:
            continue
        counts[ft] = counts.get(ft, 0) + 1
        if t.recommendation:
            last_rec[ft] = t.recommendation
        if t.concept:
            last_concept[ft] = t.concept
    if not counts:
        return None
    top = max(counts, key=counts.get)
    if counts[top] < 2:
        return None
    return {
        "failure_type": top,
        "count": counts[top],
        "recommendation": last_rec.get(top, ""),
        "concept": last_concept.get(top, concept or ""),
    }
