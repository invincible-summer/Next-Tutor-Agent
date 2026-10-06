"""JSONB document persistence for the nine business domains (ADR-0017).

Activated per domain by configuration (``DOMAIN_DOCUMENT_BACKENDS`` +
``DATABASE_URL``): SQL serves facts, the domain's file store remains the
rollback path until its cutover is verified. Domain services consume the
protocol + records; SQLAlchemy stays inside ``repository.py``.
"""
from __future__ import annotations

from ..models.documents import DOCUMENT_DOMAINS
from . import bridge  # noqa: F401  (re-exported for domain adapters)
from .config import backend_for, sql_enabled
from .context import (current_tenant, reset_tenant, set_tenant,
                      tenant_scope)
from .protocols import (DocumentConflictError, DocumentNotFoundError,
                        DocumentRepository, DocumentRepositoryError)
from .records import DocumentRecord
from .repository import SqlDocumentRepository

__all__ = [
    "DOCUMENT_DOMAINS",
    "DocumentRecord", "DocumentRepository", "DocumentRepositoryError",
    "DocumentConflictError", "DocumentNotFoundError",
    "SqlDocumentRepository", "backend_for", "sql_enabled",
    "bridge", "current_tenant", "reset_tenant", "set_tenant",
    "tenant_scope",
    "get_document_repository",
]

_repo_cache: dict[str, SqlDocumentRepository] = {}


def get_document_repository(domain: str) -> SqlDocumentRepository:
    """Cached SQL repository for one domain (enterprise mode only)."""
    cached = _repo_cache.get(domain)
    if cached is None:
        cached = SqlDocumentRepository(domain)
        _repo_cache[domain] = cached
    return cached


def reset_repository_cache() -> None:
    """Tests: drop cached repositories after retargeting DATABASE_URL."""
    _repo_cache.clear()
