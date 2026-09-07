"""Subtask recommender (M9): LLM-suggested subtasks for one week task.

Given the goal, the week's focus and one action-level task ("学完浮力前两节"),
propose 2-4 concrete, executable subtasks ("做 10 道浮力计算题"). The result
is persisted by the caller as source="auto" subtasks (regeneration rebuilds
them; user-added subtasks are never touched).

Gate: 2-4 subtasks, non-empty titles (≤80 chars), minutes clamped to 5-60.
No deterministic fallback — the caller surfaces "suggestion unavailable" to
the UI (a suggestion is advisory, not required for the plan to function).
IMPORT-CLEAN: pure functions over plain data.
"""
from __future__ import annotations

import json
import re
from typing import Any

_MIN_SUBTASKS = 2
_MAX_SUBTASKS = 4


def build_subtask_prompt(goal_title: str, week_focus: str, task_title: str,
                         concept_names: list[str],
                         grade: str = "") -> list[dict[str, str]]:
    """Build the chat messages for the subtask-recommendation LLM call."""
    system = ("你是一名学习教练，擅长把一个学习任务拆成具体可执行的步骤。"
              "只输出 JSON，不要输出任何其他内容。")
    user = f"""{f"学生的长期目标：{goal_title}。" if goal_title else ""}{f"学段：{grade}。" if grade else ""}
本周主题：{week_focus or "（未标注）"}
学习任务：{task_title}
{f"涉及概念：{'、'.join(concept_names[:6])}" if concept_names else ""}

请把这个任务拆成 {_MIN_SUBTASKS}-{_MAX_SUBTASKS} 个具体可执行的子任务（每一步都是可立即动手的动作，带可检验的产出，如「做 10 道浮力计算题并订正」而不是「复习浮力」）。每个子任务估计 5-60 分钟。

输出 JSON 格式（不要输出其他内容）：
```json
{{"subtasks": [{{"title": "...", "estimate_minutes": 20}}]}}
```"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _extract_json(text: str) -> dict[str, Any] | None:
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


def parse_subtask_response(text: str) -> list[dict[str, Any]] | None:
    """Parse + validate the LLM subtask suggestions. None on any gate failure.

    Gate: 2-4 subtasks, non-empty titles (≤80 chars), minutes clamped 5-60.
    Never raises.
    """
    try:
        obj = _extract_json(text)
        if not obj or not isinstance(obj.get("subtasks"), list):
            return None
        raw = [s for s in obj["subtasks"] if isinstance(s, dict)]
        if not (_MIN_SUBTASKS <= len(raw) <= _MAX_SUBTASKS):
            return None
        out: list[dict[str, Any]] = []
        for s in raw:
            title = str(s.get("title", "")).strip()
            if not title:
                return None
            try:
                minutes = int(s.get("estimate_minutes", 15))
            except (TypeError, ValueError):
                minutes = 15
            out.append({"title": title[:80],
                        "estimate_minutes": max(5, min(60, minutes))})
        return out
    except Exception:
        return None
