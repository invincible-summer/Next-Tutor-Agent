"""Procedural memory: teaching-strategy effectiveness (education-specific).

Records which teaching approaches produced good outcomes for this student so
the Teaching Engine can prefer them. success_rate is a sliding window over
recent trials; low-trial strategies (trials < MIN_TRIALS_FOR_INJECTION) are NOT
injected to avoid small-sample noise.

The strategy name comes from M3's TeachingMode (INTRODUCTION/EXPLANATION/...)
plus any teaching-style signal (e.g. "visual_analogy"). The outcome comes from
M3's TeachingOutcome (CORRECT/WRONG/ENGAGED/PARTIAL).
"""
from __future__ import annotations

import time
from typing import Any

from . import store
from .schema import (MIN_TRIALS_FOR_INJECTION, PROCEDURAL_WINDOW,
                     ProceduralMemory, MemoryScope)


# outcomes that count as "success" (strategy worked)
_SUCCESS_OUTCOMES = frozenset({"correct", "engaged"})


def _record_outcome(student_id: str, strategy: str, subject: str,
                    success: bool) -> ProceduralMemory | None:
    """Record one strategy trial and update the sliding-window success_rate.

    Returns the updated ProceduralMemory, or None on failure. Creates the entry
    if it doesn't exist yet.
    """
    try:
        if not strategy:
            return None
        items = store.load_procedural(student_id)
        # find existing entry by (strategy, subject)
        target = None
        for p in items:
            if p.strategy == strategy and p.subject == subject:
                target = p
                break
        if target is None:
            target = ProceduralMemory(
                strategy=strategy, subject=subject, success_rate=0.0, trials=0,
                scope=MemoryScope.SUBJECT if subject else MemoryScope.GLOBAL)
            items.append(target)

        # sliding window: keep last PROCEDURAL_WINDOW outcomes as a rolling rate
        # exponential moving average with faster convergence for small samples
        # alpha shrinks with trials so early observations count more, converging
        # to the true rate as evidence accumulates.
        target.trials += 1
        alpha = 2.0 / (target.trials + 2.0)  # trials already incremented above
        observed = 1.0 if success else 0.0
        target.success_rate = (1 - alpha) * target.success_rate + alpha * observed
        target.last_used_ts = time.time()

        store.save_procedural(student_id, items)
        return target
    except Exception:
        return None


def record_outcome(student_id: str, strategy: str, subject: str,
                   outcome: str) -> ProceduralMemory | None:
    """Convenience: record from an M3 TeachingOutcome string."""
    success = outcome.lower() in _SUCCESS_OUTCOMES
    return _record_outcome(student_id, strategy, subject, success)


def all_procedural(student_id: str) -> list[ProceduralMemory]:
    """Return all procedural memories."""
    try:
        return store.load_procedural(student_id)
    except Exception:
        return []


def injectable_strategies(student_id: str, subject: str = "",
                          limit: int = 3) -> list[ProceduralMemory]:
    """Strategies effective enough to recommend during retrieval.

    Filters: trials >= MIN_TRIALS_FOR_INJECTION (avoid noise), success_rate
    above 0.5. Prefers subject-scoped, then by success_rate.
    """
    try:
        items = store.load_procedural(student_id)
        good = [p for p in items
                if p.trials >= MIN_TRIALS_FOR_INJECTION and p.success_rate > 0.5]
       # prefer subject-scoped, then by success_rate descending
        good.sort(key=lambda p: (
            not (subject and p.subject and subject.lower() in p.subject.lower()),
            -p.success_rate))
        return good[:limit]
    except Exception:
        return []
