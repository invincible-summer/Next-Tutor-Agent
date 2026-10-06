"""SQL adapter for the learning-orchestration domain
(orchestration_documents, ADR-0017).

Kinds:

- ``state``  — doc ``state``: the full OrchestrationState working set
  (rewrite per save, mirroring the file mode's atomic full rewrite)
- ``events`` — doc ``events``: the whole append-only event JSONL as one
  document (``{"lines": [...]}``); append runs read→derive→replace inside
  a single row-locked mutate, mirroring the file mode's file_lock append
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.persistence.documents import DocumentRecord, bridge, sql_enabled

STATE_DOC = "state"
EVENTS_DOC = "events"


def use_sql() -> bool:
    return sql_enabled("orchestration")


def _repo():
    return bridge.repository("orchestration")


def owner_key(student_id: str) -> str:
    """Mirror store._resolve normalization: bare name, known ext stripped."""
    bare = Path(student_id).name
    for ext in (".orchestration.json", ".orchestration_events.jsonl"):
        if bare.endswith(ext):
            return bare[: -len(ext)]
    return bare


# -- state ------------------------------------------------------------------


def load_state_payload(student_id: str) -> dict[str, Any] | None:
    async def _load() -> Any:
        row = await _repo().get(student_id, STATE_DOC, kind="state")
        return dict(row.payload or {}) if row else None

    return bridge.call(_load)


def save_state_payload(student_id: str, payload: dict[str, Any]) -> None:
    async def _put() -> None:
        await _repo().put(DocumentRecord(
            doc_id=STATE_DOC, owner_id=student_id, kind="state",
            payload=payload))

    bridge.call(_put)


# -- events -----------------------------------------------------------------


def read_event_lines(student_id: str) -> list[Any]:
    async def _load() -> list[Any]:
        row = await _repo().get(student_id, EVENTS_DOC, kind="events")
        return list((row.payload or {}).get("lines") or []) if row else []

    return bridge.call(_load)


def mutate_event_lines(
        student_id: str,
        build: Callable[[list[Any] | None], list[Any]]) -> None:
    """Row-locked read-modify-write over the event line list; a closure
    exception rolls the replacement back."""

    def mutate(existing: dict | None) -> dict:
        lines = list((existing or {}).get("lines") or [])
        return {"lines": build(lines)}

    async def _mutate() -> None:
        await _repo().mutate(student_id, EVENTS_DOC, mutate, kind="events")

    bridge.call(_mutate)
