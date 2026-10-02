"""Learner profile inference: derive the UX Profile from accumulated signals.

This is the bridge that KEEPS THE SINGLE TRUTH SOURCE intact: the academic
explanation preference (step_by_step / examples_first / explanation_depth)
belongs to M2's LearningStyle. M8 reads it read-only and only OWNS the UX-only
dims (tone / detail / visual / pacing / patience).

`m2_learning_style_snapshot()` reaches into M2 defensively (returns None when
M2 is off or unavailable) so M8 degrades cleanly even with M2 disabled --
mirroring how M5's bridge reads M2's skill_graph.
"""
from __future__ import annotations

from typing import Any

from . import store
from .engagement_tracker import (apply_engagement_to_style, push_feedback,
                                 push_response_length, maybe_bump_abandon)
from .schema import (DetailLevel, FeedbackType, Tone, UXEvent, UXProfile,
                     _MAX_FEEDBACK_SIGNALS)


def m2_learning_style_snapshot(student_id: str) -> dict[str, Any] | None:
    """Read M2's LearningStyle as a READ-ONLY projection.

    Returns {"preference": ..., "explanation_depth": ...} or None when M2 is
    disabled / unavailable / corrupt. Never raises -- the caller treats None
    as "no academic style signal, use UX defaults".
    """
    try:
        from ..student_model import get_student_model, is_enabled
        if not is_enabled():
            return None
        sm = get_student_model()
        sm.load()
        return sm.profile.learning_style.to_dict()
    except Exception:
        return None


def get_profile(student_id: str) -> UXProfile:
    """Load (or create) the UX profile for a student. Never raises."""
    return store.load_profile(student_id)


def record_turn_signals(*, profile: UXProfile, student_id: str, session_id: str,
                         concept: str, message: str, answer: str,
                         feedback: FeedbackType) -> list[UXEvent]:
    """Fold this turn's signals into the profile and append UXEvents.

    Returns the UXEvents appended (0-1). Pure over inputs; persistence is left
    to the caller (the manager persists the profile + events together).
    """
    events: list[UXEvent] = []
    answer_len = len(answer or "")
    # 1. response length always recorded
    push_response_length(profile, answer_len)
    events.append(UXEvent(
        student_id=student_id, session_id=session_id, concept=concept,
        type="response", response_length=answer_len,
        feedback=FeedbackType.NONE, note=f"{answer_len} chars"))
    # 2. feedback (if any) recorded + abandon heuristic
    if feedback != FeedbackType.NONE:
        push_feedback(profile, feedback)
        maybe_bump_abandon(profile, feedback, answer_len)
        events.append(UXEvent(
            student_id=student_id, session_id=session_id, concept=concept,
            type="feedback", feedback=feedback, response_length=answer_len,
            note=(message or "")[:40]))
    # 3. re-derive style from the accumulated windows
    apply_engagement_to_style(profile)
    return events


def seed_style_from_grade(profile: UXProfile, grade: str) -> None:
    """A one-time gentle prior: younger students get an encouraging tone and
    visual preference by default, until real signals override them. Only sets
    fields that have not yet been tuned by feedback (hysteresis: real signals
    win once present)."""
    tuned = bool(profile.recent_feedback) or profile.abandon_signals > 0
    if tuned:
        return
    g = (grade or "").strip()
    if g in ("小学", "初中"):
        profile.style.tone = Tone.ENCOURAGING
        profile.style.detail_level = DetailLevel.MEDIUM
        profile.style.visual_preference = True
        profile.style.patience = "low"
    elif g in ("高中",):
        profile.style.tone = Tone.ENCOURAGING
        profile.style.detail_level = DetailLevel.MEDIUM
        profile.style.visual_preference = True
        profile.style.patience = "medium"
    else:  # 本科 / unknown
        profile.style.tone = Tone.NEUTRAL
        profile.style.detail_level = DetailLevel.DETAILED
        profile.style.visual_preference = False
        profile.style.patience = "high"


def needs_save(profile_before: UXProfile, profile_after: UXProfile) -> bool:
    """Whether a turn changed the profile enough to persist. We persist whenever
    feedback/length windows or style shifted, to keep the working set fresh."""
    return (profile_before.to_dict() != profile_after.to_dict())
