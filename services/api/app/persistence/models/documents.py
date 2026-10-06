"""Domain document tables (JSONB document persistence, ADR-0017).

One generic table per business domain — chat, library, textbooks, notes,
assessment, evidence, classroom, orchestration, assistant (assistant also
serves the illustration/tool-assistant kinds; both key on owner + kind +
resource id). The shape is deliberately identical across tables:

- ``tenant_id`` / ``owner_id`` — isolation keys (WS5c); the empty string is
  the pre-tenant legacy scope, never NULL (keeps the unique constraint
  meaningful on both PostgreSQL and sqlite).
- ``kind`` — resource subtype inside the domain (conversation, note, job…)
- ``doc_id`` — the domain's resource id (session id, note id, lesson id…)
- ``payload`` — the whole JSON document; domains own its schema versioning
- ``epoch`` — optimistic-concurrency counter (compare-and-swap writes)

Byte blobs never live here: they go to the object store; derived indexes
(BM25/KG/embeddings) stay on the file layer as rebuildable caches.
"""
from __future__ import annotations

from sqlalchemy import (BigInteger, Float, Index, Integer, JSON, String,
                        UniqueConstraint)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

#: Canonical domain order (importers, routing config and docs share it).
DOCUMENT_DOMAINS = ("chat", "library", "textbooks", "notes", "assessment",
                    "evidence", "classroom", "orchestration", "assistant")


def _json_variant():
    """JSONB on PostgreSQL, portable JSON elsewhere."""
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


def _id_variant():
    """BigInteger ids on PostgreSQL, Integer under sqlite."""
    return BigInteger().with_variant(Integer(), "sqlite")


class DomainDocumentBase(Base):
    """Abstract column shape shared by every domain document table."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(_id_variant(), primary_key=True,
                                    autoincrement=True)
    # Empty tenant_id is the legacy pre-tenant scope, not NULL.
    tenant_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    kind: Mapped[str] = mapped_column(String(48), default="doc")
    doc_id: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[dict] = mapped_column(_json_variant(), default=dict)
    # Illustration-style optimistic concurrency; domains that don't care
    # simply leave it at its automatic bump.
    epoch: Mapped[int] = mapped_column(_id_variant(), default=1)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)


def _document_model(domain: str) -> type[DomainDocumentBase]:
    """Build one concrete ``<domain>_documents`` model from the base shape."""
    table = f"{domain}_documents"
    return type(
        f"{domain.capitalize()}DocumentModel",
        (DomainDocumentBase,),
        {
            "__tablename__": table,
            "__table_args__": (
                UniqueConstraint("tenant_id", "owner_id", "kind", "doc_id",
                                 name=f"uq_{table}_scope_key"),
                Index(f"ix_{table}_owner_kind", "owner_id", "kind"),
                Index(f"ix_{table}_tenant_owner", "tenant_id", "owner_id"),
            ),
        },
    )


#: domain name -> mapped model class (single mapping per table)
DOCUMENT_MODELS: dict[str, type[DomainDocumentBase]] = {
    domain: _document_model(domain) for domain in DOCUMENT_DOMAINS
}

ChatDocumentModel = DOCUMENT_MODELS["chat"]
LibraryDocumentModel = DOCUMENT_MODELS["library"]
TextbooksDocumentModel = DOCUMENT_MODELS["textbooks"]
NotesDocumentModel = DOCUMENT_MODELS["notes"]
AssessmentDocumentModel = DOCUMENT_MODELS["assessment"]
EvidenceDocumentModel = DOCUMENT_MODELS["evidence"]
ClassroomDocumentModel = DOCUMENT_MODELS["classroom"]
OrchestrationDocumentModel = DOCUMENT_MODELS["orchestration"]
AssistantDocumentModel = DOCUMENT_MODELS["assistant"]
