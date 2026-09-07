"""LLM daily composer (M9): pick today's tasks from a deterministic pool.

This is the creative core of the interactive orchestration: instead of a fixed
materialisation, an LLM "coach" looks at a DETERMINISTIC candidate pool and
picks at most `slots` tasks for today, attaching a one-line "why this today"
note (reason) and a stage label (phase) to each.

Deterministic guardrails (the LLM never controls identity, caps, or order):
  - the candidate pool is built by pure code: SRS-due cards ∪ current-milestone
    unmastered concepts ∪ M2 weak concepts (records with p<0.6) ∪ yesterday
    carryover, each with metadata (mastery, overdue days, milestone relation);
  - the validation gate rejects any pick whose concept_id is outside the pool,
    whose kind is illegal, that duplicates a (concept_id, kind) key, or that
    exceeds the slot budget;
  - any failure (LLM down, bad JSON, gate failure) falls back to the existing
    deterministic task_executor.generate_daily_tasks + template reasons;
  - persistence goes through task_executor.materialize_day (gap-fill), so the
    task-uniqueness contract holds.

IMPORT-CLEAN: no student_model import -- the caller passes plain-data
projections (mastery_view, concept_names).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from . import spaced_repetition as srs, task_executor
from .schema import (DailyTask, DailyTaskStatus, OrchestrationState,
                     TASK_PHASES, TaskKind)

_WEAK_THRESHOLD = 0.6
_POOL_LIMIT = 12

# template reasons for the deterministic fallback path (by task kind)
_TEMPLATE_REASONS = {
    "review": "SRS 到期待复习",
    "study": "本周重点概念",
    "practice": "针对弱项巩固练习",
    "summary": "回顾总结今日所学",
}


def build_candidate_pool(state: OrchestrationState, *,
                         mastery_view: dict[str, Any],
                         concept_names: dict[str, str],
                         now: float | None = None) -> list[dict[str, Any]]:
    """Build the deterministic candidate pool for today's composition.

    Each entry: {concept_id, name, mastery, overdue_days, milestone_id,
    sources}. `sources` is a set-like list of where the candidate came from
    ("srs_due" / "milestone" / "weak" / "carryover") -- the composer prompt
    and the fallback reason templates read it. Never raises.
    """
    try:
        now = now if now is not None else time.time()
        today = task_executor._day_str(now)
        pool: dict[str, dict[str, Any]] = {}

        def _entry(cid: str, name: str = "", **extra: Any) -> dict[str, Any]:
            e = pool.get(cid)
            if e is None:
                rec = mastery_view.get(cid) or {}
                p = float(rec.get("p_known", 0)) if isinstance(rec, dict) else 0.0
                e = {"concept_id": cid,
                     "name": name or concept_names.get(cid, cid),
                     "mastery": round(p, 3), "overdue_days": 0,
                     "milestone_id": "", "sources": [],
                     # action-level refs (empty for plain concept entries)
                     "real_concept_id": "", "week_task_id": "",
                     "subtask_id": ""}
                e.update({k: v for k, v in extra.items() if v})
                pool[cid] = e
            return e

        # 1. SRS-due cards (memory decay -- highest urgency)
        for card in srs.due_cards(state.review_queue, now=now, limit=_POOL_LIMIT):
            e = _entry(card.concept_id, card.concept_name)
            if "srs_due" not in e["sources"]:
                e["sources"].append("srs_due")
            e["overdue_days"] = max(e["overdue_days"],
                int(max(0.0, now - card.next_review) // 86400))

        # 2. current-week unmastered concepts + unfinished subtasks (the
        #    plan's present focus). Subtask entries are action-level: their
        #    pool key is the subtask id, and picks materialise with
        #    week_task_id/subtask_id refs so completion writes back.
        from . import weekly_planner_llm
        cur = weekly_planner_llm.current_week(state, now=now)
        if cur:
            for pc in cur.concepts:
                rec = mastery_view.get(pc.concept_id) or {}
                p = float(rec.get("p_known", 0)) if isinstance(rec, dict) else 0.0
                if p >= pc.planned_mastery:
                    continue
                e = _entry(pc.concept_id, pc.name)
                if "current_week" not in e["sources"]:
                    e["sources"].append("current_week")
            for wt in cur.tasks:
                if wt.effective_done:
                    continue
                real_c = wt.concept_ids[0] if wt.concept_ids else ""
                for st in wt.subtasks:
                    if st.done:
                        continue
                    e = _entry(st.id, st.title,
                               real_concept_id=real_c, week_task_id=wt.id,
                               subtask_id=st.id)
                    if "current_week" not in e["sources"]:
                        e["sources"].append("current_week")

        # 3. M2 weak concepts (seen but p < 0.6)
        for sid, rec in (mastery_view or {}).items():
            if not isinstance(rec, dict):
                continue
            if int(rec.get("attempts", 0)) > 0 and \
                    float(rec.get("p_known", 0)) < _WEAK_THRESHOLD:
                e = _entry(str(sid))
                if "weak" not in e["sources"]:
                    e["sources"].append("weak")

        # 4. yesterday (and earlier) carryover tasks
        for t in state.daily_tasks:
            if t.day < today and t.status.value in (
                    "pending", "in_progress", "overdue") and t.concept_id:
                e = _entry(t.concept_id, t.concept_name)
                if "carryover" not in e["sources"]:
                    e["sources"].append("carryover")

        out = list(pool.values())
        # most urgent first: carryover/srs, then this week's action items,
        # then lowest mastery
        out.sort(key=lambda e: (
            0 if ("carryover" in e["sources"] or "srs_due" in e["sources"]) else
            1 if "current_week" in e["sources"] else 2,
            e["mastery"]))
        return out[:_POOL_LIMIT]
    except Exception:
        return []


def habit_context(patterns: list[dict[str, Any]], *, limit: int = 3) -> str:
    """Render M6 long-term habit patterns as grounded compose context.

    The write side (habit_milestone / task_batch_completed /
    milestone_completed / goal_progress events folded by M6) finally gets a
    real consumer here: the daily coach sees the student's established habits
    ("连续学习7天", "稳定完成每日任务") and can pace today's picks accordingly.
    Pure function over plain-data projections (import-clean); empty patterns
    render "" so the prompt block simply disappears. Never raises.
    """
    try:
        lines = []
        for p in patterns or []:
            fact = str((p or {}).get("fact") or "").strip()
            if not fact:
                continue
            ev = int((p or {}).get("evidence_count", 0))
            lines.append(f"{fact}（证据 {ev} 次）" if ev > 1 else fact)
            if len(lines) >= limit:
                break
        if not lines:
            return ""
        return ("学生长期学习习惯（供节奏参考，不改变候选池）："
                + "；".join(lines) + "。")
    except Exception:
        return ""


def build_compose_prompt(pool: list[dict[str, Any]], slots: int,
                         goal_title: str = "",
                         context: str = "") -> list[dict[str, str]]:
    """Build the chat messages for the daily-composition LLM call.

    context: optional grounded context (e.g. Bloom cognitive-profile
    weaknesses) so the coach's picks and reasons reference what the student
    actually struggles with, not generic filler."""
    lines = []
    for e in pool:
        meta = [f"掌握度 {e['mastery']}"]
        if e.get("overdue_days"):
            meta.append(f"逾期 {e['overdue_days']} 天")
        src = "/".join(e.get("sources", []))
        if e.get("subtask_id"):
            kind_label = "本周任务子步骤"
        else:
            kind_label = "概念"
        lines.append(
            f"- id={e['concept_id']}（{kind_label}：{e['name']}，"
            f"{'; '.join(meta)}，来源 {src}）")
    context_block = f"\n{context.strip()}\n" if context and context.strip() else ""
    system = (
        "你是一名学习教练，为学生挑选今天最值得做的学习任务并写一句简短的"
        "「为什么今天学这个」批注。只输出 JSON，不要输出任何其他内容。")
    user = f"""{f"学习目标：{goal_title}" if goal_title else ""}{context_block}候选池（只能从中挑选，concept_id 必须原样使用池中的 id；「本周任务子步骤」来自周计划，优先让它们出现在今天的安排里）：
{chr(10).join(lines)}

