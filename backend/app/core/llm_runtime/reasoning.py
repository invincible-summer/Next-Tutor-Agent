"""Stage-aware reasoning policy independent of Provider parameters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..config import settings
from .capabilities import ProviderCapabilities, current_capabilities


class ReasoningMode(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ReasoningPolicy:
    stage: str
    requested_mode: ReasoningMode
    applied_mode: str
    max_output_tokens: int
    reasoning_token_target: int
    answer_token_reserve: int
    disable_thinking: bool
    reasoning_effort: str = ""
    reasoning_budget_tokens: int = 0
    controls_applied: bool = False
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["requested_mode"] = self.requested_mode.value
        return d


def resolve_reasoning_policy(stage: str, *, has_tools: bool,
                             complex_task: bool = False,
                             capabilities: ProviderCapabilities | None = None,
                             runtime_mode: str | None = None) -> ReasoningPolicy:
    """Resolve intent-level reasoning into portable Provider controls.

    ``max_tokens`` is still a shared completion envelope on many compatible
    Providers (reasoning + visible answer).  We therefore reserve answer room
    in policy, apply a native reasoning budget only when explicitly supported,
    and rely on the executor's answer-channel recovery pass as the portable
    fallback when a model consumes the shared envelope in hidden reasoning.
    """
    caps = capabilities or current_capabilities()
    runtime = (runtime_mode or settings.llm_runtime_mode).strip().lower()
    if runtime not in {"off", "shadow", "adapter"}:
        runtime = "shadow"

    # Tool steps keep provider-default thinking (LOW) when the deployment
    # allows it (settings.executor_tool_thinking): with ample output budget
    # the reasoning improves tool-use quality and feeds the real_summary
    # reflection; the executor's budget guard and incomplete-answer recovery
    # remain the starvation safety nets.  NONE (hard disable) only when the
    # operator opts out.
    tool_thinking = bool(getattr(settings, "executor_tool_thinking", True))
    requested = (ReasoningMode.NONE if has_tools and not tool_thinking
                 else ReasoningMode.LOW if has_tools
                 else ReasoningMode.MEDIUM if complex_task
                 else ReasoningMode.LOW)
    provider_max = max(512, int(caps.max_output_tokens or settings.llm_max_output_tokens))
    configured_max = max(512, int(settings.llm_max_output_tokens))
    tool_cap = max(512, int(getattr(settings, "executor_tool_max_output_tokens", 6000)))
    max_output = min(configured_max, provider_max, tool_cap) if has_tools else min(configured_max, provider_max)

    desired_target = {ReasoningMode.NONE: 0, ReasoningMode.LOW: 800,
                      ReasoningMode.MEDIUM: 2200, ReasoningMode.HIGH: 3500}[requested]
    desired_reserve = 1800 if has_tools else 3500 if complex_task else 2800
    reserve = min(desired_reserve, max_output)
    target = min(desired_target, max(0, max_output - reserve))

    controls = runtime == "adapter"
    disable = controls and requested == ReasoningMode.NONE and caps.supports_disable_thinking
    effort = (requested.value if controls and requested != ReasoningMode.NONE
              and caps.supports_reasoning_effort else "")
    budget_tokens = (target if controls and requested != ReasoningMode.NONE
                     and caps.supports_reasoning_budget else 0)

    fallback = ""
    applied = requested.value
    if runtime == "off":
        applied = "provider_default"
    elif runtime == "shadow":
        applied = f"shadow:{requested.value}"
    elif requested == ReasoningMode.NONE and not caps.supports_disable_thinking:
        applied = "provider_default"
        fallback = "disable_thinking_unsupported"
    elif requested != ReasoningMode.NONE and not caps.supports_reasoning:
        applied = "direct_answer"
        fallback = "reasoning_unsupported"
        effort = ""
        budget_tokens = 0
    elif effort:
        applied = f"effort:{effort}"
    elif budget_tokens:
        applied = f"budget:{budget_tokens}"

    return ReasoningPolicy(
        stage=stage,
        requested_mode=requested,
        applied_mode=applied,
        max_output_tokens=max_output,
        reasoning_token_target=target,
        answer_token_reserve=reserve,
        disable_thinking=disable,
        reasoning_effort=effort,
        reasoning_budget_tokens=budget_tokens,
        controls_applied=controls,
        fallback_reason=fallback,
    )
