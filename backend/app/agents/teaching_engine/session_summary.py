"""W3/D11: session summary + resume anchor (课堂小结).

The deterministic skeleton is ALWAYS available (zero LLM): what the student
DEMONSTRATED (accepted verdicts from quiz_history only — 只引用已接受证据),
what is still open (wrong/partial/pending), and a resume anchor. When no
assessment exists the coverage note is honest: 「已讲解，待验证」— never a
fabricated mastery claim.

The optional LLM polish (STRUCTURED_ASSESSMENT_MODE=active turns that actually
contained an assessment) receives ONLY the skeleton — never the transcript —
and may rewrite the two display lines; failure keeps the deterministic text.
Stored on ``session.learning_summary`` and injected as a one-line preamble on
resume after an idle gap (supervisor).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ...core.llm_async import AsyncLLMClient
from ...prompts.registry import get as _prompt

_MAX_DEMONSTRATED = 5
_MAX_OPEN = 3
# 恢复注入的空闲阈值：短间隔连续提问属于同一次学习，不打断（D11 触发是
# 「单次学习结束或暂离」）。
RESUME_IDLE_SECONDS = 30 * 60


def _iter_question_results(session) -> list[tuple[dict, dict]]:
    """(question, result) pairs from quiz_history, newest set first."""
    out: list[tuple[dict, dict]] = []
    try:
        history = getattr(session, "quiz_history", None) or []
        for qset in reversed(history):
            if not isinstance(qset, dict):
                continue
            for q in (qset.get("questions") or []):
                if isinstance(q, dict) and isinstance(q.get("result"), dict):
                    out.append((q, q["result"]))
    except Exception:
        return []
    return out


def _unanswered_count(session) -> int:
    try:
        history = getattr(session, "quiz_history", None) or []
        for qset in reversed(history):
            if not isinstance(qset, dict):
                continue
            qs = [q for q in (qset.get("questions") or [])
                  if isinstance(q, dict)]
            if qs:
                return sum(1 for q in qs if not q.get("result"))
    except Exception:
        return 0
    return 0


def build_session_summary(session, *,
                          now: float | None = None) -> dict[str, Any] | None:
    """Deterministic skeleton (zero LLM). None when the session has nothing
    to summarize (no concepts, no quizzes). The optional LLM polish is a
    separate async step (``polish_summary``) the caller awaits in active mode."""
    now = now if now is not None else time.time()
    card = getattr(session, "context_card", None)
    concepts: list[str] = []
    if isinstance(card, dict):
        concepts = [str(c)[:30] for c in (card.get("active_concepts") or [])
                    if str(c).strip()][:4]
    pairs = _iter_question_results(session)
    if not concepts and not pairs:
        return None
    demonstrated = [{"stem": str(q.get("stem") or "")[:60],
                     "verdict": str(r.get("verdict") or "")}
                    for q, r in pairs if str(r.get("verdict")) == "correct"]
    demonstrated = demonstrated[:_MAX_DEMONSTRATED]
    open_items = [{"stem": str(q.get("stem") or "")[:60],
                   "verdict": str(r.get("verdict") or "")}
                  for q, r in pairs
                  if str(r.get("verdict")) in ("wrong", "partial")][:_MAX_OPEN]
    pending = _unanswered_count(session)
    assessed = bool(pairs)
    summary: dict[str, Any] = {
        "schema": "session_summary.v1",
        "built_at": now,
        "concepts": concepts,
        "demonstrated": demonstrated,
        "open_items": open_items,
        "pending_questions": pending,
        "coverage_note": ("已有 %d 次作答判定" % len(pairs)) if assessed
        else "已讲解，待验证",
        "resume_anchor": {
            "concept": concepts[0] if concepts else "",
            "pending": pending,
            "hint": (f"还有 {pending} 道待答题目" if pending
                     else (open_items[0]["stem"] if open_items else "")),
        },
        "polished": False,
    }
    summary["summary_line"] = _summary_line(summary)
    summary["resume_line"] = _resume_line(summary)
    return summary


def _summary_line(summary: dict[str, Any]) -> str:
    concept = "、".join(summary["concepts"][:2]) or "本次学习"
    if summary["demonstrated"]:
        shown = summary["demonstrated"][0]["stem"]
        return f"{concept}：已能独立完成「{shown}」等 {len(summary['demonstrated'])} 项；{summary['coverage_note']}"
    return f"{concept}：{summary['coverage_note']}" + (
        f"；还有 {len(summary['open_items'])} 个待解决问题" if summary["open_items"] else "")


def _resume_line(summary: dict[str, Any]) -> str:
    anchor = summary["resume_anchor"]
    if anchor["pending"]:
        return f"先完成待答的 {anchor['pending']} 道题目，再继续{anchor['concept'] or '新内容'}。"
    if summary["open_items"]:
        return f"建议先重练：「{summary['open_items'][0]['stem']}」。"
    if anchor["concept"]:
        return f"从「{anchor['concept']}」继续，或告诉我今天想学什么。"
    return "告诉我今天想学什么。"


async def polish_summary(summary: dict[str, Any],
                         llm: AsyncLLMClient) -> tuple[str, str] | None:
    """Async polish entry (the skeleton build stays sync)."""
    try:
        prompt = _prompt("session_summary").text.format(
            concepts="、".join(summary["concepts"]) or "无",
            demonstrated=json.dumps(summary["demonstrated"], ensure_ascii=False),
            open_items=json.dumps(summary["open_items"], ensure_ascii=False),
            coverage_note=summary["coverage_note"],
            pending=summary["pending_questions"],
        )
        full, _usage = await llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=300, disable_thinking=True)
        m = re.search(r"\{.*\}", full or "", re.DOTALL)
        if not m:
            return None
        obj = json.loads(m.group(0))
        if not isinstance(obj, dict):
            return None
        line = str(obj.get("summary_line") or "").strip()[:120]
        resume = str(obj.get("resume_line") or "").strip()[:80]
        if not line or not resume:
            return None
        return line, resume
    except Exception:
        return None


def resume_preamble_line(session, *, now: float | None = None) -> str:
    """The one-line resume note for the preamble, or "" when the summary is
    missing or the idle gap says the student never left (D11 triggers on
    单次学习结束/暂离, not on consecutive turns)."""
    try:
        summary = getattr(session, "learning_summary", None)
        if not isinstance(summary, dict):
            return ""
        built_at = float(summary.get("built_at") or 0.0)
        if built_at <= 0:
            return ""
        now = now if now is not None else time.time()
        if now - built_at < RESUME_IDLE_SECONDS:
            return ""
        line = str(summary.get("summary_line") or "").strip()
        resume = str(summary.get("resume_line") or "").strip()
        if not line and not resume:
            return ""
        parts = ["[上次学习小结]"]
        if line:
            parts.append(line)
        if resume:
            parts.append("恢复点：" + resume)
        return " ".join(parts)[:400]
    except Exception:
        return ""
