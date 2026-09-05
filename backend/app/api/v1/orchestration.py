"""Learning Orchestration API (M9 observability + plan management).

Exposes the student's long-term goals, weekly plan (action tasks + subtasks),
daily tasks, SRS review queue, and habit stats for human inspection and the
frontend "Learning Center" page. Mirrors the /evaluation and /ux endpoints.

Write endpoints: goal add/patch/delete (the first two trigger the async LLM
weekly planning + today's task kickoff), plan regenerate, task CRUD, task
complete. LLM calls happen ONLY in these API-initiated async paths (weekly
planning, daily composition), each gated + deterministically backstopped --
never inside supervisor hooks. Uses DEFAULT_STUDENT_ID (single-student
system, same as M2-M8). Never raises into a request (defensive); validation
errors surface as 400.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.agents.learning_orchestration import get_orchestration_service
from app.agents.learning_orchestration import schedule_engine, task_executor
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


class GoalBody(BaseModel):
    title: str
    description: str = ""
    goal_type: str = "ability"
    subjects: list[str] = []
    target_concept_ids: list[str] = []
    deadline: float = 0.0


class GoalPatchBody(BaseModel):
    title: str | None = None
    description: str | None = None
    goal_type: str | None = None
    subjects: list[str] | None = None
    target_concept_ids: list[str] | None = None
    deadline: float | None = None


class TaskCreateBody(BaseModel):
    day: str = ""
    title: str = ""
    concept_id: str = ""
    concept_name: str = ""
    kind: str = "study"
    phase: str = ""
    estimate_minutes: int = 15
    priority: int = 3
    milestone_id: str = ""


class TaskPatchBody(BaseModel):
    title: str | None = None
    day: str | None = None
    kind: str | None = None
    phase: str | None = None
    estimate_minutes: int | None = None
    priority: int | None = None
    milestone_id: str | None = None
    status: str | None = None


class WeekConceptIn(BaseModel):
    concept_id: str = ""
    name: str = ""
    difficulty: int = 3
    milestone_id: str = ""


class WeekBody(BaseModel):
    focus: str = ""
    concepts: list[WeekConceptIn] = []
    week_start: float | None = None


class WeekTaskBody(BaseModel):
    title: str
    concept_ids: list[str] = []
    kind: str = "study"


class SubTaskBody(BaseModel):
    title: str
    estimate_minutes: int = 15


async def _kickoff_payload(student_id: str) -> dict[str, Any]:
    """Shared response payload after a goal write: weeks + first_task.

    Materializes today's tasks (LLM composer, gap-fill persistence) and picks
    the first pending task of the day as the kickoff CTA target. Defensive:
    any failure degrades to empty weeks / null first_task.
    """
    weeks: list[dict[str, Any]] = []
    first_task: dict[str, Any] | None = None
    try:
        svc = get_orchestration_service()
        summary = svc.summary(student_id)
        weeks = summary.get("weekly_plan", [])
        # compose_llm: this runs right after an explicit goal write, where the
        # user expects the LLM coach's picks (the plain GET /today stays
        # deterministic so a page load never waits on a model).
        tasks = await svc.today_tasks(student_id, compose_llm=True)
        today = task_executor._day_str(time.time())
        first_task = next(
            (t for t in tasks
             if t.get("day") == today and t.get("status") == "pending"),
            None)
    except Exception:
        pass
    return {"weeks": weeks, "first_task": first_task}


@router.get("/plan")
def orchestration_plan(student_id: str = Depends(resolve_student_id)) -> dict:
    """Full orchestration state: goal, milestones, weekly plan, daily tasks,
    schedule, habit stats, SRS queue + derived counts + needs_replan flag."""
    return get_orchestration_service().summary(student_id)


@router.get("/today")
async def orchestration_today(student_id: str = Depends(resolve_student_id)) -> list[dict]:
    """Today's tasks (carryover section first; LLM-composed when absent)."""
    return await get_orchestration_service().today_tasks(student_id)


@router.post("/goal")
async def orchestration_add_goal(body: GoalBody,
                                 student_id: str = Depends(resolve_student_id)) -> dict:
    """Append a long-term learning goal (multi-goal, capped at 4).

    Tail: LLM weekly planning (deterministic fallback) + today's task
    kickoff -- every goal write immediately produces visible next steps.
    400 on an empty title or cap overflow.
    """
    svc = get_orchestration_service()
    try:
        goal = svc.add_goal(
            student_id, title=body.title, description=body.description,
            goal_type=body.goal_type, subjects=body.subjects,
            target_concept_ids=body.target_concept_ids,
            deadline=body.deadline)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await svc.regenerate_plan(student_id)
    payload = await _kickoff_payload(student_id)
    return {"ok": True, "goal_id": goal.id, **payload}


