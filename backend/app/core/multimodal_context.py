"""对话多模态上下文（B4）：本轮含图时把 tutor LLM 切到 MULTIMODAL 通道。

含图来源（去重限量）：
  1. 本轮消息的图片附件原件（会话库 ``<file_id>.orig<ext>``）；
  2. RAG 证据中的图表块页快照（figure/table 命中时从教材/资料原件
     ``.orig`` 按需渲染）。

路由契约：
  - 仅当 ``MULTIMODAL_API_KEY`` 已配置才切换（AsyncLLMClient 参数化实例，
    模型/端点用 MULTIMODAL_*，未配置时降级纯文本路径不报错）；
  - 多模态轮**开启思考推理**（不传 disable_thinking）——看图讲题需要推理，
    与教材 OCR 的"提取任务关思考"策略相反（AGENTS §6 抽取型小调用关思考）；
  - 图片只注入**最后一条 user 消息**（content parts），会话历史/持久化
    仍存纯文本（不污染 token 估算与压缩管线）。
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from .config import settings

log = logging.getLogger(__name__)

# 单轮注入图片上限（token 护栏：附件优先，其余给 RAG 图表快照）。
MAX_CONTEXT_IMAGES = 3
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB（原图过大直接跳过）

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def _data_url(raw: bytes) -> str | None:
    if len(raw) > _MAX_IMAGE_BYTES:
        return None
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return (f"data:{mime};base64,"
                    + base64.b64encode(raw).decode("ascii"))
    return None


def attachment_context_images(session: Any, limit: int = MAX_CONTEXT_IMAGES) -> list[str]:
    """本轮图片附件 → data URL 列表（≤limit；原图不可得时静默降级）。"""
    try:
        last = next((m for m in reversed(getattr(session, "messages", []) or [])
                     if m.get("role") == "user"), None)
        attachments = (last or {}).get("attachments") or []
        store = getattr(session, "knowledge", None)
        if store is None:
            return []
        out: list[str] = []
        for item in attachments:
            if len(out) >= limit:
                break
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id", ""))
            meta = next((f for f in store.files if f.get("id") == fid), None)
            if meta is None or not str(meta.get("orig_ext", "")).startswith("."):
                continue
            fname = str(meta.get("filename", "")).lower()
            if not any(fname.endswith(e) for e in (
                    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif")):
                continue
            fp = store.upload_dir / f"{fid}.orig{meta['orig_ext']}"
            try:
                url = _data_url(fp.read_bytes())
            except OSError:
                url = None
            if url:
                out.append(url)
        return out
    except Exception as exc:
        log.warning("attachment context images skipped: %s", exc)
        return []


def evidence_snapshot_images(results: list[dict[str, Any]], student_id: str,
                             *, used: int = 0,
                             limit: int = MAX_CONTEXT_IMAGES) -> list[str]:
    """RAG 图表证据 → 页快照 data URL（同步渲染，≤limit-used，失败跳过）。

    只对 block_type ∈ {figure, table} 且带 page 的证据渲染原页；原件解析
    顺序与 workspace.resolve_textbook_file 一致（先自有库再公共教材库）。
    """
    try:
        from .library import library_data_dir, load_library
        from .pdf_ocr import render_page_pixmap
        from .textbook import PUBLIC_STUDENT_ID

        def _orig_bytes(file_id: str) -> bytes | None:
            order = [student_id or "student_default", PUBLIC_STUDENT_ID]
            seen: set[str] = set()
            for sid in order:
                if sid in seen:
                    continue
                seen.add(sid)
                lib = load_library(sid)
                meta = lib.find_file(file_id)
                if meta is None or not str(meta.get("orig_ext", "")).startswith("."):
                    continue
                fp = library_data_dir(sid) / f"{file_id}.orig{meta['orig_ext']}"
                try:
                    if fp.exists():
                        return fp.read_bytes()
                except OSError:
                    continue
            return None

        out: list[str] = []
        budget = max(0, limit - used)
        for r in results:
            if budget <= 0:
                break
            if r.get("block_type") not in {"figure", "table"}:
                continue
            page = r.get("page")
            fid = str(r.get("file_id") or "")
            if not page or not fid:
                continue
            raw = _orig_bytes(fid)
            if raw is None:
                continue
            png = render_page_pixmap(raw, int(page) - 1, dpi=140)
            if not png:
                continue
            url = _data_url(png)
            if url:
                out.append(url)
                budget -= 1
        return out
    except Exception as exc:
        log.warning("evidence snapshot images skipped: %s", exc)
        return []


def get_multimodal_llm():
    """MULTIMODAL 通道的 tutor 客户端；未配置返回 None（降级纯文本）。"""
    if not settings.multimodal_api_key:
        return None
    from .llm_async import AsyncLLMClient
    return AsyncLLMClient(
        model=settings.multimodal_model or settings.llm_model,
        api_key=settings.multimodal_api_key,
        base_url=settings.multimodal_base_url or settings.llm_base_url,
        timeout=300.0,
    )


def with_context_images(messages: list[dict[str, Any]],
                        images: list[str]) -> list[dict[str, Any]]:
    """把图片注入最后一条 user 消息（content parts）；无图返回原列表。

    返回浅拷贝——不改动调用方 messages（历史/持久化保持纯文本）。
    """
    if not images:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            content = out[i].get("content")
            text = content if isinstance(content, str) else str(content or "")
            parts: list[dict[str, Any]] = [{"type": "text", "text": text or "（见图片）"}]
            for url in images:
                parts.append({"type": "image_url", "image_url": {"url": url}})
            out[i] = {**out[i], "content": parts}
            return out
    return out
