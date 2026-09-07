"""Teaching mode selection: the core policy decision of the teaching engine.

Given a TeachingContext (mastery + unmet prereqs + misconceptions + the
current task type + the PREVIOUS mode/outcome from teaching_log), decide which
of six teaching modes to use this turn:

    INTRODUCTION  : first contact -- intuition + analogies, no formula piling
    EXPLANATION   : knows the concept weakly -- concept + worked example + variant
    REMEDIATION   : a confirmed gap (unmet prereq OR active misconception) blocks it
    PRACTICE      : concept is partly solid -- apply, surface errors
    REVIEW        : consolidate / summarize (task asks for review)
    CHALLENGE     : mastered -- synthesis + transfer problems

The decision is a deterministic state machine (DESIGN M3 §3), pure function of
the context, no LLM. The cross-turn advance (INTRODUCTION -> EXPLANATION ->
PRACTICE -> CHALLENGE) is the single most important behavior here: it is what
lets the tutor say "last time you got the direction right, today we apply the
formula" instead of re-introducing every turn.

Priority order (first match wins):
  1. explicit task intent can pin a mode (practice/review)
  2. an active misconception OR a hard prereq gap forces REMEDIATION
  3. mastery band selects INTRODUCTION / EXPLANATION / PRACTICE / CHALLENGE
  4. cross-turn advancement can bump the band up by one when the previous
     turn's outcome was a clean CORRECT (reward progress, don't stall)
  5. default EXPLANATION
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from .state import BAND_NOVICE, BAND_PROGRESSING, BAND_STRONG, TeachingContext, TeachingOutcome


class TeachingMode(str, Enum):
    """The six teaching modes. str-Enum so it serializes to JSON cleanly."""
    INTRODUCTION = "introduction"
    EXPLANATION = "explanation"
    REMEDIATION = "remediation"
    PRACTICE = "practice"
    REVIEW = "review"
    CHALLENGE = "challenge"

    @classmethod
    def from_value(cls, v: Any) -> "TeachingMode":
        if isinstance(v, TeachingMode):
            return v
        try:
            return cls(str(v)) if v else cls.EXPLANATION
        except ValueError:
            return cls.EXPLANATION


# the canonical forward progression used for cross-turn advancement.
# REMEDIATION is a sidestep (fix a gap), not a rung, so it is excluded.
_PROGRESSION: tuple[TeachingMode, ...] = (
    TeachingMode.INTRODUCTION,
    TeachingMode.EXPLANATION,
    TeachingMode.PRACTICE,
    TeachingMode.CHALLENGE,
)


def _band_mode(mastery: float) -> TeachingMode:
    """Map a raw mastery score onto the band mode (ignoring cross-turn state)."""
    if mastery < BAND_NOVICE:
        return TeachingMode.INTRODUCTION
    if mastery < BAND_PROGRESSING:
        return TeachingMode.EXPLANATION
    if mastery < BAND_STRONG:
        return TeachingMode.PRACTICE
    return TeachingMode.CHALLENGE


def _advance(previous: TeachingMode, outcome: TeachingOutcome) -> TeachingMode | None:
    """If the previous turn ended in a clean CORRECT, advance one rung up the
    progression. Returns None when no advancement applies."""
    if outcome != TeachingOutcome.CORRECT:
        return None
    try:
        idx = _PROGRESSION.index(previous)
    except ValueError:
        return None  # previous was REMEDIATION/REVIEW or unknown
    if idx + 1 < len(_PROGRESSION):
        return _PROGRESSION[idx + 1]
    return None


def select_strategy(ctx: TeachingContext) -> TeachingMode:
    """Decide the teaching mode for this turn. Pure, deterministic.

    Never raises; any malformed context field degrades to a safe mode.
    """
    # 0. explicit task intent can pin a mode (review is a hard pin)
    task = (ctx.task_type or "").lower()
    if task == "review":
        return TeachingMode.REVIEW
    # practice intent: still respect a hard prerequisite gap, but otherwise pin
    if task == "practice":
        if ctx.has_unmet_prereqs and ctx.mastery < BAND_NOVICE:
            return TeachingMode.REMEDIATION
        return TeachingMode.PRACTICE

    # 1. a confirmed gap forces remediation (misconception or hard prereq).
    #    Misconception takes priority: a wrong idea is more urgent than a
    #    merely-unseen prereq, and "重新建立模型" must come before new content.
    if ctx.has_misconception:
        return TeachingMode.REMEDIATION
    if ctx.has_unmet_prereqs and ctx.mastery < BAND_NOVICE:
        # only treat prereqs as blocking when the student is also a novice on
        # the target -- a progressing student can usually follow a quick recap
        # inline (handled by policy's review_first) without a full remediation.
        return TeachingMode.REMEDIATION

    # 2. mastery band -> base mode
    base = _band_mode(ctx.mastery)

    # 3. cross-turn advancement: a clean CORRECT on the previous turn bumps
    #    the band up by one rung. This is the "上次你做对了，今天进下一步" path.
    previous = TeachingMode.from_value(ctx.previous_mode)
    advanced = _advance(previous, ctx.previous_outcome)
    if advanced is not None:
        try:
            if _PROGRESSION.index(advanced) > _PROGRESSION.index(base):
                base = advanced
        except ValueError:
            pass

    # 4. first touch with no evidence -> INTRODUCTION, regardless of the prior
    #    L0 prior (0.0 would already map there, but be explicit for clarity)
    if ctx.turns_on_concept == 0 and ctx.mastery < BAND_NOVICE:
        return TeachingMode.INTRODUCTION

    return base
