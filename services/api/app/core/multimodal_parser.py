"""Async material extraction shared by chat/workspace uploads.

Text-first short-circuit: TXT/MD and text-bearing office files stay on the
cheap parser unless an embedded image is present. PDF OCR is page-selective;
visual OCR uses the existing MULTIMODAL channel and falls back to tesseract.
The result remains plain text with form-feed page/slide boundaries so the
existing chunker keeps stable location metadata.

内嵌图片与教材图表提取同一水平：docx/pptx 内嵌媒体、PDF 稠密页插图/表格、
md data-uri 图统一走分型描述（题目转录 / 图述 / 装饰丢弃），产出
``[图|...]``/``[表|...]`` 标记块由 Structured Chunker V2 识别为一等块。
"""
from __future__ import annotations

import asyncio
import base64
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .file_parser import extract_text
from .ocr import (describe_embedded_image, is_decoration_description,
                  understand_image, is_image_file, ocr_page_image)
from . import pdf_ocr

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif")
_OFFICE_EXTS = (".docx", ".pptx")
SUPPORTED_ASYNC_EXTS = (".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown") + _IMAGE_EXTS
MAX_EMBEDDED_IMAGES = 8
# md 内嵌 data-uri 图片（![alt](data:image/png;base64,....)）。
_MD_DATAURI_RE = re.compile(
    r"!\[[^\]]*\]\(\s*data:image/(png|jpe?g|webp);base64,([A-Za-z0-9+/=\s]+)\s*\)",
    re.IGNORECASE)


@dataclass
class ExtractionResult:
    text: str = ""
    used_ocr: bool = False
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    media_count: int = 0

    @property
    def warning(self) -> str:
        return "；".join(self.warnings)


def _ext(filename: str) -> str:
    lower = (filename or "").lower()
    for suffix in _IMAGE_EXTS + (".pdf",) + _OFFICE_EXTS + (".txt", ".md", ".markdown"):
        if lower.endswith(suffix):
            return suffix
    return ""


def _office_images(raw: bytes) -> list[bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist()
                     if n.lower().startswith(("word/media/", "ppt/media/"))
                     and not n.endswith("/")]
            return [zf.read(n) for n in names[:MAX_EMBEDDED_IMAGES]]
    except Exception:
        return []


async def _ocr_images(images: list[bytes]) -> list[str]:
    """内嵌图分型描述（题目转录/图述/装饰）。装饰与空描述返回 ""（丢弃）。"""
    if not images:
        return []
    async def one(data: bytes) -> str:
        try:
            text = (await describe_embedded_image(data) or "").strip()
        except Exception:
            return ""
        if not text or is_decoration_description(text):
            return ""
        return text
    return list(await asyncio.gather(*(one(data) for data in images)))


def _media_blocks(descriptions: list[str], *, label: str = "文档图片") -> list[str]:
    """描述列表 → [图|...] 标记块（chunker figure 块；无页序信息）。

    描述内部换行归一为空格：标记块保持"标记行 + 单行描述"的原子结构，
    多行题干/图述不会被切块器从中间断开。
    """
    blocks: list[str] = []
    for i, text in enumerate(descriptions):
        if not text:
            continue
        one_line = re.sub(r"\s*\n\s*", " ", text).strip()
        blocks.append(f"[图|{label} {i + 1}]\n{one_line}")
    return blocks


