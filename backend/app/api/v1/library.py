"""Library (ziliao ku) API: per-student folders + files, private by default.

Files in the library are NOT visible to any conversation until a workspace
explicitly selects them (Workspace.selected_folder_ids/selected_file_ids).
Original binaries are kept on upload so files can be re-downloaded; legacy
files without an original are simply not downloadable (404).

M0 isolation: one library per student_id (the index file is keyed by it), so
endpoints only ever touch the caller's own library. Workspace-exclusive
folders (workspace_id set) are managed through their workspace — direct
rename/delete here is rejected with 400.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.library import (
    Library, file_scope, library_data_dir, load_library, save_library,
)
from app.core.file_parser import MAX_UPLOAD_BYTES, SUPPORTED_EXTS, extract_text
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/library", tags=["library"])


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class FolderRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class FileMove(BaseModel):
    folder_id: str = ""  # "" = move back to root (unfiled)


class FileRename(BaseModel):
    filename: str = Field(..., min_length=1, max_length=240)


def _folder_out(lib: Library, f: dict) -> dict:
    return {
        "id": f["id"],
        "name": f["name"],
        "workspace_id": f.get("workspace_id", ""),
        "file_count": lib.folder_file_count(f["id"]),
        "created_at": f.get("created_at", 0),
        "updated_at": f.get("updated_at", 0),
    }


def _file_out(f: dict) -> dict:
    return {
        "id": f["id"],
        "filename": f["filename"],
        "original_filename": f.get("original_filename", f["filename"]),
        "folder_id": f.get("folder_id", ""),
        "char_count": f.get("char_count", 0),
        "chunk_count": f.get("chunk_count", 0),
        "summary": f.get("summary", ""),
        "topics": f.get("topics", []),
        "has_original": bool(f.get("orig_ext")),
        "created_at": f.get("created_at", 0),
        "updated_at": f.get("updated_at", f.get("created_at", 0)),
    }


def _get_file(lib: Library, file_id: str) -> dict:
    f = lib.find_file(file_id)
    if f is None:
        raise HTTPException(404, "文件不存在")
    return f


@router.get("")
def get_tree(student_id: str = Depends(resolve_student_id)):
    lib = load_library(student_id)
    return {
        "folders": [_folder_out(lib, f) for f in lib.folders],
        "files": [_file_out(f) for f in lib.files],
    }


@router.post("/folders")
def create_folder(req: FolderCreate, student_id: str = Depends(resolve_student_id)):
    lib = load_library(student_id)
    folder = lib.create_folder(req.name)
    save_library(lib)
    return {"folder": _folder_out(lib, folder)}


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: str, req: FolderRename,
                  student_id: str = Depends(resolve_student_id)):
    lib = load_library(student_id)
    folder = lib.find_folder(folder_id)
    if folder is None:
        raise HTTPException(404, "文件夹不存在")
    if folder.get("workspace_id"):
        raise HTTPException(400, "工作区专属资料夹请通过重命名工作区来改名")
    lib.rename_folder(folder_id, req.name)
    save_library(lib)
    return {"folder": _folder_out(lib, lib.find_folder(folder_id))}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, student_id: str = Depends(resolve_student_id)):
    lib = load_library(student_id)
    folder = lib.find_folder(folder_id)
    if folder is None:
        raise HTTPException(404, "文件夹不存在")
    if folder.get("workspace_id"):
        raise HTTPException(400, "工作区专属资料夹随工作区一并删除")
    if any(__import__("app.core.textbook", fromlist=["textbook_for_file"])
           .textbook_for_file(student_id, f["id"]) for f in lib.files
           if f.get("folder_id") == folder_id):
        raise HTTPException(400, "文件夹含教材，请从教材库归档教材")
    from app.core.trash import archive_library_folder
    try:
        item = archive_library_folder(student_id, folder_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "archived", "folder_id": folder_id, "trash_item": item}


@router.post("/upload")
async def upload_files(folder_id: str = "",
                       files: list[UploadFile] = File(...),
                       student_id: str = Depends(resolve_student_id)):
    """Upload materials into a library folder ("" = root / unfiled).

    Stores the original binary + extracted text, chunks for retrieval, and
    fires off vector indexing + file summaries (both best-effort)."""
    lib = load_library(student_id)
    if folder_id and lib.find_folder(folder_id) is None:
        raise HTTPException(404, "文件夹不存在")
    results = []
    uploaded: list[tuple[str, str, str, str]] = []  # (file_id, filename, text, scope)
    for f in files:
        raw = await f.read()
        fname = f.filename or "upload"
        lower = fname.lower()
        ext = next((e for e in SUPPORTED_EXTS if lower.endswith(e)), "")
        if not ext:
            results.append({"filename": fname, "error": "不支持的格式（仅 PDF/DOCX/PPTX/TXT/MD）"})
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            results.append({"filename": fname, "error": f"文件过大（>{MAX_UPLOAD_BYTES // (1024 * 1024)}MB）"})
            continue
        text = await asyncio.to_thread(extract_text, fname, raw)
        if not text.strip():
            results.append({"filename": fname, "error": "无法提取文本"})
            continue
        meta = lib.add_file(folder_id, fname, text, raw=raw, orig_ext=ext)
        uploaded.append((meta["id"], fname, text, file_scope(meta)))
        results.append({"id": meta["id"], "filename": fname,
                        "folder_id": meta["folder_id"],
                        "char_count": meta["char_count"],
                        "chunk_count": meta["chunk_count"]})
    save_library(lib)
    await _ingest(lib, uploaded, student_id)
    return {"results": results}


@router.patch("/files/{file_id}")
def rename_file(file_id: str, req: FileRename,
                student_id: str = Depends(resolve_student_id)):
    """Rename a library file's display name; retrieval chunks/vector ids stay stable."""
    lib = load_library(student_id)
    f = _get_file(lib, file_id)
    if f.get("folder_id"):
        folder = lib.find_folder(f["folder_id"])
        if folder and folder.get("workspace_id"):
            raise HTTPException(400, "工作区专属文件请通过工作区设置管理")
    if not lib.rename_file(file_id, req.filename):
        raise HTTPException(400, "文件名不能为空")
    save_library(lib)
    return {"file": _file_out(f)}


