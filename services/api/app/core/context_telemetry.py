"""Aggregate non-sensitive context/runtime telemetry from per-turn traces."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .session import list_sessions, load_session
from .trace import trace_dir_path


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def aggregate_runtime_events(event_groups: Iterable[list[dict[str, Any]]]) -> dict[str, Any]:
    prompt, completion, total, estimated = [], [], [], []
    reasoning_channel, answer_channel = [], []
    tool_schema, projected, original, compaction_saved = [], [], [], []
    pressure: Counter[str] = Counter()
    reasoning: Counter[str] = Counter()
    trace_count = llm_calls = compactions = recoveries = fallbacks = 0
    empty_answer_calls = 0
    latest_provider: dict[str, Any] = {}
    for events in event_groups:
        trace_count += 1
        for event in events:
            kind = event.get("kind")
            if kind == "llm_usage":
                llm_calls += 1
                prompt.append(float(event.get("prompt_tokens", 0)))
                completion.append(float(event.get("completion_tokens", 0)))
                total.append(float(event.get("total_tokens", 0)))
            elif kind == "llm_channels":
                reasoning_channel.append(float(event.get("reasoning_estimated_tokens", 0)))
                answer_channel.append(float(event.get("answer_estimated_tokens", 0)))
                if not float(event.get("answer_estimated_tokens", 0)):
                    empty_answer_calls += 1
            elif kind == "context_budget":
                estimated.append(float(event.get("estimated_input_tokens", 0)))
                tool_schema.append(float(event.get("tool_schema_tokens", 0)))
                pressure[str(event.get("pressure", "normal"))] += 1
            elif kind == "tool_context_projection":
                projected.append(float(event.get("projected_tokens", 0)))
                original.append(float(event.get("original_tokens", 0)))
            elif kind == "reasoning_policy_call":
                reasoning[str(event.get("requested_mode", "unknown"))] += 1
            elif kind == "compaction":
                compactions += 1
                compaction_saved.append(max(0.0, float(event.get("pre_tokens", 0))
                                             - float(event.get("summary_tokens", 0))))
            elif kind in {"incomplete_answer_recovery", "empty_answer_fallback"}:
                recoveries += 1
            elif kind in {"provider_capability_fallback", "tool_message_fallback"}:
                fallbacks += 1
            elif kind == "provider_capabilities":
                latest_provider = {k: v for k, v in event.items()
                                   if k not in {"ts", "run_id", "kind"}}
    saved = max(0.0, sum(original) - sum(projected))
    return {
        "status": "ok", "trace_count": trace_count, "llm_calls": llm_calls,
        "profile": {"context_window": settings.llm_context_window,
                    "max_output_tokens": settings.llm_max_output_tokens,
                    "safety_margin": settings.llm_context_safety_margin,
                    "provider": settings.llm_provider,
                    "llm_runtime_mode": settings.llm_runtime_mode,
                    "tool_message_mode": settings.tool_message_mode,
                    "tool_projection_mode": settings.tool_context_projection_mode,
                    "latest_capabilities": latest_provider},
        "usage": {"avg_prompt_tokens": _avg(prompt),
                  "avg_completion_tokens": _avg(completion),
                  "avg_total_tokens": _avg(total),
                  "avg_estimated_input_tokens": _avg(estimated),
                  "avg_tool_schema_tokens": _avg(tool_schema),
                  "avg_reasoning_channel_tokens": _avg(reasoning_channel),
                  "avg_answer_channel_tokens": _avg(answer_channel)},
        "pressure": dict(pressure),
        "reasoning_modes": dict(reasoning),
        "compaction": {"count": compactions,
                       "estimated_saved_tokens": round(sum(compaction_saved), 2)},
        "tool_projection": {"samples": len(projected),
                            "original_tokens": round(sum(original), 2),
                            "projected_tokens": round(sum(projected), 2),
                            "estimated_saved_tokens": round(saved, 2),
                            "saved_ratio": round(saved / sum(original), 4) if sum(original) else 0.0},
        "recovery": {"count": recoveries, "provider_or_protocol_fallbacks": fallbacks,
                     "empty_answer_calls": empty_answer_calls},
    }


def context_runtime_report(student_id: str, limit: int = 200) -> dict[str, Any]:
    trace_ids: list[str] = []
    for item in list_sessions():
        owner = item.get("student_id") or "student_default"
        if owner != student_id:
            continue
        session = load_session(str(item.get("session_id", "")))
        if session:
            trace_ids.extend(session.trace_ids)
    groups: list[list[dict[str, Any]]] = []
    root = trace_dir_path()
    for trace_id in list(dict.fromkeys(trace_ids))[-limit:]:
        path = root / f"trace_{Path(trace_id).name}.jsonl"
        if not path.exists():
            continue
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try: events.append(json.loads(line))
            except json.JSONDecodeError: continue
        groups.append(events)
    return aggregate_runtime_events(groups)
