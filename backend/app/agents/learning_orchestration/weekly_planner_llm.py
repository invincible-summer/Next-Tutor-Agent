"""LLM weekly planner (M9): goal -> N weeks of action-level tasks + subtasks.

Replaces the old milestone layer: instead of goal -> milestones -> concepts
dripped into weeks, ONE LLM call lays out the whole weekly plan directly —
each week gets a focus, action-level WeekTasks ("学完浮力前两节"), and
concrete SubTasks ("做 10 道计算题"), all referencing the topo-ordered
required_skills from the gap analysis.

Deterministic guardrails (the LLM never controls identity, caps, coverage):
  - the validation gate (parse_weekly_response) rejects any plan whose
    concept_ids fall outside required_skills, that repeats a concept across
    tasks, that misses full coverage of required_skills, or that breaks the
    task/subtask caps — then the caller falls back to the deterministic
    learning_planner.generate_weekly_plan + derive_tasks_fallback;
  - ids are assigned by code (wt_{week}_{seq} / st_{task}_{seq}), never by
    the LLM, so regeneration keeps references stable;
  - user-created entries (source="user") are merged back by the caller —
    this module only ever produces source="auto" skeletons.

IMPORT-CLEAN: pure functions over plain data; the caller (manager) owns the
LLM call and persistence.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .schema import (_MAX_SUBTASKS, _MAX_WEEK_TASKS, OrchestrationState,
                     PlanConcept, SubTask, TaskKind, WeekTask, WeeklyPlan)

_DAY_SECONDS = 24 * 3600
_MAX_TASKS_PER_WEEK_LLM = 4
_MAX_SUBTASKS_LLM = 4


def build_weekly_prompt(goal_title: str, required: list[str],
                        concept_names: dict[str, str],
                        mastery_view: dict[str, Any],
                        num_weeks: int, daily_minutes: int) -> list[dict[str, str]]:
    """Build the chat messages for the weekly-planning LLM call."""
    lines = []
    for cid in required[:40]:
        rec = mastery_view.get(cid) or {}
        p = float(rec.get("p_known", 0)) if isinstance(rec, dict) else 0.0
        name = concept_names.get(cid, cid)
        lines.append(f"- {cid}（{name}，当前掌握 {p:.2f}）")
    system = (
        "你是一名学习规划师，擅长把长期学习目标拆成逐周可执行的计划。"
        "只输出 JSON，不要输出任何其他内容。")
    user = f"""学习目标：{goal_title}
规划周数：{num_weeks} 周；学生每天可学 {daily_minutes} 分钟。
待覆盖概念（已按先修顺序排好，必须全部覆盖，且只能使用这些概念 id）：
{chr(10).join(lines)}

请输出 {num_weeks} 周计划。每周：
- focus：一句本周主题（10 字以内）；
- tasks：1-{_MAX_TASKS_PER_WEEK_LLM} 个行动级任务（标题是动作，如「学完光的折射前两节并做基础练习」，不是概念名堆砌）；
  每个任务带 concept_ids（只能来自上面列表，一个概念只能出现在一个任务里）、
  kind（study 新学 / review 复习 / practice 练习 / summary 总结）、
  subtasks：2-{_MAX_SUBTASKS_LLM} 个具体可执行的子任务（如「做 10 道折射定律计算题」），
  每个子任务带 estimate_minutes（5-60）。
- 先修概念排前面的周；已掌握（掌握 ≥ 0.75）的概念最多放进 review 任务。

输出 JSON 格式（不要输出其他内容）：
```json
{{"weeks": [{{"focus": "...", "tasks": [{{"title": "...", "concept_ids": ["..."], "kind": "study", "subtasks": [{{"title": "...", "estimate_minutes": 20}}]}}]}}]}}
```"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Robustly pull a JSON object out of an LLM response (fence-tolerant)."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def parse_weekly_response(text: str, required: list[str],
                          num_weeks: int) -> list[dict[str, Any]] | None:
    """Parse + validate the LLM weekly plan. Returns week skeletons or None.

    Gate: 1..num_weeks weeks; each week 1.._MAX_WEEK_TASKS_LLM tasks; every
    concept_id inside required_skills; no concept repeated across tasks
    EXCEPT inside review/summary tasks (those intentionally revisit earlier
    concepts); full coverage of required_skills; kind legal; each task
    1.._MAX_SUBTASKS_LLM subtasks with non-empty titles and sane minutes.
    None on any violation (the caller falls back to the deterministic
    planner). Never raises.
    """
    try:
        obj = _extract_json(text)
        if not obj or not isinstance(obj.get("weeks"), list):
            return None
        raw_weeks = [w for w in obj["weeks"] if isinstance(w, dict)]
        if not raw_weeks or len(raw_weeks) > max(1, num_weeks):
            return None
        required_set = set(required)
        legal_kinds = {k.value for k in TaskKind}
        covered: set[str] = set()
        out: list[dict[str, Any]] = []
        for w in raw_weeks:
            tasks_raw = [t for t in (w.get("tasks") or []) if isinstance(t, dict)]
            if not tasks_raw or len(tasks_raw) > _MAX_TASKS_PER_WEEK_LLM:
                return None
            tasks: list[dict[str, Any]] = []
            for t in tasks_raw:
                title = str(t.get("title", "")).strip()
                kind = str(t.get("kind", "study"))
                cids = [str(c) for c in (t.get("concept_ids") or [])]
                if not title or kind not in legal_kinds:
                    return None
                if any(c not in required_set for c in cids):
                    return None
                # review/summary tasks may legitimately revisit concepts
                if kind not in ("review", "summary") and \
                        any(c in covered for c in cids):
                    return None
                subs_raw = [s for s in (t.get("subtasks") or [])
                            if isinstance(s, dict)]
                if not subs_raw or len(subs_raw) > _MAX_SUBTASKS_LLM:
                    return None
                subs: list[dict[str, Any]] = []
                for s in subs_raw:
                    stitle = str(s.get("title", "")).strip()
                    if not stitle:
                        return None
                    try:
                        minutes = int(s.get("estimate_minutes", 15))
                    except (TypeError, ValueError):
                        minutes = 15
                    subs.append({"title": stitle[:80],
                                 "estimate_minutes": max(5, min(60, minutes))})
                covered.update(cids)
                tasks.append({"title": title[:80], "concept_ids": cids,
                              "kind": kind, "subtasks": subs})
            out.append({"focus": str(w.get("focus", "")).strip()[:20],
                        "tasks": tasks})
        # full coverage: every required concept must be scheduled somewhere
        if required_set and covered != required_set:
            return None
        return out
    except Exception:
        return None


