"""Persistence layer for the UX Intelligence layer (M8).

Mirrors memory/store.py (M6) and evaluation/store.py (M7):
  - UX events: append-only JSONL black box (uncapped), like the chat transcript,
    the M2 events log, and the M6 episodes log.
  - UX profile + motivation state: JSON working set (full rewrite per save).

Path-traversal guarded identically (Path(name).name strip). Every function is
defensive: a corrupt/missing file is treated as "no state yet" so a bad file
can never break a chat turn.

Lives at project-root students/ alongside the M2 student blob, M3 teaching
log, M6 memory, M7 evaluation -- same .gitignore coverage, same student-scoping.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .schema import (FeedbackType, MotivationState, InteractionStyle,
                     UXEvent, UXProfile, _MAX_UX_EVENTS_REPLAY)
from ...core.atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"


def _resolve(student_id: str, ext: str = ".ux_profile.json") -> Path:
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


# --- UX profile: JSON working set (full rewrite) ----------------------------

def load_profile(student_id: str) -> UXProfile:
    """Load the UX profile for a student. Returns a fresh default when the
    file is missing or corrupt (never raises into a turn)."""
    path = _resolve(student_id, ext=".ux_profile.json")
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return UXProfile.from_dict(raw)
    except Exception:
        pass
    return UXProfile(student_id=Path(student_id).name)


def save_profile(student_id: str, profile: UXProfile) -> bool:
    """Persist the UX profile (full rewrite). Best-effort; never raises."""
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".ux_profile.json")
        profile.updated_at = time.time()
        with file_lock(path):
            atomic_write_text(path, json.dumps(profile.to_dict(), ensure_ascii=False))
        return True
    except Exception:
        return False


# --- UX events: append-only JSONL black box ---------------------------------

def append_event(student_id: str, event: UXEvent) -> bool:
    """Append one UXEvent to the student's ux_events.jsonl. Never raises.
    Returns True on success."""
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".ux_events.jsonl")
        # 锁防 asyncio 线程池并发 append 交错出半行
        with file_lock(path), path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_events(student_id: str, limit: int = _MAX_UX_EVENTS_REPLAY) -> list[UXEvent]:
    """Read up to `limit` most-recent UX events. Skips bad lines.

    Returns events oldest-first (chronological order for analysis).
    """
    path = _resolve(student_id, ext=".ux_events.jsonl")
    if not path.exists():
        return []
    out: list[UXEvent] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(UXEvent.from_dict(json.loads(line)))
                except Exception:
                    continue
    except Exception:
        return []
    if len(out) > limit:
        out = out[-limit:]
    return out


def profile_summary(student_id: str) -> dict[str, Any]:
    """A flattened read for the UX API (profile + derived signals). Never
    raises; returns a minimal dict on any failure."""
    try:
        profile = load_profile(student_id)
        events = read_events(student_id, limit=_MAX_UX_EVENTS_REPLAY)
        feedback_counts: dict[str, int] = {}
        for ft in profile.recent_feedback:
            feedback_counts[ft.value] = feedback_counts.get(ft.value, 0) + 1
        avg_len = (sum(profile.recent_response_lengths) / len(profile.recent_response_lengths)
                   if profile.recent_response_lengths else 0)
        return {
            "student_id": student_id,
            "style": profile.style.to_dict(),
            "motivation": profile.motivation.to_dict(),
            "recent_feedback_counts": feedback_counts,
            "avg_response_length": round(avg_len, 1),
            "abandon_signals": profile.abandon_signals,
            "event_count": len(events),
            "updated_at": profile.updated_at,
        }
    except Exception:
        return {"student_id": student_id, "style": InteractionStyle().to_dict(),
                "motivation": MotivationState().to_dict(),
                "recent_feedback_counts": {}, "avg_response_length": 0,
                "abandon_signals": 0, "event_count": 0, "updated_at": time.time()}
