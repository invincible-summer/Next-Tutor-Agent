"""Applied teaching guidance: the deploy step of M7's human-in-the-loop loop.

M7's advisor PROPOSES open-ended teaching guidance; a human approves and
applies it via the evaluation API; THIS store holds the applied entries, and
the teaching engine (manager.adapt -> compose) reads them to steer focus/
avoid/rationale. Revoking an entry here instantly rolls the teaching behavior
back — guidance is never baked into any prompt template.

Ownership note: the file lives in teaching_engine (M3), not evaluation (M7),
because M7 is a pure observer of M3 and must never write into it. The one
writer is the evaluation API's deploy action (PATCH .../proposals/{id} with
status=applied), which is a human-initiated cross-module transition — the same
way the supervisor assembles M3 contexts. M7's analyzer code never imports
this module.

Storage mirrors the sibling per-student JSON stores (teaching_log /
prompt_memory / evaluation working set): students/<id>.teaching_guidance.json,
defensive read (corrupt/missing -> no guidance), file_lock + atomic write,
bounded entry cap. Old data without new fields round-trips via defaults.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

# bounded working set (revoked entries stay for audit; oldest are dropped)
MAX_GUIDANCE_ENTRIES = 20


@dataclass
class GuidanceEntry:
    """One applied teaching-guidance principle (open-ended text, no params).

    Source is always an approved ImprovementProposal; `active=False` means
    revoked — the entry is kept for audit but no longer consumed by compose.
    """

    id: str = ""
    source_proposal: str = ""    # ImprovementProposal.id
    title: str = ""
    applicability: str = ""      # free text: 情境/学科/概念范围；空 = 通用
    guidance: str = ""           # the principle itself (open-ended text)
    cautions: list[str] = field(default_factory=list)
    confidence: float = 0.5
    applied_at: float = field(default_factory=time.time)
    active: bool = True
    revoked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "source_proposal": self.source_proposal,
            "title": self.title, "applicability": self.applicability,
            "guidance": self.guidance, "cautions": list(self.cautions),
            "confidence": round(self.confidence, 3),
            "applied_at": self.applied_at, "active": self.active,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GuidanceEntry":
        d = d or {}
        return cls(
            id=str(d.get("id", "")),
            source_proposal=str(d.get("source_proposal", "")),
            title=str(d.get("title", "")),
            applicability=str(d.get("applicability", "")),
            guidance=str(d.get("guidance", "")),
            cautions=[str(c) for c in (d.get("cautions") or []) if str(c)],
            confidence=max(0.0, min(1.0, float(d.get("confidence", 0.5)))),
            applied_at=float(d.get("applied_at", 0.0)),
            active=bool(d.get("active", True)),
            revoked_at=float(d.get("revoked_at", 0.0)),
        )


def _resolve(student_id: str) -> Path:
    """Bare student id -> students/<id>.teaching_guidance.json (traversal-safe)."""
    bare = Path(student_id).name
    if bare.endswith(".teaching_guidance.json"):
        bare = bare[: -len(".teaching_guidance.json")]
    return _STUDENTS_DIR / f"{bare}.teaching_guidance.json"


def _load_entries(student_id: str) -> list[GuidanceEntry]:
    path = _resolve(student_id)
    if not path.exists():
        return []
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        return [GuidanceEntry.from_dict(d) for d in (data.get("entries") or [])
                if isinstance(d, dict)]
    except Exception:
        return []


def _save_entries(student_id: str, entries: list[GuidanceEntry]) -> None:
    try:
        import json
        _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": [e.to_dict() for e in entries[-MAX_GUIDANCE_ENTRIES:]],
            "updated_at": time.time(),
        }
        path = _resolve(student_id)
        with file_lock(path):
            atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        pass  # best-effort; never break a turn or an API deploy


def load_all(student_id: str) -> list[GuidanceEntry]:
    """All entries (active + revoked), oldest first. Never raises."""
    return _load_entries(student_id)


def load_active(student_id: str) -> list[GuidanceEntry]:
    """Active entries only, oldest-applied first (compose consumes these)."""
    return [e for e in _load_entries(student_id) if e.active]


def apply_guidance(student_id: str, entry: GuidanceEntry) -> bool:
    """Deploy one guidance entry (from an applied proposal).

    Idempotent per entry id: re-applying an already-present entry re-activates
    it instead of duplicating. Returns True when persisted.
    """
    try:
        if not entry.guidance:
            return False
        if not entry.id:
            entry.id = f"tg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        with file_lock(_resolve(student_id)):
            entries = _load_entries(student_id)
            for i, e in enumerate(entries):
                if e.id == entry.id or (entry.source_proposal
                                        and e.source_proposal == entry.source_proposal):
                    entry.applied_at = e.applied_at or entry.applied_at
                    entries[i] = entry
                    break
            else:
                entries.append(entry)
            _save_entries(student_id, entries)
        return True
    except Exception:
        return False


def revoke_guidance(student_id: str, entry_id: str) -> bool:
    """Revoke one entry (active=False). Teaching behavior reverts immediately."""
    try:
        with file_lock(_resolve(student_id)):
            entries = _load_entries(student_id)
            found = False
            for e in entries:
                if e.id == entry_id and e.active:
                    e.active = False
                    e.revoked_at = time.time()
                    found = True
            if found:
                _save_entries(student_id, entries)
        return found
    except Exception:
        return False
