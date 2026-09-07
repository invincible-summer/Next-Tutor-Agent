"""Textbook library API (P2): 教材上传/列表/详情/编辑/重建/删除。

教材 = Library 文件（kind="textbook"）+ Textbook 注册记录 + M5.7 图谱。上传走
Library.add_file（原件+文本+chunk 立即可检索），同步建 Textbook 记录，图谱构建
经 per-owner 队列 fire-and-forget（同用户严格一本接一本，对话零等待）。教材就
是资料库文件，因此工作区/会话选教材复用既有 file_id 链路（后端零改动，仅前端
分组）。

约定（沿用既有规范）：
  - 全部 Depends(resolve_student_id)，student_id 只来自 JWT；按 id 端点对外人 404。
  - 限流：upload 10/min（照 login/OCR 先例）。
  - 孤儿清理：删教材级联（图谱归档删除 + Library 文件 + 向量 + 记录）；Library
    直删文件时也清教材记录（api/v1/library.py 的孤儿钩子）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.core.library import load_library, library_data_dir
from app.core.file_parser import MAX_UPLOAD_BYTES, SUPPORTED_EXTS, extract_text
from app.core import textbook as tb_store
from app.core.ratelimit import rate_limit
from app.identity.deps import resolve_student_id, optional_user
from app.identity.models import User

log = logging.getLogger(__name__)

#: 教材学段五值（上传时选择；图谱节点按此 stamp，知识谱系按学段分组）。
TEXTBOOK_LEVELS = ("小学", "初中", "高中", "本科", "其他")

router = APIRouter(prefix="/textbooks", tags=["textbooks"])


class TextbookPatch(BaseModel):
    title: str | None = Field(None, max_length=120, description="教材显示名；也是教材组第三级栏目名")
    group_name: str | None = Field(None, max_length=120, description="教材组/栏目名")
    group_note: str | None = Field(None, max_length=500, description="教材组备注")
    subject: str | None = Field(None, max_length=30)
    level: str | None = Field(None, description="学段：空=未指定 / 小学/初中/高中/本科/其他")


class TextbookVolumePatch(BaseModel):
    filename: str = Field(..., min_length=1, max_length=240)


class VolumeGraphLimits(BaseModel):
    max_chapters: int | None = Field(None, ge=1)
    max_concepts: int | None = Field(None, ge=1)


class TextbookGraphPolicy(BaseModel):
    default_max_chapters: int | None = Field(None, ge=1)
    default_max_concepts: int | None = Field(None, ge=1)
    volume_overrides: dict[str, VolumeGraphLimits] = Field(default_factory=dict)


class TextbookRebuildRequest(BaseModel):
    mode: str = Field("rag_graph", description="rag_graph | full_ocr | graph_only")


class TextbookBulkRebuildRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=50,
                           description="教材 id 列表（去重后逐本执行；上限 50）")
    mode: str = Field("rag_graph", description="rag_graph | full_ocr | graph_only")


class TextbookBulkCancelRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=100,
                           description="教材 id 列表（去重后逐本取消；上限 100）")


def _load_owned(student_id: str, tb_id: str) -> tuple[dict, str]:
    """Load a textbook record: 自有优先、公用兜底（404 = invisible）。

    Returns (record, owner_student_id)——公用教材的写操作须另行 admin 校验。
    """
    found = tb_store.find_textbook_scoped(student_id, tb_id)
    if found is None:
        raise HTTPException(404, "教材不存在")
    return found


def _can_public_write(user: User | None, owner_sid: str) -> bool:
    """公用教材的写操作（PATCH/DELETE/rebuild/cancel）仅管理员；自有教材恒可写。"""
    return owner_sid != tb_store.PUBLIC_STUDENT_ID or (
        user is not None and user.role == "admin")


def _require_public_write(user: User | None, owner_sid: str) -> None:
    if not _can_public_write(user, owner_sid):
        raise HTTPException(403, "公用教材仅管理员可操作")


def _textbook_out(tb: dict, owner_sid: str, lib=None) -> dict:
    """Join a textbook record with its Library file info (lazy-filter orphans).

    ``owner_sid`` 是记录所在命名空间（自有=调用者，公用=public）。
    ``lib`` 可注入已加载的 Library（列表端点每命名空间只加载一次，避免
    每条记录重复读盘解析）。教材组（kind=group）：file_id 置空、file_ids
    为卷列表，附 volumes 明细。"""
    if lib is None:
        lib = load_library(owner_sid)
    kind = tb.get("kind", "single")
    out = {
        "id": tb["id"],
        "kind": kind,
        "file_id": (tb.get("file_id") or ((tb.get("file_ids") or [""])[0]
                    if len(tb.get("file_ids") or []) == 1 else "")),
        "file_ids": list(tb.get("file_ids") or ([] if not tb.get("file_id") else [tb["file_id"]])),
        "topic_key": tb["topic_key"],
        "title": tb["title"],
        "group_name": tb.get("group_name") or tb["title"],
        "group_note": tb.get("group_note", ""),
        "subject": tb["subject"],
        "level": tb["level"],
        "scope": tb.get("scope", "private"),
        "status": tb["status"],
        "progress": tb["progress"],
        "chapter_count": tb["chapter_count"],
        "concept_count": tb["concept_count"],
        "graph_policy": tb_store.normalize_graph_policy(tb.get("graph_policy"),
                                                        list(tb.get("file_ids") or [])),
        "coverage": list(tb.get("volumes") or []),
        "warnings": tb["warnings"],
        "error": tb["error"],
        "ocr_state": dict(tb.get("ocr_state") or {}),
        "rag_index": dict(tb.get("rag_index") or {}),
        "created_at": tb["created_at"],
        "updated_at": tb["updated_at"],
    }
    if kind == "group":
        volumes = []
        for fid in tb.get("file_ids") or []:
            meta = lib.find_file(fid)
            volumes.append({
                "file_id": fid,
                "filename": meta.get("filename", "") if meta else "",
                "original_filename": meta.get("original_filename", meta.get("filename", "")) if meta else "",
                "char_count": meta.get("char_count", 0) if meta else 0,
                "has_original": bool(meta and meta.get("orig_ext")) if meta else False,
                "updated_at": meta.get("updated_at", meta.get("created_at", 0)) if meta else 0,
                "effective_limits": tb_store.effective_graph_limits(tb, fid),
                "coverage": next((v for v in (tb.get("volumes") or [])
                                  if v.get("file_id") == fid), None),
            })
        out["volumes"] = volumes
        out["filename"] = ""
        out["char_count"] = sum(v["char_count"] for v in volumes)
        out["has_original"] = any(v["has_original"] for v in volumes)
    else:
        meta = lib.find_file(tb["file_id"])
        out["volumes"] = []
        out["filename"] = meta.get("filename", "") if meta else ""
        out["original_filename"] = meta.get("original_filename", meta.get("filename", "")) if meta else ""
        out["char_count"] = meta.get("char_count", 0) if meta else 0
        out["updated_at"] = meta.get("updated_at", meta.get("created_at", 0)) if meta else 0
        out["has_original"] = bool(meta and meta.get("orig_ext")) if meta else False
    return out


@router.post("/upload", dependencies=[Depends(rate_limit("textbook_upload", 10))])
async def upload_textbooks(files: list[UploadFile] = File(...),
                           level: str = Form("其他"),
                           scope: str = Form("private"),
                           subject: str = Form(""),
                           group: str = Form(""),
                           group_note: str = Form(""),
                           group_id: str = Form(""),
                           default_max_chapters: int | None = Form(None),
                           default_max_concepts: int | None = Form(None),
                           volume_overrides: str = Form(""),
                           user: User | None = Depends(optional_user)):
    """Upload textbook PDF(s). Parse + index synchronously (BM25 immediately
    searchable), register a Textbook record, then fire-and-forget the graph build.

    ``level``：教材学段（小学/初中/高中/本科/其他，默认其他）——用户选择优先于
    骨架 LLM 推断，图谱节点按所选学段 stamp（知识谱系按学段分组）。
    ``scope``：private（默认，个人教材库）/ public（公用教材库，仅管理员，
    所有账号可选用）。公用教材落在保留命名空间 ``public``。
    ``group``：教材组名（可选）——本次全部文件编为一组，建**一个**统一知识
    谱系（多卷合一：跨卷同名概念合并、跨卷前置边）；``group_id``：追加到
    既有组（自动重建组图谱，已 OCR 卷零重 OCR）。

    Mirrors api/v1/library.py upload's per-file error handling (200 with errors).
    """
    from app.core.library import Library, save_library, file_scope
    from app.core.config import settings
    from app.core import pdf_ocr
    from app.agents.teaching_engine.stage_profile import normalize_grade, VALID_STAGES
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    lvl = normalize_grade(level)
    if lvl not in VALID_STAGES and lvl != "其他":
        raise HTTPException(400, f"level 必须是 {list(TEXTBOOK_LEVELS)} 之一")
    scope = (scope or "private").strip()
    if scope not in tb_store.SCOPES:
        raise HTTPException(400, "scope 必须是 private/public 之一")
    if scope == "public":
        if user is None or user.role != "admin":
            raise HTTPException(403, "公用教材库仅管理员可上传")
        student_id = tb_store.PUBLIC_STUDENT_ID
    else:
        student_id = user.id if user else DEFAULT_STUDENT_ID
    subject = (subject or "").strip()[:30]
    group = (group or "").strip()[:120]
    group_note = (group_note or "").strip()[:500]
    group_id = (group_id or "").strip()
    target_group = None
    if group_id:
        target_group = tb_store.find_textbook(student_id, group_id)
        if target_group is None or target_group.get("kind") != "group":
            raise HTTPException(404, "教材组不存在")
    # All uploads use the group model, including a single file. This keeps the
    # taxonomy and policy model uniform without creating a book-title graph node.
    group_mode = True
    try:
        raw_overrides = json.loads(volume_overrides) if volume_overrides else {}
        if not isinstance(raw_overrides, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, "volume_overrides 必须是 JSON 对象")
    lib = load_library(student_id)
    results: list[dict] = []
    group_file_ids: list[str] = []
    for f in files:
        raw = await f.read()
        fname = f.filename or "textbook"
        lower = fname.lower()
        ext = next((e for e in SUPPORTED_EXTS if lower.endswith(e)), "")
        if not ext:
            results.append({"filename": fname, "error": "不支持的格式（仅 PDF/DOCX/PPTX/TXT/MD）"})
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            results.append({"filename": fname, "error": f"文件过大（>{MAX_UPLOAD_BYTES // (1024 * 1024)}MB）"})
            continue
        # ocr_fallback=False：教材扫描 PDF 由 textbook_builder 后台 async OCR
        # （视觉模型优先），同步段只取文本层判定是否需要 OCR，避免重复 OCR。
        # to_thread：大 PDF 的文本提取是 CPU 密集，不阻塞事件循环。
        text = await asyncio.to_thread(extract_text, fname, raw, ocr_fallback=False)
        # OCR 判定（P5a-A2 逐页 + P8 乱码页）：文本层为空 → 扫描版仍接收（后台
        # OCR 写回 .txt 再图谱构建）；文本层非空但存在稀疏页（半文本半扫描）或
        # 稠密乱码页（定制字体无 ToUnicode 映射，取证见 core/text_quality）→ 同样
        # 标记，后台逐页择优补 OCR（良好文本层页不被降质）。off 或非 PDF 仍按
        # "无法提取文本"拒。
        needs_ocr = False
        if ext == ".pdf" and settings.pdf_ocr_mode != "off":
            if settings.pdf_ocr_mode == "on":
                needs_ocr = True
            elif not text.strip():
                needs_ocr = await asyncio.to_thread(pdf_ocr.is_scanned_pdf, raw)
            else:
                needs_ocr = bool(pdf_ocr.pages_needing_ocr(text.split("\f")))
        if not text.strip() and not needs_ocr:
            results.append({"filename": fname, "error": "无法提取文本"})
            continue
        meta = lib.add_file("", fname, text, raw=raw, orig_ext=ext)  # text 可为空，待后台 OCR 写回
        # 标记为教材（资料默认私有；选入工作区/会话才检索）。
        meta["kind"] = "textbook"
        if settings.rag_chunker_mode == "v2" and text.strip():
            from app.core.structured_chunker import CHUNK_SCHEMA_VERSION, chunk_text_v2
            chunks = chunk_text_v2(text, source=fname, file_id=meta["id"])
            lib.chunks_by_file[meta["id"]] = chunks
            meta["chunk_schema"] = CHUNK_SCHEMA_VERSION
            meta["chunk_count"] = len(chunks)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            meta["rag_index"] = {
                "version": "rag-v2", "chunk_schema": CHUNK_SCHEMA_VERSION,
                "content_sha256": content_hash, "chunk_count": len(chunks),
                "bm25_revision": f"rag-v2:{content_hash[:16]}",
                "vector_revision": "pending", "status": "bm25_ready",
                "updated_at": time.time(),
            }
        if group_mode:
            # 教材组：只收集卷文件，统一建组后一次构建（组名自定义）。
            group_file_ids.append(meta["id"])
            results.append({"filename": fname, "status": "building"})
            continue
    save_library(lib)
    # BM25 已持久化；向量在注册教材组后进入单槽后台队列。
    if group_mode:
        grp = target_group
        if grp is not None:
            grp = tb_store.add_group_files(student_id, grp["id"], group_file_ids)
        elif group_file_ids:
            mapped_overrides: dict[str, dict] = {}
            for index, fid in enumerate(group_file_ids):
                value = raw_overrides.get(str(index), raw_overrides.get(index))
                if isinstance(value, dict):
                    mapped_overrides[fid] = value
            auto_group = group or Path(str(results[0].get("filename") or "教材")).stem
            grp = tb_store.create_group(student_id, file_ids=group_file_ids,
                                        title=auto_group or "未命名教材组",
                                        subject=subject, group_note=group_note,
                                        level=lvl, scope=scope,
                                        graph_policy={
                                            "default_max_chapters": default_max_chapters,
                                            "default_max_concepts": default_max_concepts,
                                            "volume_overrides": mapped_overrides,
                                        })
        if grp is not None and target_group is not None:
            patch_fields = {}
            if subject:
                patch_fields["subject"] = subject
            if group_note:
                patch_fields["group_note"] = group_note
            if patch_fields:
                grp = tb_store.update_textbook(student_id, grp["id"], **patch_fields) or grp
        if grp is not None:
            for r in results:
                r["group_id"] = grp["id"]
                r.setdefault("id", grp["id"])
            try:
                from app.core.rag_index import summarize_textbook_rag
                tb_store.update_textbook(student_id, grp["id"],
                                         rag_index=summarize_textbook_rag(student_id, grp))
            except Exception:
                pass
            await _ingest_vectors(lib, student_id, group_file_ids)
            _spawn_build(student_id, grp["id"],
                         ocr_parallel=_effective_ocr_parallel(user))
        return {"results": results}
    for r in results:
        if "id" in r:
            _spawn_build(student_id, r["id"],
                         ocr_parallel=_effective_ocr_parallel(user))
    return {"results": results}


def _effective_ocr_parallel(user: User | None) -> bool:
    """Textbook background OCR always uses the system administrator policy."""
    return True


def _spawn_build(student_id: str, tb_id: str, *, ocr_parallel: bool = False,
                 force_reextract: bool = False, use_llm: bool = True) -> None:
    """Enqueue a fire-and-forget graph build（对话零等待）.

    走 per-owner 构建队列：同一用户的自动构建严格一本接一本——队首教材到达
    终态（ready/failed/ocr_paused 等）后才开建下一本，避免多本书同时停在
    「等重试、建一半」。手动刷新（rebuild_graph 三模式）与失败重试同样经
    该队列，构成唯一串行驱动；刷新点击之间另由 per-student 刷新锁互斥。
    """
    from app.agents.knowledge.textbook_builder import enqueue_textbook_build
    if not enqueue_textbook_build(student_id, tb_id, ocr_parallel=ocr_parallel,
                                  force_reextract=force_reextract, use_llm=use_llm):
        # No running loop (e.g. sync test context): build can be triggered later
        # via rebuild_graph. Not an error for the upload itself.
        log.warning("textbook build enqueue skipped (no running event loop): %s", tb_id)


async def _safe_build(student_id: str, tb_id: str, *, ocr_parallel: bool = False,
                      force_reextract: bool = False, use_llm: bool = True,
                      skip_ocr: bool = False, skip_harvest: bool = False,
                      force_full_ocr: bool = False) -> None:
    """手动刷新同样经 per-owner 构建队列（串行契约：先构建完当前书）。

    等待该次构建（含队列门控至终态，full_ocr 模式的重试轮也由门控驱动）；
    同步测试上下文（无事件循环）回退为直连 run_textbook_build。per-student
    刷新锁在上层持有，防止重复刷新点击互相插队。
    """
    from app.agents.knowledge.textbook_builder import (enqueue_textbook_build,
                                                       run_textbook_build)
    future = enqueue_textbook_build(student_id, tb_id, ocr_parallel=ocr_parallel,
                                    force_reextract=force_reextract,
                                    use_llm=use_llm, skip_ocr=skip_ocr,
                                    skip_harvest=skip_harvest,
                                    force_full_ocr=force_full_ocr)
    if future is not None:
        await future
        return
    await run_textbook_build(student_id, tb_id, ocr_parallel=ocr_parallel,
                             force_reextract=force_reextract, use_llm=use_llm,
                             skip_ocr=skip_ocr, skip_harvest=skip_harvest,
                             force_full_ocr=force_full_ocr)


async def _reindex_rag_after_build(student_id: str, tb_id: str) -> None:
    """构建后补一次 RAG 重建：构建可能经「原生图表收割」改写 .txt（文本层
    PDF 的表格/插图并入事实源），若先建 RAG 后构建，rag_index hash 会与最终
    .txt 不一致（语文必修实测）。失败只记 warning，绝不阻断。"""
    try:
        from app.core.rag_index import (rebuild_textbook_rag,
                                        refresh_textbook_vectors,
                                        summarize_textbook_rag)
        current = tb_store.find_textbook(student_id, tb_id) or {}
        if current.get("status") != "ready":
            return
        rag = await asyncio.to_thread(rebuild_textbook_rag, student_id, current, force=True)
        tb_store.update_textbook(student_id, tb_id, rag_index=rag)
        await refresh_textbook_vectors(student_id, current)
        tb_store.update_textbook(student_id, tb_id,
                                 rag_index=summarize_textbook_rag(student_id, current))
    except Exception as exc:
        log.warning("post-build rag reindex skipped: %s", tb_id)


async def _safe_refresh_inner(student_id: str, tb_id: str, mode: str,
                              *, ocr_parallel: bool = True) -> None:
    """Run one explicit refresh mode without ever changing the source contract."""
    try:
        tb = tb_store.find_textbook(student_id, tb_id)
        if tb is None:
            return
        # 取消标记在此入口绝不自行清除——任务可能曾在 owner 锁上排队等待，
        # 期间用户取消了它；若这里清标记，等待中的任务会在取消后照常开跑
        # （实测竞态）。标记由 rebuild_graph 端点（新意图）或 run_textbook_build
        # 开始处清除；这里只观测：已取消 → 结算并退出。
        if tb.get("parse_cancel_requested"):
            tb_store.settle_cancelled_parse(student_id, tb_id)
            return
        if mode == "rag_graph":
            from app.core.rag_index import (rebuild_textbook_rag,
                                            refresh_textbook_vectors)
            rag = await asyncio.to_thread(rebuild_textbook_rag, student_id, tb, force=True)
            tb_store.update_textbook(student_id, tb_id, rag_index=rag,
                                     progress={"stage": "index", "done": 1, "total": 2})
            await refresh_textbook_vectors(student_id, tb)
            from app.core.rag_index import summarize_textbook_rag
            tb_store.update_textbook(student_id, tb_id,
                                     rag_index=summarize_textbook_rag(student_id, tb))
            try:
                from app.agents.knowledge.textbook_builder import rebuild_concept_index_from_active
                rebuild_concept_index_from_active(student_id, tb)
            except Exception:
                pass
            # Group cache re-merge refreshes concept->chunks without OCR/LLM.
            # A legacy single has no volume cache, so use the LLM but still skip OCR.
            await _safe_build(student_id, tb_id, ocr_parallel=ocr_parallel,
                              force_reextract=False, use_llm=True, skip_ocr=True)
            # rag_graph 允许原生收割改写 .txt（表格/插图并入事实源）——事后
            # 重跑一次 RAG 重建，保证 rag_index hash 与最终 .txt 一致。
            await _reindex_rag_after_build(student_id, tb_id)
        elif mode == "graph_only":
            # 只重建图谱：跳过收割（不改 .txt 事实源）→ RAG 切片/索引完全不动。
            await _safe_build(student_id, tb_id, ocr_parallel=ocr_parallel,
                              force_reextract=True, use_llm=True, skip_ocr=True,
                              skip_harvest=True)
        else:  # full_ocr / quality_ocr
            from app.core.textbook_ocr import cancel_textbook_ocr
            cancel_textbook_ocr(student_id, tb_id)
            # 两种模式都清 ocr_state（调度器按新意图重建目标页集合）：
            # full_ocr 整本重试；quality_ocr 只针对当前文本的稀疏∪乱码页
            # （text_quality verdict），良好页保留——修复定制字体稠密乱码卷
            # 而不整本烧 OCR。
            tb_store.update_textbook(student_id, tb_id, ocr_state={})
            await _safe_build(student_id, tb_id, ocr_parallel=ocr_parallel,
                              force_reextract=True, use_llm=True,
                              skip_ocr=False, force_full_ocr=(mode == "full_ocr"))
            current = tb_store.find_textbook(student_id, tb_id) or {}
            if current.get("status") == "ready":
                from app.core.rag_index import (rebuild_textbook_rag,
                                                refresh_textbook_vectors)
                rag = await asyncio.to_thread(rebuild_textbook_rag, student_id, current, force=True)
                tb_store.update_textbook(student_id, tb_id, rag_index=rag)
                await refresh_textbook_vectors(student_id, current)
                from app.core.rag_index import summarize_textbook_rag
                tb_store.update_textbook(student_id, tb_id,
                                         rag_index=summarize_textbook_rag(student_id, current))
    except Exception as exc:
        log.exception("textbook refresh failed: %s", tb_id)
        tb_store.update_textbook(student_id, tb_id, status="graph_failed",
                                 error=f"刷新失败：{exc}")


_REFRESH_OWNER_LOCKS: dict[str, asyncio.Lock] = {}
_REFRESH_OWNER_LOCKS_GUARD = asyncio.Lock()


async def _refresh_owner_lock(student_id: str) -> asyncio.Lock:
    async with _REFRESH_OWNER_LOCKS_GUARD:
        lock = _REFRESH_OWNER_LOCKS.get(student_id)
        if lock is None:
            lock = asyncio.Lock()
            _REFRESH_OWNER_LOCKS[student_id] = lock
        return lock


async def _safe_refresh(student_id: str, tb_id: str, mode: str,
                        *, ocr_parallel: bool = True) -> None:
    """Serialize derived refreshes per owner to avoid lost JSON updates."""
    lock = await _refresh_owner_lock(student_id)
    async with lock:
        await _safe_refresh_inner(
            student_id, tb_id, mode, ocr_parallel=ocr_parallel)


def _spawn_refresh(student_id: str, tb_id: str, mode: str,
                   *, ocr_parallel: bool = True) -> bool:
    if tb_store.refresh_task_running(student_id, tb_id):
        return False
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_safe_refresh(
            student_id, tb_id, mode, ocr_parallel=ocr_parallel))
        tb_store.register_refresh_task(student_id, tb_id, task)
        task.add_done_callback(
            lambda done: tb_store.finish_refresh_task(student_id, tb_id, done))
        return True
    except RuntimeError:
        log.warning("textbook refresh spawn skipped (no running event loop): %s", tb_id)
        return False


async def _ingest_vectors(lib, student_id: str, file_ids: list[str]) -> None:
    """Queue only freshly uploaded textbook vectors; BM25 is already usable."""
    if not file_ids:
        return
    try:
        from app.core.rag_index import refresh_textbook_vectors
        await refresh_textbook_vectors(
            student_id, {"id": "", "file_ids": list(file_ids)})
    except Exception:
        pass


@router.get("")
def list_textbooks(student_id: str = Depends(resolve_student_id)):
    """自有 + 公用教材（lazy-filter: 文件已被直删的孤儿记录不返回；
    教材组只要还剩一卷就保留）。"""
    out = []
    for sid in (student_id, tb_store.PUBLIC_STUDENT_ID):
        lib = load_library(sid)
        existing = {f["id"] for f in lib.files}
        for tb in tb_store.load_textbooks(sid):
            if tb.get("kind") == "group":
                if not any(fid in existing for fid in (tb.get("file_ids") or [])):
                    continue  # 组卷全灭 → 孤儿
            elif tb["file_id"] not in existing:
                continue  # orphan (library file deleted directly)
            out.append(_textbook_out(tb, sid, lib))
    return {"textbooks": out}


@router.get("/{textbook_id}")
def get_textbook(textbook_id: str, student_id: str = Depends(resolve_student_id)):
    """Detail: record + chapter outline (derived from the graph payload's PART_OF)."""
    from app.agents.knowledge.textbook_builder import textbook_outline
    tb, owner_sid = _load_owned(student_id, textbook_id)
    outline = textbook_outline(owner_sid, textbook_id) or []
    return {"textbook": _textbook_out(tb, owner_sid), "outline": outline}


@router.get("/{textbook_id}/figure-status")
def textbook_figure_status(textbook_id: str,
                           student_id: str = Depends(resolve_student_id)):
    """图表/印刷页码标记状态（P7 图表结构化）：各卷 .txt 是否已含
    ``[图``/``[页码=`` 标记。旧书（无标记）只复用现有转录文本，刷新弹窗
    据此默认推荐「完整重新 OCR」升级一次；新转录/收割后恢复 rag_graph。"""
    tb, owner_sid = _load_owned(student_id, textbook_id)
    lib = load_library(owner_sid)
    fids = ((tb.get("file_ids") or []) if tb.get("kind") == "group"
            else ([tb["file_id"]] if tb.get("file_id") else []))
    volumes = []
    for fid in fids:
        meta = lib.find_file(fid)
        if meta is None:
            continue
        has = False
        try:
            text = (library_data_dir(owner_sid) / f"{fid}.txt").read_text(
                encoding="utf-8")
            has = any(m in text for m in ("[图", "［图", "[页码=", "［页码=", "图述"))
        except OSError:
            has = False
        volumes.append({"file_id": fid, "filename": meta.get("filename", ""),
                        "has_markers": has})
    return {"status": "ok",
            "has_markers": bool(volumes) and all(v["has_markers"] for v in volumes),
            "volumes": volumes}


@router.get("/{textbook_id}/quality")
def textbook_quality(textbook_id: str, student_id: str = Depends(resolve_student_id)):
    """逐卷文本层质量报告（只读，零 OCR 成本）：verdict 统计 + 乱码率。

    P8 路由修复的验证入口：稠密乱码卷（定制字体无 ToUnicode 映射，字符量
    达标但内容不可用）在此显形为 corrupt-heavy；确认为坏卷后可用
    ``POST /{textbook_id}/rebuild_graph mode=quality_ocr`` 手动重建——按
    verdict 逐页择优（稀疏∪乱码页 OCR，良好页保留），不整本重试。
    """
    tb, owner_sid = _load_owned(student_id, textbook_id)
    file_ids = ((tb.get("file_ids") or []) if tb.get("kind") == "group"
                else [tb.get("file_id")] if tb.get("file_id") else [])
    from app.core.text_quality import summarize_pages
    lib = load_library(owner_sid)
    volumes: list[dict] = []
    totals = {"good": 0, "corrupt": 0, "sparse": 0, "empty": 0}
    for fid in file_ids:
        meta = lib.find_file(str(fid)) or {}
        path = library_data_dir(owner_sid) / f"{fid}.txt"
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            text = ""
        summary = summarize_pages(text.split("\f")) if text else {
            "total": 0, "good": 0, "corrupt": 0, "sparse": 0, "empty": 0,
            "garble_rate": 0.0}
        for key in totals:
            totals[key] += int(summary.get(key, 0))
        staging = dict((meta.get("rag_index") or {}).get("staging_quality") or {})
        volumes.append({
            "file_id": str(fid), "filename": meta.get("filename", ""),
            "chars": len(text), "text_quality": summary,
            "staging_quality": str(staging.get("status") or ""),
        })
    non_empty = totals["good"] + totals["corrupt"] + totals["sparse"]
    corrupt_ratio = (totals["corrupt"] / non_empty) if non_empty else 0.0
    return {
        "textbook_id": textbook_id, "volumes": volumes, "page_verdicts": totals,
        "corrupt_ratio": round(corrupt_ratio, 4),
        "recommended_mode": ("quality_ocr" if corrupt_ratio >= 0.10 else "rag_graph"),
    }


@router.get("/{textbook_id}/download")
def download_textbook(textbook_id: str, student_id: str = Depends(resolve_student_id)):
    """下载教材原件（复用 library 的下载响应，保留原文件名）。公用教材所有账号可下载。"""
    from app.api.v1.library import _download_response
    tb, owner_sid = _load_owned(student_id, textbook_id)
    lib = load_library(owner_sid)
    meta = lib.find_file(tb["file_id"])
    if meta is None:
        raise HTTPException(404, "教材文件不存在")
    return _download_response(library_data_dir(owner_sid), meta)


@router.get("/{group_id}/volumes/{file_id}/download")
def download_group_volume(group_id: str, file_id: str,
                          student_id: str = Depends(resolve_student_id)):
    """下载教材组某卷原件（公用组所有账号可下载）。"""
    from app.api.v1.library import _download_response
    grp, owner_sid = _load_owned(student_id, group_id)
    if grp.get("kind") != "group" or file_id not in (grp.get("file_ids") or []):
        raise HTTPException(404, "该卷不在教材组中")
    lib = load_library(owner_sid)
    meta = lib.find_file(file_id)
    if meta is None:
        raise HTTPException(404, "教材文件不存在")
    return _download_response(library_data_dir(owner_sid), meta)


@router.patch("/{textbook_id}")
def patch_textbook(textbook_id: str, req: TextbookPatch,
                   student_id: str = Depends(resolve_student_id),
                   user: User | None = Depends(optional_user)):
    _tb, owner_sid = _load_owned(student_id, textbook_id)
    _require_public_write(user, owner_sid)
    fields: dict = {}
    if req.title is not None:
        fields["title"] = req.title
    if req.group_name is not None:
        fields["group_name"] = req.group_name
    if req.group_note is not None:
        fields["group_note"] = req.group_note
    if req.subject is not None:
        fields["subject"] = req.subject
    if req.level is not None:
        from app.agents.teaching_engine.stage_profile import VALID_STAGES, normalize_grade
        lvl = normalize_grade(req.level)
        if lvl and lvl not in VALID_STAGES and lvl != "其他":
            raise HTTPException(400, f"level 必须是 {list(TEXTBOOK_LEVELS)} 之一或空")
        fields["level"] = lvl
    if not fields:
        raise HTTPException(400, "至少提供一项要修改的字段")
    updated = tb_store.update_textbook(owner_sid, textbook_id, **fields)
    return {"textbook": _textbook_out(updated, owner_sid)}


def _start_rebuild(owner_sid: str, tb_id: str, mode: str,
                   *, ocr_parallel: bool = True) -> dict:
    """幂等发起一本重建（单本与批量端点共用；mode 须已校验）。

    调用方负责 404/admin 校验；执行经 per-owner 刷新锁 + 构建队列串行。
    """
    from app.core.textbook_ocr import cancel_textbook_ocr
    if tb_store.refresh_task_running(owner_sid, tb_id):
        return {"textbook_id": tb_id, "status": "building", "mode": mode,
                "idempotent_reuse": True,
                "uses_existing_text": mode != "full_ocr",
                "ocr_requested": mode in ("full_ocr", "quality_ocr")}
    cancel_textbook_ocr(owner_sid, tb_id)
    # 用户主动发起重建 = 新意图：清旧的终止标记（否则上次取消会杀死本次重建）。
    ocr_mode = mode in ("full_ocr", "quality_ocr")
    tb_store.update_textbook(owner_sid, tb_id, status="building",
                             progress={"stage": "ocr" if ocr_mode else "index",
                                       "done": 0, "total": 1},
                             error="", warnings=[],
                             parse_cancel_requested=False)
    _spawn_refresh(owner_sid, tb_id, mode, ocr_parallel=ocr_parallel)
    return {"textbook_id": tb_id, "status": "building", "mode": mode,
            "idempotent_reuse": False,
            "uses_existing_text": mode != "full_ocr",
            "ocr_requested": ocr_mode}


def _cancel_parse_core(owner_sid: str, tb_id: str) -> str:
    """合作式取消核心：OCR resume 任务 + 进程内刷新任务 + 标记 + 就地结算。

    返回结算后的终态 record status；单本端点保持幂等语义（空闲书无副作用）。
    """
    from app.core.textbook_ocr import cancel_textbook_ocr
    cancel_textbook_ocr(owner_sid, tb_id)
    tb_store.cancel_refresh_task(owner_sid, tb_id)
    tb_store.update_textbook(owner_sid, tb_id, parse_cancel_requested=True)
    return tb_store.settle_cancelled_parse(owner_sid, tb_id)


@router.post("/bulk/rebuild")
async def bulk_rebuild(req: TextbookBulkRebuildRequest | None = None,
                       student_id: str = Depends(resolve_student_id),
                       user: User | None = Depends(optional_user)):
    """批量重建：逐本幂等发起（missing/forbidden 分级回报，不整体失败）。

    执行语义与单本 rebuild_graph 完全一致（per-owner 刷新锁 + 构建队列
    串行、逐章合作取消检查点）；默认 rag_graph 复用既有解析/OCR 文本，
    不会重新 OCR。注意：本路由必须声明在 /{textbook_id}/cancel 等
    参数化路由之前（否则 "bulk" 会被当作 textbook_id 吞掉）。
    """
    ids = list(dict.fromkeys((req.ids if req else []) or []))
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    mode = str((req.mode if req else "rag_graph") or "rag_graph").strip().lower()
    if mode not in {"rag_graph", "full_ocr", "quality_ocr", "graph_only"}:
        raise HTTPException(400, "mode 必须是 rag_graph/full_ocr/quality_ocr/graph_only")
    ocr_parallel = _effective_ocr_parallel(user)
    results: list[dict] = []
    for tb_id in ids:
        try:
            _tb, owner_sid = _load_owned(student_id, tb_id)
        except HTTPException:
            results.append({"textbook_id": tb_id, "status": "missing"})
            continue
        if not _can_public_write(user, owner_sid):
            results.append({"textbook_id": tb_id, "status": "forbidden"})
            continue
        results.append(_start_rebuild(owner_sid, tb_id, mode,
                                      ocr_parallel=ocr_parallel))
    return {"status": "ok", "mode": mode, "count": len(results),
            "results": results}


@router.post("/bulk/cancel")
def bulk_cancel(req: TextbookBulkCancelRequest | None = None,
                student_id: str = Depends(resolve_student_id),
                user: User | None = Depends(optional_user)):
    """批量取消：只对活动态（building/ocr_waiting/ocr_paused）执行合作式取消；
    空闲教材跳过（不置取消标记，避免留 stale flag），分级回报。"""
    ids = list(dict.fromkeys((req.ids if req else []) or []))
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    results: list[dict] = []
    for tb_id in ids:
        try:
            tb, owner_sid = _load_owned(student_id, tb_id)
        except HTTPException:
            results.append({"textbook_id": tb_id, "status": "missing"})
            continue
        if not _can_public_write(user, owner_sid):
            results.append({"textbook_id": tb_id, "status": "forbidden"})
            continue
        if tb.get("status") not in ("building", "ocr_waiting", "ocr_paused"):
            results.append({"textbook_id": tb_id, "status": "skipped",
                            "record_status": tb.get("status", "")})
            continue
        final = _cancel_parse_core(owner_sid, tb_id)
        results.append({"textbook_id": tb_id, "status": "cancelled",
                        "record_status": final})
    return {"status": "ok", "count": len(results), "results": results}


@router.post("/{textbook_id}/rebuild_graph")
async def rebuild_graph(textbook_id: str, req: TextbookRebuildRequest | None = None,
                        student_id: str = Depends(resolve_student_id),
                        user: User | None = Depends(optional_user)):
    """Refresh derived textbook assets. Default ``rag_graph`` never invokes OCR."""
    _tb, owner_sid = _load_owned(student_id, textbook_id)
    _require_public_write(user, owner_sid)
    mode = str((req.mode if req else "rag_graph") or "rag_graph").strip().lower()
    if mode not in {"rag_graph", "full_ocr", "quality_ocr", "graph_only"}:
        raise HTTPException(400, "mode 必须是 rag_graph/full_ocr/quality_ocr/graph_only")
    return _start_rebuild(owner_sid, textbook_id, mode,
                          ocr_parallel=_effective_ocr_parallel(user))


@router.post("/{textbook_id}/cancel")
def cancel_parse(textbook_id: str,
                 student_id: str = Depends(resolve_student_id),
                 user: User | None = Depends(optional_user)):
    """Cancel in-flight textbook parsing (OCR rounds / resume scheduler / refresh).

    合作式终止：先置 parse_cancel_requested 标记（构建各检查点观测后停），
    再取消进程内任务（resume 任务 + refresh 任务），最后就地结算终态——
    任一卷已有可用文本则 ready（文本/切片保留可检索），否则 failed。
    幂等：对空闲教材调用无副作用。
    """
    _tb, owner_sid = _load_owned(student_id, textbook_id)
    _require_public_write(user, owner_sid)
    final = _cancel_parse_core(owner_sid, textbook_id)
    return {"status": "cancelled", "textbook_id": textbook_id,
            "record_status": final}


@router.get("/{textbook_id}/graph-policy")
def get_graph_policy(textbook_id: str, student_id: str = Depends(resolve_student_id)):
    tb, owner_sid = _load_owned(student_id, textbook_id)
    return {"textbook_id": textbook_id, "scope": tb.get("scope", "private"),
            "graph_policy": tb_store.normalize_graph_policy(
                tb.get("graph_policy"), list(tb.get("file_ids") or [])),
            "volumes": _textbook_out(tb, owner_sid).get("volumes", [])}


@router.put("/{textbook_id}/graph-policy")
async def put_graph_policy(textbook_id: str, req: TextbookGraphPolicy,
                           student_id: str = Depends(resolve_student_id),
                           user: User | None = Depends(optional_user)):
    tb, owner_sid = _load_owned(student_id, textbook_id)
    _require_public_write(user, owner_sid)
    allowed = set(tb.get("file_ids") or [])
    if any(fid not in allowed for fid in req.volume_overrides):
        raise HTTPException(404, "教材文件不属于该教材组")
    policy = tb_store.normalize_graph_policy(req.model_dump(), list(allowed))
    tb_store.update_textbook(owner_sid, textbook_id, graph_policy=policy,
                             status="building",
                             progress={"stage": "merge", "done": 0, "total": 1})
    _spawn_build(owner_sid, textbook_id, ocr_parallel=True,
                 force_reextract=False, use_llm=False)
    return {"status": "building", "textbook_id": textbook_id,
            "graph_policy": policy, "mode": "cache_remerge"}


@router.delete("/{textbook_id}")
def delete_textbook(textbook_id: str, student_id: str = Depends(resolve_student_id),
                    user: User | None = Depends(optional_user)):
    """Archive the textbook/group and its graph as one recoverable bundle."""
    tb, owner_sid = _load_owned(student_id, textbook_id)
    _require_public_write(user, owner_sid)
    from app.core.trash import archive_textbook
    item = archive_textbook(owner_sid, textbook_id)
    try:
        from app.agents.knowledge.manager import get_knowledge_service
        get_knowledge_service().invalidate_custom_cache(owner_sid)
    except Exception:
        pass
    return {"status": "archived", "textbook_id": textbook_id, "trash_item": item}


@router.patch("/{textbook_id}/volumes/{file_id}")
def patch_textbook_volume(textbook_id: str, file_id: str, req: TextbookVolumePatch,
                          student_id: str = Depends(resolve_student_id),
                          user: User | None = Depends(optional_user)):
    """Rename the PDF display name without re-OCR/re-index/rebuilding the graph."""
    tb, owner_sid = _load_owned(student_id, textbook_id)
    _require_public_write(user, owner_sid)
    allowed = file_id in ((tb.get("file_ids") or []) if tb.get("kind") == "group" else [tb.get("file_id")])
    if not allowed:
        raise HTTPException(404, "文件不属于该教材")
    lib = load_library(owner_sid)
    meta = lib.find_file(file_id)
    if meta is None:
        raise HTTPException(404, "教材文件不存在")
    if not lib.rename_file(file_id, req.filename):
        raise HTTPException(400, "文件名不能为空")
    from app.core.library import save_library
    save_library(lib)
    return {"status": "ok", "file_id": file_id, "filename": meta["filename"]}


@router.delete("/{group_id}/volumes/{file_id}")
async def delete_group_volume(group_id: str, file_id: str,
                              student_id: str = Depends(resolve_student_id),
                              user: User | None = Depends(optional_user)):
    """移除教材组的一卷：删该卷文件/向量 + 从组卷表移除；剩余卷自动重建组
    图谱（已 OCR 卷零重 OCR），删空则整组删除。公用组仅管理员。"""
    grp, owner_sid = _load_owned(student_id, group_id)
    _require_public_write(user, owner_sid)
    if grp.get("kind") != "group":
        raise HTTPException(400, "仅教材组支持卷操作")
    if file_id not in (grp.get("file_ids") or []):
        raise HTTPException(404, "该卷不在教材组中")
    # 最后一卷：整组删除（delete_textbook 级联含该卷文件+图谱+记录）——
    # 不能先 remove_group_file：空卷组记录在 sanitize 即不可见，级联会 404。
    if len(grp.get("file_ids") or []) <= 1:
        delete_textbook(group_id, student_id=student_id, user=user)
        return {"status": "deleted", "group_id": group_id, "empty": True}
    # 归档单卷，保留教材组其余卷；活动图谱由下一次构建替换。
    from app.core.trash import archive_textbook_volume
    item = archive_textbook_volume(owner_sid, group_id, file_id)
    # 剩余卷重建组图谱（后台）
    tb_store.update_textbook(owner_sid, group_id, status="building",
                             progress={"stage": "index", "done": 0, "total": 1},
                             error="", warnings=[])
    _spawn_build(owner_sid, group_id,
                 ocr_parallel=_effective_ocr_parallel(user))
    remaining = [f for f in (grp.get("file_ids") or []) if f != file_id]
    return {"status": "building", "archive_status": "archived",
            "group_id": group_id, "remaining": remaining, "trash_item": item}
