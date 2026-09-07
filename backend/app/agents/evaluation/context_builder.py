"""Context builder for the evaluation directive: "[评估智能·...]" soft block.

Reads recent traces + strategy effectiveness + recurring failure patterns and
renders an advisory directive for the Supervisor to inject (step 3f). Like the
M5 knowledge directive and M6 memory directive, this is PURE-READ and advisory:
it tells the LLM "past teaching on this concept failed in this way, consider
adjusting" without forcing a specific action.

Returns "" when nothing actionable is found, so the turn is unchanged.
"""
from __future__ import annotations

from typing import Any

from . import store, strategy_analyzer, trace_analyzer
from .schema import EVAL_DIRECTIVE_WINDOW, FailureType


def build_evaluation_directive(*, student_id: str, concept: str = "",
                               subject: str = "") -> str:
    """Build the [评估智能·...] advisory block for this turn.

    Inspects recent traces for: (1) recurring failure patterns on this concept,
    (2) known weak strategies, (3) the system's overall effectiveness trend.
    Returns "" when nothing actionable. Never raises.
    """
    try:
        traces = store.read_traces(student_id, limit=EVAL_DIRECTIVE_WINDOW)
        if not traces:
            return ""

        lines: list[str] = []

        # 1. recurring failure pattern for this concept/subject
        pattern = trace_analyzer.recurring_failure_pattern(
            traces, concept=concept, subject=subject)
        if pattern:
            ft = FailureType.from_value(pattern.get("failure_type"))
            label = _FAILURE_LABELS.get(ft, ft.value)
            lines.append(
                f"[评估智能·历史失败模式] 近期「{pattern.get('concept', concept)}」"
                f"重复出现「{label}」问题（{pattern['count']}次）。")
            rec = pattern.get("recommendation", "")
            if rec:
                lines.append(f"[评估智能·改进建议] {rec}")

        # 2. least effective strategy (if enough data)
        if subject or concept:
            worst = strategy_analyzer.worst_strategies(student_id, limit=1)
            if worst:
                w = worst[0]
                lines.append(
                    f"[评估智能·策略反思] 统计显示「{w.strategy}」模式效果欠佳"
                    f"（平均提升{w.avg_gain:+.2f}，{w.sample_size}轮），"
                    "若本轮教学效果不佳，考虑切换教学方式。")

        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


# human-readable labels for failure types (for the directive text)
_FAILURE_LABELS = {
    FailureType.TEACHING_DEPTH_MISMATCH: "讲解深度不匹配",
    FailureType.PREREQUISITE_MISSING: "前置知识缺失",
    FailureType.RETRIEVAL_MISS: "知识检索未命中",
    FailureType.ASSESSMENT_TOO_HARD: "测评难度过高",
    FailureType.STRATEGY_MISMATCH: "教学策略不适配",
    FailureType.NO_ASSESSMENT: "缺少收尾检测",
    FailureType.NONE: "",
}
