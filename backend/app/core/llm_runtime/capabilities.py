"""Declarative Provider capability profile without secrets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import settings


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    model: str
    context_window: int
    max_output_tokens: int
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_native_tool_messages: bool = True
    supports_reasoning: bool = True
    supports_reasoning_effort: bool = False
    supports_reasoning_budget: bool = False
    supports_disable_thinking: bool = True
    reports_reasoning_tokens: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def current_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_id=settings.llm_provider,
        model=settings.llm_model,
        context_window=settings.llm_context_window,
        max_output_tokens=settings.llm_max_output_tokens,
        supports_native_tool_messages=settings.llm_supports_native_tool_messages,
        supports_reasoning=settings.llm_supports_reasoning,
        supports_reasoning_effort=settings.llm_supports_reasoning_effort,
        supports_reasoning_budget=settings.llm_supports_reasoning_budget,
        supports_disable_thinking=settings.llm_supports_disable_thinking,
        reports_reasoning_tokens=settings.llm_reports_reasoning_tokens,
    )