@router.patch("/goal/{goal_id}")
async def orchestration_patch_goal(goal_id: str, body: GoalPatchBody,
                                   student_id: str = Depends(resolve_student_id)) -> dict:
    """Patch fields of one goal (all optional).

    Re-runs the gap analysis and regenerates the weekly plan; the SRS queue,
    all historical tasks, and user-created plan entries are preserved.
    404 when the goal id does not exist.
    """
    svc = get_orchestration_service()
    ok = svc.update_goal(
        student_id, goal_id, title=body.title, description=body.description,
        goal_type=body.goal_type, subjects=body.subjects,
        target_concept_ids=body.target_concept_ids,
        deadline=body.deadline)
    if not ok:
        raise HTTPException(status_code=404, detail="goal not found")
    await svc.regenerate_plan(student_id)
    payload = await _kickoff_payload(student_id)
    return {"ok": True, **payload}


@router.delete("/goal/{goal_id}")
async def orchestration_delete_goal(goal_id: str,
                                    student_id: str = Depends(resolve_student_id)) -> dict:
    """Delete one goal and re-plan so its concepts leave the auto plan.

    The SRS queue, historical tasks, and user-created plan entries are
    preserved. 404 when the goal id does not exist.
    """
    svc = get_orchestration_service()
    ok = svc.delete_goal(student_id, goal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="goal not found")
    if svc.summary(student_id).get("goals"):
        # remaining goals keep driving the plan; rebuild without the deleted one
        await svc.regenerate_plan(student_id)
    payload = await _kickoff_payload(student_id)
    return {"ok": True, **payload}


@router.post("/regenerate")
async def orchestration_regenerate(student_id: str = Depends(resolve_student_id),
                                   num_weeks: int = Query(default=4, ge=1, le=12)) -> dict:
    """Regenerate the weekly plan from current goal/mastery/graph.

    Only future / unmaterialized content is recomputed; persisted daily
    tasks and user-created plan entries are never touched. reason:
    "" | "no_goal" | "empty_plan" -- an empty plan with a goal is a
    legitimate end state (ok=True), not an error.
    """
    svc = get_orchestration_service()
    ok, reason = await svc.regenerate_plan(student_id, num_weeks=num_weeks)
    weeks: list[dict[str, Any]] = []
    if reason != "no_goal":
        try:
            weeks = svc.summary(student_id).get("weekly_plan", [])
        except Exception:
            pass
    return {"ok": ok, "reason": reason, "weeks": weeks}


def _day_capacity_warning(student_id: str, day: str) -> dict[str, Any] | None:
    """W4 容量可行性：当日负载超预算时的 advisory 载荷（不阻断写入）。
    None = 该日在预算内或读取失败（静默降级，永不抛）。"""
    try:
        if not day:
            return None
        svc = get_orchestration_service()
        report = schedule_engine.capacity_report(svc._load(student_id))
        for entry in report.get("days", []):
            if entry.get("day") == day and entry.get("overload"):
                return {
                    "day": day,
                    "planned_minutes": int(entry.get("planned_minutes", 0)),
                    "daily_minutes": int(report.get("daily_minutes", 0)),
                }
    except Exception:
        pass
    return None


