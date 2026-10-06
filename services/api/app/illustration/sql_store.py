"""SQL adapter for illustration / tool-assistant documents (ADR-0017).

The illustration kinds ride the ``assistant_documents`` table — the shared
owner+kind+resource-id key space reserved in
``persistence/models/documents.py`` ("assistant also serves the
illustration/tool-assistant kinds"). Site-assistant conversations/drafts and
the six illustration kinds below never collide, and both cut over together
under ``DOMAIN_DOCUMENT_BACKENDS=assistant=sql``.

Kinds: ``jobs`` / ``runs`` / ``artifacts`` / ``sessions`` /
``scenario_jobs`` / ``scenario_revisions``.

Preview PNG bytes stay on the file layer (object-store side), and the owner
epoch marker stays a FILE in both modes: it is the cross-process purge fence
that must survive the row purge itself — same argument as the classroom
tombstone. Closures running through :func:`mutate_payload` execute on the
bridge worker loop and must never call back into ``bridge.call``.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.persistence.documents import DocumentRecord, bridge, sql_enabled

#: Every document kind this store owns (routing, owner scans, importer).
KINDS = ("jobs", "runs", "artifacts", "sessions", "scenario_jobs",
         "scenario_revisions")


def use_sql() -> bool:
    return sql_enabled("assistant")


def _repo():
    return bridge.repository("assistant")


def load_payload(owner: str, kind: str, key: str) -> dict[str, Any] | None:
    async def _get() -> dict[str, Any] | None:
        row = await _repo().get(owner, key, kind=kind)
        return dict(row.payload or {}) if row else None

    return bridge.call(_get)


def save_payload(owner: str, kind: str, key: str,
                 value: dict[str, Any]) -> None:
    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=key, owner_id=owner, kind=kind, payload=value))

    bridge.call(_put)


def mutate_payload(owner: str, kind: str, key: str,
                   closure: Callable[[dict[str, Any] | None],
                                     dict[str, Any]]) -> dict[str, Any]:
    """Row-locked read-modify-write; an absent row passes ``None`` to the
    closure (illustration closures seed their own defaults) and a returned
    dict is the replacement payload."""

    async def _mutate() -> dict[str, Any]:
        record = await _repo().mutate(owner, key, closure, kind=kind)
        return dict(record.payload or {})

    return bridge.call(_mutate)


def delete_doc(owner: str, kind: str, key: str) -> None:
    async def _delete() -> None:
        await _repo().delete(owner, key, kind=kind)

    bridge.call(_delete)


def list_payloads(owner: str, kind: str) -> list[dict[str, Any]]:
    async def _list() -> list[dict[str, Any]]:
        rows = await _repo().list_documents(owner, kind=kind)
        return [dict(row.payload or {}) for row in rows]

    return bridge.call(_list)


def list_owners() -> list[str]:
    """Distinct owners holding any illustration document."""
    async def _owners() -> list[str]:
        return await _repo().list_owners(kinds=KINDS)

    return bridge.call(_owners)


def purge_owner(owner: str) -> int:
    """Delete every illustration row of one owner (account purge lane)."""
    async def _purge() -> int:
        return await _repo().purge_owner(owner, tenant_id=None)

    return bridge.call(_purge)
