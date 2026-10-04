from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerationBudget:
    max_calls: int = 7
    deadline: float = field(default_factory=lambda: time.monotonic() + 180)
    calls: int = 0
    repairs: int = 0
    max_repairs: int = 1
    started_at: float = field(default_factory=time.monotonic)
    completion_tokens: int = 0
    protocol_corrections: int = 0
    max_protocol_corrections: int = 2

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    @property
    def available(self) -> bool:
        return self.calls < self.max_calls and self.remaining_seconds > 0

    def take_repair(self) -> bool:
        if self.repairs >= self.max_repairs or not self.available:
            return False
        self.repairs += 1
        return True

    def can_call(self, *, reserve_calls: int = 0) -> bool:
        """Reserve the calls needed to finish and independently review a draft."""
        return self.available and self.max_calls - self.calls > reserve_calls

    def take_protocol_correction(self, *, reserve_calls: int = 0) -> bool:
        if self.protocol_corrections >= self.max_protocol_corrections or not self.can_call(reserve_calls=reserve_calls):
            return False
        self.protocol_corrections += 1
        return True

    def summary(self) -> dict[str, int]:
        return {
            "generation_calls": self.calls,
            "illustration_repairs": self.repairs,
            "protocol_corrections": self.protocol_corrections,
            "completion_tokens": self.completion_tokens,
            "generation_elapsed_ms": round((time.monotonic() - self.started_at) * 1000),
        }


def new_quiz_budget() -> GenerationBudget:
    """Create the shared bounded budget used by every quiz entry point.

    Keeping this in one constructor prevents chat tools, CAT and repair paths
    from silently falling back to the old 180-second/7-call defaults.
    """
    from .config import settings
    return GenerationBudget(
        max_calls=settings.assessment_generation_max_calls,
        max_repairs=max(1, settings.assessment_generation_max_attempts),
        deadline=time.monotonic() + settings.assessment_generation_deadline_seconds,
    )


class BudgetedLLM:
    def __init__(
        self,
        llm: Any,
        budget: GenerationBudget,
        *,
        call_timeout: float | None = None,
        phase_deadline: float | None = None,
    ):
        if call_timeout is not None and (
            not math.isfinite(call_timeout) or call_timeout <= 0
        ):
            raise ValueError("call_timeout must be finite and positive")
        self.llm = llm
        self.budget = budget
        self.call_timeout = call_timeout
        self.phase_deadline = phase_deadline

    def unwrap_provider(self):
        """Start an independent phase budget without charging the text budget.

        Also callable as ``BudgetedLLM.unwrap_provider(provider)`` for callers
        that accept either a provider or a wrapper. The enclosing phase owns
        its deadline and cancellation; no provider retry bypass is introduced.
        """
        client = self
        while isinstance(client, BudgetedLLM):
            client = client.llm
        return client

    async def complete(self, **kwargs):
        now = time.monotonic()
        deadline = self.budget.deadline
        if self.phase_deadline is not None:
            deadline = min(deadline, self.phase_deadline)
        if self.call_timeout is not None:
            deadline = min(deadline, now + self.call_timeout)
        if not self.budget.available or deadline <= now:
            raise TimeoutError("quiz_generation_budget_exhausted")
        self.budget.calls += 1
        async with asyncio.timeout(deadline - now):
            result = await self.llm.complete(**kwargs)
        usage = result[1] if len(result) > 1 else None
        if isinstance(usage, dict):
            try:
                self.budget.completion_tokens += max(
                    0, int(usage.get("completion_tokens") or 0)
                )
            except (TypeError, ValueError, OverflowError):
                pass
        return result
