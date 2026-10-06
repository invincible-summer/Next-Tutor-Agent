"""SQL adapter for the classroom domain (classroom_documents, ADR-0017).

Kinds:

- ``owner``  — doc ``owner_id``: owner record (lifecycle/idempotency/
  quota). The purge tombstone stays a root-outside FILE in both modes:
  account purge deletes this row via the 9-domain ``purge_owner`` loop
  AFTER ``mark_owner_purged`` ran, so the file tombstone is the durable
  fence for late writes; the SQL record only mirrors lifecycle while it
  exists.
- ``lesson`` — doc ``lesson_id``: ``sc.Lesson`` payload
- ``job``    — doc ``job_id``: ``sc.GenerationJob`` payload
- ``run``    — doc ``run_id``: ``sc.ClassroomRun`` payload
- ``index``  — doc ``workspace_id``: the rebuildable workspace index

Revision staging dirs, assets, audio and export zips stay on the
file/object-store side; the six-step commit pipeline keeps operating on
directories and only rides the dispatching lesson/job primitives.
"""
from __future__ import annotations

from typing import Any, Callable

from app.persistence.documents import DocumentRecord, bridge, sql_enabled


class MissingDocumentError(Exception):
    """Raised out of a mutate closure when the row is absent."""


def use_sql() -> bool:
    return sql_enabled("classroom")


def _repo():
    return bridge.repository("classroom")


# -- owner lifecycle --------------------------------------------------------


def get_owner_record(owner_id: str) -> dict[str, Any] | None:
    async def _get() -> Any:
        row = await _repo().get(owner_id, owner_id, kind="owner")
        return dict(row.payload or {}) if row else None

    return bridge.call(_get)


def put_owner_record(owner_id: str, record: dict[str, Any]) -> None:
    async def _put() -> None:
        await _repo().put(DocumentRecord(
            doc_id=owner_id, owner_id=owner_id, kind="owner",
            payload=record))

    bridge.call(_put)


def mutate_owner_record(owner_id: str,
                        closure: Callable[[dict[str, Any]], None]
                        ) -> dict[str, Any]:
    """Row-locked owner-record read-modify-write; a missing row seeds the
    defaults (mirrors the file mode's read-then-default). The closure
    mutates the record in place — its return value is ignored (a stray
    ``None`` must never be read as "delete the row").

    The closure runs on the worker loop: it must not call back into
    ``bridge.call`` (nested submit deadlocks) — seed defaults inline."""

    def mutate(existing: dict | None) -> dict:
        record = dict(existing or {})
        record.setdefault("lifecycle", "active")
        record.setdefault("idempotency", {})
        record.setdefault("quota", {})
        closure(record)
        return record

    async def _mutate() -> dict[str, Any]:
        record = await _repo().mutate(owner_id, owner_id, mutate,
                                      kind="owner")
        return dict(record.payload or {})

    return bridge.call(_mutate)


# -- generic model documents ------------------------------------------------


def load_payload(kind: str, owner_id: str, doc_id: str) -> dict[str, Any] | None:
    async def _get() -> Any:
        row = await _repo().get(owner_id, doc_id, kind=kind)
        return dict(row.payload or {}) if row else None

    return bridge.call(_get)


def save_payload(kind: str, owner_id: str, doc_id: str,
                 payload: dict[str, Any]) -> None:
    async def _put() -> None:
        await _repo().put(DocumentRecord(
            doc_id=doc_id, owner_id=owner_id, kind=kind, payload=payload))

    bridge.call(_put)


def mutate_payload(kind: str, owner_id: str, doc_id: str,
                   closure: Callable[[dict[str, Any]], dict[str, Any]]
                   ) -> dict[str, Any]:
    """Row-locked read-modify-write; absent rows raise
    MissingDocumentError from the closure and roll everything back."""

    def mutate(existing: dict | None) -> dict:
        if existing is None:
            raise MissingDocumentError(doc_id)
        return closure(existing)

    async def _mutate() -> dict[str, Any]:
        record = await _repo().mutate(owner_id, doc_id, mutate, kind=kind)
        return dict(record.payload or {})

    return bridge.call(_mutate)


def mutate_index(owner_id: str, workspace_id: str,
                 closure: Callable[[dict[str, Any] | None], dict[str, Any]]
                 ) -> dict[str, Any]:
    """Index mutate: a missing row seeds the empty index (like the file
    mode's read-then-default), so closures always see a dict. The closure
    returns the replacement dict; ``None`` falls back to the in-place
    seed (never interpreted as a delete)."""

    def mutate(existing: dict | None) -> dict:
        data = existing if isinstance(existing, dict) else \
            {"version": 1, "lessons": {}, "jobs": {}}
        replacement = closure(data)
        return replacement if isinstance(replacement, dict) else data

    async def _mutate() -> dict[str, Any]:
        record = await _repo().mutate(owner_id, workspace_id, mutate,
                                      kind="index")
        return dict(record.payload or {})

    return bridge.call(_mutate)


def delete_payload(kind: str, owner_id: str, doc_id: str) -> bool:
    async def _delete() -> bool:
        return await _repo().delete(owner_id, doc_id, kind=kind)

    return bridge.call(_delete)


def list_payloads(kind: str, owner_id: str) -> list[dict[str, Any]]:
    async def _list() -> list[dict[str, Any]]:
        rows = await _repo().list_documents(owner_id, kind=kind)
        return [dict(r.payload or {}) for r in rows]

    return bridge.call(_list)


def list_docs(kind: str, owner_id: str) -> list[tuple[str, dict[str, Any]]]:
    """(doc_id, payload) pairs — doc_id carries the addressing key the
    payload may not repeat (e.g. workspace_id for index docs)."""
    async def _list() -> list[tuple[str, dict[str, Any]]]:
        rows = await _repo().list_documents(owner_id, kind=kind)
        return [(r.doc_id, dict(r.payload or {})) for r in rows]

    return bridge.call(_list)