@router.post("/files/{file_id}/move")
def move_file(file_id: str, req: FileMove,
              student_id: str = Depends(resolve_student_id)):
    lib = load_library(student_id)
    f = _get_file(lib, file_id)
    if req.folder_id and lib.find_folder(req.folder_id) is None:
        raise HTTPException(404, "目标文件夹不存在")
    old_scope = file_scope(f)
    lib.move_file(file_id, req.folder_id)
    save_library(lib)
    new_scope = file_scope(f)
    if new_scope != old_scope:
        _rescope_vectors(lib, file_id, new_scope)
    return {"status": "moved", "file_id": file_id, "folder_id": req.folder_id}


@router.delete("/files/{file_id}")
def delete_file(file_id: str, student_id: str = Depends(resolve_student_id)):
    lib = load_library(student_id)
    _get_file(lib, file_id)
    from app.core import textbook as tb_store
    if tb_store.textbook_for_file(student_id, file_id):
        raise HTTPException(400, "该文件属于教材，请从教材库归档")
    from app.core.trash import archive_library_file
    item = archive_library_file(student_id, file_id)
    return {"status": "archived", "file_id": file_id, "trash_item": item}


@router.get("/files/{file_id}/download")
def download_file(file_id: str, student_id: str = Depends(resolve_student_id)):
    """Re-download the original binary; legacy files without one get 404."""
    lib = load_library(student_id)
    f = _get_file(lib, file_id)
    return _download_response(library_data_dir(student_id), f)


@router.get("/files/{file_id}/page/{page}")
def file_page_snapshot(file_id: str, page: int,
                       student_id: str = Depends(resolve_student_id)):
    """按需渲染 PDF 原件某页快照 PNG（P7 图表证据「查看原页」通道）。

    解析顺序：自有 library → 公共教材库（与 workspace.resolve_textbook_file
    一致）；外人/非 PDF/页越界一律 404（不泄露存在性）。缓存 1 天。"""
    from fastapi.responses import Response
    if page < 1 or page > 5000:
        raise HTTPException(404, "页码无效")
    from app.core import textbook as tb_store
    from app.core.pdf_ocr import render_page_pixmap
    data_dir: Path | None = None
    orig_ext = ""
    for sid in (student_id, tb_store.PUBLIC_STUDENT_ID):
        lib = load_library(sid)
        meta = lib.find_file(file_id)
        if meta is not None:
            orig_ext = str(meta.get("orig_ext") or "")
            if orig_ext:
                data_dir = library_data_dir(sid)
            break
    if data_dir is None or orig_ext.lower() != ".pdf":
        raise HTTPException(404, "未找到可渲染的 PDF 原件")
    raw_path = data_dir / f"{file_id}.orig{orig_ext}"
    try:
        raw = raw_path.read_bytes()
    except OSError:
        raise HTTPException(404, "未找到可渲染的 PDF 原件")
    png = render_page_pixmap(raw, page - 1, dpi=150)
    if not png:
        raise HTTPException(404, "页码超出范围")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