@router.post("/task")
def orchestration_add_task(body: TaskCreateBody,
                           student_id: str = Depends(resolve_student_id)) -> dict:
    """Create a user task (custom; id user_{day}_{seq}). 400 on cap overflow
    or illegal kind/phase."""
    try:
        task = get_orchestration_service().add_task(
            student_id, day=body.day, title=body.title,
            concept_id=body.concept_id, concept_name=body.concept_name,
            kind=body.kind, phase=body.phase,
            estimate_minutes=body.estimate_minutes, priority=body.priority,
            milestone_id=body.milestone_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out = {"ok": True, "task": task.to_dict()}
    warning = _day_capacity_warning(student_id, task.day)
    if warning:
        out["capacity_warning"] = warning
    return out


@router.patch("/task/{task_id}")
def orchestration_update_task(task_id: str, body: TaskPatchBody,
                              student_id: str = Depends(resolve_student_id)) -> dict:
    """Patch mutable fields of a daily task. 400 on illegal values."""
    svc = get_orchestration_service()
    try:
        ok = svc.update_task(
            student_id, task_id, title=body.title, day=body.day,
            kind=body.kind, phase=body.phase,
            estimate_minutes=body.estimate_minutes, priority=body.priority,
            milestone_id=body.milestone_id, status=body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out: dict[str, Any] = {"ok": ok}
    # 容量 advisory：改期或改时长后重算受影响日（无 day 字段时取任务当前日）。
    if body.day is not None or body.estimate_minutes is not None:
        try:
            day = body.day or next(
                (t.day for t in svc._load(student_id).daily_tasks
                 if t.id == task_id), "")
            warning = _day_capacity_warning(student_id, day)
            if warning:
                out["capacity_warning"] = warning
        except Exception:
            pass
    return out


@router.delete("/task/{task_id}")
def orchestration_delete_task(task_id: str,
                              student_id: str = Depends(resolve_student_id)) -> dict:
    """Delete a daily task by id."""
    ok = get_orchestration_service().delete_task(student_id, task_id)
    return {"ok": ok}


@router.post("/week")
def orchestration_add_week(body: WeekBody,
                           student_id: str = Depends(resolve_student_id)) -> dict:
    """Append a manual week to the weekly plan. 400 on concept cap."""
    try:
        week = get_orchestration_service().add_week(
            student_id, focus=body.focus,
            concepts=[c.model_dump() for c in body.concepts],
            week_start=body.week_start)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "week": week.to_dict()}


@router.delete("/week/{week_index}")
def orchestration_delete_week(week_index: int,
                              student_id: str = Depends(resolve_student_id)) -> dict:
    """Delete one week; materialized daily tasks are never touched.
    404 when not found."""
    ok = get_orchestration_service().delete_week(student_id, week_index)
    if not ok:
        raise HTTPException(status_code=404, detail="week not found")
    return {"ok": True}


@router.post("/week/{week_index}/concept")
def orchestration_add_week_concept(week_index: int, body: WeekConceptIn,
                                   student_id: str = Depends(resolve_student_id)) -> dict:
    """Add a concept to a week (free-text name allowed). 400 on duplicate or
    per-week cap; 404 when the week does not exist."""
    try:
        pc = get_orchestration_service().add_week_concept(
            student_id, week_index, concept_id=body.concept_id,
            name=body.name, difficulty=body.difficulty,
            milestone_id=body.milestone_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if pc is None:
        raise HTTPException(status_code=404, detail="week not found")
    return {"ok": True, "concept": pc.to_dict()}


@router.delete("/week/{week_index}/concept/{concept_id}")
def orchestration_remove_week_concept(week_index: int, concept_id: str,
                                      student_id: str = Depends(resolve_student_id)) -> dict:
    """Remove a concept from a week (free-text concepts match by name).
    404 when the week or concept is not found."""
    ok = get_orchestration_service().remove_week_concept(
        student_id, week_index, concept_id)
    if not ok:
        raise HTTPException(status_code=404, detail="week or concept not found")
    return {"ok": True}


@router.post("/week/{week_index}/task")
def orchestration_add_week_task(week_index: int, body: WeekTaskBody,
                                student_id: str = Depends(resolve_student_id)) -> dict:
    """Create a user week task (survives regeneration). 400 on empty title /
    illegal kind / cap; 404 when the week does not exist."""
    try:
        wt = get_orchestration_service().add_week_task(
            student_id, week_index, title=body.title,
            concept_ids=body.concept_ids, kind=body.kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if wt is None:
        raise HTTPException(status_code=404, detail="week not found")
    return {"ok": True, "task": wt.to_dict()}


@router.delete("/week/{week_index}/task/{task_id}")
def orchestration_delete_week_task(week_index: int, task_id: str,
                                   student_id: str = Depends(resolve_student_id)) -> dict:
    """Delete one week task. 404 when the week or task is not found."""
    ok = get_orchestration_service().delete_week_task(
        student_id, week_index, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="week or task not found")
    return {"ok": True}


@router.post("/week/{week_index}/task/{task_id}/subtask")
def orchestration_add_subtask(week_index: int, task_id: str,
                              body: SubTaskBody,
                              student_id: str = Depends(resolve_student_id)) -> dict:
    """Append a manual subtask to a week task. 400 on empty title / cap;
    404 when the week or task does not exist."""
    try:
        st = get_orchestration_service().add_subtask(
            student_id, week_index, task_id, title=body.title,
            estimate_minutes=body.estimate_minutes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if st is None:
        raise HTTPException(status_code=404, detail="week or task not found")
    return {"ok": True, "subtask": st.to_dict()}


@router.patch("/week/{week_index}/task/{task_id}/subtask/{subtask_id}")
def orchestration_toggle_subtask(week_index: int, task_id: str,
                                 subtask_id: str,
                                 student_id: str = Depends(resolve_student_id)) -> dict:
    """Flip a subtask's done flag. 404 when not found."""
    ok = get_orchestration_service().toggle_subtask(
        student_id, week_index, task_id, subtask_id)
    if not ok:
        raise HTTPException(status_code=404, detail="subtask not found")
    return {"ok": True}


@router.delete("/week/{week_index}/task/{task_id}/subtask/{subtask_id}")
def orchestration_delete_subtask(week_index: int, task_id: str,
                                 subtask_id: str,
                                 student_id: str = Depends(resolve_student_id)) -> dict:
    """Delete one subtask. 404 when not found."""
    ok = get_orchestration_service().delete_subtask(
        student_id, week_index, task_id, subtask_id)
    if not ok:
        raise HTTPException(status_code=404, detail="subtask not found")
    return {"ok": True}


@router.post("/week/{week_index}/task/{task_id}/suggest")
async def orchestration_suggest_subtasks(week_index: int, task_id: str,
                                         student_id: str = Depends(resolve_student_id)) -> dict:
    """LLM-recommend subtasks for a week task (persisted as source=auto).
    404 when the week/task does not exist; 502 when generation failed."""
    svc = get_orchestration_service()
    state_week = svc._find_week(svc._load(student_id), week_index)  # existence check for 404 vs 502
    if state_week is None or svc._find_week_task(state_week, task_id) is None:
        raise HTTPException(status_code=404, detail="week or task not found")
    task = await svc.suggest_subtasks(student_id, week_index, task_id)
    if task is None:
        raise HTTPException(status_code=502,
                            detail="suggestion unavailable, please retry")
    return {"ok": True, "task": task.to_dict()}


@router.post("/task/{task_id}/complete")
def orchestration_complete_task(task_id: str,
                                student_id: str = Depends(resolve_student_id)) -> dict:
    """Mark a daily task as completed."""
    ok, emitted = get_orchestration_service().complete_task(student_id, task_id)
    # forward any batch-completion event to M6's event bus (modification 1)
    if emitted:
        try:
            from app.agents.memory import get_memory_service, is_enabled as mem_on
            if mem_on():
                from app.agents.learning_orchestration import event_emitter as ee
                get_memory_service().consume_turn(
                    student_id=student_id,
                    events=ee.to_event_dicts(emitted))
        except Exception:
            pass
    return {"ok": ok, "emitted_events": len(emitted)}


class SchedulePatchBody(BaseModel):
    daily_minutes: int | None = None


@router.post("/task/{task_id}/launch")
def orchestration_launch_task(task_id: str,
                              student_id: str = Depends(resolve_student_id)) -> dict:
    """Bind a daily task to a chat session (W4/A12).

    Server-side replacement for the text-only ``/chat?q=概念&send=1`` deep
    link: validates the task belongs to the caller, pre-creates a session
    carrying task_binding, and returns {episode_id, session_id, launch_url}.
    Idempotent — relaunching an uncompleted task resumes the same episode
    and session. Zero LLM. 404 when the task does not exist (or is already
    completed — nothing left to launch)."""
    out = get_orchestration_service().launch_task(student_id, task_id)
    if out is None:
        raise HTTPException(status_code=404, detail="task not found or completed")
    return out


@router.patch("/schedule")
def orchestration_patch_schedule(body: SchedulePatchBody,
                                 student_id: str = Depends(resolve_student_id)) -> dict:
    """Patch the schedule config (daily time budget)."""
    svc = get_orchestration_service()
    out = svc.update_schedule(student_id, daily_minutes=body.daily_minutes)
    if out is None:
        raise HTTPException(status_code=500, detail="schedule update failed")
    # W4 容量可行性：预算变化后重算全日负载（advisory——存量超载日可见，
    # 不回滚学生的既有任务）。
    resp: dict[str, Any] = {"ok": True, "schedule": out}
    try:
        resp["capacity"] = schedule_engine.capacity_report(svc._load(student_id))
    except Exception:
        pass
    return resp


@router.get("/habit")
def orchestration_habit(student_id: str = Depends(resolve_student_id)) -> dict:
    """Study-habit stats: streak, completion rate, procrastination count."""
    summary = get_orchestration_service().summary(student_id)
    return summary.get("habit", {})


@router.get("/review")
def orchestration_review(student_id: str = Depends(resolve_student_id)) -> list[dict]:
    """SRS-due review cards for today."""
    return get_orchestration_service().due_reviews(student_id)
