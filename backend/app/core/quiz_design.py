"""Two-pass quiz generation, round 1: the question blueprint.

Why this exists: single-pass generation (design + write + explain in one shot)
reliably drifts toward the safest questions — definition recall and one-step
formula application — because the model spends no explicit effort on *what
makes a good question* before committing to stems. Splitting design into its
own LLM call (the blueprint) forces the model to first enumerate the angles a
concept can be tested from (essence / mechanism / transfer / synthesis /
traps / counterexamples), assign each question a target Bloom level and a
trap design, and only then (round 2, in the quiz tools) write the actual
questions against that blueprint.

Contract:
  - Shared by tools/quiz.py (multi-question) and agents/assessment/
    generator.py (single constraint-driven question, count=1).
  - Fail-open, same philosophy as core/quiz_verify: any error or unparseable
    blueprint returns ("", "fallback") and generation proceeds single-pass.
  - Mode is controlled by ``QUIZ_DESIGN_MODE``: ``two_pass`` (default) runs
    the blueprint round, ``single`` restores the legacy one-shot behavior.
  - The blueprint prompt text lives in prompts/registry.py
    (``quiz_blueprint`` + anchor variants) so trace prompt-version
    provenance covers it; this module is glue: render, call, validate, render
    the injection block. Never raises.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import settings
from .llm_async import AsyncLLMClient
from ..prompts.registry import get as _prompt

_DIFFICULTY_ZH = {"easy": "基础", "medium": "中等", "hard": "挑战"}

# Injection header prepended to the rendered blueprint when it is spliced
# into the round-2 generation prompt.
_INJECT_HEAD = (
    "[命题蓝图 · 第一轮设计结果，必须逐题落实]\n"
    "逐题按蓝图的角度/认知层级/陷阱/构想写成正式题目，不得退化为定义复述或"
    "一步套公式题；蓝图与其它要求冲突时，以蓝图的考查角度与认知层级为准。"
)


def _build_prompt(*, topic: str, grade: str, difficulty: str, count: int,
                  focus: str, avoid_stems: list[str]) -> str:
    from ..agents.teaching_engine.stage_profile import (
        difficulty_anchor, is_auto)
    if is_auto(grade):
        anchor_block = _prompt("quiz_blueprint_anchor_auto").text
        grade_label = "（未指定学段，按知识点本身自适应）"
    else:
        anchor_block = _prompt("quiz_blueprint_anchor").text.format(
            anchor=difficulty_anchor(grade))
        grade_label = f"「{grade}」"
    focus_block = ""
    if focus:
        focus_block = (f"\n本轮讲解的侧重点是「{focus}」：蓝图必须围绕它设计，"
                       "不要泛化成知识点的常识考法。")
    prompt = _prompt("quiz_blueprint").text.format(
        topic=topic, grade=grade_label,
        difficulty_zh=_DIFFICULTY_ZH.get(difficulty, difficulty or "中等"),
        count=count, focus_block=focus_block, anchor_block=anchor_block)
    if avoid_stems:
        prompt += ("\n以下题目本会话已经出过，设计角度不得与它们重复：\n"
                   + "\n".join(f"  · {s}" for s in avoid_stems[:8]))
    return prompt


def _parse_blueprint(raw: str) -> list[dict[str, Any]]:
    """Extract and validate the blueprint item list; [] on any problem."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = m.group(0) if m else raw
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    items = data.get("blueprint") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and (item.get("idea") or item.get("angle")):
            out.append(item)
    return out


def _render(items: list[dict[str, Any]]) -> str:
    """Render validated blueprint items as the round-2 injection block."""
    lines = [_INJECT_HEAD]
    for i, item in enumerate(items, 1):
        parts = [f"角度={item.get('angle', '')}"]
        if item.get("bloom"):
            parts.append(f"认知层级={item['bloom']}")
        if item.get("q_type"):
            parts.append(f"题型={item['q_type']}")
        if item.get("trap"):
            parts.append(f"陷阱={item['trap']}")
        parts.append(f"构想={item.get('idea', '')}")
        lines.append(f"第 {i} 题：" + "｜".join(parts))
    return "\n".join(lines)


async def design_blueprint(llm: AsyncLLMClient, *, topic: str, grade: str,
                           difficulty: str, count: int, focus: str = "",
                           avoid_stems: list[str] | None = None
                           ) -> tuple[str, str]:
    """Run the blueprint design round.

    Returns ``(injection_block, status)`` where status is:
      - ``"two_pass"``: blueprint designed and validated; injection_block is
        ready to splice into the generation prompt;
      - ``"single"``:   QUIZ_DESIGN_MODE=single, blueprint round skipped;
      - ``"fallback"``: blueprint round attempted but failed/unparseable;
        generation should proceed single-pass.
    Never raises.
    """
    if settings.quiz_design_mode != "two_pass":
        return "", "single"
    try:
        prompt = _build_prompt(
            topic=topic, grade=grade, difficulty=difficulty, count=count,
            focus=focus, avoid_stems=[s for s in (avoid_stems or []) if s])
        full, _usage = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=1500, disable_thinking=True)
        items = _parse_blueprint(full)
        if not items:
            return "", "fallback"
        return _render(items), "two_pass"
    except Exception:
        return "", "fallback"
