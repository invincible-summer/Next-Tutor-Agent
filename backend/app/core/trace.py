"""Structured JSONL trace for observability.

For any failure we must answer: what did it do? (call chain) what came back?
(result chain) why did it decide that? (decision chain). Traces are append-only
JSONL; failures are kept on purpose.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from html import escape as _hesc

from .config import trace_dir_path


class Trace:
    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.path = trace_dir_path() / f"trace_{self.run_id}.jsonl"
        self.events: list[dict[str, Any]] = []
        self._t0 = time.time()
        # R11: usage/cost accumulator
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_answer_tokens: int = 0
        self._llm_calls: int = 0
        self._decision_chain: list[dict[str, Any]] = []

    def log(self, kind: str, **payload: Any) -> None:
        ev = {
            "ts": round(time.time() - self._t0, 3),
            "run_id": self.run_id,
            "kind": kind,
            **payload,
        }
        self.events.append(ev)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def llm_call(self, step: int | None, usage: dict[str, Any] | None) -> None:
        """R11: Record per-LLM-call token usage. Called when the stream yields
        `done`. Accumulates for cost estimation."""
        if not usage:
            return
        self._llm_calls += 1
        pt = usage.get("prompt_tokens", 0) or 0
        ct = usage.get("completion_tokens", 0) or 0
        tt = usage.get("total_tokens", (pt + ct)) or (pt + ct)
        self.total_prompt_tokens += pt
        self.total_completion_tokens += ct
        self.total_tokens += tt
        reasoning_tokens = usage.get("reasoning_tokens")
        answer_tokens = usage.get("answer_tokens")
        if reasoning_tokens is not None:
            self.total_reasoning_tokens += int(reasoning_tokens or 0)
        if answer_tokens is not None:
            self.total_answer_tokens += int(answer_tokens or 0)
        self.log(
            "llm_usage",
            step=step,
            call_idx=self._llm_calls,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            reasoning_tokens=reasoning_tokens,
            answer_tokens=answer_tokens,
            cumulative_total=self.total_tokens,
        )

    def decision(self, step: int, thought: str | None, tool: str | None,
                 has_tool: bool, finish_reason: str | None = None) -> None:
        """R11: Structured decision-chain entry."""
        entry: dict[str, Any] = {
            "step": step,
            "thought_len": len(thought) if thought else 0,
            "tool": tool,
            "has_tool": has_tool,
            "finish_reason": finish_reason,
        }
        self._decision_chain.append(entry)
        self.log("decision", step=step, thought_len=entry["thought_len"],
                 tool=tool, has_tool=has_tool, finish_reason=finish_reason)

    def step(self, step: int, thought: str | None, tool_calls: list[dict[str, Any]] | None) -> None:
        self.log("step", step=step, thought=thought, tool_calls=tool_calls)

    def observation(self, step: int, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]) -> None:
        # keep full result for diagnosability, but it is already compact
        self.log(
            "observation",
            step=step,
            tool=tool_name,
            args=tool_args,
            status=result.get("status"),
            error=result.get("error"),
            text=(result.get("text") or "")[:500],
            data=result.get("data"),
        )

    def finish(self, step: int, answer: str, reason: str) -> None:
        self.log("finish", step=step, reason=reason, answer=answer[:1000])

    def error(self, step: int, message: str) -> None:
        self.log("error", step=step, message=message)

    def to_html(self) -> str:
        """R12: Render the JSONL trace as a self-contained, foldable HTML doc.
        Each event is a <details> row so a human can expand/collapse the chain.
        Includes a summary header with token usage and decision chain."""
        rows = []
        kind_colors = {
            "turn_start": "#6366f1", "step": "#0ea5e9", "decision": "#f59e0b",
            "llm_usage": "#8b5cf6", "observation": "#10b981", "tool_result": "#10b981",
            "warning": "#f97316", "finish": "#22c55e", "error": "#ef4444",
            # V2 Supervisor events (orchestration layer, distinct hue family)
            "supervisor_understanding": "#0891b2", "supervisor_plan": "#0891b2",
            "supervisor_snapshot": "#0891b2", "supervisor_state_update": "#0891b2",
            "supervisor_understand_error": "#dc2626", "supervisor_plan_error": "#dc2626",
            "supervisor_fallback_to_legacy": "#dc2626",
        }
        for ev in self.events:
            k = ev.get("kind", "?")
            color = kind_colors.get(k, "#64748b")
            ts = ev.get("ts", 0)
            label = f"[{ts:>6.1f}s] {k}"
            detail = ", ".join(
                f"{key}={val}" for key, val in ev.items()
                if key not in ("ts", "run_id", "kind")
            )
            rows.append(
                f'<details style="margin:2px 0"><summary style="cursor:pointer;'
                f'font-family:monospace;font-size:12px;color:{color}">'
                f'{_hesc(label)}</summary><pre style="margin:4px 0 4px 16px;'
                f'font-size:11px;color:#334155;background:#f8fafc;padding:6px;'
                f'border-radius:4px;white-space:pre-wrap">'
                f'{_hesc(detail)}</pre></details>'
            )
        summary_block = ""
        s = self.summary()
        usage_items = []
        for key in ("llm_calls", "prompt_tokens", "completion_tokens", "total_tokens",
                     "n_steps", "n_tool_calls", "tools_called", "duration_s"):
            if key in s:
                usage_items.append(f"<b>{key}</b>: {_hesc(str(s[key]))}")
        if usage_items:
            summary_block = (
                f'<div style="background:#f1f5f9;padding:10px;border-radius:8px;'
                f'margin-bottom:12px;font-family:monospace;font-size:12px">'
                f'<b>Run {self.run_id}</b><br>'
                f'{" · ".join(usage_items)}</div>'
            )
        chain_block = ""
        if s.get("decision_chain"):
            chain_items = "".join(
                f'<li style="font-family:monospace;font-size:11px">'
                f'step {c.get("step")}: tool={_hesc(str(c.get("tool")))}, '
                f'has_tool={c.get("has_tool")}, '
                f'finish={_hesc(str(c.get("finish_reason")))}</li>'
                for c in s["decision_chain"]
            )
            chain_block = (
                f'<details style="margin-bottom:12px"><summary style="cursor:pointer;'
                f'font-weight:bold;font-size:13px">Decision Chain</summary>'
                f'<ul style="margin:6px 0">{chain_items}</ul></details>'
            )
        return (
            f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>Trace {self.run_id}</title></head><body '
            f'style="font-family:system-ui,sans-serif;max-width:800px;'
            f'margin:0 auto;padding:16px">'
            f'<h2 style="font-size:18px;margin:0 0 8px">Trace {self.run_id}</h2>'
            f'{summary_block}{chain_block}'
            f'<div style="margin-top:8px">{" ".join(rows)}</div>'
            f'</body></html>'
        )

    def summary(self) -> dict[str, Any]:
        steps = [e for e in self.events if e["kind"] == "step"]
        obs = [e for e in self.events if e["kind"] == "observation"]
        s = {
            "run_id": self.run_id,
            "trace_path": str(self.path),
            "n_steps": len(steps),
            "n_tool_calls": len(obs),
            "tools_called": [o["tool"] for o in obs],
            "duration_s": round(time.time() - self._t0, 2),
        }
        if self._llm_calls:
            s["llm_calls"] = self._llm_calls
            s["prompt_tokens"] = self.total_prompt_tokens
            s["completion_tokens"] = self.total_completion_tokens
            s["total_tokens"] = self.total_tokens
        if self._decision_chain:
            s["decision_chain"] = self._decision_chain
        return s
