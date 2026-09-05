"""Cross-turn persistence for adaptive test sessions (M4 Phase 3).

Mirrors student_model/store.py and teaching_engine/teaching_log.py: a JSON
working file at the project root under students/, path-traversal guarded the
same way (Path(name).name), every function defensive (corrupt file -> empty).
Only ONE active session per student is kept (a CAT is a focused single-concept
probe); starting a new one replaces the prior.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"


def _resolve(student_id: str) -> Path:
    """Resolve a student id to its assessment-session path. Path-traversal
    guard mirroring core/session._resolve and student_model/store._resolve."""
    bare = Path(student_id).name
    return _STUDENTS_DIR / f"{bare}.assessment.json"


def session_path(student_id: str) -> Path:
    """Public path helper so the manager can hold the SAME per-file lock key
    that save_session uses internally across a whole load->grade->save cycle
    (W2/A03: two tabs must not both append a result for one question)."""
    return _resolve(student_id)


def _ensure_dir() -> None:
    _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)


def save_session(student_id: str, data: dict[str, Any]) -> None:
    """Persist an AssessmentSession dict. Never raises into a turn."""
    try:
        _ensure_dir()
        data["updated_at"] = time.time()
        path = _resolve(student_id)
        with file_lock(path):
            atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError:
        pass


def load_session(student_id: str) -> dict[str, Any] | None:
    """Load the active session dict, or None when there is none/corrupt."""
    path = _resolve(student_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def clear_session(student_id: str) -> None:
    """Remove the active session (after a CAT finishes or is abandoned)."""
    try:
        path = _resolve(student_id)
        if path.exists():
            path.unlink()
    except OSError:
        pass
