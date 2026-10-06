"""SQL adapter for chat session persistence (chat domain cutover, ADR-0017).

Mirrors the ``app.core.session`` file-mode surface onto the generic
``chat_documents`` table:

- ``kind="session"``  — one row per conversation, ``doc_id`` = session id,
  ``owner_id`` = the session's ``student_id`` (empty = unstamped legacy
  session, same fallback rule as file mode).
- ``kind="trace_ref"`` — tiny ownership rows mapping trace/run id → owner
  so ``trace_owner_index`` resolves without scanning payloads.

Material bytes (uploaded originals, extracted text) stay on the
file/object-store side; ``knowledge_files`` metadata lives inside the
session payload exactly as in file mode.
"""
from __future__ import annotations

from typing import Any

from app.persistence.documents import bridge, sql_enabled


def use_sql() -> bool:
    return sql_enabled("chat")


def _repo():
    return bridge.repository("chat")


# -- session rows -----------------------------------------------------------


def save_session_payload(payload: dict[str, Any]) -> str:
    from app.persistence.documents import DocumentRecord

    session_id = str(payload.get("session_id") or "")
    owner = str(payload.get("student_id") or "")

    async def _put() -> Any:
        existing = await _repo().get(owner, session_id, kind="session")
        record = DocumentRecord.scoped(doc_id=session_id, owner_id=owner,
                                kind="session", payload=payload)
        if existing is not None:
            record.created_at = existing.created_at
        return await _repo().put(record)

    record = bridge.call(_put)
    return str(record.doc_id)


def load_session_payload(session_id: str) -> dict[str, Any] | None:
    """Load one session without knowing its owner (the id is the key).

    The caller re-checks ownership against the payload's ``student_id``
    — identical to the file-mode contract (foreign sessions 404).
    """

    async def _find() -> Any:
        # Unstamped legacy sessions sit under owner ""… but an arbitrary
        # owner may own the id: search the kind, newest first.
        found = await _repo().find_by_doc_id(session_id, kind="session",
                                             limit=1)
        return found[0] if found else None

    record = bridge.call(_find)
    return dict(record.payload) if record is not None else None


def list_session_payloads(student_id: str) -> list[dict[str, Any]]:
    """Full payloads for one owner (account-clearing paths)."""

    async def _list() -> list[dict[str, Any]]:
        records = await _repo().list_documents(student_id, kind="session")
        return [dict(r.payload or {}) for r in records]

    return bridge.call(_list)


def session_exists(session_id: str) -> bool:
    return load_session_payload(session_id) is not None


def save_session_record_created(session: Any) -> bool:
    """True when the session has no SQL row yet (first durable write)."""

    async def _exists() -> bool:
        found = await _repo().find_by_doc_id(session.session_id,
                                             kind="session", limit=1)
        return found is not None and len(found) > 0

    return not bridge.call(_exists)


def list_session_summaries(student_id: str) -> list[dict[str, Any]]:
    """Summaries for one owner, newest first (list_sessions contract)."""

    async def _list() -> list[dict[str, Any]]:
        records = await _repo().list_documents(student_id, kind="session")
        out: list[dict[str, Any]] = []
        for r in records:
            d = r.payload or {}
            out.append({
                "session_id": d.get("session_id", r.doc_id),
                "workspace_id": d.get("workspace_id", ""),
                "student_id": d.get("student_id", student_id),
                "grade": d.get("grade", ""),
                "title": d.get("title", "") or "未命名对话",
                "message_count": len(d.get("messages", [])),
                "round_count": sum(1 for m in d.get("messages", [])
                                   if m.get("role") == "assistant"),
                "quiz_count": len(d.get("quiz_history", [])),
                "file_count": len(d.get("knowledge_files", [])),
                "updated_at": d.get("updated_at", 0),
            })
        return out

    return bridge.call(_list)