async def extract_text_async(filename: str, raw: bytes, *, purpose: str = "chat") -> ExtractionResult:
    suffix = _ext(filename)
    if not suffix:
        return ExtractionResult(warnings=["不支持的文件格式"])

    if suffix in _IMAGE_EXTS or is_image_file(filename):
        try:
            text = await understand_image(raw, filename)
        except Exception as exc:
            return ExtractionResult(warnings=[f"图片 OCR 失败：{exc}"])
        return ExtractionResult(text=(text or "").strip(), used_ocr=True,
                                media_count=1)

    # Base text extraction never calls OCR for this async path. PDF is handled
    # below because it needs page-level visual OCR rather than the sync fallback.
    base_text = await asyncio.to_thread(extract_text, filename, raw,
                                        ocr_fallback=False)
    result = ExtractionResult(text=base_text or "")

    if suffix == ".pdf":
        pages = result.text.split("\f")
        mode = settings.pdf_ocr_mode
        if mode == "off" or not pages:
            return result
        sparse = pdf_ocr.sparse_page_indices(pages)
        if mode == "on":
            targets = list(range(len(pages)))
        else:
            targets = sparse
        cap = max(0, int(settings.pdf_ocr_sync_max_pages))
        # 无稀疏页（稠密原生 PDF）/cap=0：跳过整页 OCR，但仍走下方图表收割。
        if targets and cap > 0:
            try:
                if mode == "on":
                    ocr_pages = await pdf_ocr.ocr_pdf_pages(
                        raw, ocr_page_image, max_pages=cap,
                        dpi=settings.pdf_ocr_dpi, concurrency=1)
                    merged = list(pages)
                    for i, text in enumerate(ocr_pages):
                        if i < len(merged):
                            merged[i] = text or merged[i]
                    stats = {"sparse": len(pages), "ocr_done": len(ocr_pages),
                             "ocr_failed": sum(1 for p in ocr_pages if not p.strip())}
                else:
                    merged, stats = await pdf_ocr.ocr_pdf_pages_mixed(
                        raw, ocr_page_image, page_texts=pages, max_ocr_pages=cap,
                        dpi=settings.pdf_ocr_dpi, concurrency=1)
                if merged:
                    result.text = "\f".join(merged)
                    result.used_ocr = bool(stats.get("ocr_done"))
                    attempted = (min(len(pages), cap) if mode == "on"
                                 else min(len(targets), cap))
                    result.ocr_pages = [i + 1 for i in targets[:attempted]]
                    if len(targets) > stats.get("ocr_done", 0):
                        result.warnings.append(
                            f"扫描/图片页超过对话 OCR 上限 {cap} 页，仅识别前 {cap} 页")
            except Exception as exc:
                result.warnings.append(f"PDF OCR 失败，保留文本层：{exc}")
        # 稠密文本层页的插图/表格收割（对齐教材图表提取；稀疏页已由上面的
        # 视觉 OCR 整页覆盖）。失败只记 warning，绝不阻塞上传。
        if settings.rag_figure_harvest:
            try:
                from . import figure_harvest
                harvested = await figure_harvest.harvest_native_blocks(
                    raw, describer=describe_embedded_image)
                merged = figure_harvest.merge_harvest_into_text(result.text, harvested)
                if merged != result.text:
                    result.text = merged
                    result.media_count = max(
                        result.media_count,
                        sum(len([b for b in (e.get("blocks") or [])
                                 if b.startswith("[图")])
                            for e in harvested.values()))
            except Exception as exc:
                result.warnings.append(f"PDF 图表提取跳过：{exc}")
        return result

    if suffix in _OFFICE_EXTS:
        images = _office_images(raw)
        result.media_count = len(images)
        # Pure text office files take the cheap path. Embedded media is
        # described by type (题目转录/图述) and appended as [图|...] blocks —
        # the same marker contract the textbook figure pipeline emits.
        if images:
            ocr_texts = await _ocr_images(images)
            media_parts = _media_blocks(ocr_texts)
            if media_parts:
                result.text = (result.text.rstrip() + "\n\n" +
                               "\n\n".join(media_parts)).strip()
                result.used_ocr = True
        return result

    if suffix in (".md", ".markdown"):
        # md 内嵌 data-uri 图：解码后分型描述（外链图无法取回，跳过）。
        matches = _MD_DATAURI_RE.findall(result.text or "")[:MAX_EMBEDDED_IMAGES]
        images: list[bytes] = []
        for _mime, b64 in matches:
            try:
                data = base64.b64decode(re.sub(r"\s+", "", b64))
                if data:
                    images.append(data)
            except Exception:
                continue
        if images:
            result.media_count = len(images)
            try:
                descriptions = await _ocr_images(images)
                media_parts = _media_blocks(descriptions, label="内嵌图")
                if media_parts:
                    result.text = (result.text.rstrip() + "\n\n" +
                                   "\n\n".join(media_parts)).strip()
                    result.used_ocr = True
            except Exception as exc:
                result.warnings.append(f"md 内嵌图提取跳过：{exc}")
        return result

    return result
