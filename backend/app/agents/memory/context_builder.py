"""MemoryContextBuilder: render retrieved memory as a [记忆智能·...] directive.

This is the ONLY place that decides what past-experience hints a turn sees,
which keeps the coupling surface to exactly one guarded call from the Supervisor
(step 3e). It takes the MemoryHit list from retrieval.py and renders it as
advisory text (the LLM stays in charge; these are soft hints, not commands).

Rendering rules (each line is advisory):
  - [记忆智能·过往经验]     past episodic events relevant to this concept
  - [记忆智能·有效策略]     teaching strategies that worked before
  - [记忆智能·长期事实]     stable semantic facts about the student
Below the emit threshold or no hits -> returns "" (M6 invisible, never noise).
"""
from __future__ import annotations

from typing import Any

from .retrieval import MemoryHit


# minimum fused score to emit a hit
_EMIT_THRESHOLD = 0.05
# max lines per directive block
_MAX_LINES = 5


def build_memory_context(hits: list[MemoryHit]) -> dict[str, list[str]]:
    """Group hits by kind for rendering. Returns {episodic: [...], ...}."""
    ctx: dict[str, list[str]] = {"episodic": [], "procedural": [], "semantic": []}
    for hit in hits:
        if hit.score < _EMIT_THRESHOLD:
            continue
        if hit.kind in ctx:
            ctx[hit.kind].append(hit.text)
    return ctx


def render_memory_directive(ctx: dict[str, list[str]]) -> str:
    """Render the grouped context into a [记忆智能·...] block.

    Returns "" when there is nothing actionable so the caller skips it.
    """
    lines: list[str] = []
    episodic_texts = ctx.get("episodic", [])[:2]
    procedural_texts = ctx.get("procedural", [])[:2]
    semantic_texts = ctx.get("semantic", [])[:2]

    if episodic_texts:
        lines.append("[记忆智能·过往经验] 该生过往：" + "；".join(episodic_texts))
    if procedural_texts:
        lines.append("[记忆智能·有效策略] 此前有效的教学方式：" +
                     "；".join(procedural_texts))
    if semantic_texts:
        lines.append("[记忆智能·长期事实] 该生稳定特征：" +
                     "；".join(semantic_texts))

    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
    return "\n".join(lines) if lines else ""


def build_and_render(hits: list[MemoryHit]) -> str:
    """Convenience: build context from hits then render. Returns "" if empty."""
    try:
        ctx = build_memory_context(hits)
        return render_memory_directive(ctx)
    except Exception:
        return ""