def delete_session(session_id: str) -> dict[str, Any] | None:
    """Delete the session row; return its payload (callers clean materials)."""

    async def _delete() -> Any:
        found = await _repo().find_by_doc_id(session_id, kind="session",
                                             limit=1)
        if not found:
            return None
        row = found[0]
        await _repo().delete(row.owner_id, session_id, kind="session")
        await _repo().delete(row.owner_id, session_id, kind="transcript")
        for trace_id in (row.payload or {}).get("trace_ids") or []:
            await _repo().delete(row.owner_id, str(trace_id),
                                 kind="trace_ref")
        return row.payload

    return bridge.call(_delete)


def mutate_session(session_id: str, mutate) -> dict[str, Any] | None:
    """Read-modify-write one session payload; None when absent."""

    async def _mutate() -> Any:
        found = await _repo().find_by_doc_id(session_id, kind="session",
                                             limit=1)
        if not found:
            return None
        row = found[0]

        def wrapped(existing: dict | None):
            replacement = mutate(dict(existing or {}))
            return replacement

        record = await _repo().mutate(row.owner_id, session_id, wrapped,
                                      kind="session")
        return record.payload

    return bridge.call(_mutate)


# -- transcript rows (kind="transcript") -----------------------------------


def append_transcript_lines(session_id: str, lines: list[dict]) -> None:
    """Append serialized transcript lines (crash-safe backup journal)."""
    if not lines:
        return

    async def _append() -> None:
        owner = await _owner_async(session_id)

        def mutate(existing: dict | None):
            merged = list((existing or {}).get("lines") or [])
            merged.extend(lines)
            return {"lines": merged}

        await _repo().mutate(owner, session_id, mutate, kind="transcript")

    bridge.call(_append)


async def _owner_async(session_id: str) -> str:
    found = await _repo().find_by_doc_id(session_id, kind="session", limit=1)
    if found:
        return found[0].owner_id
    found = await _repo().find_by_doc_id(session_id, kind="transcript",
                                         limit=1)
    return found[0].owner_id if found else ""


def get_transcript_lines(session_id: str) -> list[dict]:
    async def _get() -> list[dict]:
        owner = await _owner_async(session_id)
        row = await _repo().get(owner, session_id, kind="transcript")
        return list((row.payload or {}).get("lines") or []) if row else []

    return bridge.call(_get)


def put_transcript_lines(session_id: str, lines: list[dict]) -> None:
    from app.persistence.documents import DocumentRecord

    async def _put() -> None:
        owner = await _owner_async(session_id)
        await _repo().put(DocumentRecord.scoped(
            doc_id=session_id, owner_id=owner, kind="transcript",
            payload={"lines": list(lines)}))

    bridge.call(_put)


def delete_transcript(session_id: str) -> None:
    async def _delete() -> None:
        owner = await _owner_async(session_id)
        await _repo().delete(owner, session_id, kind="transcript")

    bridge.call(_delete)


# -- trace ownership rows ---------------------------------------------------


def add_trace_ref(session_id: str, trace_id: str) -> None:
    """Point a trace_ref row at the session's owner (idempotent insert)."""

    async def _add() -> None:
        found = await _repo().find_by_doc_id(session_id, kind="session",
                                             limit=1)
        if not found:
            return
        owner = found[0].owner_id
        existing = await _repo().get(owner, trace_id, kind="trace_ref")
        if existing is None:
            from app.persistence.documents import DocumentRecord

            await _repo().put(DocumentRecord.scoped(
                doc_id=trace_id, owner_id=owner, kind="trace_ref",
                payload={"session_id": session_id}))

    bridge.call(_add)


def trace_owner_lookup(trace_id: str, default_student_id: str) -> str | None:
    """Owner for one trace id; None when unknown (→ default by caller)."""

    async def _find() -> str | None:
        found = await _repo().find_by_doc_id(trace_id, kind="trace_ref",
                                             limit=1)
        if found:
            return found[0].owner_id or default_student_id
        return None

    return bridge.call(_find)
