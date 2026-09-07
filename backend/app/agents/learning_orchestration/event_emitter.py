"""Event Emitter: M9 -> M6 event flow (M9 modification 1).

Realises the reviewer's correction: M9 must NOT be a "never writes M6" pure
observer. Instead it *produces* orchestration-level events (milestone reached,
streak achieved, goal threshold crossed) and the supervisor *forwards* them to
M6.consume_turn. M6 -- not M9 -- decides whether to persist them as episodic /
semantic memory. This keeps the data-flow one-directional (M9 emits, M6 owns)
while letting important longitudinal milestones reach long-term memory.

BOUNDARY (must hold):
  - This module NEVER calls memory.store.append_episode or any M6 write
    primitive. It only builds OrchestrationLearningEvent objects and returns
    them. The supervisor is the sole bridge into M6.
  - Events are validated against ORCHESTRATION_EVENT_TYPES so a bug in M9
    cannot inject arbitrary event types into M6's bus.
  - Importance is computed deterministically (zero LLM) from the milestone's
    significance.

Emitted events:
  milestone_completed  -- a milestone crossed its target-mastery threshold.
  habit_milestone      -- a study-streak threshold (7/30/100 days) was reached.
  goal_progress        -- mastered_ratio crossed 25/50/75/90 %.
  task_batch_completed -- every daily task for the day was completed.
"""
from __future__ import annotations

from typing import Any

from .schema import (GoalState, OrchestrationLearningEvent,
                     OrchestrationState)

# streak thresholds that warrant a habit_milestone event (days)
_STREAK_THRESHOLDS = (3, 7, 14, 30, 60, 100)
# goal-progress checkpoints (mastered_ratio) that warrant a goal_progress event
_PROGRESS_CHECKPOINTS = (0.25, 0.50, 0.75, 0.90)

# milestone importance by type
_IMPORTANCE = {
    "milestone_completed": 0.8,
    "habit_milestone": 0.75,
    "goal_progress": 0.6,
    "task_batch_completed": 0.4,
    "plan_regenerated": 0.3,
}


def milestone_completed_event(milestone_title: str, subject: str = "") \
        -> OrchestrationLearningEvent:
    """Build a milestone_completed event (a milestone reached target mastery)."""
    return OrchestrationLearningEvent(
        event_type="milestone_completed",
        summary=f"完成学习里程碑「{milestone_title}」",
        subject=subject, importance=_IMPORTANCE["milestone_completed"],
        payload={"milestone": milestone_title})


def habit_milestone_event(streak: int, subject: str = "") \
        -> OrchestrationLearningEvent | None:
    """Build a habit_milestone event if the streak crossed a threshold.

    Returns None when the streak is not a recognised threshold (avoids
    spamming M6 with an event every single day).
    """
    if streak not in _STREAK_THRESHOLDS:
        return None
    return OrchestrationLearningEvent(
        event_type="habit_milestone",
        summary=f"连续学习{streak}天，保持良好学习习惯",
        subject=subject, importance=_IMPORTANCE["habit_milestone"],
        payload={"streak": streak})


def goal_progress_event(ratio: float, subject: str = "") \
        -> OrchestrationLearningEvent | None:
    """Build a goal_progress event if the mastered ratio crossed a checkpoint.

    Returns None when the ratio is not a recognised checkpoint. The caller is
    expected to track the *last* reported checkpoint so each threshold fires
    only once; this function just decides whether the current ratio is one.
    """
    r = round(ratio, 3)
    if r not in _PROGRESS_CHECKPOINTS:
        return None
    return OrchestrationLearningEvent(
        event_type="goal_progress",
        summary=f"目标进度达到{int(r * 100)}%",
        subject=subject, importance=_IMPORTANCE["goal_progress"],
        payload={"mastered_ratio": r})


def task_batch_completed_event(day: str, count: int, subject: str = "") \
        -> OrchestrationLearningEvent:
    """Build a task_batch_completed event (all of a day's tasks done)."""
    return OrchestrationLearningEvent(
        event_type="task_batch_completed",
        summary=f"完成{day}全部{count}项学习任务",
        subject=subject, importance=_IMPORTANCE["task_batch_completed"],
        payload={"day": day, "count": count})


def emit_for_milestone_transition(old_status: str, new_status: str,
                                  milestone_title: str, subject: str = "") \
        -> list[OrchestrationLearningEvent]:
    """Emit events for a milestone status transition.

    Fires milestone_completed only when a milestone moves INTO completed.
    Returns a (possibly empty) list. Pure function.
    """
    out: list[OrchestrationLearningEvent] = []
    if new_status == "completed" and old_status != "completed":
        out.append(milestone_completed_event(milestone_title, subject=subject))
    return out


def emit_for_streak(streak: int, *, last_reported: int = 0,
                    subject: str = "") -> list[OrchestrationLearningEvent]:
    """Emit a habit_milestone event when a new streak threshold is crossed.

    last_reported is the highest threshold already emitted (tracked by the
    caller). Returns at most one event. Pure function.
    """
    out: list[OrchestrationLearningEvent] = []
    crossed = [t for t in _STREAK_THRESHOLDS if t <= streak and t > last_reported]
    if crossed:
        ev = habit_milestone_event(max(crossed), subject=subject)
        if ev is not None:
            out.append(ev)
    return out


def emit_for_goal_progress(ratio: float, *,
                           last_reported: float = -1.0,
                           subject: str = "") -> list[OrchestrationLearningEvent]:
    """Emit a goal_progress event when a new checkpoint is crossed.

    last_reported is the last emitted checkpoint ratio. Returns at most one
    event. Pure function.
    """
    out: list[OrchestrationLearningEvent] = []
    crossed = [c for c in _PROGRESS_CHECKPOINTS
               if c <= ratio and c > last_reported]
    if crossed:
        ev = goal_progress_event(max(crossed), subject=subject)
        if ev is not None:
            out.append(ev)
    return out


def valid_events(events: list[OrchestrationLearningEvent]) \
        -> list[OrchestrationLearningEvent]:
    """Filter out any event whose type is not in the closed set.

    Defensive: guarantees the supervisor only forwards validated events to M6,
    so a bug in M9 cannot inject arbitrary event types.
    """
    return [e for e in events if e.valid]


def to_event_dicts(events: list[OrchestrationLearningEvent]) \
        -> list[dict[str, Any]]:
    """Convert validated events to the plain-dict format M6.consume_turn
    expects in its events list. This is the serialization boundary."""
    return [e.to_event_dict() for e in valid_events(events)]
