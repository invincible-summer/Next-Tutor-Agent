"""Motivation engine: the EXPRESSION of encouragement and milestones.

The activity DATA belongs to the L1 unified profile layer
(activity_aggregator: a day-union over the five live ledgers — learning
records / teaching log / orchestration events / UX events / eval traces —
with the retired M6 episodic log as compatibility fallback). M8 only READS
that union to compute "how many consecutive days has this student been
active" and decides what to SAY. It never writes anything.

This keeps the boundary intact: the aggregator owns "what happened", M8
owns "how to frame it as encouragement".
"""
from __future__ import annotations

import time
from typing import Any

# Milestone days we surface congratulations for (avoid spamming every day).
_MILESTONES = (3, 7, 14, 30, 60, 100)


def _active_days(student_id: str) -> set[str]:
    """Read the unified activity day-union and return the set of local
    "YYYY-MM-DD" day strings the student was active on. Returns an empty set
    when no source has data. Never raises. (Day strings, not epoch midnights,
    so the streak math stays timezone-correct on any host offset.)"""
    try:
        from .. import activity_aggregator
        return activity_aggregator.active_days(student_id)
    except Exception:
        return set()


def current_streak(student_id: str, now: float | None = None) -> int:
    """Consecutive-day learning streak ending today (or yesterday).

    A day "today" counts even if the student is active right now. Walks
    backward from today; stops at the first gap. Returns 0 when no activity.
    """
    now = now if now is not None else time.time()
    try:
        from .. import activity_aggregator
        return activity_aggregator.streak_from_days(
            _active_days(student_id), now=now)[0]
    except Exception:
        return 0


def next_milestone(streak: int) -> int | None:
    """The next milestone >= current streak, or None if past the last."""
    for m in _MILESTONES:
        if streak <= m:
            return m
    return None


def milestone_due(streak: int, last_surfaced: int) -> int | None:
    """A milestone worth surfacing this turn: the highest reached milestone the
    student has NOT yet been congratulated for. Returns None if none new."""
    for m in _MILESTONES:
        if streak >= m and last_surfaced < m:
            return m
    return None


def motivation_snapshot(student_id: str, now: float | None = None) -> dict[str, Any]:
    """Read-only motivation summary for the API. Never raises."""
    try:
        from .. import activity_aggregator
        now = now if now is not None else time.time()
        current, _longest, _last, total = activity_aggregator.streak_from_days(
            _active_days(student_id), now=now)
        return {
            "streak_days": current,
            "next_milestone": next_milestone(current),
            "milestones": list(_MILESTONES),
            "active_days": total,
        }
    except Exception:
        return {"streak_days": 0, "next_milestone": _MILESTONES[0],
                "milestones": list(_MILESTONES), "active_days": 0}
