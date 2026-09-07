"""Strategy analyzer: aggregate teaching-mode effectiveness across turns.

This is M7's cross-turn analysis engine. Given the accumulated TurnTraces, it
groups them by (mode, subject) and computes avg learning_gain + avg success
rate, producing a StrategyEffectiveness ranking -- the "which strategy works
best" table from the M7 spec.

Boundary with M6 (avoid double truth source): M6 procedural tracks per-student
strategy success_rate (does this work for THIS student). M7 strategy_analyzer
aggregates avg learning_gain across ALL turns to compare strategies against
each other at the system level. It reads M7's OWN traces (which already encode
mode + outcome + gain); it does NOT re-read M6 procedural raw data -- the
strategy_analyzer owns the aggregation layer, M6 owns the per-student layer.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import store
from .schema import (MIN_TRACES_FOR_EFFECTIVENESS, StrategyEffectiveness,
                     TurnTrace)
from .learning_gain import classify_effectiveness


# outcomes that count as "success" (strategy produced a good result)
_SUCCESS_OUTCOMES = frozenset({"correct", "engaged", "\u5bf9"})


def analyze_traces(traces: list[TurnTrace]) -> list[StrategyEffectiveness]:
    """Aggregate traces into per-(mode, subject) StrategyEffectiveness records.

    Pure function of the traces list; deterministic. Returns records sorted by
    avg_gain descending. Records with fewer than MIN_TRACES_FOR_EFFECTIVENESS
    samples are still returned (sample_size exposes the count) but flagged via
    the cap so callers can filter noise.
    """
    if not traces:
        return []
    # group by (mode, subject)
    buckets: dict[tuple[str, str], list[TurnTrace]] = defaultdict(list)
    for t in traces:
        if not t.mode:
            continue
        buckets[(t.mode, t.subject or "")].append(t)

    results: list[StrategyEffectiveness] = []
    for (mode, subject), group in buckets.items():
        n = len(group)
        gains = [t.learning_gain for t in group if t.learning_gain is not None]
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        successes = sum(1 for t in group if t.outcome.lower() in _SUCCESS_OUTCOMES)
        avg_success = successes / n if n else 0.0
        results.append(StrategyEffectiveness(
            strategy=mode, subject=subject,
            avg_gain=round(avg_gain, 4), avg_success_rate=round(avg_success, 4),
            sample_size=n,
        ))
    results.sort(key=lambda s: s.avg_gain, reverse=True)
    return results


def refresh_effectiveness(student_id: str) -> list[StrategyEffectiveness]:
    """Recompute strategy effectiveness from the trace log and persist.

    Called periodically by the manager after new traces are appended. Reads all
    traces, aggregates, and upserts each record. Returns the updated list.
    Never raises.
    """
    try:
        traces = store.read_traces(student_id)
        records = analyze_traces(traces)
        for r in records:
            store.upsert_strategy(student_id, r)
        return records
    except Exception:
        return []


def best_strategies(student_id: str, subject: str = "",
                    limit: int = 3) -> list[StrategyEffectiveness]:
    """Return the most effective strategies, filtered to trustworthy samples.

    Filters: sample_size >= MIN_TRACES_FOR_EFFECTIVENESS (avoid noise).
    Prefers subject-scoped when a subject is given, then by avg_gain.
    """
    try:
        items = store.load_strategies(student_id)
        good = [s for s in items
                if s.sample_size >= MIN_TRACES_FOR_EFFECTIVENESS and s.avg_gain > 0]
        good.sort(key=lambda s: (
            not (subject and s.subject and subject.lower() in s.subject.lower()),
            -s.avg_gain))
        return good[:limit]
    except Exception:
        return []


def worst_strategies(student_id: str, limit: int = 3) -> list[StrategyEffectiveness]:
    """Return the least effective strategies (for the advisor to target)."""
    try:
        items = store.load_strategies(student_id)
        bad = [s for s in items if s.sample_size >= MIN_TRACES_FOR_EFFECTIVENESS]
        bad.sort(key=lambda s: s.avg_gain)
        return bad[:limit]
    except Exception:
        return []


def summarize(traces: list[TurnTrace]) -> dict[str, Any]:
    """Build a compact stats dict for the MetricSnapshot / API.

    Returns {total, by_mode: {mode: {count, avg_gain, success_rate}},
    failure_distribution: {type: count}, avg_tokens}. Pure function.
    """
    if not traces:
        return {"total": 0, "by_mode": {}, "failure_distribution": {},
                "avg_tokens": 0.0}
    by_mode: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "gains": [], "successes": 0, "tokens": 0})
    failures: dict[str, int] = defaultdict(int)
    total_tokens = 0
    for t in traces:
        m = t.mode or "unknown"
        by_mode[m]["count"] += 1
        if t.learning_gain is not None:
            by_mode[m]["gains"].append(t.learning_gain)
        if t.outcome.lower() in _SUCCESS_OUTCOMES:
            by_mode[m]["successes"] += 1
        by_mode[m]["tokens"] += t.tokens_used
        total_tokens += t.tokens_used
        ft = t.failure_type or "none"
        if ft != "none":
            failures[ft] += 1
    # flatten
    by_mode_out: dict[str, Any] = {}
    for mode, d in by_mode.items():
        n = d["count"]
        by_mode_out[mode] = {
            "count": n,
            "avg_gain": round(sum(d["gains"]) / len(d["gains"]), 4) if d["gains"] else 0.0,
            "success_rate": round(d["successes"] / n, 4) if n else 0.0,
            "avg_tokens": round(d["tokens"] / n, 1) if n else 0.0,
        }
    return {
        "total": len(traces),
        "by_mode": by_mode_out,
        "failure_distribution": dict(failures),
        "avg_tokens": round(total_tokens / len(traces), 1),
    }
