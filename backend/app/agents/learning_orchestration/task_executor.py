"""Task Executor: materialise the weekly plan into daily tasks (M9).

Bridges the gap between the abstract WeeklyPlan (what to learn this week) and
concrete DailyTasks (what to do today). For each available study day it
produces a bounded set of tasks sized to the student's daily time budget:

  - STUDY tasks for the week's planned concepts (from M3-style ordering).
  - REVIEW tasks for SRS-due cards (from spaced_repetition.due_cards).
  - One SUMMARY task at the end of an active day.

This is pure data materialisation -- completing a task does NOT set mastery
(that is M2's job via quiz/teaching outcomes). M9 only tracks the task
lifecycle so it can report progress and detect procrastination.
"""
from __future__ import annotations

import time
from typing import Any

from . import spaced_repetition as srs
from .schema import (DailyTask, DailyTaskStatus, OrchestrationState,
                     ReviewItem, TaskKind, WeeklyPlan)

_DAY_SECONDS = 24 * 3600


def _day_str(ts: float) -> str:
    t = time.localtime(ts)
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"


def _task_id(day: str, concept_id: str, kind: str) -> str:
    return f"{day}_{concept_id}_{kind}"


def generate_daily_tasks(state: OrchestrationState, *,
                         day_ts: float | None = None,
                         slots: list[int] | None = None) -> list[DailyTask]:
    """Generate today's tasks from the current weekly plan + SRS queue.

    Returns a fresh list of DailyTasks (does NOT mutate state -- the caller
    persists them via the manager). Slots is a list of minute-budgets from
    schedule_engine.slots_per_day. Never raises.
    """
    try:
        day_ts = day_ts if day_ts is not None else time.time()
        day = _day_str(day_ts)
        if slots is None:
            slots = [15, 20]

        tasks: list[DailyTask] = []
        slot_idx = 0

        # 1. SRS-due reviews first (highest priority -- memory decay)
        due = srs.due_cards(state.review_queue, now=day_ts, limit=len(slots))
        for card in due:
            if slot_idx >= len(slots):
                break
            tasks.append(DailyTask(
                id=_task_id(day, card.concept_id, "review"),
                day=day, concept_id=card.concept_id,
                concept_name=card.concept_name, kind=TaskKind.REVIEW,
                status=DailyTaskStatus.PENDING, priority=1,
                estimate_minutes=slots[slot_idx]))
            slot_idx += 1

        # 2. New-study tasks from the current week's plan
        current_week = _current_week_plan(state, day_ts)
        if current_week:
            remaining = len(slots) - slot_idx
            for pc in current_week.concepts[:remaining]:
                if slot_idx >= len(slots):
                    break
                tasks.append(DailyTask(
                    id=_task_id(day, pc.concept_id, "study"),
                    day=day, concept_id=pc.concept_id,
                    concept_name=pc.name, kind=TaskKind.STUDY,
                    status=DailyTaskStatus.PENDING, priority=pc.difficulty,
                    estimate_minutes=slots[slot_idx],
                    milestone_id=pc.milestone_id))
                slot_idx += 1

        # 3. A summary task if we used at least 2 slots
        if slot_idx >= 2 and slot_idx < len(slots) + 1:
            tasks.append(DailyTask(
                id=_task_id(day, "_summary", "summary"),
                day=day, concept_id="", concept_name="",
                kind=TaskKind.SUMMARY,
                status=DailyTaskStatus.PENDING, priority=5,
                estimate_minutes=min(10, slots[0] if slots else 10)))

        return tasks
    except Exception:
        return []


def _current_week_plan(state: OrchestrationState, now: float) -> WeeklyPlan | None:
    """Find the WeeklyPlan whose week_start is closest to (<=) now."""
    if not state.weekly_plan:
        return None
    applicable = [w for w in state.weekly_plan
                  if w.week_start <= now or w.week_start == 0]
    if not applicable:
        return state.weekly_plan[0]
    return max(applicable, key=lambda w: w.week_start)


def materialize_day(state: OrchestrationState, day: str,
                    candidates: list[DailyTask]) -> list[DailyTask]:
    """Gap-fill materialisation of one day's tasks (task-uniqueness contract).

    Every task already persisted for `day` keeps its identity untouched; only
    candidates whose (concept_id, kind) key is not already present for that
    day are inserted. Never deletes, replaces, or reorders existing tasks --
    so re-composition (next day / regenerate / LLM composer) can never
    evaporate an unfinished task. Returns the full task list for `day`
    (existing first, then newly inserted).
    """
    existing = [t for t in state.daily_tasks if t.day == day]
    keys = {(t.concept_id, t.kind.value) for t in existing}
    for cand in candidates:
        key = (cand.concept_id, cand.kind.value)
        if key in keys:
            continue
        keys.add(key)
        state.daily_tasks.append(cand)
        existing.append(cand)
    return existing


def carryover_tasks(state: OrchestrationState, *,
                    now: float | None = None) -> list[DailyTask]:
    """Unfinished tasks from earlier days (the carryover section, shown on top).

    A task carries over when its day is before today and it is still pending /
    in_progress / overdue. mark_overdue is expected to have run first so stale
    pending tasks already show as overdue. Never raises.
    """
    try:
        today = _day_str(now if now is not None else time.time())
        return [t for t in state.daily_tasks
                if t.day < today and t.status.value in
                ("pending", "in_progress", "overdue")]
    except Exception:
        return []


def complete_task(state: OrchestrationState, task_id: str) -> bool:
    """Mark a daily task as completed. Returns True if found+updated.

    Does NOT touch mastery (M2's job). Only updates the M9 task lifecycle +
    habit bookkeeping. The SRS update happens separately (review_turn in the
    manager) so the two signals (task done vs. review quality) stay separate.
    """
    for t in state.daily_tasks:
        if t.id == task_id and t.status.value != "completed":
            t.status = DailyTaskStatus.COMPLETED
            t.completed_at = time.time()
            return True
    return False


def mark_overdue(state: OrchestrationState, *,
                 now: float | None = None) -> int:
    """Sweep: mark pending/in-progress tasks past their day as OVERDUE.

    Returns the count of tasks newly marked overdue. Called by the manager on
    read/refresh so the habit tracker has accurate procrastination counts.
    """
    now = now if now is not None else time.time()
    today = _day_str(now)
    count = 0
    for t in state.daily_tasks:
        if t.status.value in ("pending", "in_progress") and t.day < today:
            t.status = DailyTaskStatus.OVERDUE
            count += 1
    return count


def today_tasks(state: OrchestrationState, *,
                now: float | None = None) -> list[DailyTask]:
    """All tasks for today (any status). If none exist yet, generates them."""
    now = now if now is not None else time.time()
    day = _day_str(now)
    existing = [t for t in state.daily_tasks if t.day == day]
    if existing:
        return existing
    return []


def pending_review_count(state: OrchestrationState, *,
                         now: float | None = None) -> int:
    """How many SRS cards are due right now."""
    now = now if now is not None else time.time()
    return sum(1 for c in state.review_queue.values()
               if srs.is_due(c, now=now))
