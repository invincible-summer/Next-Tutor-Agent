"""Authorized textbook-scope resolution for Assessment/CAT grounding.

plan.md §5.2: the assessment package stays import-clean (plain data only);
this API-layer helper owns the authorization dance and the single retrieval
call, then hands back a QuizGroundingBundle the /assessment/start endpoint
projects into AssessmentContext:

  1. student_id comes only from the JWT (resolve_student_id upstream);
  2. session_id (optional): load + ownership check (404 = invisible);
  3. textbook_ids (optional): find_textbook_scoped — private must be owned by
     the caller, public readable; group file_ids become the authorized scope;
  4. one KnowledgeSearchTool over that authorized scope;
  5. QuizGroundingProvider.resolve(topic=concept) — the same evidence gate
     and BM25/hybrid retrieval the chat path uses.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.quiz_grounding import QuizGroundingBundle


def _session_scoped_search_tool(student_id: str, session_id: str) -> Any:
    """Build a KnowledgeSearchTool over a session's authorized knowledge space
    (session store + workspace-selected overlay), mirroring _build_tools."""
    from app.core.session import load_session
    session = load_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    owner = getattr(session, "student_id", "") or ""
    # Ownership: a foreign session is invisible (404, no existence leak).
    # Legacy unstamped sessions are treated as the caller's own.
    if owner and owner != student_id:
        raise HTTPException(404, "会话不存在")

    from app.core.embedding import get_embedding_client
    from app.core.knowledge_store import KnowledgeStore
    from app.tools.knowledge_search import KnowledgeSearchTool
    embed = get_embedding_client()
    scoped = None
    if embed is not None:
        from app.core.workspace import scoped_knowledge_stores
        scoped = scoped_knowledge_stores(session) or None
    store = session.knowledge
    files = list(getattr(store, "files", []) or [])
    if session.workspace_id:
        from app.core.workspace import readable_files, readable_stores, workspace_for_session
        ws = workspace_for_session(session)
        if ws:
            ws_stores = readable_stores(ws)
            ws_chunks = [c for _s, st in ws_stores for c in st.chunks]
            if ws_chunks:
                overlay = KnowledgeStore()
                overlay.chunks = list(session.knowledge.chunks) + ws_chunks
                overlay.files = list(session.knowledge.files) + readable_files(ws)
                store = overlay
                files = list(overlay.files)
    tool = KnowledgeSearchTool(
        store, scoped_stores=scoped, embed_client=embed,
        student_id=student_id)
    return tool


def _textbook_scoped_search_tool(student_id: str, textbook_ids: list[str]) -> Any:
    """Build a KnowledgeSearchTool over the selected textbooks' files only.

    Private textbooks must be owned by the caller; public ones are readable
    by everyone.  Anything else is 404 (invisible, not 403)."""
    from app.core.knowledge_store import KnowledgeStore
    from app.core.library import load_library
    from app.core.textbook import PUBLIC_STUDENT_ID, find_textbook_scoped
    from app.tools.knowledge_search import KnowledgeSearchTool

    file_ids: list[str] = []
    files_meta: list[dict[str, Any]] = []
    chunks: list[Any] = []
    lib_cache: dict[str, Any] = {}
    for tb_id in textbook_ids[:8]:
        found = find_textbook_scoped(student_id, tb_id)
        if found is None:
            raise HTTPException(404, "教材不存在")
        record, owner_sid = found
        scope = record.get("scope") or ("public" if owner_sid == PUBLIC_STUDENT_ID else "private")
        if scope == "private" and owner_sid != student_id:
            raise HTTPException(404, "教材不存在")
        vols = (record.get("file_ids") or []) if record.get("kind") == "group" \
            else ([record["file_id"]] if record.get("file_id") else [])
        if not vols:
            continue
        lib = lib_cache.get(owner_sid)
        if lib is None:
            lib = load_library(owner_sid)  # 裸 Library() 不读盘上 files 元数据
            lib_cache[owner_sid] = lib
        for fid in vols:
            if fid in file_ids:
                continue
            file_ids.append(fid)
            chunks.extend(lib.chunks_for(fid))
            meta = lib.find_file(fid)
            if meta:
                files_meta.append(meta)
    if not chunks:
        return None
    store = KnowledgeStore()
    store.chunks = chunks
    store.files = files_meta
    return KnowledgeSearchTool(store, student_id=student_id)


async def build_assessment_grounding(
    *,
    student_id: str,
    concept: str,
    session_id: str = "",
    textbook_ids: list[str] | None = None,
    strict_textbook: bool = False,
) -> QuizGroundingBundle | None:
    """Resolve the authorized textbook evidence bundle for a CAT start.

    Returns None when no textbook scope is available (generic CAT).  Raises
    HTTPException(404) for foreign sessions/textbooks.  strict_textbook with
    no resolvable scope raises HTTPException(400, "textbook_scope_required").
    """
    tb_ids = [str(t).strip() for t in (textbook_ids or []) if str(t).strip()]
    search_tool = None
    if session_id:
        search_tool = _session_scoped_search_tool(student_id, session_id)
    if tb_ids:
        tb_tool = _textbook_scoped_search_tool(student_id, tb_ids)
        search_tool = tb_tool or search_tool
    if search_tool is None:
        if strict_textbook:
            raise HTTPException(400, "textbook_scope_required")
        return None
    from app.core.quiz_grounding import KnowledgeSearchQuizGroundingProvider
    provider = KnowledgeSearchQuizGroundingProvider(
        search_tool, required=strict_textbook, reason="assessment_textbook_scope")
    return await provider.resolve(topic=concept)


def bundle_to_context_fields(bundle: QuizGroundingBundle | None) -> dict[str, Any]:
    """Project a bundle into AssessmentContext's additive grounding kwargs."""
    if bundle is None:
        return {}
    return {
        "grounding_required": bool(bundle.required),
        "grounding_mode": "textbook" if bundle.source_refs else "generic",
        "grounding_tier": bundle.tier,
        "grounding_query": bundle.query,
        "grounding_sources": [r.to_dict() for r in bundle.source_refs[:8]],
    }
