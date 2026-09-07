"""Goal Manager: long-term learning goal management (M9).

Owns the top of the plan hierarchy: the student sets one or more goals (exam
date / ability target / interest, capped at _MAX_GOALS), and the weekly
planner merges all of them into weeks of action-level tasks. Progress is
DERIVED read-only from M2 mastery over the planned concepts -- M9 owns the
plan *structure*, never the mastery values.

Distinct from M2 StudentProfile.goals (a free-text list of stated intents
collected passively from conversation). LearningGoal here is the structured,
scheduled object that drives weekly planning.
"""
from __future__ import annotations

import time
from typing import Any

from .schema import (GoalType, LearningGoal, OrchestrationState,
                     _MAX_GOALS)


def add_goal(state: OrchestrationState, *, title: str, description: str = "",
             goal_type: str = "ability", subjects: list[str] | None = None,
             deadline: float = 0.0,
             target_concept_ids: list[str] | None = None) -> LearningGoal:
    """Append a new long-term learning goal (id ``g_{seq}``).

    Raises ValueError on an empty title or when the goal cap is exceeded.
    This does NOT auto-generate the plan -- that comes from the weekly
    planner (which needs the M5 knowledge graph to order concepts). The goal
    is just the top-level intent + deadline. target_concept_ids optionally
    binds the goal to specific graph concepts (prerequisite-closure
    analysis).
    """
    title = title.strip()
    if not title:
        raise ValueError("goal title required")
    if len(state.goals) >= _MAX_GOALS:
        raise ValueError(f"goal cap ({_MAX_GOALS}) reached")
    seq = 1 + max(
        (int(g.id[len("g_"):]) for g in state.goals
         if g.id.startswith("g_") and g.id[len("g_"):].isdigit()),
        default=0)
    goal = LearningGoal(
        id=f"g_{seq}",
        title=title,
        description=description.strip(),
        goal_type=GoalType.from_value(goal_type),
        subjects=list(subjects or []),
        target_concept_ids=[c for c in (target_concept_ids or []) if str(c).strip()],
        deadline=float(deadline),
    )
    goal.updated_at = time.time()
    state.goals.append(goal)
    return goal


def update_goal(state: OrchestrationState, goal_id: str, *,
                title: str | None = None, description: str | None = None,
                goal_type: str | None = None,
                subjects: list[str] | None = None,
                deadline: float | None = None,
                target_concept_ids: list[str] | None = None) -> LearningGoal | None:
    """Patch fields of one goal (all parameters optional). Returns the goal,
    or None when the id does not exist."""
    goal = next((g for g in state.goals if g.id == goal_id), None)
    if goal is None:
        return None
    if title is not None:
        goal.title = title.strip()
    if description is not None:
        goal.description = description.strip()
    if goal_type is not None:
        goal.goal_type = GoalType.from_value(goal_type)
    if subjects is not None:
        goal.subjects = list(subjects)
    if target_concept_ids is not None:
        goal.target_concept_ids = [
            c for c in target_concept_ids if str(c).strip()]
    if deadline is not None:
        goal.deadline = float(deadline)
    goal.updated_at = time.time()
    return goal


def remove_goal(state: OrchestrationState, goal_id: str) -> bool:
    """Remove one goal and its gap analysis. Returns False when not found."""
    before = len(state.goals)
    state.goals = [g for g in state.goals if g.id != goal_id]
    if len(state.goals) == before:
        return False
    state.goal_states = [gs for gs in state.goal_states
                         if gs.goal_id != goal_id]
    return True


def overall_progress(state: OrchestrationState,
                     mastery_view: dict[str, Any]) -> float:
    """A 0..1 progress score across the weekly plan, read-only over M2 mastery.

    The fraction of planned concepts whose mastery reached their planned
    target. Returns 0 when there is no plan. (Milestone-based before the
    plan-hierarchy rebuild; now computed over weekly_plan concepts.)
    """
    total = 0
    mastered = 0
    for w in state.weekly_plan:
        for pc in w.concepts:
            total += 1
            rec = mastery_view.get(pc.concept_id) or {}
            p = float(rec.get("p_known", 0)) if isinstance(rec, dict) else 0.0
            if p >= pc.planned_mastery:
                mastered += 1
    if not total:
        return 0.0
    return round(mastered / total, 3)
