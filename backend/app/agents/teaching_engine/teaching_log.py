"""Cross-turn teaching memory: what mode we taught in last, and how it went.

This is the load-bearing piece for "feels like a teacher" (M3 spec §6):
without it the engine is stateless and can only say "you are at 40% mastery",
never "last time you got the direction right, so today we go to formulas". It
persists, per concept, the last few (mode, outcome) turns so
strategy.select_strategy can advance the teaching mode across turns.

Mirrors student_model/store.py: JSON at the project root under students/,
path-traversal guarded identically (Path(name).name), and every public
function is defensive -- a corrupt file is treated as "no history" and never
breaks a turn. Lives next to the student blob (students/<id>.teaching.json)
so it shares the same .gitignore coverage and student-scoping.

Single-student first (DEFAULT_STUDENT_ID), parametrized for multi-student,
matching student_model's design.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

# how many recent turns per concept we keep in the working file. Older turns
# are recoverable from the events log if ever needed; this keeps the file small.
_LOG_CAP_PER_CONCEPT = 6
# how many distinct concepts we track history for (LRU-ish: most-recent first).
_LOG_CAP_CONCEPTS = 60


def _resolve(student_id: str, ext: str = ".teaching.json") -> Path:
    """Resolve a bare student id to a Path under students/. Path-traversal
    guard mirroring core/session._resolve and student_model/store._resolve."""
    bare = Path(student_id).name
    if bare.endswith(ext):
        bare = bare[: -len(ext)]
    return _STUDENTS_DIR / f"{bare}{ext}"


@dataclass
class TeachingLogEntry:
    """One past teaching turn on one concept."""
    mode: str = ""            # TeachingMode value used that turn
    outcome: str = "unknown"  # TeachingOutcome value (correct/wrong/engaged/...)
    ts: float = field(default_factory=time.time)
    note: str = ""            # short context (e.g. "已讲直觉", optional)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "outcome": self.outcome,
                "ts": self.ts, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TeachingLogEntry":
        d = d or {}
        return cls(mode=str(d.get("mode", "") or ""),
                   outcome=str(d.get("outcome", "unknown") or "unknown"),
                   ts=float(d.get("ts", 0.0)),
                   note=str(d.get("note", "") or ""))


def _empty_log() -> dict[str, Any]:
    return {"concepts": {}, "updated_at": 0.0}


def _load_raw(student_id: str) -> dict[str, Any]:
    """Load the raw teaching log JSON; missing/corrupt -> empty log."""
    path = _resolve(student_id)
    if not path.exists():
        return _empty_log()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_log()
        data.setdefault("concepts", {})
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return _empty_log()


def _save_raw(student_id: str, data: dict[str, Any]) -> None:
    try:
        _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = time.time()
        path = _resolve(student_id)
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError:
        pass  # best-effort; never break a turn


def load_teaching_log(student_id: str = "student_default") -> dict[str, list[TeachingLogEntry]]:
    """Return {concept_key: [TeachingLogEntry, ...]} (oldest-first within a concept).

    concept_key is whatever the caller normalized the concept to (typically
    the skill_id from the graph, falling back to the bare concept string).
    """
    data = _load_raw(student_id)
    out: dict[str, list[TeachingLogEntry]] = {}
    for ck, entries in (data.get("concepts") or {}).items():
        if not isinstance(entries, list):
            continue
        out[ck] = [TeachingLogEntry.from_dict(e) for e in entries
                   if isinstance(e, dict)]
    return out


def recent_for_concept(student_id: str, concept_key: str, *,
                       limit: int = 3) -> list[TeachingLogEntry]:
    """The most recent `limit` log entries for one concept (newest first)."""
    entries = load_teaching_log(student_id).get(concept_key) or []
    if not entries:
        return []
    return list(reversed(entries))[:limit]


def last_turn(student_id: str, concept_key: str) -> TeachingLogEntry | None:
    """The single most recent teaching turn on a concept, or None."""
    entries = recent_for_concept(student_id, concept_key, limit=1)
    return entries[0] if entries else None


def record_turn_outcome(student_id: str, concept_key: str, *, mode: str,
                        outcome: str, note: str = "") -> None:
    """Append a (mode, outcome) turn record for a concept.

    Trims to _LOG_CAP_PER_CONCEPT per concept and keeps the overall concept
    set to _LOG_CAP_CONCEPTS (dropping the least-recently-touched concept).
    Never raises into a turn.
    """
    if not concept_key or not mode:
        return
    try:
        with file_lock(_resolve(student_id)):
            data = _load_raw(student_id)
            concepts = data.setdefault("concepts", {})
            entries = concepts.get(concept_key) or []
            entries.append(TeachingLogEntry(mode=mode, outcome=outcome, note=note).to_dict())
            concepts[concept_key] = entries[-_LOG_CAP_PER_CONCEPT:]
            # LRU cap on distinct concepts: drop oldest-updated beyond the cap
            if len(concepts) > _LOG_CAP_CONCEPTS:
                scored = sorted(
                    concepts.items(),
                    key=lambda kv: ((kv[1][-1].get("ts", 0.0) if kv[1] else 0.0)),
                )
                for ck, _ in scored[: len(concepts) - _LOG_CAP_CONCEPTS]:
                    concepts.pop(ck, None)
            _save_raw(student_id, data)
    except Exception:
        return
