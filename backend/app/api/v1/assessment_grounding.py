from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import HTTPException

from app.core.quiz_grounding import QuizGroundingBundle

logger = logging.getLogger(__name__)
_TEXTBOOK_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assessment-grounding")
_TEXTBOOK_SLOTS = threading.BoundedSemaphore(2)
_GROUNDING_SECONDS = 6.0


def _session_scoped_search_tool(student_id: str, session_id: str) -> Any:
    from app.core.session import load_session
    from app.core.embedding import get_embedding_client
    from app.core.knowledge_store import KnowledgeStore
    from app.tools.knowledge_search import KnowledgeSearchTool
    session = load_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    owner = getattr(session, "student_id", "") or ""
    if owner and owner != student_id:
        raise HTTPException(404, "会话不存在")
    embed = get_embedding_client()
    scoped = None
    if embed is not None:
        from app.core.workspace import scoped_knowledge_stores
        scoped = scoped_knowledge_stores(session) or None
    store = session.knowledge
    if session.workspace_id:
        from app.core.workspace import readable_files, readable_stores, workspace_for_session
        workspace = workspace_for_session(session)
        if workspace:
            chunks = [chunk for _, source in readable_stores(workspace) for chunk in source.chunks]
            if chunks:
                overlay = KnowledgeStore()
                overlay.chunks = list(session.knowledge.chunks) + chunks
                overlay.files = list(session.knowledge.files) + readable_files(workspace)
                store = overlay
    return KnowledgeSearchTool(store, scoped_stores=scoped, embed_client=embed, student_id=student_id)


def _textbook_scoped_search_tool(student_id: str, textbook_ids: list[str]) -> Any:
    from app.core.knowledge_store import KnowledgeStore
    from app.core.library import load_library
    from app.core.textbook import PUBLIC_STUDENT_ID, find_textbook_scoped
    from app.tools.knowledge_search import KnowledgeSearchTool
    file_ids: set[str] = set()
    files_meta = []
    chunks = []
    libraries = {}
    for textbook_id in textbook_ids[:8]:
        found = find_textbook_scoped(student_id, textbook_id)
        if found is None:
            raise HTTPException(404, "教材不存在")
        record, owner = found
        scope = record.get("scope") or ("public" if owner == PUBLIC_STUDENT_ID else "private")
        if scope == "private" and owner != student_id:
            raise HTTPException(404, "教材不存在")
        volumes = (record.get("file_ids") or []) if record.get("kind") == "group" else (
            [record["file_id"]] if record.get("file_id") else [])
        if not volumes:
            continue
        if owner not in libraries:
            libraries[owner] = load_library(owner)
        library = libraries[owner]
        for file_id in volumes:
            if file_id in file_ids:
                continue
            file_ids.add(file_id)
            chunks.extend(library.chunks_for(file_id))
            metadata = library.find_file(file_id)
            if metadata:
                files_meta.append(metadata)
    if not chunks:
        return None
    store = KnowledgeStore()
    store.chunks = chunks
    store.files = files_meta
    return KnowledgeSearchTool(store, student_id=student_id)


async def _resolve_tool(tool, concept: str, strict: bool) -> QuizGroundingBundle | None:
    if tool is None:
        return None
    from app.core.quiz_grounding import KnowledgeSearchQuizGroundingProvider
    provider = KnowledgeSearchQuizGroundingProvider(
        tool, required=strict, reason="assessment_textbook_scope")
    return await provider.resolve(topic=concept)


async def _textbook_bundle(student_id: str, textbook_ids: list[str], concept: str,
                           strict: bool) -> QuizGroundingBundle | None:
    if not _TEXTBOOK_SLOTS.acquire(blocking=False):
        raise TimeoutError("assessment_grounding_busy")

    def run():
        try:
            tool = _textbook_scoped_search_tool(student_id, textbook_ids)
            return asyncio.run(_resolve_tool(tool, concept, strict))
        finally:
            _TEXTBOOK_SLOTS.release()

    try:
        submitted = _TEXTBOOK_POOL.submit(run)
    except BaseException:
        _TEXTBOOK_SLOTS.release()
        raise
    future = asyncio.wrap_future(submitted)
    future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    async with asyncio.timeout(_GROUNDING_SECONDS):
        return await asyncio.shield(future)


async def build_assessment_grounding(*, student_id: str, concept: str,
                                    session_id: str = "",
                                    textbook_ids: list[str] | None = None,
                                    strict_textbook: bool = False) -> QuizGroundingBundle | None:
    textbook_ids = [str(item).strip() for item in (textbook_ids or []) if str(item).strip()]
    try:
        session_tool = None
        if session_id:
            session_tool = _session_scoped_search_tool(student_id, session_id)
        if textbook_ids:
            bundle = await _textbook_bundle(student_id, textbook_ids, concept, strict_textbook)
            if bundle is not None:
                return bundle
        if session_tool is not None:
            async with asyncio.timeout(_GROUNDING_SECONDS):
                return await _resolve_tool(session_tool, concept, strict_textbook)
        if strict_textbook:
            raise HTTPException(400, "textbook_scope_required")
        return None
    except TimeoutError:
        logger.warning("assessment grounding timed out or busy")
        if strict_textbook:
            raise HTTPException(503, "textbook_grounding_unavailable")
        return None


def bundle_to_context_fields(bundle: QuizGroundingBundle | None) -> dict[str, Any]:
    if bundle is None:
        return {}
    return {
        "grounding_required": bool(bundle.required),
        "grounding_mode": "textbook" if bundle.source_refs else "generic",
        "grounding_tier": bundle.tier,
        "grounding_query": bundle.query,
        "grounding_sources": [ref.to_dict() for ref in bundle.source_refs[:8]],
    }