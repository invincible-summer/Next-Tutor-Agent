"""A shared bounded budget across blueprint/generation/audit/CAT retries."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerationBudget:
    max_calls: int = 7
    deadline: float = field(default_factory=lambda: time.monotonic() + 180)
    calls: int = 0
    repairs: int = 0
    started_at: float = field(default_factory=time.monotonic)
    completion_tokens: int = 0

    def summary(self) -> dict[str, int]:
        return {"generation_calls": self.calls, "illustration_repairs": self.repairs,
                "completion_tokens": self.completion_tokens,
                "generation_elapsed_ms": round((time.monotonic() - self.started_at) * 1000)}

    @property
    def available(self) -> bool:
        return self.calls < self.max_calls and time.monotonic() < self.deadline

    def take_repair(self) -> bool:
        if self.repairs >= 1 or not self.available:
            return False
        self.repairs += 1
        return True


class BudgetedLLM:
    def __init__(self, llm: Any, budget: GenerationBudget):
        self.llm = llm
        self.budget = budget

    async def complete(self, **kwargs):
        if not self.budget.available:
            raise TimeoutError("quiz_generation_budget_exhausted")
        self.budget.calls += 1
        async with asyncio.timeout(max(0.01, self.budget.deadline - time.monotonic())):
            result = await self.llm.complete(**kwargs)
            usage = result[1] if len(result) > 1 else None
            if isinstance(usage, dict):
                self.budget.completion_tokens += int(usage.get("completion_tokens") or 0)
            return result
