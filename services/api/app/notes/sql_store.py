"""SQL adapter for the notes domain (notes_documents, ADR-0017).

Kinds:

- ``vault``     — doc ``index``: the NoteVault persistable (folders/notes/
  custom_templates metadata)
- ``note``      — doc ``note_id``: ``{"content": markdown}``
- ``revisions`` — doc ``note_id``: bounded revision list (append + prune in
  one row-locked mutate; mirrors the file mode's keep-last-N snapshots)
- ``agent``     — doc ``note_id`` (or ``_vault`` for vault-level chats):
  per-note assistant state

Uploads (manifest + bytes) stay on the file/object-store side; the
legacy read-only suggestions file is never written and stays file-side.
"""
from __future__ import annotations

from typing import Any

from app.persistence.documents import bridge, sql_enabled

VAULT_DOC = "index"
VAULT_LEVEL_AGENT_DOC = "_vault"


def use_sql() -> bool:
    return sql_enabled("notes")


def _repo():
    return bridge.repository("notes")


# -- vault index ------------------------------------------------------------


def load_vault_payload(student_id: str) -> dict[str, Any] | None:
    async def _load() -> Any:
        row = await _repo().get(student_id, VAULT_DOC, kind="vault")
        return dict(row.payload or {}) if row else None

    return bridge.call(_load)


def save_vault_payload(student_id: str, payload: dict[str, Any]) -> None:
    from app.persistence.documents import DocumentRecord

    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=VAULT_DOC, owner_id=student_id, kind="vault",
            payload=payload))

    bridge.call(_put)


# -- note content -----------------------------------------------------------


def read_note_content(student_id: str, note_id: str) -> str:
    async def _read() -> str:
        row = await _repo().get(student_id, note_id, kind="note")
        return str((row.payload or {}).get("content") or "") if row else ""

    return bridge.call(_read)


def write_note_content(student_id: str, note_id: str, content: str) -> None:
    from app.persistence.documents import DocumentRecord

    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=note_id, owner_id=student_id, kind="note",
            payload={"content": content}))

    bridge.call(_put)


# -- revisions --------------------------------------------------------------


def save_revision(student_id: str, note_id: str, revision: int, ts: float,
                  author: str, content: str, keep: int) -> None:
    """Append one revision and prune to the last ``keep`` in one mutate."""

    def mutate(existing: dict | None) -> dict:
        items = list((existing or {}).get("items") or [])
        items.append({"revision": int(revision), "ts": float(ts),
                      "author": author or "user", "content": content})
        items.sort(key=lambda r: int(r.get("revision") or 0))
        return {"items": items[-max(int(keep), 1):]}

    async def _mutate() -> None:
        await _repo().mutate(student_id, note_id, mutate, kind="revisions")

    bridge.call(_mutate)


def list_revisions(student_id: str, note_id: str) -> list[dict[str, Any]]:
    async def _list() -> list[dict[str, Any]]:
        row = await _repo().get(student_id, note_id, kind="revisions")
        return list((row.payload or {}).get("items") or []) if row else []

    return bridge.call(_list)


def read_revision(student_id: str, note_id: str,
                  revision: int) -> str | None:
    items = list_revisions(student_id, note_id)
    for item in items:
        if int(item.get("revision") or 0) == int(revision):
            return str(item.get("content") or "")
    return None


def delete_note_everything(student_id: str, note_id: str) -> None:
    """Content + revisions + agent state for one note."""

    async def _delete() -> None:
        for kind in ("note", "revisions", "agent"):
            await _repo().delete(student_id, note_id, kind=kind)

    bridge.call(_delete)


# -- per-note agent state ---------------------------------------------------


def load_agent_state(student_id: str, note_id: str) -> dict[str, Any] | None:
    doc = note_id or VAULT_LEVEL_AGENT_DOC

    async def _load() -> Any:
        row = await _repo().get(student_id, doc, kind="agent")
        return dict(row.payload or {}) if row else None

    return bridge.call(_load)


def save_agent_state(student_id: str, note_id: str,
                     state: dict[str, Any]) -> None:
    from app.persistence.documents import DocumentRecord

    doc = note_id or VAULT_LEVEL_AGENT_DOC

    async def _put() -> None:
        await _repo().put(DocumentRecord.scoped(
            doc_id=doc, owner_id=student_id, kind="agent", payload=state))

    bridge.call(_put)


def delete_agent_state(student_id: str, note_id: str) -> bool:
    if not note_id:
        return False

    async def _delete() -> bool:
        return await _repo().delete(student_id, note_id, kind="agent")

    return bridge.call(_delete)
