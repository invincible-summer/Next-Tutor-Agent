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

    def summary(self) -> dict[str, int]:
        return {
            "generation_calls": self.calls,
            "illustration_repairs": self.repairs,
            "completion_tokens": self.completion_tokens,
            "generation_elapsed_ms": round((time.monotonic() - self.started_at) * 1000),
        }


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
