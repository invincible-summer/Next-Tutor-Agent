"""Context Builder: render [编排智能·...] soft-directive blocks (M9).

Mirrors the pattern of every other layer's context_builder (M5/M6/M7/M8):
takes M9's orchestration state and renders an advisory soft-directive block
that the Supervisor injects into the LLM context (step 3h). The block tells
the LLM about the student's long-term plan, today's tasks, and SRS-due
reviews so the answer can reference continuity ("you're on week 2 of the
calculus plan", "let's review integration since it's due").

Returns "" when there is nothing actionable -- so the turn is unchanged (M9 is
invisible when there's no plan). Never raises.
"""
from __future__ import annotations

import time
from typing import Any

from .schema import OrchestrationState
from . import task_executor


def build_orchestration_directive(state: OrchestrationState, *,
                                  concept: str = "", subject: str = "",
                                  intent: str = "", now: float | None = None) -> str:
    """Build the [编排智能·...] advisory block for this turn.

    Returns "" when there is no goal/plan or nothing actionable. The block is
    advisory only -- the LLM composes the answer within the boundary. Never
    raises.
    """
    try:
        now = now if now is not None else time.time()
        if not state.has_goals:
            return ""

        lines: list[str] = []

        # long-term goals (multi-goal: one joined line) + current week
        goals_desc = "、".join(
            f"「{g.title}」（{g.goal_type.value}）"
            for g in state.goals if g.title)
        lines.append(f"[编排智能·长期目标] 目标：{goals_desc}")
        from . import weekly_planner_llm
        cur = weekly_planner_llm.current_week(state, now=now)
        if cur:
            wi = cur.week_index + 1
            lines.append(f"[编排智能·本周重点] 第{wi}周：{cur.focus}")

        # today's tasks
        todays = task_executor.today_tasks(state, now=now)
        pending = [t for t in todays if t.status.value == "pending"]
        if pending:
            task_desc = "、".join(
                f"{t.concept_name or t.kind.value}" for t in pending[:4])
            lines.append(f"[编排智能·今日任务] {len(pending)}项待完成：{task_desc}")

        # SRS-due reviews
        due_count = task_executor.pending_review_count(state, now=now)
        if due_count > 0:
            lines.append(f"[编排智能·复习提醒] 有{due_count}个知识点到期需要复习")

        # habit signal (adaptive granularity hint)
        from .habit_tracker import should_granularize
        if should_granularize(state.habit):
            lines.append("[编排智能·节奏适配] 学生近期学习连续性偏低，"
                         "建议拆分讲解、增加互动频率")

        return "\n".join(lines) if lines else ""
    except Exception:
        return ""