def weeks_from_skeletons(skeletons: list[dict[str, Any]],
                         concept_names: dict[str, str],
                         *, now: float | None = None) -> list[WeeklyPlan]:
    """Materialise validated week skeletons into WeeklyPlan objects.

    Week starts are Mondays from the current week; ids are deterministic
    (wt_{week}_{seq} / st_{task}_{seq}) so regeneration is reference-stable.
    PlanConcept entries are derived from the tasks' concept_ids (the
    concept-granularity view the rest of the pipeline reads). Never raises.
    """
    try:
        now = now if now is not None else time.time()
        t = time.localtime(now)
        monday = now - t.tm_wday * _DAY_SECONDS
        # int() drops the sub-second drift of the planning moment so week
        # starts are stable across regenerations (merge matching relies on
        # the week window).
        monday_midnight = int(monday - (t.tm_sec + t.tm_min * 60 + t.tm_hour * 3600))
        weeks: list[WeeklyPlan] = []
        for wi, sk in enumerate(skeletons):
            week_start = monday_midnight + wi * 7 * _DAY_SECONDS
            tasks: list[WeekTask] = []
            concepts: list[PlanConcept] = []
            seen_c: set[str] = set()
            for ti, traw in enumerate(sk.get("tasks") or [], 1):
                wt_id = f"wt_{wi}_{ti}"
                subs = [SubTask(id=f"st_{wt_id}_{si}",
                                title=str(s.get("title", "")),
                                source="auto",
                                estimate_minutes=int(s.get("estimate_minutes", 15)))
                        for si, s in enumerate(traw.get("subtasks") or [], 1)]
                tasks.append(WeekTask(
                    id=wt_id, title=str(traw.get("title", "")),
                    concept_ids=list(traw.get("concept_ids") or []),
                    kind=str(traw.get("kind", "study")),
                    source="auto", subtasks=subs[:_MAX_SUBTASKS]))
                for cid in traw.get("concept_ids") or []:
                    if cid in seen_c:
                        continue
                    seen_c.add(cid)
                    concepts.append(PlanConcept(
                        concept_id=cid, name=concept_names.get(cid, cid),
                        week_index=wi))
            focus = str(sk.get("focus", "")) or (
                tasks[0].title if tasks else "")
            weeks.append(WeeklyPlan(
                week_index=wi, week_start=week_start, focus=focus,
                concepts=concepts, tasks=tasks[:_MAX_WEEK_TASKS],
                origin="auto"))
        return weeks
    except Exception:
        return []


def derive_tasks_fallback(weeks: list[WeeklyPlan]) -> None:
    """Attach deterministic WeekTasks to planner-produced weeks (in place).

    The deterministic learning_planner only fills concepts; this gives every
    such week one action-level task with per-concept study subtasks plus a
    practice subtask, so the week→day materialisation has structure to draw
    from even when the LLM is unavailable. Mutates and returns None.
    """
    for w in weeks:
        if w.tasks:
            continue
        names = [c.name for c in w.concepts][:3]
        focus = w.focus or (names[0] if names else "本周内容")
        subs = [SubTask(id=f"st_wt_{w.week_index}_1_{i}",
                        title=f"学习《{n}》", source="auto", estimate_minutes=20)
                for i, n in enumerate(names, 1)]
        subs.append(SubTask(id=f"st_wt_{w.week_index}_1_{len(subs) + 1}",
                            title=f"做「{focus}」相关练习并订正",
                            source="auto", estimate_minutes=25))
        w.tasks = [WeekTask(
            id=f"wt_{w.week_index}_1",
            title=f"学习{('、'.join(names[:2])) or focus}并完成配套练习",
            concept_ids=[c.concept_id for c in w.concepts],
            kind="study", source="auto", subtasks=subs[:_MAX_SUBTASKS])]


def current_week(state: OrchestrationState, *,
                 now: float | None = None) -> WeeklyPlan | None:
    """The week whose [week_start, week_start+7d) window contains now.

    Replaces the deleted current_milestone as the plan's 'present focus'.
    Falls back to the first week with any unfinished business, else None.
    """
    try:
        now = now if now is not None else time.time()
        weeks = sorted(state.weekly_plan, key=lambda w: w.week_start)
        for w in weeks:
            if w.week_start <= now < w.week_start + 7 * _DAY_SECONDS:
                return w
        for w in weeks:
            if any(not t.effective_done for t in w.tasks):
                return w
        return weeks[0] if weeks else None
    except Exception:
        return None
