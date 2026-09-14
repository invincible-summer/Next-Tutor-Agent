"""Goal Analyzer: gap analysis + backward planning (M9 modification 3).

Answers the question the original M9 design left open: *why* does the plan
look the way it does? Given a LearningGoal (intent) and a read-only projection
of the student's mastery over the M5 skill graph, the analyzer computes:

  1. current level  -- what fraction of the goal's subject is mastered
  2. gap analysis   -- which required skills are missing (never touched) or
                       weak (seen but below target mastery)
  3. backward plan  -- the ordered prerequisite path from the student's
                       current frontier to the goal's target skills
  4. urgency        -- deadline pressure (0..1) driving schedule density

The output is a GoalState, consumed by learning_planner as the *reasoning
layer* between LearningGoal (intent) and WeeklyPlan (schedule).

DESIGN CONTRACT:
  - PURE FUNCTION + READ-ONLY projections: the caller (manager) passes plain
    dicts (mastery_view, subject_skills, prereq_map). This module never
    imports student_model or knowledge at runtime -- it stays import-clean
    exactly like learning_planner.
  - REUSE NOT REBUILD: it calls M3's build_learning_path as its
    single-session inference engine for the backward path, layering gap
    analysis on top. It does NOT re-implement prerequisite traversal beyond a
    light topo-sort (same primitive learning_planner uses).
  - DETERMINISTIC-FIRST: zero LLM. The natural-language -> structured parsing
    uses keyword rules; the gap math is arithmetic. This keeps the per-turn
    critical path LLM-free (matching every other M9 component).
"""
from __future__ import annotations

import time
from typing import Any

from .schema import (GapItem, GoalAnalysisLevel, GoalState, GoalType,
                     LearningGoal)

# --- keyword tables for rule-based goal parsing (zero LLM) ------------------

# subject keywords -> canonical subject label (matches the M5 seed graph
# subjects: 数学 / 物理 / 化学 / 生物 / 英语)
_SUBJECT_KEYWORDS: dict[str, str] = {
    "数学": "数学", "高数": "数学", "微积分": "数学", "代数": "数学",
    "几何": "数学", "考研数学": "数学", "线代": "数学", "概率": "数学",
    "物理": "物理", "力学": "物理", "电磁": "物理", "牛顿": "物理",
    "化学": "化学", "有机": "化学", "无机": "化学",
    "生物": "生物", "细胞": "生物", "遗传": "生物",
    "英语": "英语", "english": "英语", "六级": "英语", "四级": "英语",
    "math": "数学", "physics": "物理", "calculus": "数学",
}

# goal-type keywords -> GoalType
_EXAM_KEYWORDS = {"考试", "考", "考研", "高考", "中考", "期末", "期中", "模拟",
                  "exam", "test", "cet", "六级", "四级"}
_ABILITY_KEYWORDS = {"掌握", "精通", "学会", "熟练", "理解", "master", "learn"}
_INTEREST_KEYWORDS = {"了解", "兴趣", "看看", "随便学", "explore", "curiosity"}


def parse_goal_text(text: str) -> dict[str, Any]:
    """Rule-based parse of a natural-language goal string (zero LLM).

    Returns a dict with subject, goal_type, and a cleaned target label.
    Never raises; unknown input degrades to an ABILITY goal with empty subject.
    This is the V1 parser -- a later version can layer an LLM call on top, but
    the deterministic core stays as the fallback (same DETERMINISTIC-FIRST
    discipline as M6's classifier).
    """
    t = (text or "").strip().lower()
    subject = ""
    for kw, canonical in _SUBJECT_KEYWORDS.items():
        if kw in t:
            subject = canonical
            break
    if any(kw in t for kw in _EXAM_KEYWORDS):
        gtype = GoalType.EXAM
    elif any(kw in t for kw in _INTEREST_KEYWORDS):
        gtype = GoalType.INTEREST
    elif any(kw in t for kw in _ABILITY_KEYWORDS):
        gtype = GoalType.ABILITY
    else:
        gtype = GoalType.ABILITY
    return {"subject": subject, "goal_type": gtype, "target": text.strip()}


def prerequisite_closure(target_ids: list[str],
                         prereq_map: dict[str, list[str]],
                         mastered_ids: set[str],
                         *, cap: int = 120) -> list[str]:
    """The unmastered concept chain a goal's target concepts depend on.

    Walks PREREQUISITE edges downward from each target; a node is kept when
    it is NOT mastered (p >= target mastery, per the caller's mastery view).
    Mastered nodes are dropped and NOT descended through (their own prereqs
    are treated as satisfied). Deterministic BFS, capped at `cap` nodes so a
    cyclic or huge graph can never blow up the analysis. Pure function.
    """
    targets = [t for t in (target_ids or []) if t]
    if not targets:
        return []
    out: list[str] = []
    seen: set[str] = set()
    queue = list(targets)
    while queue and len(out) < cap:
        cid = queue.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        if cid in mastered_ids and cid not in targets:
            continue  # mastered prerequisite: chain satisfied below it
        if cid not in out:
            out.append(cid)
        for pre in prereq_map.get(cid, []):
            if pre not in seen:
                queue.append(pre)
    return out


