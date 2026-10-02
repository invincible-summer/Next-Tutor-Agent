"""Response Quality Evaluator: score the effectiveness of ONE turn's expression.

This is the M8 component the M8 review requested. It answers a question that
NO other layer answers:

    M7 evaluates "is the SYSTEM getting better"  (cross-turn aggregation).
    M8 evaluates "was THIS explanation effective" (single-turn, per-response).

Both are evaluation signals, but at different scopes -- conflating them would
either lose per-turn granularity (M7 aggregates) or pollute the system-level
advisor with noise (M8's signal is too local). So they stay separate, exactly
like M3 (this lesson) vs M9 (this month).

DESIGN CONTRACT -- why this lives in M8, not M7:
  - It scores EXPRESSION quality, not academic correctness. A correct answer
    that the student found "too abstract" scores low here; that is a UX/
    presentation failure, not a knowledge failure. Academic correctness is M4's
    verdict (which the evaluator consumes read-only as one input).
  - It is PURE-FUNCTION and deterministic: it combines observable per-turn
    signals (the student's feedback phrasing, whether they kept asking
    follow-ups, the answer length vs their tolerance, the M4 verdict) into a
    communication_score + failure_reason + next_adjustment. Zero LLM.
  - It never writes M2/M3/M4/M7. It OWNS only the score artifact; the score
    feeds back into M8's own profile (adjusting example_density / abstraction)
    and is surfaced as a read-only metric for the API.

The evaluator's output closes a loop the review described:
    student says "还是不懂"
      -> evaluator records "Explanation failed: abstraction_too_high"
      -> next turn M8 raises example_density / lowers abstraction
This adjustment lives on the UXProfile as presentation hints, never on the
TeachingPlan (M3 owns what to teach; M8 owns how to present it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .schema import (DetailLevel, FeedbackType, Tone, UXProfile,
                     _MAX_RECENT_LENGTHS)


class ExpressionFailure(str, Enum):
    """Why a single turn's expression was ineffective (first-match wins).

    Mirrors M7's FailureType conceptually but at the EXPRESSION layer: M7
    diagnoses "where did the teaching go wrong" (depth/prereq/retrieval),
    while these diagnose "where did the PRESENTATION go wrong".
    """
    NONE = "none"                       # the explanation landed
    ABSTRACTION_TOO_HIGH = "abstraction_too_high"   # "看不懂/太抽象" -> too formal
    TOO_VERBOSE = "too_verbose"         # "太长了/太啰嗦" -> over-explained
    TOO_TERSE = "too_terse"             # "没讲清/太简略" -> under-explained
    WRONG_REGISTER = "wrong_register"   # tone mismatch (e.g. formal for a kid)
    PACE_MISMATCH = "pace_mismatch"     # "太快了/跟不上" or "太慢了"

    @classmethod
    def from_value(cls, v: Any) -> "ExpressionFailure":
        if isinstance(v, ExpressionFailure):
            return v
        try:
            return cls(str(v)) if v else cls.NONE
        except ValueError:
            return cls.NONE


@dataclass
class ResponseQualityScore:
    """The output of one evaluation: how well THIS turn was expressed.

    Stored as a UXEvent variant so it lives in M8's black box (ux_events.jsonl)
    alongside feedback/abandon signals. The score feeds the profile's
    presentation hints; the failure_reason feeds next-turn adjustment.
    """
    communication_score: float = 0.8       # 0.0 (total fail) .. 1.0 (great)
    failure: ExpressionFailure = ExpressionFailure.NONE
    follow_up_count: int = 0               # how many clarifying questions followed
    answer_length: int = 0
    over_length_tolerance: bool = False    # answer longer than this student tolerates
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "communication_score": round(self.communication_score, 3),
            "failure": self.failure.value,
            "follow_up_count": self.follow_up_count,
            "answer_length": self.answer_length,
            "over_length_tolerance": self.over_length_tolerance,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ResponseQualityScore":
        d = d or {}
        return cls(
            communication_score=float(d.get("communication_score", 0.8)),
            failure=ExpressionFailure.from_value(d.get("failure")),
            follow_up_count=int(d.get("follow_up_count", 0)),
            answer_length=int(d.get("answer_length", 0)),
            over_length_tolerance=bool(d.get("over_length_tolerance", False)),
            note=str(d.get("note", "")),
        )


# --- thresholds (characters), conservative and tunable ----------------------
_LONG_FOR_CONCISE = 900
_SHORT_FOR_DETAILED = 150


def evaluate_response(*, answer: str, feedback: FeedbackType,
                      profile: UXProfile, follow_up_count: int = 0,
                      verdict: str = "") -> ResponseQualityScore:
    """Score how effectively one turn was expressed. Pure function.

    Combines the student's UX feedback (rule-classified), the answer length
    vs the student's detail tolerance, the follow-up count (a "still
    confused" proxy), and the M4 verdict (read-only academic signal). Produces
    a communication_score + the first-matching ExpressionFailure + a note.
    Never raises; bad inputs yield a neutral score.
    """
    try:
        answer = answer or ""
        answer_len = len(answer)
        score = ResponseQualityScore(
            answer_length=answer_len, follow_up_count=max(0, follow_up_count))

        # base score from the feedback signal (the strongest single indicator)
        _feedback_scores = {
            FeedbackType.NONE: 0.85,
            FeedbackType.PRAISE: 1.0,
            FeedbackType.EXPLANATION_TOO_HARD: 0.25,
            FeedbackType.EXPLANATION_TOO_LONG: 0.35,
            FeedbackType.EXPLANATION_TOO_SHORT: 0.40,
            FeedbackType.TOO_FAST: 0.30,
            FeedbackType.TOO_SLOW: 0.50,
        }
        score.communication_score = _feedback_scores.get(feedback, 0.80)

        # length-vs-tolerance: did the answer overshoot this student's detail
        # level? (the detail level is M8-owned UX tolerance, NOT M2 academic
        # depth -- see schema.InteractionStyle)
        dl = profile.style.detail_level
        over_tol = False
        if dl == DetailLevel.CONCISE and answer_len > _LONG_FOR_CONCISE:
            over_tol = True
            score.communication_score = min(score.communication_score, 0.45)
        elif dl == DetailLevel.DETAILED and answer_len < _SHORT_FOR_DETAILED \
                and feedback == FeedbackType.EXPLANATION_TOO_SHORT:
            over_tol = True
        score.over_length_tolerance = over_tol

        # follow-up penalty: many clarifying questions => the explanation
        # failed to land (regardless of feedback words)
        if follow_up_count >= 3:
            score.communication_score = min(score.communication_score, 0.40)

        # M4 verdict (read-only): a wrong answer is not necessarily a UX
        # failure, but a wrong answer WITH negative feedback compounds it
        v = (verdict or "").lower()
        if v in ("wrong", "错", "incorrect") and feedback != FeedbackType.NONE:
            score.communication_score = min(score.communication_score, 0.35)

        # clamp + diagnose (first-match priority waterfall, like M7 trace_analyzer)
        if feedback == FeedbackType.PRAISE:
            score.failure = ExpressionFailure.NONE
            score.note = "学生明确表示听懂/认可"
        elif feedback == FeedbackType.EXPLANATION_TOO_HARD:
            score.failure = ExpressionFailure.ABSTRACTION_TOO_HIGH
            score.note = "抽象程度过高，学生反馈看不懂——建议增加生活化例子、降低术语密度"
        elif feedback == FeedbackType.EXPLANATION_TOO_LONG or over_tol:
            score.failure = ExpressionFailure.TOO_VERBOSE
            score.note = "讲解过于冗长，超出该生耐受——建议先给结论、精简展开"
        elif feedback == FeedbackType.EXPLANATION_TOO_SHORT:
            score.failure = ExpressionFailure.TOO_TERSE
            score.note = "讲解过于简略，学生反馈没讲清——建议展开关键步骤"
        elif feedback == FeedbackType.TOO_FAST:
            score.failure = ExpressionFailure.PACE_MISMATCH
            score.note = "节奏过快，学生跟不上——建议一次只推进一个要点"
        elif feedback == FeedbackType.TOO_SLOW:
            score.failure = ExpressionFailure.PACE_MISMATCH
            score.note = "节奏过慢——建议合并步骤、直奔要点"
        elif follow_up_count >= 3:
            score.failure = ExpressionFailure.ABSTRACTION_TOO_HIGH
            score.note = "多次追问，可能讲解抽象度偏高——建议增加例子"
        elif feedback == FeedbackType.NONE:
            score.failure = ExpressionFailure.NONE
            score.note = ""
        # final clamp
        score.communication_score = max(0.0, min(1.0, score.communication_score))
        return score
    except Exception:
        return ResponseQualityScore()


def apply_score_to_profile(score: ResponseQualityScore,
                           profile: UXProfile) -> None:
    """Fold an evaluation score into the profile's presentation hints.

    This is the feedback loop the review described: a failed explanation
    raises example_density / lowers abstraction for NEXT turn. The hints are
    advisory (the directive still goes through the LLM) and purely UX -- they
    adjust HOW to present, never WHAT to teach (that stays in M3's
    TeachingPlan, which M8 never mutates).
    """
    try:
        f = score.failure
        if f == ExpressionFailure.ABSTRACTION_TOO_HIGH:
            # student needs more concrete grounding: nudge toward examples +
            # visuals, and an encouraging tone (soft landing after confusion)
            profile.style.visual_preference = True
            if profile.style.tone != Tone.ENCOURAGING:
                profile.style.tone = Tone.ENCOURAGING
        elif f == ExpressionFailure.TOO_VERBOSE:
            # shorten future answers for this student
            if profile.style.detail_level == DetailLevel.DETAILED:
                profile.style.detail_level = DetailLevel.MEDIUM
            elif profile.style.detail_level == DetailLevel.MEDIUM:
                profile.style.detail_level = DetailLevel.CONCISE
        elif f == ExpressionFailure.TOO_TERSE:
            if profile.style.detail_level == DetailLevel.CONCISE:
                profile.style.detail_level = DetailLevel.MEDIUM
        elif f == ExpressionFailure.PACE_MISMATCH:
            profile.style.pacing = "steady"
    except Exception:
        pass
