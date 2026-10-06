"""SQL adapters for the library/textbooks storage family (ADR-0017).

Two domain tables, one storage family (both live under the ``library/``
runtime root):

- ``library_documents``   — kind ``index``, doc ``<student_key>``: the
  Library folders/files aggregate (``core/library.py``).
- ``textbooks_documents`` — kind ``registry``, doc ``<student_key>``: the
  textbook registration array (``core/textbook.py``).

The extracted texts (``<fid>.txt``) and original binaries stay on the
file/object-store layer — the process-level chunk cache keys on the .txt
mtime, and bytes never enter JSONB. Both modules keep whole-document
load/modify/save semantics (the file mode's read-then-write profile);
SQL ``put`` is the same last-writer-wins under the row lock.
"""
from __future__ import annotations

from typing import Any

from app.persistence.documents import DocumentRecord, bridge, sql_enabled


def library_use_sql() -> bool:
    return sql_enabled("library")


def textbooks_use_sql() -> bool:
    return sql_enabled("textbooks")


# -- library_documents (kind "index") ---------------------------------------


def _library_repo():
    return bridge.repository("library")


def load_index(key: str) -> dict[str, Any] | None:
    async def _get() -> dict[str, Any] | None:
        row = await _library_repo().get(key, "index", kind="index")
        return dict(row.payload or {}) if row else None

    return bridge.call(_get)


def save_index(key: str, payload: dict[str, Any]) -> None:
    async def _put() -> None:
        await _library_repo().put(DocumentRecord(
            doc_id="index", owner_id=key, kind="index", payload=payload))

    bridge.call(_put)


def delete_index(key: str) -> None:
    async def _delete() -> None:
        await _library_repo().delete(key, "index", kind="index")

    bridge.call(_delete)


def library_owners() -> list[str]:
    async def _owners() -> list[str]:
        return await _library_repo().list_owners(kinds=("index",))

    return bridge.call(_owners)


# -- textbooks_documents (kind "registry") ----------------------------------


def _textbooks_repo():
    return bridge.repository("textbooks")


def load_registry(key: str) -> dict[str, Any] | None:
    async def _get() -> dict[str, Any] | None:
        row = await _textbooks_repo().get(key, "registry", kind="registry")
        return dict(row.payload or {}) if row else None

    return bridge.call(_get)


def save_registry(key: str, payload: dict[str, Any]) -> None:
    async def _put() -> None:
        await _textbooks_repo().put(DocumentRecord(
            doc_id="registry", owner_id=key, kind="registry",
            payload=payload))

    bridge.call(_put)


def delete_registry(key: str) -> None:
    async def _delete() -> None:
        await _textbooks_repo().delete(key, "registry", kind="registry")

    bridge.call(_delete)


def registry_owners() -> list[str]:
    async def _owners() -> list[str]:
        return await _textbooks_repo().list_owners(kinds=("registry",))

    return bridge.call(_owners)