def estimate_schedule(required_count: int, deadline: float,
                      now: float, *, weekly_pace: int = 5,
                      daily_minutes: int = 45, available_days: int = 7,
                      minutes_per_concept: int = 20) -> dict[str, Any]:
    """Deterministic "can I make it" estimate for the GoalCard.

    Pure arithmetic, zero LLM. Two independent paces bound the estimate
    (W4/A13: a single concept-count point hid the time-budget reality):

      - weekly_pace (default 5 concepts/week): the planning convention;
      - time pace: daily_minutes x available_days of weekly study capacity
        divided by minutes_per_concept — what the student's schedule can
        actually absorb.

    est_weeks (legacy single point) keeps the weekly_pace semantics; the new
    est_weeks_min / est_weeks_max bracket it with the faster/slower pace.
    fit: "tight" (needs more than the weeks left), "ok", "loose" (plenty of
    slack), "none" (no deadline) — computed on the legacy point, unchanged.
    """
    pace = max(1, int(weekly_pace))
    est_weeks = max(1, -(-int(required_count) // pace)) if required_count > 0 else 0
    days = max(1, min(7, int(available_days or 7)))
    minutes = max(5, int(daily_minutes or 45))
    per_concept = max(5, int(minutes_per_concept or 20))
    weekly_capacity = minutes * days
    time_pace = max(1, weekly_capacity // per_concept)
    pace_fast, pace_slow = max(pace, time_pace), max(1, min(pace, time_pace))
    est_weeks_min = (max(1, -(-int(required_count) // pace_fast))
                     if required_count > 0 else 0)
    est_weeks_max = (max(1, -(-int(required_count) // pace_slow))
                     if required_count > 0 else 0)
    weeks_left: float | None = None
    fit = "none"
    if deadline and deadline > 0:
        weeks_left = round(max(0.0, (deadline - now) / (7 * 86400.0)), 1)
        if est_weeks <= 0:
            fit = "loose"
        elif weeks_left <= 0:
            fit = "tight"
        elif est_weeks > weeks_left:
            fit = "tight"
        elif est_weeks <= weeks_left * 0.6:
            fit = "loose"
        else:
            fit = "ok"
    return {"weekly_pace": pace, "est_weeks": est_weeks,
            "est_weeks_min": est_weeks_min, "est_weeks_max": est_weeks_max,
            "weekly_capacity_minutes": weekly_capacity,
            "time_pace": time_pace,
            "weeks_left": weeks_left, "fit": fit,
            "required_count": int(required_count)}


def compute_gap_analysis(goal: LearningGoal, *,
                         subject_skills: list[dict[str, Any]],
                         evaluation_view: dict[str, Any],
                         prereq_map: dict[str, list[str]] | None = None,
                         now: float | None = None,
                         chain_mode: str = "subject",
                         weekly_pace: int = 5,
                         daily_minutes: int = 45,
                         available_days: int = 7) -> GoalState:
    """Compute a GoalState from one goal + a read-only evaluation/graph
    projection (G4 §13.8：语义分组，无数值掌握).

    subject_skills: [{skill_id, name, subject, difficulty}] from the M5 graph.
        The caller decides the口径: with a concept-level goal binding this is
        the goal's prerequisite closure; otherwise it is the whole subject.
    evaluation_view: {skill_id: {"state": ...}} 统一评价只读投影
        （supported_in_scope/emerging/fragile/conflicting/not_observed）。
    prereq_map: {skill_id: [prerequisite_id]} from M5 (read-only), for the
        backward-plan topo-sort. Optional -- without it, gaps are unordered.
    chain_mode: "concept_chain" | "subject" -- echoed into the GoalState so
        the UI can label which口径 gaps/progress describe.
    daily_minutes/available_days: the student's schedule (W4/A13 estimate
        range input), forwarded to estimate_schedule.

    G4 gap status: "unknown" = 未观察（无证据，不宣称缺口，也不等于不会）;
    "weak" = 已观察待解决（fragile/conflicting/emerging）。Planning still
    covers unknown concepts — a plan teaches what has not been tested; the
    status only labels the evidence state.

    Never raises; returns a best-effort GoalState on any failure.
    """
    try:
        now = now if now is not None else time.time()
        subject = (goal.subjects[0] if goal.subjects
                   else parse_goal_text(goal.title).get("subject", ""))

        total = len(subject_skills)
        supported = 0
        gaps: list[GapItem] = []
        for s in subject_skills:
            sid = str(s.get("skill_id", ""))
            rec = evaluation_view.get(sid) or {}
            st = str(rec.get("state", "")) if isinstance(rec, dict) else ""
            if st == "supported_in_scope":
                supported += 1
                continue
            status = "weak" if st in ("emerging", "fragile",
                                      "conflicting") else "unknown"
            gaps.append(GapItem(
                skill_id=sid, name=str(s.get("name", "")),
                subject=str(s.get("subject", "")),
                difficulty=int(s.get("difficulty", 3)),
                status=status))

        supported_ratio = (supported / total) if total > 0 else 0.0
        current_level = GoalAnalysisLevel.from_supported_ratio(supported_ratio)
        target_level = GoalAnalysisLevel.PROFICIENT
        if goal.goal_type == GoalType.INTEREST:
            target_level = GoalAnalysisLevel.INTERMEDIATE

        # backward plan: topo-sort the gap skills by prerequisites so the
        # planner receives a dependency-respecting order
        required = _backward_plan_order(
            [g.skill_id for g in gaps], prereq_map or {})
        # 拓扑分层：layer = 前置链最长路径深度（1 = 现在就能学）
        layer_of = _gap_layers([g.skill_id for g in gaps], prereq_map or {})
        for g in gaps:
            g.layer = layer_of.get(g.skill_id, 0)

        urgency = _deadline_urgency(goal.deadline, now)

        return GoalState(
            goal_id=goal.id, goal_title=goal.title,
            goal_type=goal.goal_type, subject=subject,
            deadline=goal.deadline, current_level=current_level,
            target_level=target_level, supported_ratio=supported_ratio,
            total_skills=total, supported_skills=supported,
            gaps=gaps, required_skills=required,
            recommended_strategy=_recommend_strategy(current_level, urgency),
            urgency=urgency, analyzed_at=now,
            chain_mode=chain_mode,
            target_concept_ids=list(goal.target_concept_ids or []),
            estimate=estimate_schedule(
                len(required), goal.deadline, now, weekly_pace=weekly_pace,
                daily_minutes=daily_minutes, available_days=available_days))
    except Exception:
        return GoalState(goal_id=goal.id, goal_title=goal.title,
                         goal_type=goal.goal_type)


def _gap_layers(skill_ids: list[str],
                prereq_map: dict[str, list[str]]) -> dict[str, int]:
    """Dependency layer per skill: 1 = no unmet prerequisite (learnable now),
    N = needs some layer-(N-1) skill first (longest prerequisite path).
    Pure function with cycle-safe memoization; {} when no prereq data."""
    target = set(skill_ids)
    memo: dict[str, int] = {}

    def depth(cid: str, seen: frozenset[str] = frozenset()) -> int:
        if cid in memo:
            return memo[cid]
        if cid in seen:  # cycle guard
            return 1
        d = 0
        for pre in prereq_map.get(cid, []):
            if pre in target:
                d = max(d, depth(pre, seen | {cid}))
        memo[cid] = d + 1
        return memo[cid]

    return {cid: depth(cid) for cid in skill_ids}


def _backward_plan_order(skill_ids: list[str],
                         prereq_map: dict[str, list[str]]) -> list[str]:
    """Topo-sort skills so a skill appears after its prerequisites.

    Reuses the same lightweight topo-sort logic learning_planner uses; kept
    local to avoid a circular import (learning_planner imports schema only).
    Returns only the skills present in skill_ids, in dependency order.
    """
    target = set(skill_ids)
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

    for cid in skill_ids:
        visit(cid)
    return order


def _deadline_urgency(deadline: float, now: float) -> float:
    """Map days-until-deadline to a 0..1 urgency score.

    1.0 = deadline today/past; 0.0 = no deadline or >365 days away. This
    drives schedule density (how many concepts per week).
    """
    if deadline <= 0:
        return 0.0
    days_left = max(0.0, (deadline - now) / 86400.0)
    if days_left <= 0:
        return 1.0
    if days_left >= 365:
        return 0.0
    return round(1.0 - days_left / 365.0, 3)


def _recommend_strategy(current: GoalAnalysisLevel,
                        urgency: float) -> str:
    """A one-line strategy hint derived from the gap analysis (deterministic).

    Combines the student's current level with deadline pressure to suggest
    whether to front-load foundations, accelerate, or review-first. Pure
    function; the planner may override it.
    """
    if current == GoalAnalysisLevel.NOVICE:
        return "foundation_first"
    if urgency > 0.7:
        return "intensive_review"
    if current in (GoalAnalysisLevel.BEGINNER, GoalAnalysisLevel.INTERMEDIATE):
        return "mixed_progress"
    return "advanced_refinement"
