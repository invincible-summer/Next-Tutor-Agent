"""Chat API: SSE streaming + file upload + session history CRUD.

SSE events are sent directly (not through Next.js proxy, which buffers). File
upload parses PDF/DOCX/PPTX/TXT server-side, chunks, and indexes into the
session's BM25 knowledge store. Sessions persist as JSON for resume.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.identity.deps import resolve_student_id
from app.agents.student_model.store import DEFAULT_STUDENT_ID
from app.core.file_parser import MAX_IMAGE_BYTES, MAX_UPLOAD_BYTES
from app.core.multimodal_parser import SUPPORTED_ASYNC_EXTS, extract_text_async
from app.core.ocr import understand_image, is_image_file
from app.core.session import (TutorSession, list_sessions,
                              load_session, rename_session, save_session,
                              set_session_grade)
from app.schemas.chat import (ChatRequest, RenameRequest, SessionItem,
                              SessionListResponse, SessionPatchRequest,
                              UploadResponse, UploadResult)

router = APIRouter(prefix="/chat", tags=["chat"])


def _session_owned_by(session: TutorSession, student_id: str) -> bool:
    """Legacy sessions (no student_id stamp) belong to the shared guest."""
    return (session.student_id or DEFAULT_STUDENT_ID) == student_id


def _load_owned_session(session_id: str, student_id: str) -> TutorSession:
    """Load a session and enforce ownership (404 = invisible, not 403)."""
    session = load_session(session_id)
    if session is None or not _session_owned_by(session, student_id):
        raise HTTPException(404, "会话不存在")
    return session


def _validate_workspace_binding(workspace_id: str | None, student_id: str) -> None:
    if not workspace_id:
        return
    from app.core.workspace import load_workspace
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    ws = load_workspace(workspace_id)
    if ws is None or (ws.student_id or DEFAULT_STUDENT_ID) != student_id:
        raise HTTPException(404, "工作学习区不存在")


def _build_tools(session: TutorSession):
    """Wire tools for a session (knowledge_search needs the session's store).

    If the session belongs to a workspace, merge the workspace's SELECTED
    library sources (its exclusive folder + picked folders/files) into the
    session's store so knowledge_search searches both session-level and
    workspace-readable materials — unselected library files stay invisible.
    When the embedding track is configured, knowledge_search additionally
    gets the scoped (session/folder/file) stores for hybrid retrieval;
    otherwise the BM25 overlay alone remains the whole retrieval path.
    """
    from app.core.llm_async import get_llm
    from app.tools.knowledge_search import KnowledgeSearchTool
    from app.tools.knowledge_read import KnowledgeReadTool
    from app.tools.quiz import GenerateQuizTool
    from app.tools.fit_quiz import FitQuizTool
    from app.tools.recall_history import RecallHistoryTool
    llm = get_llm()
    # Hybrid track: scoped stores + embed client (None when unconfigured).
    from app.core.embedding import get_embedding_client
    embed = get_embedding_client()
    scoped = None
    if embed is not None:
        from app.core.workspace import scoped_knowledge_stores
        scoped = scoped_knowledge_stores(session) or None
    # Merge workspace-readable knowledge if applicable.
    # Prior quiz stems feed generate_quiz's anti-repeat list so successive
    # turns don't re-issue the same canonical question.
    avoid_stems = [
        str(q.get("stem", "")).strip()[:40]
        for qh in (session.quiz_history or [])[-3:]
        for q in ((qh.get("questions") or []) if isinstance(qh, dict) else [])
        if isinstance(q, dict) and str(q.get("stem", "")).strip()
    ]
    if session.workspace_id:
        from app.core.workspace import readable_files, readable_stores, workspace_for_session
        ws = workspace_for_session(session)
        if ws:
            ws_stores = readable_stores(ws)
            ws_chunks = [c for _s, st in ws_stores for c in st.chunks]
            if ws_chunks:
                # Create a SEPARATE KnowledgeStore copy so workspace chunks are
                # NOT persisted to the session file. Temporary overlay for
                # knowledge_search tool only.
                from app.core.knowledge_store import KnowledgeStore
                overlay = KnowledgeStore()
                overlay.chunks = list(session.knowledge.chunks) + ws_chunks
                overlay.files = list(session.knowledge.files) + readable_files(ws)
                return [
                    KnowledgeSearchTool(overlay, scoped_stores=scoped, embed_client=embed,
                                        student_id=getattr(session, "student_id", "") or ""),
                    KnowledgeReadTool(overlay, scoped_stores=scoped),
                    GenerateQuizTool(llm, avoid_stems=avoid_stems),
                    FitQuizTool(llm),
                    RecallHistoryTool(session.session_id,
                                      getattr(session, "student_id", "") or "",
                                      getattr(session, "workspace_id", "") or ""),
                ]
    return [
        KnowledgeSearchTool(session.knowledge, scoped_stores=scoped, embed_client=embed,
                            student_id=getattr(session, "student_id", "") or ""),
        KnowledgeReadTool(session.knowledge, scoped_stores=scoped),
        GenerateQuizTool(llm, avoid_stems=avoid_stems),
        FitQuizTool(llm),
        RecallHistoryTool(session.session_id,
                          getattr(session, "student_id", "") or "",
                          getattr(session, "workspace_id", "") or ""),
    ]


@router.post("/stream")
async def chat_stream(req: ChatRequest, student_id: str = Depends(resolve_student_id)):
    """SSE endpoint for conversational chat with tool-calling."""
    from app.agents.chat_agent import run_turn
    # P1: normalize 「自动」-> "" (auto) at the API boundary so the session
    # stores the canonical sentinel regardless of which client sent it.
    from app.agents.teaching_engine.stage_profile import normalize_grade
    req.grade = normalize_grade(req.grade)

    session = None
    if req.session_id:
        session = load_session(req.session_id)
        # Ownership: a stamped session belongs to its owner only. A foreign
        # id is invisible (404, no existence leak) and its student_id stamp
        # is NEVER overwritten with the caller's identity.
        if session is not None and session.student_id \
                and session.student_id != student_id:
            raise HTTPException(404, "会话不存在")
    if session is None:
        session = TutorSession(grade=req.grade)
    else:
        # Three-level grade resolution: session-level choice (persisted) wins
        # over the store default; "" (auto) is the lowest priority. req.grade
        # "" therefore keeps an existing explicit stage rather than clearing it
        # (the in-session switch path goes through PATCH, which persists first).
        session.grade = req.grade or session.grade
    # M0: bind the resolved student identity to the session so it persists
    # across turns and M1-M9 hooks use the correct data namespace. For an
    # unstamped legacy session this also claims it for the caller.
    session.student_id = student_id
    # Persist the user's explicit answer-language choice on the session so
    # resumed conversations remember it (None = auto mode).
    session.output_language = req.output_language
    if not session.title and req.message:
        session.title = req.message[:20]

    # Eagerly assign the session id BEFORE building tools + before workspace
    # binding. Paper_Agent-style: a stable, timestamped id created at first
    # turn so the transcript file + recall_history share it from turn 1 onward.
    # save_session keeps an existing id (only assigns if empty), so this is
    # safe for resumed sessions (which already carry their id).
    from app.core.session import new_session_id
    if not session.session_id:
        session.session_id = new_session_id(session.title or req.message or "untitled")

    # Bind session to a workspace if specified (new chat within a workspace).
    # This must run AFTER session_id assignment so the workspace gets the real id.
    if req.workspace_id and not session.workspace_id:
        _validate_workspace_binding(req.workspace_id, student_id)
        session.workspace_id = req.workspace_id
        from app.core.workspace import add_session_to_workspace
        add_session_to_workspace(req.workspace_id, session.session_id)

    tools = _build_tools(session)
    progress_queue: asyncio.Queue = asyncio.Queue()

    def progress_cb(msg: str):
        progress_queue.put_nowait(("progress", msg))

    async def event_stream():
        async def run_chat():
            async for event in run_turn(req.message, session, tools, progress_cb=progress_cb, lang=req.lang, output_language=req.output_language, attachments=req.attachments, student_id=student_id):
                # Bind the conversation: stamp the stable session_id onto the
                # done event so the frontend persists it immediately. The
                # follow-up history_saved is a backup; an early return in the
                # frontend done-handler used to skip it, so every turn started
                # a fresh session and multi-turn memory was lost.
                if event.get("type") == "done":
                    event["session_id"] = session.session_id
                if event.get("type") == "done" and event.get("trace_id"):
                    if event["trace_id"] not in session.trace_ids:
                        session.trace_ids.append(event["trace_id"])
                        # Lightweight immediate trace_id persistence (Paper_Agent
                        # add_trace_id) — avoids a full-session rewrite and the
                        # one-turn lag where chat_turn's save_session ran before
                        # this done-event trace_id was appended.
                        from app.core.session import add_trace_id
                        add_trace_id(session.session_id, event["trace_id"])
                await progress_queue.put(("event", event))
            await progress_queue.put(("saved", session.session_id))

        task = asyncio.create_task(run_chat())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(progress_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    hb = {"type": "heartbeat", "elapsed": 15}
                    yield f"event: heartbeat\ndata: {json.dumps(hb, ensure_ascii=False)}\n\n"
                    continue
                kind, payload = item
                if kind == "event":
                    yield f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                elif kind == "progress":
                    msg = {"type": "tool_progress", "message": payload}
                    yield f"event: tool_progress\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                elif kind == "saved":
                    done_data = {"type": "history_saved", "session_id": payload}
                    yield f"event: history_saved\ndata: {json.dumps(done_data, ensure_ascii=False)}\n\n"
                    break
            await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@router.post("/upload", response_model=UploadResponse,
             dependencies=[Depends(rate_limit("chat_upload", 30))])
async def upload_files(session_id: str | None = None, grade: str = "",
                       workspace_id: str | None = None,
                       files: list[UploadFile] = File(...),
                       student_id: str = Depends(resolve_student_id)):
    """Upload course materials (PDF/DOCX/PPTX/TXT). Parses, chunks, indexes."""
    session = None
    if session_id:
        session = load_session(session_id)
        # Ownership: never attach files to another user's session (404, no
        # existence leak), mirroring /chat/stream.
        if session is not None and session.student_id \
                and session.student_id != student_id:
            raise HTTPException(404, "会话不存在")
    if session is None:
        session = TutorSession(grade=grade)
    # Stamp the caller's identity: new sessions get owned immediately (an
    # upload-before-first-message flow otherwise leaves an unowned session);
    # unstamped legacy sessions are claimed, foreign stamps are untouched.
    if not session.student_id:
        session.student_id = student_id

    # Eagerly assign the session id BEFORE workspace binding (same pattern as
    # chat_stream): previously a "upload attachment first, then send the first
    # message" flow created an orphan session with no workspace binding, and
    # chat_stream's binding logic never ran again because the session_id
    # already existed -- the session could never read workspace shared files.
    from app.core.session import new_session_id
    if not session.session_id:
        topic = session.title or (files[0].filename if files else "") or "untitled"
        session.session_id = new_session_id(topic)

    # Bind session to a workspace if specified and not already bound.
    if workspace_id and not session.workspace_id:
        _validate_workspace_binding(workspace_id, student_id)
        session.workspace_id = workspace_id
        from app.core.workspace import add_session_to_workspace
        add_session_to_workspace(workspace_id, session.session_id)

    results: list[UploadResult] = []
    uploaded: list[tuple[str, str, str]] = []  # (file_id, filename, text)
    for f in files:
        raw = await f.read()
        fname = f.filename or "upload"
        lower = fname.lower()
        ext = next((e for e in SUPPORTED_ASYNC_EXTS if lower.endswith(e)), "")
        if not ext:
            results.append(UploadResult(filename=fname,
                                        error="不支持的格式（仅 PDF/DOCX/PPTX/TXT/MD/常见图片）"))
            continue
        limit = MAX_IMAGE_BYTES if ext in (".png", ".jpg", ".jpeg", ".webp",
                                           ".bmp", ".tiff", ".tif") else MAX_UPLOAD_BYTES
        if len(raw) > limit:
            results.append(UploadResult(filename=fname,
                                        error=f"文件过大（>{limit//(1024*1024)}MB）"))
            continue
        extracted = await extract_text_async(fname, raw, purpose="chat")
        text = extracted.text
        if not text.strip():
            results.append(UploadResult(filename=fname,
                                        error="无法提取文本",
                                        warning=extracted.warning or None,
                                        ocr_used=extracted.used_ocr))
            continue
        file_id = uuid.uuid4().hex[:12]
        # Keep the original binary alongside the extracted text for re-download.
        meta = session.knowledge.add_file(
            file_id, fname, text, raw=raw, orig_ext=ext,
            metadata={"source_scope": "session",
                      "source_visibility": "session_private",
                      "ocr_used": extracted.used_ocr,
                      "ocr_pages": extracted.ocr_pages,
                      "media_count": extracted.media_count})
        if file_id not in session.pending_material_file_ids:
            session.pending_material_file_ids.append(file_id)
        uploaded.append((file_id, fname, text))
        results.append(UploadResult(id=file_id, filename=fname,
                                    char_count=meta["char_count"],
                                    chunk_count=meta["chunk_count"],
                                    warning=extracted.warning or None,
                                    ocr_used=extracted.used_ocr,
                                    source_scope="session",
                                    source_visibility="session_private",
                                    preview_text=(text[:4000] if ext in (
                                        ".png", ".jpg", ".jpeg", ".webp", ".bmp",
                                        ".tiff", ".tif") else None)))
    save_session(session)
    await _post_upload_ingest(
        scope=f"session:{session.session_id}", store=session.knowledge,
        uploaded=uploaded,
        summarize=lambda fid, fname, text: _schedule_session_summary(
            session.session_id, fid, fname, text))
    return UploadResponse(results=results, session_id=session.session_id)


def _schedule_session_summary(session_id: str, file_id: str,
                              filename: str, text: str) -> None:
    from app.core.file_summary import schedule_session_file_summary
    schedule_session_file_summary(session_id, file_id, filename, text)


class AttachLibraryRequest(BaseModel):
    file_ids: list[str]


@router.post("/sessions/{session_id}/attach_library")
async def attach_library_files(session_id: str, req: AttachLibraryRequest,
                               workspace_id: str | None = None,
                               grade: str = "",
                               student_id: str = Depends(resolve_student_id)):
    """Attach library files to ONE session as session-private references.

    Copies (not links) the extracted text + original binary into the session
    store under fresh session-scoped ids: the reference stays invisible to
    every other session, never re-enters the library, survives restarts via
    the normal session rebuild, and keeps downloading even if the library
    original is later deleted. The library itself is never modified.

    session_id="new" creates the session first (mirrors /chat/upload's eager
    id assignment + optional workspace binding) so a brand-new chat can
    reference library files from its very first turn.
    """
    if session_id == "new":
        session = TutorSession(grade=grade)
        session.student_id = student_id
        from app.core.session import new_session_id
        session.session_id = new_session_id("资料引用")
        if workspace_id:
            _validate_workspace_binding(workspace_id, student_id)
            session.workspace_id = workspace_id
            from app.core.workspace import add_session_to_workspace
            add_session_to_workspace(workspace_id, session.session_id)
    else:
        session = load_session(session_id)
    if session is None or (session.student_id or DEFAULT_STUDENT_ID) != student_id:
        raise HTTPException(404, "会话不存在")
    from app.core.library import load_library, library_data_dir
    from app.core.workspace import resolve_textbook_file
    import hashlib as _hashlib
    attached: list[dict] = []
    uploaded: list[tuple[str, str, str]] = []
    errors: list[dict] = []
    existing_names = {f.get("filename") for f in session.knowledge.files}
    for fid in req.file_ids:
        # P6-C3：会话引用只保留教材（自有或公用），非教材一律拒绝。
        meta, owner_sid = resolve_textbook_file(student_id, fid)
        if meta is None:
            errors.append({"file_id": fid,
                           "error": "仅支持引用教材（请到资料中心教材库上传/选用）"})
            continue
        fname = meta.get("filename", "文件")
        # 引用是快照副本：库文本在引用后更新过（OCR 续跑/收割/重建 RAG）时，
        # 旧副本永远搜不到新内容。同库文件重新挂载且内容 hash 变化 → 原地
        # 替换（先删旧副本再建新副本）；内容未变维持幂等提示。
        prior_ref = next((f for f in session.knowledge.files
                          if str(f.get("library_file_id") or "") == fid), None)
        if prior_ref is not None:
            prior_path = session.knowledge.upload_dir / f"{prior_ref['id']}.txt"
            prior_text = (prior_path.read_text(encoding="utf-8")
                          if prior_path.exists() else "")
        else:
            prior_text = None
        if fname in existing_names and prior_text is None:
            errors.append({"file_id": fid, "filename": fname,
                           "error": "会话中已有同名文件"})
            continue
        data_dir = library_data_dir(owner_sid)
        txt_path = data_dir / f"{fid}.txt"
        if not txt_path.exists():
            errors.append({"file_id": fid, "filename": fname, "error": "文件内容已不存在"})
            continue
        text = txt_path.read_text(encoding="utf-8")
        if prior_text is not None:
            if _hashlib.sha256(prior_text.encode()).hexdigest() == \
                    _hashlib.sha256(text.encode()).hexdigest():
                errors.append({"file_id": fid, "filename": fname,
                               "error": "已在会话中引用（内容未变化）"})
                continue
            session.knowledge.remove_file(str(prior_ref["id"]))
            existing_names.discard(str(prior_ref.get("filename") or ""))
        orig_ext = meta.get("orig_ext") or ""
        raw: bytes | None = None
        if orig_ext:
            orig_path = data_dir / f"{fid}.orig{orig_ext}"
            if orig_path.exists():
                raw = orig_path.read_bytes()
        new_id = uuid.uuid4().hex[:12]
        # structured=True：教材引用走 V2 结构化分块（页边界/标题硬边界/
        # 图表公式保护块 + printed_page 标注），重载不会退回 V1 暴力分块。
        m = session.knowledge.add_file(
            new_id, fname, text, raw=raw, orig_ext=orig_ext if raw else "",
            metadata={"source_scope": "library",
                      "source_visibility": "session_private",
                      "library_file_id": fid},
            structured=True)
        attached.append({"id": new_id, "filename": fname,
                         "char_count": m["char_count"],
                         "chunk_count": m["chunk_count"],
                         "has_original": bool(raw),
                         "source_scope": "library",
                         "source_visibility": "session_private",
                         "library_file_id": fid})
        uploaded.append((new_id, fname, text))
        if new_id not in session.pending_material_file_ids:
            session.pending_material_file_ids.append(new_id)
        existing_names.add(fname)
    if uploaded:
        save_session(session)
        await _post_upload_ingest(
            scope=f"session:{session.session_id}", store=session.knowledge,
            uploaded=uploaded,
            summarize=lambda fid, fname, text: _schedule_session_summary(
                session.session_id, fid, fname, text))
    return {"results": attached, "errors": errors, "session_id": session.session_id}


async def _post_upload_ingest(scope: str, store, uploaded: list[tuple[str, str, str]],
                              summarize) -> None:
    """Shared post-upload pipeline for both upload endpoints (session-level
    and workspace-shared):

      1. Pre-build the vector index for the new files (best-effort — a
         vector failure must never fail or block the upload itself).
      2. Fire-and-forget a file-level summary (LLM, off the critical path).
    """
    if not uploaded:
        return
    try:
        from app.core.embedding import get_embedding_client
        embed = get_embedding_client()
        if embed is not None:
            new_chunks = [c for c in store.chunks
                          if c.file_id in {fid for fid, _n, _t in uploaded}]
            if new_chunks:
                from app.core.vector_jobs import schedule_index
                schedule_index(scope, new_chunks, embed,
                               key=f"upload:{scope}:" + ",".join(sorted(
                                   {c.file_id for c in new_chunks})))
    except Exception:
        pass  # vector track is optional; the upload already succeeded
    for fid, fname, text in uploaded:
        try:
            summarize(fid, fname, text)
        except Exception:
            pass


@router.post("/ocr", dependencies=[Depends(rate_limit("chat_ocr", 20))])
async def ocr_upload(file: UploadFile = File(...),
                     _student_id: str = Depends(resolve_student_id)):
    """Understand a problem image via vision model (glm-4.6v)."""
    raw = await file.read()
    fname = file.filename or "image.png"
    if not is_image_file(fname):
        raise HTTPException(status_code=400, detail="only image formats supported (PNG/JPG/JPEG/WebP/BMP)")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail=f"image too large (>{MAX_IMAGE_BYTES // (1024 * 1024)}MB)")
    text = await understand_image(raw, fname)
    if not text.strip():
        return {"text": "", "warning": "Image understanding failed, please type the problem manually"}
    return {"text": text, "filename": fname}


@router.get("/sessions", response_model=SessionListResponse)
def get_sessions(student_id: str = Depends(resolve_student_id)):
    # M0: 历史记录按身份隔离——每个用户只看到自己的会话。
    # 无 student_id 戳的遗留会话（M0 之前创建）归属共享游客 student_default。
    visible = [s for s in list_sessions()
               if (s.get("student_id") or DEFAULT_STUDENT_ID) == student_id]
    return SessionListResponse(sessions=[SessionItem(**s) for s in visible])


@router.get("/sessions/{session_id}")
def get_session(session_id: str, tail: int = 0,
                student_id: str = Depends(resolve_student_id)):
    # Ownership guard: foreign sessions are invisible (404, no existence
    # leak); unstamped legacy sessions belong to the guest.
    session = _load_owned_session(session_id, student_id)
    from app.core.workspace import material_sources
    # P3 渐进加载：tail>0 时只返回最近 N 条消息 + 总数（message_total），
    # 前端先渲染尾部、"加载更早"再取全量；缺省 0 保持全量兼容。
    messages = session.messages
    message_total = len(messages)
    if tail > 0 and message_total > tail:
        messages = messages[-tail:]
    return {
        "session_id": session.session_id,
        "workspace_id": session.workspace_id,
        "grade": session.grade,
        "title": session.title,
        "messages": messages,
        "message_total": message_total,
        "quiz_history": session.quiz_history,
        "knowledge_files": [{**f, "has_original": bool(f.get("orig_ext"))}
                            for f in session.knowledge.file_list()],
        "knowledge_summary": session.knowledge.to_dict(),
        "material_sources": material_sources(session),
        "trace_ids": session.trace_ids,
    }


@router.get("/sessions/{session_id}/files/{file_id}/download")
def download_session_file(session_id: str, file_id: str,
                          student_id: str = Depends(resolve_student_id)):
    """Re-download a session attachment's original binary; attachments from
    before the original-keeping feature have none and get 404."""
    session = load_session(session_id)
    if session is None or (session.student_id or DEFAULT_STUDENT_ID) != student_id:
        raise HTTPException(404, "会话不存在")
    meta = next((f for f in session.knowledge.files if f.get("id") == file_id), None)
    if meta is None:
        raise HTTPException(404, "文件不存在")
    from app.api.v1.library import _download_response
    return _download_response(session.knowledge.upload_dir, meta)


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str, forget_prompt_memory: bool = False,
                   student_id: str = Depends(resolve_student_id)):
    # Ownership guard before mutating (404 = invisible, not 403).
    _load_owned_session(session_id, student_id)
    from app.core.trash import archive_session
    try:
        item = archive_session(student_id, session_id,
                               forget_prompt_memory=forget_prompt_memory)
    except FileNotFoundError:
        raise HTTPException(404, "会话不存在")
    return {"status": "archived", "session_id": session_id, "trash_item": item}


@router.patch("/sessions/{session_id}")
def patch_session(session_id: str, req: SessionPatchRequest,
                  student_id: str = Depends(resolve_student_id)):
    """Rename and/or switch the in-session stage (P1).

    Both fields optional; at least one must be present. ``grade`` accepts ""
    （自动）or one of the four explicit stages; anything else is 400.
    Ownership guard before mutating (404 = invisible, not 403).
    """
    from app.agents.teaching_engine.stage_profile import VALID_STAGES, normalize_grade
    _load_owned_session(session_id, student_id)
    resp = {"status": "ok", "session_id": session_id}
    if req.title is not None:
        if not rename_session(session_id, req.title):
            raise HTTPException(404, "会话不存在")
        resp["title"] = req.title
    if req.grade is not None:
        grade = normalize_grade(req.grade)
        if grade and grade not in VALID_STAGES:
            raise HTTPException(400, f"grade 必须是 {list(VALID_STAGES)} 之一或空（自动）")
        if not set_session_grade(session_id, grade):
            raise HTTPException(404, "会话不存在")
        resp["grade"] = grade
    return resp
