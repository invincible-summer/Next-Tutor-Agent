"""Persistence layer for the Student Model.

Mirrors core/session.py: JSON working state at the project root under
`students/`, plus an append-only `<id>.events.jsonl` black box (like the
chat transcript). Path-traversal guarded the same way with _resolve.

Single-student first: DEFAULT_STUDENT_ID is the implicit learner. The store
is parametrized by student_id so multi-student (V4) is a one-line change in
how a session maps to an id; nothing here assumes there is only ever one.

Every public function is defensive: malformed files are treated as "no
state yet" and a fresh default is returned, so a corrupt student file can
never break a chat turn.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock
from ...core.config import settings  # for project-root parity if needed later
from .state import ConceptRecord, EventType, LearningEvent, StudentProfile

# students/ lives at the project root (parent of backend/), independent of the
# backend cwd -- same resolution policy as chat_history in core/session.py.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

DEFAULT_STUDENT_ID = "student_default"
_EVENT_LOG_CAP = 1000  # in-memory replay cap; the jsonl file is uncapped


def _resolve(student_id: str, ext: str = ".json") -> Path:
    """Resolve a bare student id to a Path under _STUDENTS_DIR.

    Path-traversal guard, mirroring core/session._resolve: strip any directory
    component with .name so a crafted id like '../etc/passwd' cannot escape.
    Tolerates a trailing extension.
    """
    bare = Path(student_id).name
    if bare.endswith(ext):
        bare = bare[: -len(ext)]
    return _STUDENTS_DIR / f"{bare}{ext}"


def _ensure_dir() -> None:
    _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)


# --- profile / mastery / memory (one JSON file per student) -----------------

@dataclass
class StudentStateBlob:
    """Everything persisted for one student, in a single JSON file.

    profile: long-term facts. mastery: {skill_id: Mastery dict}. memory:
    {skill_id_or_concept: ConceptRecord dict}. events are *not* stored here
    (they live in the append-only jsonl); this keeps the working file small.
    """
    profile: StudentProfile = field(default_factory=StudentProfile)
    mastery: dict[str, dict[str, Any]] = field(default_factory=dict)
    memory: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "mastery": {k: dict(v) for k, v in self.mastery.items()},
            "memory": {k: v.to_dict() for k, v in self._memory_records.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "StudentStateBlob":
        d = d or {}
        mastery = {k: dict(v) for k, v in (d.get("mastery") or {}).items()
                   if isinstance(v, dict)}
        memory = {}
        for k, v in (d.get("memory") or {}).items():
            if isinstance(v, dict):
                memory[k] = ConceptRecord.from_dict(v)
        return cls(
            profile=StudentProfile.from_dict(d.get("profile")),
            mastery=mastery,
            memory=memory,
        )

    @property
    def _memory_records(self) -> dict[str, ConceptRecord]:
        # internal view: memory is stored as ConceptRecord objects
        return self.memory  # type: ignore[return-value]


def load_blob(student_id: str = DEFAULT_STUDENT_ID) -> StudentStateBlob:
    """Load a student's full state. Missing/corrupt file -> fresh default."""
    path = _resolve(student_id)
    if not path.exists():
        return StudentStateBlob(profile=StudentProfile(id=student_id))
    try:
        return StudentStateBlob.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError):
        return StudentStateBlob(profile=StudentProfile(id=student_id))


def save_blob(student_id: str, blob: StudentStateBlob) -> None:
    _ensure_dir()
    path = _resolve(student_id)
    # the memory dict holds ConceptRecord objects in-process; serialize them
    payload = {
        "profile": blob.profile.to_dict(),
        "mastery": {k: dict(v) for k, v in blob.mastery.items()},
        "memory": {k: (v.to_dict() if hasattr(v, "to_dict") else dict(v))
                   for k, v in blob.memory.items()},
    }
    with file_lock(path):
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# --- events log (append-only black box) -------------------------------------

def append_events(student_id: str, events: list[LearningEvent]) -> None:
    """Append learning events to the student's append-only jsonl log.

    Never raises into a turn: failures are swallowed (best-effort black box,
    like the chat transcript append).
    """
    if not events:
        return
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".events.jsonl")
        # 锁防 asyncio 线程池并发 append 交错出半行
        with file_lock(path), path.open("a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_events(student_id: str, limit: int = _EVENT_LOG_CAP) -> list[LearningEvent]:
    """Read up to `limit` most-recent events for replay/debug. Skips bad lines."""
    path = _resolve(student_id, ext=".events.jsonl")
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[LearningEvent] = []
    for line in lines[-limit:]:
        try:
            ev = LearningEvent.from_dict(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
        if ev is not None:
            out.append(ev)
    return out
