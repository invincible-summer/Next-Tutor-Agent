from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import trace_dir_path
from app.identity import is_auth_required
from app.identity.deps import resolve_student_id
from app.agents.student_model.store import DEFAULT_STUDENT_ID

router = APIRouter(prefix="/trace", tags=["trace"])


def _trace_path(run_id: str) -> Path:
    return trace_dir_path() / f"trace_{run_id}.jsonl"


def _authorize_trace(run_id: str, student_id: str) -> None:
    """Trace files carry no identity stamp of their own, so ownership is
    derived from the chat session that references run_id in its trace_ids:
    the trace inherits that session's owner (404 = invisible, no existence
    leak). When no session claims the trace (e.g. background runs), it is
    only open to authenticated users under AUTH_MODE=1; guest mode keeps
    the debug endpoints open for local development.
    """
    from app.core.session import list_sessions, load_session
    for meta in list_sessions():
        s = load_session(meta["session_id"])
        if s is not None and run_id in s.trace_ids:
            if (s.student_id or DEFAULT_STUDENT_ID) != student_id:
                raise HTTPException(404, "Trace not found")
            return
    if is_auth_required() and student_id == DEFAULT_STUDENT_ID:
        raise HTTPException(401, "not_authenticated")


@router.get("/{run_id}/html")
def trace_html(run_id: str, student_id: str = Depends(resolve_student_id)) -> HTMLResponse:
    """R12: Foldable HTML rendering of a trace for human debugging."""
    _authorize_trace(run_id, student_id)
    path = _trace_path(run_id)
    if not path.exists():
        raise HTTPException(404, "Trace not found")
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    kind_colors = {
        "turn_start": "#6366f1", "step": "#0ea5e9", "decision": "#f59e0b",
        "llm_usage": "#8b5cf6", "observation": "#10b981", "tool_result": "#10b981",
        "warning": "#f97316", "finish": "#22c55e", "error": "#ef4444",
    }
    from html import escape as _hesc
    rows = []
    for ev in events:
        k = ev.get("kind", "?")
        color = kind_colors.get(k, "#64748b")
        label = f"[{ev.get('ts', 0):>6.1f}s] {k}"
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
    html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<title>Trace {run_id}</title></head><body '
        f'style="font-family:system-ui,sans-serif;max-width:800px;'
        f'margin:0 auto;padding:16px">'
        f'<h2 style="font-size:18px;margin:0 0 8px">Trace {run_id}</h2>'
        f'<div style="margin-top:8px">{" ".join(rows)}</div>'
        f'</body></html>'
    )
    return HTMLResponse(content=html)


@router.get("/{run_id}")
def trace_json(run_id: str, student_id: str = Depends(resolve_student_id)):
    """R12: Raw JSONL trace events for API consumers."""
    _authorize_trace(run_id, student_id)
    path = _trace_path(run_id)
    if not path.exists():
        raise HTTPException(404, "Trace not found")
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"run_id": run_id, "events": events, "n_events": len(events)}
