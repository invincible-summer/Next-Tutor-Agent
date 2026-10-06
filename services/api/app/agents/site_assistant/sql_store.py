"""SQL adapter for the site-assistant domain
(assistant_documents, ADR-0017).

Kinds:

- ``conversation`` — doc ``conversation_id``: the full conversation record
  (messages/turns/actions/accepted)
- ``draft``        — doc ``draft_id``: the full draft record (TTL/consumed
  flags ride in the payload)

``index.json`` is a rebuildable projection: in SQL mode the conversation
index is derived from the conversation documents on demand instead of
being persisted. ``references.json``/``invalidations.json`` stay
file-side (rebuildable reverse index + fence), so their file functions
keep running unchanged in both modes. Preferences live in the identity
profile store, not here.
"""
from __future__ import annotations

from typing import Any, Callable

from app.persistence.documents import DocumentRecord, bridge, sql_enabled


def use_sql() -> bool:
    return sql_enabled("assistant")


def _repo():
    return bridge.repository("assistant")


# -- conversations ----------------------------------------------------------


def load_conversation_payload(student_id: str,
                              conversation_id: str) -> dict[str, Any] | None:
    async def _load() -> Any:
        row = await _repo().get(student_id, conversation_id,
                                kind="conversation")
        return dict(row.payload or {}) if row else None

    return bridge.call(_load)


def save_conversation_payload(student_id: str, record: dict[str, Any]) -> None:
    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=str(record["conversation_id"]), owner_id=student_id,
            kind="conversation", payload=record))

    bridge.call(_put)


def delete_conversation_doc(student_id: str, conversation_id: str) -> bool:
    async def _delete() -> bool:
        return await _repo().delete(student_id, conversation_id,
                                    kind="conversation")

    return bridge.call(_delete)


def list_conversation_payloads(student_id: str,
                               limit: int | None = None
                               ) -> list[dict[str, Any]]:
    async def _list() -> list[dict[str, Any]]:
        rows = await _repo().list_documents(
            student_id, kind="conversation", limit=limit)
        return [dict(r.payload or {}) for r in rows]

    return bridge.call(_list)


# -- drafts -----------------------------------------------------------------


def load_draft_payload(student_id: str, draft_id: str) -> dict[str, Any] | None:
    async def _load() -> Any:
        row = await _repo().get(student_id, draft_id, kind="draft")
        return dict(row.payload or {}) if row else None

    return bridge.call(_load)


def save_draft_payload(student_id: str, record: dict[str, Any]) -> None:
    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=str(record["draft_id"]), owner_id=student_id,
            kind="draft", payload=record))

    bridge.call(_put)


def mutate_draft(student_id: str, draft_id: str,
                 closure: Callable[[dict[str, Any] | None],
                                   dict[str, Any] | None]) -> dict[str, Any] | None:
    """Row-locked read-modify-write; returns the resulting payload (None
    when the row is absent or the closure chose not to write)."""

    def mutate(existing: dict | None) -> dict | None:
        return closure(existing)

    async def _mutate() -> Any:
        record = await _repo().mutate(student_id, draft_id, mutate,
                                      kind="draft")
        return dict(record.payload or {}) if record.epoch else None

    return bridge.call(_mutate)


def delete_draft_doc(student_id: str, draft_id: str) -> bool:
    async def _delete() -> bool:
        return await _repo().delete(student_id, draft_id, kind="draft")

    return bridge.call(_delete)


def list_draft_payloads(student_id: str) -> list[dict[str, Any]]:
    async def _list() -> list[dict[str, Any]]:
        rows = await _repo().list_documents(student_id, kind="draft")
        return [dict(r.payload or {}) for r in rows]

    return bridge.call(_list)


# -- owner scan (startup recovery + draft purge loops) ----------------------


def list_owner_scopes() -> list[tuple[str, str]]:
    """(tenant_id, owner) scopes holding assistant/illustration documents."""
    async def _scopes() -> list[tuple[str, str]]:
        return await _repo().list_owner_scopes(
            kinds=("conversation", "draft"))

    return bridge.call(_scopes)


def list_owners() -> list[str]:
    """Distinct owners holding assistant documents (any kind)."""
    async def _list() -> list[str]:
        return await _repo().list_owners(
            kinds=("conversation", "draft"))

    return bridge.call(_list)