请挑选最多 {slots} 个今天最值得做的任务。每个任务给出：
- concept_id：必须来自上面的候选池；
- kind：study（新学）/ review（复习）/ practice（练习）/ summary（总结）；
- phase：foundation（打基础）/ reinforce（巩固）/ sprint（冲刺）；
- reason：一句简短的中文批注，说明为什么今天做这个（20 字以内）。

输出 JSON 格式（不要输出其他内容）：
```json
{{"tasks": [{{"concept_id": "...", "kind": "review", "phase": "reinforce", "reason": "..."}}]}}
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


def parse_compose_response(text: str, pool: list[dict[str, Any]],
                           slots: int) -> list[dict[str, Any]] | None:
    """Parse + validate the LLM daily composition.

    Gate: every concept_id must be inside the pool, kind must be a legal
    TaskKind, phase must be empty or a legal TASK_PHASES label, no duplicate
    (concept_id, kind) keys, and the pick count must fit the slot budget.
    Returns a list of pick dicts or None on any gate failure (the caller then
    falls back to the deterministic path). Never raises.
    """
    try:
        obj = _extract_json(text)
        if not obj or not isinstance(obj.get("tasks"), list):
            return None
        raw = [t for t in obj["tasks"] if isinstance(t, dict)]
        if not raw or len(raw) > max(1, slots):
            return None
        pool_by_id = {str(e["concept_id"]): e for e in pool}
        legal_kinds = {k.value for k in TaskKind}
        seen: set[tuple[str, str]] = set()
        picks: list[dict[str, Any]] = []
        for t in raw:
            cid = str(t.get("concept_id", ""))
            kind = str(t.get("kind", ""))
            phase = str(t.get("phase", "")).strip()
            if cid not in pool_by_id or kind not in legal_kinds:
                return None
            if phase and phase not in TASK_PHASES:
                return None
            key = (cid, kind)
            if key in seen:
                return None
            seen.add(key)
            picks.append({"concept_id": cid, "kind": kind, "phase": phase,
                          "reason": str(t.get("reason", "")).strip()[:60]})
        return picks
    except Exception:
        return None


def tasks_from_picks(state: OrchestrationState, picks: list[dict[str, Any]],
                     pool: list[dict[str, Any]], slots_minutes: list[int],
                     *, now: float | None = None) -> list[DailyTask]:
    """Materialise validated LLM picks into DailyTask objects (not persisted).

    The caller persists them via task_executor.materialize_day (gap-fill).
    Never raises.
    """
    try:
        now = now if now is not None else time.time()
        day = task_executor._day_str(now)
        pool_by_id = {str(e["concept_id"]): e for e in pool}
        out: list[DailyTask] = []
        for i, pick in enumerate(picks):
            e = pool_by_id.get(pick["concept_id"], {})
            minutes = slots_minutes[i] if i < len(slots_minutes) else 15
            # action-level entries (week subtasks) carry refs; plain concept
            # entries keep the concept identity.
            real_concept = str(e.get("real_concept_id") or "") or (
                "" if e.get("subtask_id") else pick["concept_id"])
            out.append(DailyTask(
                id=task_executor._task_id(day, pick["concept_id"], pick["kind"]),
                day=day, concept_id=real_concept,
                concept_name=str(e.get("name", pick["concept_id"])),
                kind=TaskKind.from_value(pick["kind"]),
                status=DailyTaskStatus.PENDING, priority=2,
                estimate_minutes=minutes,
                milestone_id=str(e.get("milestone_id", "")),
                week_task_id=str(e.get("week_task_id", "")),
                subtask_id=str(e.get("subtask_id", "")),
                title=str(e.get("name", "")) if e.get("subtask_id") else "",
                phase=pick.get("phase", ""), reason=pick.get("reason", "")))
        return out
    except Exception:
        return []


def annotate_fallback(tasks: list[DailyTask]) -> list[DailyTask]:
    """Attach template "why today" reasons to deterministic fallback tasks."""
    for t in tasks:
        if not t.reason:
            t.reason = _TEMPLATE_REASONS.get(t.kind.value, "")
    return tasks
