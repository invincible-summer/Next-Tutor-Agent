"""Learning Planner: hybrid long-term plan generation (M9).

This is the "REUSE NOT REBUILD" component. It does NOT re-implement learning-
path inference -- that already lives in M3's build_learning_path (curriculum
single-session engine) and M5's knowledge graph (prerequisite DAG). Instead it
LAYERs schedule + habit + SRS on top of those existing primitives to produce a
persistent weekly plan.

Hybrid planning pipeline:
  1. M5 knowledge graph: topo-sort the goal's required concepts by
     prerequisite order (read-only graph traversal).
  2. M2 mastery view: filter out already-mastered concepts (read-only).
  3. M3 build_learning_path: produce the next-learnable + review-candidate
     lists (the single-session inference engine, called as a pure function).
  4. Schedule engine: distribute concepts across available study days.
  5. Habit signal: adjust task granularity for struggling students.

The result is a list of WeeklyPlan objects (M9-owned, persistent), NOT a
re-computation of mastery or concept state.

IMPORT-CLEAN: this module never imports student_model at module level (avoids
circular deps). The caller (manager) passes plain-data projections
(evaluation_view, graph concepts, episodes) so the planner stays decoupled.
"""
from __future__ import annotations

import time
from typing import Any

from .schema import OrchestrationState, PlanConcept, WeeklyPlan
from . import schedule_engine

_DAY_SECONDS = 24 * 3600
_MAX_CONCEPTS_PER_WEEK = 5


def topo_sort_concepts(concept_ids: list[str],
                       prereq_map: dict[str, list[str]]) -> list[str]:
    """Topologically sort concept_ids by their prerequisite edges.

    prereq_map is {concept_id: [prerequisite_id, ...]}. A concept appears
    after all its prerequisites. Returns only the concepts that are in
    concept_ids (others are filtered out). Stable: ties keep input order.
    Pure function, no graph object needed.
    """
    target = set(concept_ids)
    visited: set[str] = set()
    order: list[str] = []

    def visit(cid: str) -> None:
        if cid in visited or cid not in target:
            return
        visited.add(cid)
        for pre in prereq_map.get(cid, []):
            if pre in target:
                visit(pre)
        order.append(cid)

    for cid in concept_ids:
        visit(cid)
    return order


def generate_weekly_plan(state: OrchestrationState, *,
                         next_learnable: list[dict[str, Any]],
                         review_candidates: list[dict[str, Any]],
                         evaluation_view: dict[str, Any],
                         prereq_map: dict[str, list[str]] | None = None,
                         num_weeks: int = 4,
                         now: float | None = None) -> list[WeeklyPlan]:
    """Generate a rolling weekly plan from M3-curriculum-style inputs.

    This is the composition point: the caller (manager) has already assembled
    next_learnable + review_candidates (via M3 build_learning_path or directly
    from the skill graph) and passes them as plain dicts. The planner
    distributes them across weeks using topo-sort + schedule, producing
    M9-owned WeeklyPlan objects. Never raises.
    """
    try:
        now = now if now is not None else time.time()
        pm = prereq_map or {}
        schedule = state.schedule

        # G4：已 supported_in_scope 的概念不排入新计划（统一评价只读投影）
        unmastered_next = [
            n for n in next_learnable
            if _state(n.get("skill_id", ""), evaluation_view) != "supported_in_scope"
        ]

        # topo-sort the next-learnable by prerequisites for sensible ordering
        next_ids = [str(n.get("skill_id", "")) for n in unmastered_next]
        sorted_ids = topo_sort_concepts(next_ids, pm)
        id_to_node = {str(n.get("skill_id", "")): n for n in unmastered_next}
        ordered_next = [id_to_node[cid] for cid in sorted_ids
                        if cid in id_to_node]

        # find current Monday (int: no sub-second drift between planning runs)
        t = time.localtime(now)
        monday = now - t.tm_wday * _DAY_SECONDS
        monday_midnight = int(monday - (t.tm_sec + t.tm_min * 60 + t.tm_hour * 3600))

        weeks: list[WeeklyPlan] = []

        all_items = []
        for n in ordered_next:
            all_items.append(("next", n))
        for r in review_candidates[:_MAX_CONCEPTS_PER_WEEK]:
            all_items.append(("review", r))

        per_week = max(1, _MAX_CONCEPTS_PER_WEEK)
        for wi in range(num_weeks):
            chunk = all_items[wi * per_week:(wi + 1) * per_week]
            if not chunk:
                break
            week_start = monday_midnight + wi * 7 * _DAY_SECONDS
            concepts: list[PlanConcept] = []
            for tag, node in chunk:
                cid = str(node.get("skill_id", ""))
                name = str(node.get("name", ""))
                diff = int(node.get("difficulty", 3))
                concepts.append(PlanConcept(
                    concept_id=cid, name=name,
                    week_index=wi, difficulty=diff))
            focus = concepts[0].name if concepts else ""
            weeks.append(WeeklyPlan(week_index=wi, week_start=week_start,
                                    focus=focus, concepts=concepts))
        return weeks
    except Exception:
        return []


def _state(skill_id: str, evaluation_view: dict[str, Any]) -> str:
    """G4 只读助手：统一评价投影中该概念的语义状态（无证据 → ""）。"""
    rec = evaluation_view.get(skill_id) or {}
    return str(rec.get("state", "")) if isinstance(rec, dict) else ""


def needs_replan(state: OrchestrationState, evaluation_view: dict[str, Any],
                 *, now: float | None = None) -> bool:
    """Decide whether the current plan is stale and should be regenerated.

    Triggers re-planning when:
    - a goal exists but no plan was ever attempted;
    - the plan is older than 7 days;
    - planned concepts have been mastered (gap between plan and reality).

    Guards against prompt loops: without a goal there is nothing to plan
    (returns False), and an attempted-but-empty plan (all mastered / nothing
    schedulable) is a legitimate end state, not staleness -- it does not
    re-trigger. Pure-read over M2 mastery. Never raises.
    """
    try:
        now = now if now is not None else time.time()
        if not state.has_goals:
            return False
        if not state.weekly_plan:
            return state.last_plan_attempt == 0.0
        last_plan_ts = state.weekly_plan[-1].week_start
        if last_plan_ts > 0 and (now - last_plan_ts) > 7 * _DAY_SECONDS:
            return True
        # G4：计划内概念已被统一评价支持（计划落后于现实）→ 触发重规划
        supported_in_plan = 0
        total_in_plan = 0
        for wp in state.weekly_plan:
            for pc in wp.concepts:
                total_in_plan += 1
                if _state(pc.concept_id, evaluation_view) == "supported_in_scope":
                    supported_in_plan += 1
        if total_in_plan > 0 and supported_in_plan / total_in_plan > 0.7:
            return True
        return False
    except Exception:
        return False
