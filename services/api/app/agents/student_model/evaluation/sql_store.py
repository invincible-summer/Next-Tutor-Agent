"""SQL adapter for the learner evidence journal + student profile (ADR-0017).

evidence_documents kinds:

- ``journal`` — doc = student id: the full JSONL transaction log as a
  ``{"lines": [...]}`` payload. The row lock is the cross-process
  serialization point (the file mode's ``file_lock`` equivalent), so the
  whole read→derive→append cycle runs inside one ``mutate``.
- ``profile`` — doc = student id: the ``student_model/store.py`` blob
  (``{"version": 2, "profile": …}``).

Derived projections (.index.json / .learner_views.json) stay file-side as
rebuildable caches.
"""
from __future__ import annotations

from typing import Any, Callable

from app.persistence.documents import bridge, sql_enabled


def use_sql() -> bool:
    return sql_enabled("evidence")


def _repo():
    return bridge.repository("evidence")


def read_journal_lines(student_id: str) -> tuple[list[str], tuple | None]:
    """(lines, cache_signature) for one journal row; ([], None) when absent."""

    async def _read() -> tuple[list[str], tuple | None]:
        row = await _repo().get(student_id, student_id, kind="journal")
        if row is None:
            return [], None
        lines = [str(l) for l in (row.payload or {}).get("lines") or []]
        return lines, (int(row.epoch), float(row.updated_at))

    return bridge.call(_read)


def mutate_journal(student_id: str,
                   mutate: Callable[[list[str] | None], list[str]]) -> Any:
    """Atomic read-modify-write over the journal lines.

    ``mutate`` receives the current lines (None when the row is absent)
    and returns the replacement list; raising inside it rolls back.
    """

    async def _run() -> Any:
        def wrapped(existing: dict | None):
            lines = mutate(None if existing is None
                           else [str(l) for l in existing.get("lines") or []])
            return {"lines": list(lines)}

        return await _repo().mutate(student_id, student_id, wrapped,
                                    kind="journal")

    return bridge.call(_run)


# -- student profile blob ---------------------------------------------------


def load_profile_blob(student_id: str) -> dict[str, Any] | None:
    async def _load() -> Any:
        row = await _repo().get(student_id, student_id, kind="profile")
        return dict(row.payload or {}) if row else None

    return bridge.call(_load)


def save_profile_blob(student_id: str, blob: dict[str, Any]) -> None:
    from app.persistence.documents import DocumentRecord

    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=student_id, owner_id=student_id, kind="profile",
            payload=blob))

    bridge.call(_put)
