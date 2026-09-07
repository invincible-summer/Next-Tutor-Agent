"""Persistence layer for the Learning Orchestration layer (M9).

Mirrors memory/store.py (M6), evaluation/store.py (M7), ux_intelligence/store.py
(M8):
  - orchestration events: append-only JSONL black box (uncapped), like the chat
    transcript, the M2 events log, the M6 episodes log, and the M8 UX log.
  - orchestration state: JSON working set (full rewrite per save).

Path-traversal guarded identically (Path(name).name strip). Every function is
defensive: a corrupt/missing file is treated as "no state yet" so a bad file
can never break a chat turn.

Lives at project-root students/ alongside the M2 student blob, M3 teaching
log, M6 memory, M7 evaluation, M8 UX -- same .gitignore coverage, same
student-scoping.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .schema import (OrchestrationEvent, OrchestrationState,
                     _MAX_EVENTS_REPLAY)
from ...core.atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"


def _resolve(student_id: str, ext: str = ".orchestration.json") -> Path:
    """Resolve a bare student id to a Path under students/. Path-traversal
    guard mirroring core/session._resolve and the other modules' _resolve."""
    bare = Path(student_id).name
    if bare.endswith(ext):
        bare = bare[: -len(ext)]
    return _STUDENTS_DIR / f"{bare}{ext}"


def _ensure_dir() -> None:
    try:
        _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# --- orchestration state: JSON working set (full rewrite) ------------------

def load_state(student_id: str) -> OrchestrationState:
    """Load the orchestration state for a student. Returns a fresh default
    when the file is missing or corrupt (never raises into a turn)."""
    path = _resolve(student_id, ext=".orchestration.json")
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return OrchestrationState.from_dict(raw)
    except Exception:
        pass
    return OrchestrationState(student_id=Path(student_id).name)


def save_state(student_id: str, state: OrchestrationState) -> bool:
    """Persist the orchestration state (full rewrite). Best-effort."""
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".orchestration.json")
        state.updated_at = time.time()
        with file_lock(path):
            atomic_write_text(path, json.dumps(state.to_dict(), ensure_ascii=False))
        return True
    except Exception:
        return False


# --- orchestration events: append-only JSONL black box ---------------------

def append_event(student_id: str, event: OrchestrationEvent) -> bool:
    """Append one OrchestrationEvent to the student's orchestration_events.jsonl.
    Never raises. Returns True on success."""
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".orchestration_events.jsonl")
        # 锁防 asyncio 线程池并发 append 交错出半行
        with file_lock(path), path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_events(student_id: str, limit: int = _MAX_EVENTS_REPLAY) -> list[OrchestrationEvent]:
    """Read up to `limit` most-recent orchestration events. Skips bad lines.

    Returns events oldest-first (chronological order for analysis).
    """
    path = _resolve(student_id, ext=".orchestration_events.jsonl")
    if not path.exists():
        return []
    out: list[OrchestrationEvent] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(OrchestrationEvent.from_dict(json.loads(line)))
                except Exception:
                    continue
    except Exception:
        return []
    if len(out) > limit:
        out = out[-limit:]
    return out


def state_summary(student_id: str) -> dict[str, Any]:
    """A flattened read for the orchestration API (state + derived signals).
    Never raises; returns a minimal dict on any failure."""
    try:
        state = load_state(student_id)
        events = read_events(student_id, limit=_MAX_EVENTS_REPLAY)
        due_count = sum(1 for r in state.review_queue.values() if r.next_review > 0)
        pending_today = sum(1 for t in state.daily_tasks
                            if t.status.value == "pending")
        return {
            "student_id": student_id,
            "goals": [g.to_dict() for g in state.goals],
            "goal_states": [gs.to_dict() for gs in state.goal_states],
            "milestones": [m.to_dict() for m in state.milestones],
            "weekly_plan": [w.to_dict() for w in state.weekly_plan],
            "daily_tasks": [t.to_dict() for t in state.daily_tasks],
            "schedule": state.schedule.to_dict(),
            "habit": state.habit.to_dict(),
            "review_queue": {k: v.to_dict() for k, v in state.review_queue.items()},
            "srs_due_count": due_count,
            "pending_today": pending_today,
            "event_count": len(events),
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "events_processed": state.events_processed,
        }
    except Exception:
        return {"student_id": student_id, "goals": [], "goal_states": [],
                "milestones": [],
                "weekly_plan": [], "daily_tasks": [],
                "schedule": {},
                "habit": {}, "review_queue": {}, "srs_due_count": 0,
                "pending_today": 0, "event_count": 0,
                "created_at": time.time(), "updated_at": time.time(),
                "events_processed": 0}
