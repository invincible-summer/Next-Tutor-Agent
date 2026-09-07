"""M-Notes API：每用户 Obsidian 式笔记仓库的路由层。

CRUD / 文件夹 / 修订 / 模板 / 链接图 / 建议队列 / 温故复习 / 导出。
智能体（生成 + 对话 SSE）在 agents/notes_agent.py，经本文件的
/generate 与 /chat/stream 端点暴露。

约定（对齐 library.py / chat.py）：
  - 身份一律 Depends(resolve_student_id)；资源不存在或越权统一 404。
  - 保存走乐观并发：base_revision 不匹配返回 409（携带最新内容）。
  - 删除经 core/trash.py 归档（notes_note bundle，可恢复）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Query, UploadFile)
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.agents.learning_orchestration import manager as m9_manager
from app.core import notes as notes_store
from app.core import trash
from app.core.notes_templates import get_template, list_templates
from app.core.ratelimit import rate_limit
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/notes", tags=["notes"])


# --- request models ----------------------------------------------------------


class NoteCreate(BaseModel):
    title: str = Field(default="", max_length=300)
    folder_id: str = Field(default="", max_length=64)
    template_id: str = Field(default="", max_length=64)
    content: str = Field(default="", max_length=notes_store._MAX_CONTENT_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=20)
    review_enabled: bool | None = None
    status: str = Field(default="active", max_length=16)


class NoteSave(BaseModel):
    title: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=notes_store._MAX_CONTENT_CHARS)
    base_revision: int | None = None
    summary: str = Field(default="编辑笔记", max_length=240)


class NotePatch(BaseModel):
    title: str = Field(default="", max_length=300)
    folder_id: str | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    status: str = Field(default="", max_length=16)
    review_enabled: bool | None = None


class FolderCreate(BaseModel):
    name: str = Field(default="", max_length=120)
    parent_id: str = Field(default="", max_length=64)


class FolderRename(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    parent_id: str | None = Field(default=None, max_length=64)


class BulkMove(BaseModel):
    note_ids: list[str] = Field(default_factory=list, max_length=200)
    folder_id: str = Field(default="", max_length=64)


class BulkDelete(BaseModel):
    note_ids: list[str] = Field(default_factory=list, max_length=200)


class AgentPatch(BaseModel):
    # 三模式：ask 问答 / plan 计划 / authorize 授权（旧值由 store 归一化）
    mode: str = Field(..., max_length=16)


class TemplateCreate(BaseModel):
    name: str = Field(..., max_length=300)
    content: str = Field(default="", max_length=notes_store._MAX_CONTENT_CHARS)


class ReviewBody(BaseModel):
    quality: int = Field(..., ge=0, le=5)


# --- helpers -----------------------------------------------------------------


def _load_vault(student_id: str) -> notes_store.NoteVault:
    return notes_store.load_vault(student_id)


def _require_note(vault: notes_store.NoteVault, note_id: str) -> dict[str, Any]:
    meta = vault.find_note(note_id)
    if meta is None:
        raise HTTPException(404, "笔记不存在")
    return meta


def _sync_review_card(student_id: str, meta: dict[str, Any]) -> None:
    """温故开启时向 M9 注册卡片并回填调度字段（best-effort）。"""
    if not (meta.get("review") or {}).get("enabled"):
        return
    try:
        card = m9_manager.get_orchestration_service().upsert_review_card(
            student_id, concept_id=f"note:{meta['id']}",
            concept_name=str(meta.get("title") or meta["id"]))
        if card:
            meta["review"] = {
                "enabled": True,
                "next_review_at": float(card.get("next_review") or 0.0),
                "easiness": float(card.get("easiness") or 2.5),
                "interval": int(card.get("interval") or 0),
                "repetitions": int(card.get("repetitions") or 0),
            }
    except Exception:
        pass


def _drop_review_card(student_id: str, note_id: str) -> None:
    try:
        m9_manager.get_orchestration_service().remove_review_card(
            student_id, concept_id=f"note:{note_id}")
    except Exception:
        pass


def _note_detail(vault: notes_store.NoteVault, meta: dict[str, Any]) -> dict[str, Any]:
    content = vault.read_note(meta["id"])
    return {
        "note": vault.note_summary(meta),
        "content": content,
        "backlinks": vault.backlinks(meta["id"]),
        "links": vault.resolve_links(content),
        "inline_tags": notes_store.parse_inline_tags(content),
    }


def _content_disposition(filename: str) -> str:
    return f"attachment; filename=\"note.md\"; filename*=UTF-8''{quote(filename)}"


# --- vault / search / graph ----------------------------------------------------


@router.get("/vault")
def get_vault(student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    return notes_store.vault_summary(vault)


@router.get("/search")
def search_notes(q: str = Query("", max_length=200),
                 student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    return {"results": vault.search(q)}


@router.get("/graph")
def notes_graph(student_id: str = Depends(resolve_student_id)) -> dict:
    return _load_vault(student_id).link_graph()


# --- notes CRUD ---------------------------------------------------------------


@router.post("/notes", dependencies=[Depends(rate_limit("notes_create", 30))])
def create_note(req: NoteCreate,
                student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    template = get_template(req.template_id)
    custom = None
    if template is None and req.template_id.startswith("ct_"):
        custom = next((t for t in vault.custom_templates
                       if t.get("id") == req.template_id), None)
        if custom is None:
            raise HTTPException(404, "模板不存在")
    content = req.content
    if not content.strip():
        content = template.skeleton if template else (custom or {}).get("content", "")
    tags = req.tags or (list(template.suggested_tags) if template else [])
    folder_id = req.folder_id
    if not folder_id and template and template.folder_hint:
        folder_id = vault.ensure_folder(template.folder_hint)["id"]
    review_enabled = req.review_enabled
    if review_enabled is None:
        review_enabled = bool(template and template.review_enabled)
    meta = vault.create_note(
        title=req.title, content=content, folder_id=folder_id,
        template_id=req.template_id, tags=tags, review_enabled=review_enabled,
        status=req.status, author="user")
    _sync_review_card(student_id, meta)
    notes_store.save_vault(vault)
    return {"note": vault.note_summary(meta)}


@router.get("/notes/{note_id}")
def get_note(note_id: str, student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    meta = _require_note(vault, note_id)
    return _note_detail(vault, meta)


@router.put("/notes/{note_id}")
def save_note(note_id: str, req: NoteSave,
              student_id: str = Depends(resolve_student_id)) -> Any:
    vault = _load_vault(student_id)
    _require_note(vault, note_id)
    try:
        meta = vault.write_note(note_id, req.content, author="user",
                                base_revision=req.base_revision,
                                summary=req.summary or "编辑笔记")
    except notes_store.StaleRevisionError as exc:
        return JSONResponse(status_code=409, content={
            "detail": str(exc),
            "note": exc.note,
            "content": exc.content,
        })
    if req.title.strip() and req.title.strip() != meta.get("title"):
        try:
            vault.rename_note(note_id, req.title)
        except ValueError:
            pass
    notes_store.save_vault(vault)
    return {"note": vault.note_summary(vault.find_note(note_id))}


@router.patch("/notes/{note_id}")
def patch_note(note_id: str, req: NotePatch,
               student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    meta = _require_note(vault, note_id)
    renamed: dict[str, Any] = {}
    if req.title.strip():
        try:
            renamed = vault.rename_note(note_id, req.title)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if req.folder_id is not None:
        if req.folder_id and vault.find_folder(req.folder_id) is None:
            raise HTTPException(404, "文件夹不存在")
        vault.move_note(note_id, req.folder_id)
    if req.tags is not None or req.status:
        vault.set_note_meta(note_id, tags=req.tags, status=req.status)
    if req.review_enabled is not None:
        review = dict(meta.get("review") or {})
        review["enabled"] = bool(req.review_enabled)
        meta["review"] = review
        if req.review_enabled:
            _sync_review_card(student_id, meta)
        else:
            review["next_review_at"] = 0.0
            _drop_review_card(student_id, note_id)
    meta["updated_at"] = notes_store._now()
    notes_store.save_vault(vault)
    out = vault.note_summary(vault.find_note(note_id))
    if "links_rewritten" in renamed:
        out["links_rewritten"] = renamed["links_rewritten"]
    return {"note": out}


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    _require_note(vault, note_id)
    try:
        item = trash.archive_note(student_id, note_id)
    except FileNotFoundError:
        raise HTTPException(404, "笔记不存在")
    except Exception as exc:
        raise HTTPException(500, f"归档失败：{exc}")
    return {"status": "archived", "item": item}


# --- revisions ------------------------------------------------------------------


@router.get("/notes/{note_id}/revisions")
def list_revisions(note_id: str,
                   student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    _require_note(vault, note_id)
    revisions = vault.list_revisions(note_id)
    return {"revisions": revisions or []}


@router.get("/notes/{note_id}/revisions/{revision}")
def read_revision(note_id: str, revision: int,
                  student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    _require_note(vault, note_id)
    content = vault.read_revision(note_id, revision)
    if content is None:
        raise HTTPException(404, "版本不存在")
    return {"revision": revision, "content": content}


@router.post("/notes/{note_id}/revisions/{revision}/restore")
def restore_revision(note_id: str, revision: int,
                     student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    _require_note(vault, note_id)
    try:
        meta = vault.restore_revision(note_id, revision)
    except FileNotFoundError:
        raise HTTPException(404, "版本不存在")
    _sync_review_card(student_id, vault.find_note(note_id))
    notes_store.save_vault(vault)
    return {"note": vault.note_summary(meta)}


# --- folders ----------------------------------------------------------------------


@router.post("/folders")
def create_folder(req: FolderCreate,
                  student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    try:
        folder = vault.create_folder(req.name, req.parent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    notes_store.save_vault(vault)
    return {"folder": {**folder, "note_count": 0}}


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: str, req: FolderRename,
                  student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    if vault.find_folder(folder_id) is None:
        raise HTTPException(404, "文件夹不存在")
    if req.name is not None:
        vault.rename_folder(folder_id, req.name)
    if req.parent_id is not None:
        try:
            if not vault.move_folder(folder_id, req.parent_id):
                raise HTTPException(404, "目标文件夹不存在")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    notes_store.save_vault(vault)
    return {"status": "ok"}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str,
                  student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    moved = vault.delete_folder(folder_id)
    if moved is None:
        raise HTTPException(404, "文件夹不存在")
    notes_store.save_vault(vault)
    return {"status": "ok", **moved, "moved_to_unfiled": moved["moved_notes"]}


@router.post("/bulk/move")
def bulk_move(req: BulkMove, student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    if req.folder_id and vault.find_folder(req.folder_id) is None:
        raise HTTPException(404, "目标文件夹不存在")
    moved: list[str] = []
    missing: list[str] = []
    for note_id in dict.fromkeys(req.note_ids):
        if vault.move_note(Path(note_id).name, req.folder_id):
            moved.append(note_id)
        else:
            missing.append(note_id)
    notes_store.save_vault(vault)
    return {"status": "ok", "moved": moved, "missing": missing}


@router.post("/bulk/delete")
def bulk_delete(req: BulkDelete, student_id: str = Depends(resolve_student_id)) -> dict:
    archived: list[dict[str, Any]] = []
    missing: list[str] = []
    for note_id in dict.fromkeys(req.note_ids):
        try:
            archived.append(trash.archive_note(student_id, Path(note_id).name))
            _drop_review_card(student_id, Path(note_id).name)
        except FileNotFoundError:
            missing.append(note_id)
        except Exception as exc:
            raise HTTPException(500, f"批量归档失败：{exc}")
    return {"status": "archived", "archived": archived, "missing": missing}


# --- templates ----------------------------------------------------------------------


@router.get("/templates")
def list_all_templates(student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    out = list_templates()
    out.extend({**t, "builtin": False} for t in vault.custom_templates)
    return {"templates": out}


@router.post("/templates")
def create_custom_template(req: TemplateCreate,
                           student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    try:
        tpl = vault.add_custom_template(req.name, req.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    notes_store.save_vault(vault)
    return {"template": {**tpl, "builtin": False}}


@router.delete("/templates/{template_id}")
def delete_custom_template(template_id: str,
                           student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    if not template_id.startswith("ct_") or \
            not vault.remove_custom_template(template_id):
        raise HTTPException(404, "模板不存在")
    notes_store.save_vault(vault)
    return {"status": "ok"}


# --- 笔记智能体（每笔记专属） ------------------------------------------------------


def _agent_note_key(note_id: str) -> str:
    """agent 历史的存储键：笔记 id；空串 = 仓库级对话。"""
    return Path(note_id or "").name


@router.get("/notes/{note_id}/agent")
def get_note_agent(note_id: str,
                   student_id: str = Depends(resolve_student_id)) -> dict:
    """某笔记专属智能体的模式 / 对话历史 / 待批复计划 / 工作态。"""
    vault = _load_vault(student_id)
    key = _agent_note_key(note_id)
    if key and key != "_vault" and vault.find_note(key) is None:
        raise HTTPException(404, "笔记不存在")
    view = notes_store.agent_history_view(student_id, key)
    view["modes"] = list(notes_store.AGENT_MODES)
    return view


@router.patch("/notes/{note_id}/agent")
def patch_note_agent(note_id: str, req: AgentPatch,
                     student_id: str = Depends(resolve_student_id)) -> dict:
    """切换该笔记智能体的模式（服务端枚举校验，堵客户端自报越权）。"""
    vault = _load_vault(student_id)
    key = _agent_note_key(note_id)
    if key and key != "_vault" and vault.find_note(key) is None:
        raise HTTPException(404, "笔记不存在")
    raw = req.mode.strip().lower()
    if raw not in notes_store.AGENT_MODES and raw not in notes_store._LEGACY_AGENT_MODES:
        raise HTTPException(422, f"未知模式：{req.mode}（可选 {'/'.join(notes_store.AGENT_MODES)}）")
    notes_store.set_agent_mode(student_id, key, raw)
    return {"note_id": key,
            "mode": notes_store.normalize_agent_mode(raw)}


@router.delete("/notes/{note_id}/agent")
def clear_note_agent(note_id: str,
                     student_id: str = Depends(resolve_student_id)) -> dict:
    """清空该笔记智能体的对话历史与待批复计划（保留模式选择）。"""
    vault = _load_vault(student_id)
    key = _agent_note_key(note_id)
    if key and key != "_vault" and vault.find_note(key) is None:
        raise HTTPException(404, "笔记不存在")
    notes_store.clear_agent_history(student_id, key)
    return {"status": "ok"}


# --- review (M9 同步) ----------------------------------------------------------------------


@router.post("/notes/{note_id}/review")
def submit_review(note_id: str, req: ReviewBody,
                  student_id: str = Depends(resolve_student_id)) -> dict:
    vault = _load_vault(student_id)
    meta = _require_note(vault, note_id)
    if not (meta.get("review") or {}).get("enabled"):
        raise HTTPException(400, "该笔记未开启温故复习")
    service = m9_manager.get_orchestration_service()
    concept_id = f"note:{note_id}"
    card = service.submit_review(student_id, concept_id=concept_id,
                                 quality=req.quality)
    if not card:
        service.upsert_review_card(
            student_id, concept_id=concept_id,
            concept_name=str(meta.get("title") or note_id))
        card = service.submit_review(student_id, concept_id=concept_id,
                                     quality=req.quality)
    if card:
        meta["review"] = {
            "enabled": True,
            "next_review_at": float(card.get("next_review") or 0.0),
            "easiness": float(card.get("easiness") or 2.5),
            "interval": int(card.get("interval") or 0),
            "repetitions": int(card.get("repetitions") or 0),
        }
        meta["updated_at"] = notes_store._now()
        notes_store.save_vault(vault)
    return {"review": meta.get("review") or {}, "note": vault.note_summary(meta)}


@router.get("/reviews/due")
def due_reviews(student_id: str = Depends(resolve_student_id)) -> dict:
    service = m9_manager.get_orchestration_service()
    cards = [c for c in service.due_reviews(student_id)
             if str(c.get("concept_id") or "").startswith("note:")]
    vault = _load_vault(student_id)
    out = []
    for c in cards:
        note_id = str(c.get("concept_id"))[5:]
        meta = vault.find_note(note_id)
        if meta is not None:
            out.append({"note": vault.note_summary(meta), "card": c})
    return {"due": out}


# --- export ---------------------------------------------------------------------------------


@router.get("/notes/{note_id}/export")
def export_note(note_id: str, student_id: str = Depends(resolve_student_id)) -> Response:
    vault = _load_vault(student_id)
    meta = _require_note(vault, note_id)
    md = notes_store.export_markdown(meta, vault.read_note(note_id))
    filename = notes_store._slugify(str(meta.get("title") or "note")) + ".md"
    return Response(content=md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": _content_disposition(filename)})


@router.get("/export")
def export_vault(folder_id: str = Query(""),
                 student_id: str = Depends(resolve_student_id),
                 background: BackgroundTasks = None) -> FileResponse:
    vault = _load_vault(student_id)
    if folder_id and vault.find_folder(folder_id) is None:
        raise HTTPException(404, "文件夹不存在")
    tmp = notes_store.export_zip(vault, folder_id=folder_id)
    folder = vault.find_folder(folder_id)
    filename = "notes_export.zip" if folder is None else \
        notes_store._slugify(str(folder.get("name") or "folder")) + ".zip"

    def _cleanup() -> None:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if background is not None:
        background.add_task(_cleanup)
    return FileResponse(tmp, filename=filename, media_type="application/zip")


# --- agent（生成 + 对话 SSE）------------------------------------------------------


class GenerateRequest(BaseModel):
    template_id: str = Field(..., max_length=64)
    sources: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    instructions: str = Field(default="", max_length=2000)


class NotesChatRequest(BaseModel):
    message: str = Field(..., max_length=8000)
    context: dict[str, Any] = Field(default_factory=dict)
    # 三模式：ask / plan / authorize（旧值 suggest/collab→plan、
    # cowrite/auto→authorize，由 store 归一化；未知值 422 拒绝）
    mode: str = Field("ask", max_length=16)
    # 动作：approve_plan = 批复待批复计划（仅一次）并自动切入授权执行；
    # reject_plan = 驳回计划。空串 = 普通消息。
    action: str = Field("", max_length=32)
    # 图片附件（/notes/upload 返回的 {id, filename}）；MULTIMODAL 配置时
    # 注入视觉通道，未配置时降级用消息内的 <ocr_material> OCR 文本
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=3)


_CHAT_ACTIONS = ("", "approve_plan", "reject_plan")


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: dict[str, Any]) -> str:
    import json as _json
    return (f"event: {event.get('type', 'message')}\n"
            f"data: {_json.dumps(event, ensure_ascii=False)}\n\n")


@router.post("/generate", dependencies=[Depends(rate_limit("notes_generate", 6))])
async def notes_generate(req: GenerateRequest,
                         student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import StreamingResponse
    from app.agents import notes_agent

    async def event_stream():
        if not notes_agent.is_enabled():
            yield _sse({"type": "error",
                        "message": "笔记智能体未启用（NOTES_AGENT_MODE=off）"})
            return
        async for event in notes_agent.generate_note(
                student_id, template_id=req.template_id, sources=req.sources,
                target=req.target, instructions=req.instructions):
            yield _sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.post("/upload", dependencies=[Depends(rate_limit("notes_upload", 20))])
async def notes_upload(files: list[UploadFile] = File(...),
                       student_id: str = Depends(resolve_student_id)):
    """笔记页附件上传（图片 OCR / 文档文本提取，对齐 /chat/upload）。

    存储在 notes/<sid>/uploads/（提取文本 + 原件），供笔记助手的
    knowledge_search 检索（scope notes:<sid>）与 MULTIMODAL 视觉通道
    使用；图片返回 OCR 预览，前端发送时包成 <ocr_material> 前缀。
    """
    import uuid

    from app.core.file_parser import MAX_IMAGE_BYTES, MAX_UPLOAD_BYTES
    from app.core.multimodal_parser import SUPPORTED_ASYNC_EXTS, extract_text_async

    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif")
    results: list[dict[str, Any]] = []
    for f in files[:6]:
        raw = await f.read()
        fname = f.filename or "upload"
        lower = fname.lower()
        ext = next((e for e in SUPPORTED_ASYNC_EXTS if lower.endswith(e)), "")
        if not ext:
            results.append({"filename": fname,
                            "error": "不支持的格式（仅 PDF/DOCX/PPTX/TXT/MD/常见图片）"})
            continue
        limit = MAX_IMAGE_BYTES if ext in image_exts else MAX_UPLOAD_BYTES
        if len(raw) > limit:
            results.append({"filename": fname,
                            "error": f"文件过大（>{limit // (1024 * 1024)}MB）"})
            continue
        extracted = await extract_text_async(fname, raw, purpose="notes")
        text = extracted.text
        if not text.strip():
            results.append({"filename": fname, "error": "无法提取文本",
                            "warning": extracted.warning or None})
            continue
        file_id = uuid.uuid4().hex[:12]
        meta = notes_store.add_upload_file(
            student_id, file_id, fname, text, raw=raw, orig_ext=ext,
            metadata={"source_scope": "notes",
                      "ocr_used": extracted.used_ocr,
                      "ocr_pages": extracted.ocr_pages})
        # 向量轨道（可选）：按 notes:<sid> scope 入库，失败不影响上传
        try:
            from app.core.embedding import get_embedding_client
            embed = get_embedding_client()
            if embed is not None:
                store = notes_store.load_uploads_store(student_id)
                chunks = [c for c in store.chunks
                          if getattr(c, "file_id", "") == file_id]
                if chunks:
                    from app.core.vector_jobs import schedule_index
                    scope = notes_store.uploads_vector_scope(student_id)
                    schedule_index(scope, chunks, embed,
                                   key=f"notes:{student_id}:{file_id}")
        except Exception:
            pass
        results.append({"id": file_id, "filename": fname,
                        "char_count": meta.get("char_count", 0),
                        "chunk_count": meta.get("chunk_count", 0),
                        "ocr_used": extracted.used_ocr,
                        "preview_text": (text[:4000] if ext in image_exts else None)})
    return {"results": results}


@router.post("/chat/stream", dependencies=[Depends(rate_limit("notes_chat", 30))])
async def notes_chat_stream(req: NotesChatRequest,
                            student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import StreamingResponse
    from app.agents import notes_agent

    raw_mode = req.mode.strip().lower()
    if (raw_mode not in notes_store.AGENT_MODES
            and raw_mode not in notes_store._LEGACY_AGENT_MODES):
        raise HTTPException(422, f"未知模式：{req.mode}"
                                  f"（可选 {'/'.join(notes_store.AGENT_MODES)}）")
    if req.action.strip() not in _CHAT_ACTIONS:
        raise HTTPException(422, f"未知操作：{req.action}")

    async def event_stream():
        if not notes_agent.is_enabled():
            yield _sse({"type": "error",
                        "message": "笔记智能体未启用（NOTES_AGENT_MODE=off）"})
            return
        async for event in notes_agent.run_notes_chat(
                student_id, message=req.message, context=req.context,
                mode=raw_mode, action=req.action.strip(),
                attachments=req.attachments):
            yield _sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)
