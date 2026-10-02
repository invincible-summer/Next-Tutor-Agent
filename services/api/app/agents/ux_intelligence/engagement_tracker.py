"""Engagement tracker: derive presentation preferences from how the student
actually engages with answers.

Two signal sources, both pure functions:
  - response length: the assistant answer length this turn feeds a rolling
    average; sustained long answers + abandon heuristics => lower patience /
    shorter preferred detail.
  - abandon heuristic: a long answer with no follow-up interaction is a weak
    "they left" signal. Since the backend can't see viewport scroll, we use a
    conservative proxy: when a student EXPLICITLY complains the answer was too
    long (FeedbackType.EXPLANATION_TOO_LONG) we treat it as one abandon signal.
    This avoids fabricating abandonment from noise.

These signals adjust InteractionStyle (tone/detail/patience), which is exactly
the UX-only state M8 owns. They never touch academic state.
"""
from __future__ import annotations

from .schema import (DetailLevel, FeedbackType, InteractionStyle, Tone,
                     UXProfile, _MAX_FEEDBACK_SIGNALS, _MAX_RECENT_LENGTHS)

# Heuristic thresholds (characters). Tunable, kept conservative.
_LONG_ANSWER = 900       # answers above this are "long" relative to chat UX
_SHORT_ANSWER = 150
_ABANDON_LONG_THRESHOLD = 1100  # a long answer that drew a length complaint


def push_response_length(profile: UXProfile, length: int) -> None:
    """Record this turn's answer length into the rolling window (capped)."""
    if length < 0:
        length = 0
    profile.recent_response_lengths.append(length)
    if len(profile.recent_response_lengths) > _MAX_RECENT_LENGTHS:
        profile.recent_response_lengths = profile.recent_response_lengths[-_MAX_RECENT_LENGTHS:]


def push_feedback(profile: UXProfile, ftype: FeedbackType) -> None:
    """Record a feedback signal (capped). Praise is kept (morale), but does
    not mutate style -- the style mutation is applied separately below."""
    if ftype == FeedbackType.NONE:
        return
    profile.recent_feedback.append(ftype)
    if len(profile.recent_feedback) > _MAX_FEEDBACK_SIGNALS:
        profile.recent_feedback = profile.recent_feedback[-_MAX_FEEDBACK_SIGNALS:]


def apply_engagement_to_style(profile: UXProfile) -> None:
    """Fold the accumulated signals into InteractionStyle. Pure function over
    the profile's rolling windows.

    Rules (all gentle, hysteresis-like):
      - repeated "too long" complaints  => detail CONCISE, patience low
      - repeated "too short" complaints => detail DETAILED, patience high
      - repeated "too hard"             => tone ENCOURAGING (softer landing)
      - repeated "too fast/slow"        => adjust pacing
      - abandon signals (long + left)   => detail CONCISE
    """
    fb = profile.recent_feedback
    style = profile.style
    n_long = sum(1 for f in fb if f == FeedbackType.EXPLANATION_TOO_LONG)
    n_short = sum(1 for f in fb if f == FeedbackType.EXPLANATION_TOO_SHORT)
    n_hard = sum(1 for f in fb if f == FeedbackType.EXPLANATION_TOO_HARD)
    n_fast = sum(1 for f in fb if f == FeedbackType.TOO_FAST)
    n_slow = sum(1 for f in fb if f == FeedbackType.TOO_SLOW)
    n_praise = sum(1 for f in fb if f == FeedbackType.PRAISE)

    # detail / patience from length signals + abandon heuristic
    long_pressure = n_long + profile.abandon_signals
    if long_pressure >= 2:
        style.detail_level = DetailLevel.CONCISE
        style.patience = "low"
    elif n_short >= 2:
        style.detail_level = DetailLevel.DETAILED
        style.patience = "high"
    elif long_pressure == 1 or n_short == 1:
        style.detail_level = DetailLevel.MEDIUM
        style.patience = "medium"

    # tone: hard explanations => encouraging; strong praise with no complaints
    # => can stay neutral/formal (they are comfortable). Default encouraging.
    if n_hard >= 1 and n_praise == 0:
        style.tone = Tone.ENCOURAGING

    # pacing
    if n_fast >= 2:
        style.pacing = "slow"
    elif n_slow >= 2:
        style.pacing = "fast"
    elif n_fast == 0 and n_slow == 0:
        style.pacing = "steady"


def maybe_bump_abandon(profile: UXProfile, ftype: FeedbackType,
                        answer_length: int) -> None:
    """Conservative abandon heuristic: only when the student explicitly
    complained a LONG answer was too long do we count one abandon signal.
    Avoids inventing abandonment from missing scroll data."""
    if ftype == FeedbackType.EXPLANATION_TOO_LONG and answer_length >= _ABANDON_LONG_THRESHOLD:
        profile.abandon_signals = min(profile.abandon_signals + 1, 9)


def avg_response_length(profile: UXProfile) -> float:
    """Mean of the rolling window, 0 when empty."""
    if not profile.recent_response_lengths:
        return 0.0
    return sum(profile.recent_response_lengths) / len(profile.recent_response_lengths)
