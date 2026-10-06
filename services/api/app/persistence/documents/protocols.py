"""Document repository protocol (domain persistence, ADR-0017).

Every domain that cuts over to PostgreSQL implements its storage against
this protocol: a File adapter over the domain's existing store (migration
window / rollback) or the SQL adapter over ``<domain>_documents``. Domain
services never see SQLAlchemy types.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .records import DocumentRecord


class DocumentRepositoryError(Exception):
    """Storage-level failure surfaced to the domain."""


class DocumentConflictError(DocumentRepositoryError):
    """A compare-and-swap write lost (epoch mismatch) — re-read and retry."""


class DocumentNotFoundError(DocumentRepositoryError):
    """The addressed document does not exist."""


@runtime_checkable
class DocumentRepository(Protocol):
    """Keyed JSON document access, scoped by tenant + owner + kind.

    Keys are ``(tenant_id, owner_id, kind, doc_id)``; every method filters
    on all four (``kind=None`` widens to any kind in list/count calls only).
    """

    async def put(self, record: DocumentRecord, *,
                  expected_epoch: int | None = None) -> DocumentRecord:
        """Insert or update. With ``expected_epoch`` the write is a
        compare-and-swap: it fails with DocumentConflictError when the
        stored epoch differs (absent row compares as 0)."""
        ...

    async def get(self, owner_id: str, doc_id: str, *, kind: str = "doc",
                  tenant_id: str = "") -> DocumentRecord | None: ...

    async def delete(self, owner_id: str, doc_id: str, *, kind: str = "doc",
                     tenant_id: str = "") -> bool:
        """True when a row was removed; missing rows are not an error."""
        ...

    async def list_documents(self, owner_id: str, *, kind: str | None = None,
                             tenant_id: str = "",
                             limit: int | None = None,
                             ) -> list[DocumentRecord]:
        """Owner's documents, newest ``updated_at`` first."""
        ...

    async def count_documents(self, owner_id: str, *,
                              kind: str | None = None,
                              tenant_id: str = "") -> int: ...

    async def mutate(self, owner_id: str, doc_id: str,
                     mutate: Callable[[dict | None], dict], *,
                     kind: str = "doc",
                     tenant_id: str = "") -> DocumentRecord:
        """Atomic read-modify-write inside one row lock.

        ``mutate`` receives the stored payload (None when absent) and
        returns the replacement; returning None deletes the document.
        Concurrent mutators serialize; the row's epoch bumps.
        """
        ...

    async def purge_owner(self, owner_id: str, *,
                          tenant_id: str | None = None) -> int:
        """Delete every document of the owner (optionally tenant-scoped);
        returns the removed count. Account-purge path."""
        ...
