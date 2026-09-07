"""Task-Completion Tracker + consistency projection (M9 modification 2).

After the M9 review the ownership split is explicit:

  - M9 TaskCompletionTracker (THIS module's task_* functions): task EXECUTION
    stats derived from M9's own daily-task lifecycle -- completed_tasks /
    total_tasks / completion_rate / procrastination_count. M9 OWNS these.
  - Learning-day union: the L1 activity aggregator owns "on which days did
    the student learn" (a day-union over the live ledgers, with the retired
    M6 episodic log as compatibility fallback). M9 READS it (refresh_habit
    derives streak stats from that union) and M8 reads the same source for
    its streak expression -- one derivation, no double computation.

So this module has two clearly separated concerns: task_completion_stats
(M9-owned truth) and refresh_habit (unified-activity read + task lifecycle).
The M9 manager never persists streak as its own truth.
"""
from __future__ import annotations

import time
from typing import Any

from .schema import HabitStats, OrchestrationState

_DAY_SECONDS = 24 * 3600


def task_completion_stats(state: OrchestrationState, *,
                          now: float | None = None) -> dict[str, Any]:
    """Compute the M9-OWNED task-execution stats (TaskCompletionTracker).

    These are derived purely from M9's own daily-task lifecycle: how many
    tasks exist, how many are completed, the completion rate, and how many are
    overdue. M9 owns these because they reflect *task execution*, not long-term
    behavioural patterns (which M6's HabitPatternMemory owns).

    Pure function (read-only over state.daily_tasks). Never raises.
    """
    try:
        now = now if now is not None else time.time()
        completed = sum(1 for t in state.daily_tasks
                        if t.status.value == "completed")
        total = len(state.daily_tasks)
        overdue = sum(
            1 for t in state.daily_tasks
            if t.status.value in ("pending", "in_progress")
            and t.day and _day_to_epoch(t.day) < (now - _DAY_SECONDS))
        return {
            "completed_tasks": completed,
            "total_tasks": total,
            "completion_rate": round(completed / total, 3) if total else 0.0,
            "procrastination_count": overdue,
        }
    except Exception:
        return {"completed_tasks": 0, "total_tasks": 0,
                "completion_rate": 0.0, "procrastination_count": 0}



def _day_to_epoch(day_str: str) -> float:
    """Parse a YYYY-MM-DD string back to a local-midnight epoch."""
    try:
        t = time.strptime(day_str, "%Y-%m-%d")
        return float(time.mktime(t))
    except Exception:
        return 0.0


def refresh_habit(state: OrchestrationState, *, now: float | None = None,
                  student_id: str = "") -> None:
    """Re-derive habit stats from the unified activity day-union + the task
    lifecycle.

    Read-only over the L1 activity aggregator (the same day-union M8 reads,
    so streak numbers agree across modules) and over M9's own daily_tasks.
    Updates state.habit in place. Never raises.
    """
    try:
        now = now if now is not None else time.time()
        current, longest, last_active, total_days = 0, 0, "", 0
        if student_id:
            from .. import activity_aggregator
            current, longest, last_active, total_days = (
                activity_aggregator.streak_stats(student_id, now=now))

        completed = sum(1 for t in state.daily_tasks
                        if t.status.value == "completed")
        total_tasks = len(state.daily_tasks)
        overdue = sum(1 for t in state.daily_tasks
                      if t.status.value in ("pending", "in_progress")
                      and t.day and _day_to_epoch(t.day) < (now - _DAY_SECONDS))

        state.habit = HabitStats(
            current_streak=current,
            longest_streak=longest,
            last_active_day=last_active,
            total_active_days=total_days,
            completed_tasks=completed,
            total_tasks=total_tasks,
            procrastination_count=overdue,
            updated_at=now,
        )
    except Exception:
        pass


def should_granularize(habit: HabitStats) -> bool:
    """Schedule-adaptation signal: should task_executor produce smaller-grain
    tasks? True when the student is struggling with consistency (low streak OR
    low completion rate OR high procrastination). This feeds schedule_engine.
    """
    if habit.current_streak <= 1 and habit.total_active_days >= 3:
        return True
    if habit.total_tasks >= 5 and habit.completion_rate < 0.5:
        return True
    if habit.procrastination_count >= 3:
        return True
    return False
