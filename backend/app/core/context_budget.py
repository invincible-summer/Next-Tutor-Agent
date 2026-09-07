"""Per-call context and output budget accounting."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import settings
from .context import estimate_tokens


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate the whole protocol message, not only its visible content.

    Native tool-call arguments and ``tool_call_id`` are part of the Provider
    prompt and must count toward the window.  A small fixed envelope covers
    role/name framing that the rough CJK/Latin estimator cannot see directly.
    """
    projected = {k: message[k] for k in (
        "role", "content", "name", "tool_call_id", "tool_calls"
    ) if k in message and message[k] is not None}
    return estimate_tokens(_compact_json(projected)) + 4


@dataclass(frozen=True)
class ContextBudgetSnapshot:
    stage: str
    context_window: int
    requested_output_tokens: int
    max_output_tokens: int
    available_output_tokens: int
    output_budget_reduced: bool
    safety_margin: int
    estimated_input_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    usable_input_tokens: int
    soft_trigger_tokens: int
    hard_trigger_tokens: int
    pressure: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def build_budget_snapshot(messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]] | None,
                          *, stage: str,
                          max_output_tokens: int) -> ContextBudgetSnapshot:
    message_tokens = sum(estimate_message_tokens(m) for m in messages)
    tool_schema_tokens = estimate_tokens(_compact_json(tools or []))
    estimated = message_tokens + tool_schema_tokens
    requested = max(1, int(max_output_tokens))
    available_output = max(
        1,
        settings.llm_context_window - estimated - settings.llm_context_safety_margin,
    )
    effective_output = min(requested, available_output)
    usable = max(1, settings.llm_context_window - effective_output
                 - settings.llm_context_safety_margin)
    soft = max(1, int(usable * settings.context_soft_trigger_ratio))
    hard = max(soft + 1, int(usable * settings.context_hard_trigger_ratio))
    pressure = "hard" if estimated >= hard else "soft" if estimated >= soft else "normal"
    return ContextBudgetSnapshot(
        stage=stage,
        context_window=settings.llm_context_window,
        requested_output_tokens=requested,
        max_output_tokens=effective_output,
        available_output_tokens=available_output,
        output_budget_reduced=effective_output < requested,
        safety_margin=settings.llm_context_safety_margin,
        estimated_input_tokens=estimated,
        message_tokens=message_tokens,
        tool_schema_tokens=tool_schema_tokens,
        usable_input_tokens=usable,
        soft_trigger_tokens=soft,
        hard_trigger_tokens=hard,
        pressure=pressure,
    )
