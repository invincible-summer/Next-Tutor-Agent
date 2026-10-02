"""Context builder for the UX directive: "[交互智能·...]" soft block.

Reads the UX profile + recent feedback + motivation and renders an advisory
directive for the Supervisor to inject (step 3g). Like the M5 knowledge
directive, M6 memory directive, and M7 evaluation directive, this is PURE-READ
and advisory: it tells the LLM "express it this way for THIS student" without
forcing specific content.

Returns "" when nothing actionable, so the turn is unchanged.
"""
from __future__ import annotations

from . import store
from .explanation_adapter import ResponseDirective, build_directive
from .learner_profile import get_profile, seed_style_from_grade
from .schema import UXProfile


def build_ux_directive(*, student_id: str, concept: str = "", subject: str = "",
                       intent: str = "explain", grade: str = "") -> str:
    """Build the [交互智能·...] advisory block for this turn.

    Composes style guidance + feedback reaction + motivation + (read-only) M2
    academic style. Returns "" when nothing actionable. Never raises.

    Note: this may mutate the profile's motivation surfacing bookkeeping (so a
    milestone nudge is only emitted once). The manager persists the profile.
    """
    try:
        profile = get_profile(student_id)
        seed_style_from_grade(profile, grade)
        directive: ResponseDirective = build_directive(
            student_id=student_id, profile=profile, concept=concept,
            subject=subject, intent=intent, grade=grade)
        if directive.is_empty():
            return ""
        # persist the (possibly updated) motivation surfacing state
        store.save_profile(student_id, profile)
        return _render(directive)
    except Exception:
        return ""


def _render(directive: ResponseDirective) -> str:
    lines: list[str] = ["[交互智能·表达适配]"]
    for ln in directive.style_lines:
        lines.append(f"- {ln}")
    if directive.academic_style_note:
        lines.append(f"- {directive.academic_style_note}")
    if directive.feedback_adjustment:
        lines.append(f"[交互智能·反馈响应] {directive.feedback_adjustment}")
    if directive.intent_guard:
        lines.append(f"[交互智能·场景适配] {directive.intent_guard}")
    if directive.motivation_line:
        lines.append(f"[交互智能·学习激励] {directive.motivation_line}")
    return "\n".join(lines)


def greeting(student_id: str, *, grade: str = "", lang: str = "zh") -> str:
    """A personalized opener for a new/empty session: resume hint + streak.

    Used by the /ux/greeting API and the frontend empty state. Reads the L1
    activity aggregator (read-only) for "what was last studied" (M3 teaching
    log with graph name resolution, learning-ledger fallback) and the streak.
    Returns a plain greeting string; "" only on total failure (caller falls
    back)."""
    try:
        from .. import activity_aggregator
        from .motivation_engine import current_streak
        streak = current_streak(student_id)
        last_concept = activity_aggregator.last_learned_concept(student_id)
        if lang == "en":
            if last_concept and streak >= 2:
                return (f"Welcome back. You've been learning for {streak} days "
                        f"in a row. Want to pick up where we left off — {last_concept}?")
            if streak >= 2:
                return f"Welcome back. That's {streak} days in a row — keep it going!"
            if last_concept:
                return f"Welcome back. Shall we continue with {last_concept}?"
            return "Hi. What would you like to learn today?"
        # zh (default)
        if last_concept and streak >= 2:
            return (f"欢迎回来。你已经连续学习 {streak} 天了，"
                    f"要继续上次的内容吗——{last_concept}？")
        if streak >= 2:
            return f"欢迎回来。已经连续学习 {streak} 天，状态很稳，继续保持。"
        if last_concept:
            return f"欢迎回来。要继续上次的内容吗——{last_concept}？"
        return "你好，今天想学点什么？"
    except Exception:
        return ""