def _download_response(data_dir: Path, meta: dict) -> FileResponse:
    """Serve the ORIGINAL uploaded binary only. Files uploaded before the
    original-keeping feature have no binary on disk (only extracted text);
    per product decision their download is removed entirely (404) instead of
    silently substituting a .txt of extracted text.

    The served filename ALWAYS carries the original extension: metas with a
    missing filename fall back to "file<orig_ext>", and a filename missing
    the extension gets orig_ext appended — a downloaded file must stay
    openable by double-click."""
    orig_ext = meta.get("orig_ext") or ""
    if orig_ext:
        orig = data_dir / f"{meta['id']}.orig{orig_ext}"
        if orig.exists():
            fname = meta.get("filename") or f"file{orig_ext}"
            if not fname.lower().endswith(orig_ext.lower()):
                fname += orig_ext
            return FileResponse(orig, filename=fname,
                                media_type="application/octet-stream")
    raise HTTPException(404, "原始文件未保留（该文件上传于原件保留功能上线前），无法下载")


async def _ingest(lib: Library, uploaded: list[tuple[str, str, str, str]],
                  student_id: str) -> None:
    """Vector pre-index + summary scheduling for freshly uploaded files."""
    if not uploaded:
        return
    try:
        from app.core.embedding import get_embedding_client
        embed = get_embedding_client()
        if embed is not None:
            by_scope: dict[str, list] = {}
            ids = {fid for fid, _n, _t, _s in uploaded}
            chunks = lib.chunks_for_files(list(ids))
            scope_of = {fid: scope for fid, _n, _t, scope in uploaded}
            for c in chunks:
                by_scope.setdefault(scope_of.get(c.file_id, ""), []).append(c)
            for scope, scope_chunks in by_scope.items():
                if scope and scope_chunks:
                    from app.core.vector_jobs import schedule_index
                    schedule_index(scope, scope_chunks, embed,
                                   key=f"library:{student_id}:{scope}")
    except Exception:
        pass  # vector track is optional; the upload already succeeded
    try:
        from app.core.file_summary import schedule_library_file_summary
        for fid, fname, text, _scope in uploaded:
            schedule_library_file_summary(student_id, fid, fname, text)
    except Exception:
        pass


def _rescope_vectors(lib: Library, file_id: str, new_scope: str) -> None:
    """Re-index one file's vectors under a new scope (best-effort)."""
    try:
        from app.core import vector_store
        vector_store.delete_file(file_id)
        from app.core.embedding import get_embedding_client
        embed = get_embedding_client()
        if embed is not None:
            chunks = lib.chunks_for_files([file_id])
            if chunks:
                from app.core.vector_jobs import schedule_index
                schedule_index(new_scope, chunks, embed,
                               key=f"rescope:{file_id}:{new_scope}")
    except Exception:
        pass


def _delete_vectors(file_ids: list[str]) -> None:
    try:
        from app.core import vector_store
        for fid in file_ids:
            vector_store.delete_file(fid)
    except Exception:
        pass


def _cleanup_textbook_orphans(student_id: str, file_ids: list[str]) -> None:
    """P2: when a Library file is deleted directly, cascade-remove any textbook
    record + its graph that was registered for that file_id. Best-effort: a
    failure here must never break the file deletion. ``GET /textbooks`` also
    lazy-filters orphans as a second line of defense."""
    if not file_ids:
        return
    try:
        from app.core import textbook as tb_store
        from app.agents.knowledge import store as kg_store
        for fid in file_ids:
            tb = tb_store.textbook_for_file(student_id, fid)
            if tb is None:
                continue
            try:
                kg_store.delete_custom_graph(student_id, tb["topic_key"])
            except Exception:
                pass
            tb_store.remove_textbook(student_id, tb["id"])
    except Exception:
        pass
