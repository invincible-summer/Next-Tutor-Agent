"""Workspace (gongzuo xuexi qu) API: CRUD + move session + source-selected upload.

A workspace groups chat sessions under a named folder with a cross-conversation
public memory. Its readable materials come from the owner's library
(api/v1/library.py): the workspace reads exactly its selected folders/files —
its exclusive folder (auto-created, receives workspace uploads) is selected by
default, and the selection can be changed at any time via PATCH.

M0 isolation: every endpoint resolves the caller's student_id. Workspaces
created before M0 carry no student_id stamp and belong to the shared guest
(DEFAULT_STUDENT_ID). By-id endpoints 404 on foreign workspaces (no
existence leak); "shared" means shared across the OWNER's own sessions.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from app.core.library import file_scope, load_library, save_library
from app.core.workspace import (
    Workspace, save_workspace, load_workspace, list_workspaces,
    delete_workspace, rename_workspace, add_session_to_workspace,
    remove_session_from_workspace, ensure_library_folder, readable_files,
    workspace_owned_file_ids,
)
from app.core.file_parser import MAX_IMAGE_BYTES, MAX_UPLOAD_BYTES
from app.core.multimodal_parser import SUPPORTED_ASYNC_EXTS, extract_text_async
from app.core.workspace_memory import init_workspace_memory_from_session
from app.identity.deps import resolve_student_id
from app.agents.student_model.store import DEFAULT_STUDENT_ID

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    folder_ids: list[str] = []
    file_ids: list[str] = []


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    folder_ids: list[str] | None = None  # None = unchanged; [] = clear
    file_ids: list[str] | None = None


class MoveSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


def _owned(ws: Workspace, student_id: str) -> bool:
    """Legacy workspaces (no student_id stamp) belong to the guest."""
    return (ws.student_id or DEFAULT_STUDENT_ID) == student_id


def _load_owned(ws_id: str, student_id: str) -> Workspace:
    """Load a workspace and enforce ownership (404 = invisible, not 403)."""
    ws = load_workspace(ws_id)
    if ws is None or not _owned(ws, student_id):
        raise HTTPException(404, "工作学习区不存在")
    return ws


def _validate_selection(student_id: str, folder_ids: list[str],
                        file_ids: list[str]) -> tuple[list[str], list[str]]:
    """P6-C3：工作区来源只保留教材——folder_ids 已废弃（一律返回空），
    file_ids 仅保留已注册教材（自有或公用），去重保序。"""
    from app.core.workspace import resolve_textbook_file
    files = [i for i in dict.fromkeys(file_ids)
             if resolve_textbook_file(student_id, i)[0] is not None]
    return [], files


def _ws_detail(ws: Workspace) -> dict:
    return {
        "workspace_id": ws.workspace_id,
        "name": ws.name,
        "session_ids": ws.session_ids,
        "knowledge_files": [{**f, "has_original": bool(f.get("orig_ext"))}
                            for f in readable_files(ws)],
        "library_folder_id": ws.library_folder_id,
        "selected_folder_ids": ws.selected_folder_ids,
        "selected_file_ids": ws.selected_file_ids,
        "workspace_file_ids": workspace_owned_file_ids(ws),
        "public_memory": ws.public_memory,
        "public_memory_updated_at": ws.public_memory_updated_at,
        "created_at": ws.created_at,
        "updated_at": ws.updated_at,
    }


def _visible_summaries(student_id: str) -> list[dict]:
    """可见工作区摘要（含 file_count），供 GET /workspaces 与 /sidebar 复用。

    P4：教材有效性批量判定（每命名空间一次 store 加载），替代每个
    selected_file_id 一次 resolve_textbook_file 的 N 次重载。"""
    from app.core.library import load_library as _load_lib
    from app.core.workspace import valid_textbook_file_ids
    visible = [w for w in list_workspaces()
               if (w.get("student_id") or DEFAULT_STUDENT_ID) == student_id]
    if not visible:
        return visible
    valid_ids = valid_textbook_file_ids(student_id)
    lib = _load_lib(student_id)
    for w in visible:
        ws = load_workspace(w["workspace_id"])
        owned_count = len(workspace_owned_file_ids(ws, lib)) if ws else 0
        # file_count = workspace-owned shared files + selected valid textbooks.
        w["file_count"] = owned_count + sum(
            1 for fid in w.get("selected_file_ids", []) if fid in valid_ids)
    return visible


@router.get("")
def list_all(student_id: str = Depends(resolve_student_id)):
    return {"workspaces": _visible_summaries(student_id)}


@router.post("")
def create(req: WorkspaceCreate, student_id: str = Depends(resolve_student_id)):
    ws = Workspace(name=req.name.strip(), student_id=student_id)
    wid = save_workspace(ws)
    ws = load_workspace(wid)
    # 专属夹照常创建（存储语义），但 P6-C3 起文件夹不参与来源选择。
    ensure_library_folder(ws)
    _folders, files = _validate_selection(student_id, req.folder_ids, req.file_ids)
    ws.selected_folder_ids = []
    ws.selected_file_ids = files
    save_workspace(ws)
    return {"workspace_id": wid, "name": ws.name, "status": "created",
            "library_folder_id": ws.library_folder_id}


@router.get("/{ws_id}")
def get_one(ws_id: str, student_id: str = Depends(resolve_student_id)):
    ws = _load_owned(ws_id, student_id)
    ensure_library_folder(ws)
    return _ws_detail(ws)


@router.patch("/{ws_id}")
def update(ws_id: str, req: WorkspaceUpdate,
           student_id: str = Depends(resolve_student_id)):
    ws = _load_owned(ws_id, student_id)
    if req.name is not None:
        rename_workspace(ws_id, req.name)  # also renames the exclusive folder
        ws = _load_owned(ws_id, student_id)
    if req.folder_ids is not None or req.file_ids is not None:
        folders, files = _validate_selection(
            student_id,
            req.folder_ids if req.folder_ids is not None else ws.selected_folder_ids,
            req.file_ids if req.file_ids is not None else ws.selected_file_ids)
        # 替换语义：用户可取消选入任何来源（包括专属夹），选择即持久化
        ws.selected_folder_ids = folders
        ws.selected_file_ids = files
        save_workspace(ws)
    return {"status": "updated", "workspace_id": ws_id, **_ws_detail(ws)}


@router.delete("/{ws_id}")
def remove(ws_id: str, student_id: str = Depends(resolve_student_id)):
    _load_owned(ws_id, student_id)
    from app.core.trash import archive_workspace
    try:
        item = archive_workspace(student_id, ws_id)
    except FileNotFoundError:
        raise HTTPException(404, "工作学习区不存在")
    return {"status": "archived", "workspace_id": ws_id, "trash_item": item}


@router.post("/{ws_id}/sessions")
async def move_session(ws_id: str, req: MoveSessionRequest,
                       student_id: str = Depends(resolve_student_id)):
    """Move a session into a workspace. Triggers public memory update."""
    _load_owned(ws_id, student_id)
    # The session must belong to the same identity (legacy = guest).
    from app.core.session import load_session, save_session
    session = load_session(req.session_id)
    if session is not None and (session.student_id or DEFAULT_STUDENT_ID) != student_id:
        raise HTTPException(404, "会话不存在")
    ws = add_session_to_workspace(ws_id, req.session_id)
    if ws is None:
        raise HTTPException(404, "工作学习区不存在")
    # Stamp workspace_id on the session file.
    if session:
        session.workspace_id = ws_id
        save_session(session)
    # Fire public memory update from the moved session's transcript.
    await init_workspace_memory_from_session(ws_id, req.session_id)
    return {"status": "moved", "workspace_id": ws_id, "session_id": req.session_id}


@router.delete("/{ws_id}/sessions/{session_id}")
def remove_session(ws_id: str, session_id: str,
                   student_id: str = Depends(resolve_student_id)):
    """Remove a session from a workspace (back to loose)."""
    _load_owned(ws_id, student_id)
    ws = remove_session_from_workspace(ws_id, session_id)
    if ws is None:
        raise HTTPException(404, "工作学习区不存在")
    # Clear workspace_id on the session file.
    from app.core.session import load_session, save_session
    session = load_session(session_id)
    if session:
        session.workspace_id = ""
        save_session(session)
    return {"status": "removed", "workspace_id": ws_id, "session_id": session_id}


@router.post("/{ws_id}/upload")
async def upload_shared(ws_id: str, files: list[UploadFile] = File(...),
                        student_id: str = Depends(resolve_student_id)):
    """Upload workspace-owned shared materials.

    These files live in the workspace's exclusive Library folder and are
    explicitly authorized through ``workspace_file_ids``.  They are searchable
    by every session in this workspace, but never become global textbooks or
    automatically visible to another workspace.
    """
    ws = _load_owned(ws_id, student_id)
    folder_id = ensure_library_folder(ws)
    lib = load_library(student_id)
    results = []
    uploaded: list[tuple[str, str, str]] = []  # (file_id, filename, text)
    for f in files:
        raw = await f.read()
        fname = f.filename or "upload"
        lower = fname.lower()
        ext = next((e for e in SUPPORTED_ASYNC_EXTS if lower.endswith(e)), "")
        if not ext:
            results.append({"filename": fname,
                            "error": "不支持的格式（仅 PDF/DOCX/PPTX/TXT/MD/常见图片）"})
            continue
        limit = MAX_IMAGE_BYTES if ext in (".png", ".jpg", ".jpeg", ".webp",
                                           ".bmp", ".tiff", ".tif") else MAX_UPLOAD_BYTES
        if len(raw) > limit:
            results.append({"filename": fname,
                            "error": f"文件过大（>{limit // (1024 * 1024)}MB）"})
            continue
        extracted = await extract_text_async(fname, raw, purpose="workspace")
        text = extracted.text
        if not text.strip():
            results.append({"filename": fname, "error": "无法提取文本",
                            "warning": extracted.warning or None,
                            "ocr_used": extracted.used_ocr})
            continue
        meta = lib.add_file(folder_id, fname, text, raw=raw, orig_ext=ext)
        meta.update({"source_scope": "workspace",
                     "source_visibility": "workspace_shared",
                     "ocr_used": extracted.used_ocr,
                     "ocr_pages": extracted.ocr_pages,
                     "media_count": extracted.media_count})
        if meta["id"] not in ws.workspace_file_ids:
            ws.workspace_file_ids.append(meta["id"])
        uploaded.append((meta["id"], fname, text))
        results.append({"id": meta["id"], "filename": fname,
                        "char_count": meta["char_count"],
                        "chunk_count": meta["chunk_count"],
                        "source_scope": "workspace",
                        "source_visibility": "workspace_shared",
                        "warning": extracted.warning or None,
                        "ocr_used": extracted.used_ocr})
    save_library(lib)
    if uploaded:
        save_workspace(ws)
    # Pre-index vectors + fire off file summaries (both best-effort).
    from app.api.v1.chat import _post_upload_ingest
    from app.core.file_summary import schedule_library_file_summary

    class _Store:  # minimal duck-typed chunks view for _post_upload_ingest
        chunks = lib.chunks_for_files([fid for fid, _n, _t in uploaded])

    await _post_upload_ingest(
        scope=f"folder:{folder_id}", store=_Store(), uploaded=uploaded,
        summarize=lambda fid, fname, text: schedule_library_file_summary(
            student_id, fid, fname, text))
    if uploaded:
        # Public memory receives only a bounded filename-level event; extracted
        # text/OCR never enters the workspace memory prompt.
        from app.core.workspace_memory import update_workspace_memory
        names = "、".join(fname for _fid, fname, _text in uploaded[:8])
        asyncio.create_task(update_workspace_memory(
            ws_id, f"工作区新增共享资料：{names}", "", [], session_title=ws.name))
    return {"results": results, "workspace_id": ws_id}


@router.delete("/{ws_id}/files/{file_id}")
def remove_shared_file(ws_id: str, file_id: str,
                       student_id: str = Depends(resolve_student_id)):
    """Remove a file from the workspace.

    Workspace-owned files are archived and removed from the active library;
    individually selected files are just unselected (the file stays in the
    library). Files visible through a selected FOLDER must be removed via the
    source selection (PATCH), not one-by-one."""
    ws = _load_owned(ws_id, student_id)
    lib = load_library(student_id)
    f = lib.find_file(file_id)
    if f is not None and f.get("folder_id") == ws.library_folder_id:
        from app.core.trash import archive_library_file
        item = archive_library_file(student_id, file_id)
        return {"status": "archived", "workspace_id": ws_id,
                "file_id": file_id, "trash_item": item}
    if file_id in ws.selected_file_ids:
        ws.selected_file_ids.remove(file_id)
        save_workspace(ws)
        return {"status": "unselected", "workspace_id": ws_id, "file_id": file_id}
    if f is not None and f.get("folder_id") in set(ws.selected_folder_ids):
        raise HTTPException(400, "该文件随文件夹整体选入，请在工作区设置中调整")
    raise HTTPException(404, "文件不存在")
