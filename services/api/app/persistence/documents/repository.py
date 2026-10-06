"""SQL implementation of the document repository over ``<domain>_documents``.

One repository instance per domain (the model class selects the table).
All access is scoped by (tenant_id, owner_id, kind, doc_id); the unique
constraint on those columns is the serialization point for racing inserts.
``mutate`` runs inside one transaction with a row lock (SELECT … FOR UPDATE
on PostgreSQL; sqlite serializes writers database-wide, which the unit lane
accepts).
"""
from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..models.documents import DOCUMENT_MODELS, DomainDocumentBase
from .protocols import DocumentConflictError
from .records import DocumentRecord


def _now() -> float:
    return time.time()


class SqlDocumentRepository:
    """Generic JSONB document access for one domain table."""

    def __init__(self, model: type[DomainDocumentBase] | str,
                 session_factory=None) -> None:
        if isinstance(model, str):
            model = DOCUMENT_MODELS[model]
        self._model = model
        # ``_open_session()`` yields one AsyncSession. Default: the shared
        # DATABASE_URL context manager; tests inject an async_sessionmaker
        # bound to their sqlite engine (both support ``async with``).
        if session_factory is None:
            from .. import db as _db
            self._open_session = _db.session
        else:
            self._open_session = session_factory

    # -- row mapping -------------------------------------------------------

    def _to_record(self, row: DomainDocumentBase) -> DocumentRecord:
        return DocumentRecord(
            doc_id=row.doc_id, owner_id=row.owner_id, kind=row.kind,
            tenant_id=row.tenant_id, payload=dict(row.payload or {}),
            epoch=int(row.epoch), created_at=float(row.created_at),
            updated_at=float(row.updated_at))

    def _key_filter(self, owner_id: str, doc_id: str, kind: str,
                    tenant_id: str):
        m = self._model
        return ((m.tenant_id == tenant_id) & (m.owner_id == owner_id)
                & (m.kind == kind) & (m.doc_id == doc_id))

    # -- protocol operations ----------------------------------------------

    async def put(self, record: DocumentRecord, *,
                  expected_epoch: int | None = None) -> DocumentRecord:
        now = _now()
        async with self._open_session() as sess:
            async with sess.begin():
                row = (await sess.execute(
                    select(self._model)
                    .where(self._key_filter(record.owner_id, record.doc_id,
                                            record.kind, record.tenant_id))
                    .with_for_update())).scalar_one_or_none()
                if row is None:
                    if expected_epoch not in (None, 0):
                        raise DocumentConflictError(
                            f"document {record.kind}/{record.doc_id} absent "
                            f"but expected epoch {expected_epoch}")
                    row = self._model(tenant_id=record.tenant_id,
                                      owner_id=record.owner_id,
                                      kind=record.kind, doc_id=record.doc_id,
                                      payload=record.payload, epoch=1,
                                      created_at=now, updated_at=now)
                    sess.add(row)
                    try:
                        await sess.flush()
                    except IntegrityError:
                        # A racing insert won the unique key: converge by
                        # treating our write as an update of the winner.
                        await sess.rollback()
                        return await self._put_as_update(record, now)
                else:
                    if expected_epoch is not None \
                            and int(row.epoch) != int(expected_epoch):
                        raise DocumentConflictError(
                            f"document {record.kind}/{record.doc_id} epoch "
                            f"{row.epoch} != expected {expected_epoch}")
                    row.payload = record.payload
                    row.epoch = int(row.epoch) + 1
                    row.updated_at = now
                await sess.flush()
                return self._to_record(row)

    async def _put_as_update(self, record: DocumentRecord,
                             now: float) -> DocumentRecord:
        async with self._open_session() as sess:
            async with sess.begin():
                row = (await sess.execute(
                    select(self._model)
                    .where(self._key_filter(record.owner_id, record.doc_id,
                                            record.kind, record.tenant_id))
                    .with_for_update())).scalar_one()
                row.payload = record.payload
                row.epoch = int(row.epoch) + 1
                row.updated_at = now
                await sess.flush()
                return self._to_record(row)

    async def get(self, owner_id: str, doc_id: str, *, kind: str = "doc",
                  tenant_id: str = "") -> DocumentRecord | None:
        async with self._open_session() as sess:
            row = (await sess.execute(
                select(self._model).where(self._key_filter(
                    owner_id, doc_id, kind, tenant_id)))).scalar_one_or_none()
            return self._to_record(row) if row is not None else None

    async def delete(self, owner_id: str, doc_id: str, *, kind: str = "doc",
                     tenant_id: str = "") -> bool:
        async with self._open_session() as sess:
            async with sess.begin():
                result = await sess.execute(
                    sa_delete(self._model).where(self._key_filter(
                        owner_id, doc_id, kind, tenant_id)))
                return bool(result.rowcount)

    async def list_documents(self, owner_id: str, *, kind: str | None = None,
                             tenant_id: str = "",
                             limit: int | None = None) -> list[DocumentRecord]:
        m = self._model
        query = select(m).where(m.owner_id == owner_id,
                                m.tenant_id == tenant_id)
        if kind is not None:
            query = query.where(m.kind == kind)
        query = query.order_by(m.updated_at.desc(), m.id.desc())
        if limit is not None:
            query = query.limit(int(limit))
        async with self._open_session() as sess:
            rows = (await sess.execute(query)).scalars().all()
            return [self._to_record(row) for row in rows]

    async def count_documents(self, owner_id: str, *, kind: str | None = None,
                              tenant_id: str = "") -> int:
        m = self._model
        query = select(func.count()).select_from(m).where(
            m.owner_id == owner_id, m.tenant_id == tenant_id)
        if kind is not None:
            query = query.where(m.kind == kind)
        async with self._open_session() as sess:
            return int(await sess.scalar(query) or 0)

    async def mutate(self, owner_id: str, doc_id: str,
                     mutate: Callable[[dict | None], dict], *,
                     kind: str = "doc",
                     tenant_id: str = "") -> DocumentRecord:
        now = _now()
        async with self._open_session() as sess:
            async with sess.begin():
                row = (await sess.execute(
                    select(self._model)
                    .where(self._key_filter(owner_id, doc_id, kind,
                                            tenant_id))
                    .with_for_update())).scalar_one_or_none()
                existing = dict(row.payload or {}) if row is not None else None
                replacement = mutate(existing)
                if replacement is None:
                    if row is not None:
                        await sess.delete(row)
                        await sess.flush()
                    return DocumentRecord(doc_id=doc_id, owner_id=owner_id,
                                           kind=kind, tenant_id=tenant_id,
                                           epoch=0, updated_at=now)
                if row is None:
                    row = self._model(tenant_id=tenant_id, owner_id=owner_id,
                                      kind=kind, doc_id=doc_id,
                                      payload=replacement, epoch=1,
                                      created_at=now, updated_at=now)
                    sess.add(row)
                else:
                    row.payload = replacement
                    row.epoch = int(row.epoch) + 1
                    row.updated_at = now
                await sess.flush()
                return self._to_record(row)

    async def purge_owner(self, owner_id: str, *,
                          tenant_id: str | None = None) -> int:
        m = self._model
        async with self._open_session() as sess:
            async with sess.begin():
                query = sa_delete(m).where(m.owner_id == owner_id)
                if tenant_id is not None:
                    query = query.where(m.tenant_id == tenant_id)
                result = await sess.execute(query)
                return int(result.rowcount or 0)
