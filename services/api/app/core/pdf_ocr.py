"""扫描版/图片型 PDF 的逐页 OCR 回退（接入既有 RAG/图谱管线）。

页面渲染/文本层抽取由 ``core/pdf``（pypdf + pypdfium2，ADR-0015）承担；
扫描版书籍（每页是图片、无文本层）文本层返回空，上传会被拒。本模块判定
扫描 PDF + 逐页渲染 PNG → OCR → 按页顺序返回文本列表，调用方用
``"\\f".join`` 拼成与 ``file_parser._extract_pdf`` 同构的文本
（``retriever.chunk_text`` 的 ``\\f`` 页边界天然带页码）。

永不抛出：任何解析/OCR 异常返回空/部分结果，由调用方落 warning/降级。OCR
双通道见 ``ocr.ocr_page_image``（视觉模型优先 / 本地 tesseract 回退）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from .config import settings
from .pdf import PDFIUM_LOCK, page_count as _pdf_page_count_impl
from .pdf import page_texts as _pdf_page_texts_impl
from .pdf import render_page_png

log = logging.getLogger(__name__)

#: PDFium 非线程安全（共享全局上下文）：多线程同时打开/渲染会段错误整个进程
#: （2026-08-16 实测：教材 OCR 并发 50 时 uvicorn SIGSEGV）。所有 pypdfium2
#: 调用必须持此锁完成；渲染为毫秒~秒级，全局串行的吞吐损失远小于视觉模型
#: 调用延迟，换取进程级稳定。异步函数中不得跨 ``await`` 持锁——需要多页渲染
#: 时逐页调用 ``render_page_png``（内部独立加锁），不共享长命文档对象。
#: （pypdf/pdfplumber 各自持有独立 reader，不经过本锁。）

# 页均字符低于此值判定为「文本层稀疏」（扫描版或图片主导）。20 是经验值：
# 一页正常教材正文通常数百字符以上；扫描页 get_text() 基本为 0。
_SCANNED_MIN_CHARS_PER_PAGE = 20


def sparse_page_indices(page_texts: list[str], *,
                        min_chars: int = _SCANNED_MIN_CHARS_PER_PAGE) -> list[int]:
    """逐页稀疏判定（P5a-A2）：返回文本层不足阈值的页下标列表。

    纯函数，输入为按页拆分的文本层（``text.split("\\f")`` 或逐页 get_text）。
    替代旧的全书页均判定——半文本半扫描的混合书只有稀疏页需要 OCR，
    良好文本层页绝不被 OCR 降质覆盖。
    """
    return [i for i, t in enumerate(page_texts)
            if len((t or "").strip()) < min_chars]


def pages_needing_ocr(page_texts: list[str]) -> list[int]:
    """逐页 OCR 需求判定（P8 路由修复）：稀疏页 ∪ 乱码页。

    仅按字符量判扫描会漏掉「稠密乱码页」——定制数学字体无
    ToUnicode 映射，文本层每页数百个乱码字符（全角公式/PUA 音标/犃犅
    型替换），字符数远超稀疏阈值却不可用（2026-08 取证：部分公共教材卷因此从未 OCR）。``core/text_quality.classify_page`` 对稠密页
    做乱码证据判定，本入口取 sparse ∪ corrupt 的页下标；良好文本层页
    仍绝不 OCR。
    """
    from .text_quality import page_verdicts
    return [i for i, verdict in enumerate(page_verdicts(page_texts or []))
            if verdict in ("empty", "sparse", "corrupt")]


def is_scanned_pdf(raw: bytes, *, min_chars_per_page: int = _SCANNED_MIN_CHARS_PER_PAGE) -> bool:
    """True when the PDF's text layer is too sparse to be useful (needs OCR).

    判定：页均字符 < 阈值。混合 PDF（部分页有文本）若整体仍稀疏也按需 OCR 处理。
    非 PDF / 引擎缺失 / 打开失败 → False（调用方按原文本层路径处理）。永不抛出。
    """
    try:
        pages = _pdf_page_texts_impl(raw)
        if not pages:
            return False
        total = sum(len(p or "") for p in pages)
        return (total / len(pages)) < min_chars_per_page
    except Exception:
        return False


def pdf_page_count(raw: bytes) -> int:
    """Total PDF page count (0 on any failure)."""
    try:
        return int(_pdf_page_count_impl(raw))
    except Exception:
        return 0


def pdf_page_texts(raw: bytes) -> list[str]:
    """Per-page text layer (``[]`` on any failure)."""
    try:
        return list(_pdf_page_texts_impl(raw))
    except Exception:
        return []


async def _gather_ocr_batch(pngs: list[bytes | None],
                            ocr_page_fn: Callable[[bytes], Awaitable[str]],
                            *, ocr_job: Any | None = None) -> list[str]:
    """并发 OCR 一批已渲染页；单页失败 → 该页 ""（不中断整批）。"""
    async def _one(png: bytes | None) -> str:
        if png is None:
            return ""
        try:
            if ocr_job is not None:
                from .ocr_policy import run_page
                return await run_page(ocr_job, lambda: ocr_page_fn(png)) or ""
            return await ocr_page_fn(png) or ""
        except Exception as e:
            log.warning("batched OCR page failed: %s", e)
            return ""
    return list(await asyncio.gather(*[_one(p) for p in pngs]))


async def ocr_pdf_pages(
    raw: bytes,
    ocr_page_fn: Callable[[bytes], Awaitable[str]],
    *,
    max_pages: int | None = None,
    dpi: int | None = None,
    on_progress: Optional[Callable[[int, int], Any]] = None,
    concurrency: int = 1,
    global_limit: bool = False,
    _ocr_job: Any | None = None,
) -> list[str]:
    """逐页渲染 + OCR，返回按页顺序的文本列表（调用方 ``"\\f".join`` 拼接）。

    ``ocr_page_fn``: async (png_bytes) -> text（教材库后台传 ``ocr.ocr_page_image``）。
    ``max_pages``: 上限，超出截断（None 用 settings.pdf_ocr_max_pages）。
    ``on_progress(done, total)``: 每页 OCR 后回调，供状态机更新 progress。
    ``concurrency`` > 1 时按批并发（批内串行渲染 pixmap → gather 并发 OCR），
    页序与单页失败隔离语义与串行一致。
    单页渲染/OCR 失败 → 该页为 ""（不中断整体），由调用方统计空页落 warning。
    """
    if global_limit and _ocr_job is None:
        from .ocr_policy import textbook_ocr_job
        async with textbook_ocr_job() as job:
            return await ocr_pdf_pages(
                raw, ocr_page_fn, max_pages=max_pages, dpi=dpi,
                on_progress=on_progress, concurrency=job.limit,
                global_limit=False, _ocr_job=job)
    total = pdf_page_count(raw)
    if total <= 0:
        return []
    cap = max_pages if max_pages is not None else settings.pdf_ocr_max_pages
    total = min(total, max(0, cap))

    if concurrency > 1:
        pages: list[str] = []
        eff_dpi = dpi or settings.pdf_ocr_dpi
        for off in range(0, total, concurrency):
            batch = range(off, min(off + concurrency, total))
            pngs: list[bytes | None] = [
                render_page_png(raw, i, dpi=eff_dpi) for i in batch]
            pages.extend(await _gather_ocr_batch(pngs, ocr_page_fn,
                                                 ocr_job=_ocr_job))
            if on_progress is not None:
                try:
                    on_progress(len(pages), total)
                except Exception:
                    pass
        return pages

    pages = []
    for i in range(total):
        png = render_page_png(raw, i, dpi=dpi)
        if png is None:
            pages.append("")
        else:
            try:
                if _ocr_job is not None:
                    from .ocr_policy import run_page
                    text = await run_page(_ocr_job, lambda: ocr_page_fn(png))
                else:
                    text = await ocr_page_fn(png)
            except Exception as e:
                log.warning("OCR page %d failed: %s", i, e)
                text = ""
            pages.append(text or "")
        if on_progress is not None:
            try:
                on_progress(i + 1, total)
            except Exception:
                pass
    return pages


def ocr_pdf_pages_sync(
    raw: bytes,
    ocr_page_fn: Callable[[bytes], str],
    *,
    max_pages: int | None = None,
    dpi: int | None = None,
) -> list[str]:
    """同步版逐页 OCR（对话/资料库上传段用，只用本地 tesseract，无网络/await）。

    与 ``ocr_pdf_pages`` 同形，但 ``ocr_page_fn`` 是同步函数（如
    ``ocr._tesseract_ocr`` 封装 psm=3）。受 ``settings.pdf_ocr_sync_max_pages``
    保护以避免大书卡住同步上传请求。
    """
    total = pdf_page_count(raw)
    if total <= 0:
        return []
    cap = max_pages if max_pages is not None else settings.pdf_ocr_sync_max_pages
    total = min(total, max(0, cap))

    pages: list[str] = []
    for i in range(total):
        png = render_page_png(raw, i, dpi=dpi)
        if png is None:
            pages.append("")
        else:
            try:
                pages.append(ocr_page_fn(png) or "")
            except Exception as e:
                log.warning("sync OCR page %d failed: %s", i, e)
                pages.append("")
    return pages


# ---------------------------------------------------------------------------
# per-page mixed OCR (P5a-A1/A2)
# ---------------------------------------------------------------------------

async def ocr_pdf_pages_mixed(
    raw: bytes,
    ocr_page_fn: Callable[[bytes], Awaitable[str]],
    *,
    page_texts: list[str] | None = None,
    ocr_indices: list[int] | None = None,
    max_ocr_pages: int | None = None,
    dpi: int | None = None,
    on_progress: Optional[Callable[[int, int], Any]] = None,
    min_chars: int = _SCANNED_MIN_CHARS_PER_PAGE,
    concurrency: int = 1,
    global_limit: bool = False,
    _ocr_job: Any | None = None,
) -> tuple[list[str], dict[str, int]]:
    """逐页择优合并（P5a）：文本层达标的页保留文本层，稀疏/乱码页才渲染+OCR。

    与 ``ocr_pdf_pages`` 的差别：
      - 返回页列表长度 == 输入页数，OCR 失败/空页以 "" 占位——页序与物理页
        一一对应（A1 页码对齐，chunk_text 的页码元数据不漂移）。
      - ``max_ocr_pages`` 限制的是**实际 OCR 的页数**（OCR 才是昂贵操作），
        而不是处理的总页数；超出后剩余稀疏页保留原文本层。
      - ``page_texts``：判定稀疏用的**当前最佳文本**（默认 None=取 PDF 文本层）。
        重建场景传入已 OCR 合并的 .txt 分页——稀疏判定基于现状而非原始文本层，
        否则扫描书的 rebuild 会把全部页重 OCR 一遍（实测浪费）。
      - ``ocr_indices``：外部已判定的目标页下标（P8 质量路由传入稀疏∪乱码
        页）；默认 None 时内部按 ``pages_needing_ocr``（稀疏 ∪ 稠密乱码）判定。
      - ``concurrency`` > 1 时稀疏页按批并发 OCR（批内串行渲染 pixmap →
        gather 并发），页序对齐/失败隔离/进度语义与串行一致。
    返回 (pages, stats)，stats = {sparse, ocr_done, ocr_failed}
    （"sparse" 语义为「本轮判定的目标页数」，含乱码页）。
    永不抛出；非 PDF/打开失败返回 ([], {0,0,0})。
    """
    if global_limit and _ocr_job is None:
        from .ocr_policy import textbook_ocr_job
        async with textbook_ocr_job() as job:
            return await ocr_pdf_pages_mixed(
                raw, ocr_page_fn, page_texts=page_texts, ocr_indices=ocr_indices,
                max_ocr_pages=max_ocr_pages, dpi=dpi, min_chars=min_chars,
                on_progress=on_progress, concurrency=job.limit,
                global_limit=False, _ocr_job=job)
    empty_stats = {"sparse": 0, "ocr_done": 0, "ocr_failed": 0}
    try:
        if page_texts is None:
            page_texts = pdf_page_texts(raw)
        if not page_texts:
            return [], dict(empty_stats)
        if ocr_indices is None:
            ocr_indices = pages_needing_ocr(page_texts)
        sparse = [i for i in ocr_indices if 0 <= i < len(page_texts)]
        cap = max_ocr_pages if max_ocr_pages is not None else settings.pdf_ocr_max_pages
        targets = sparse[:max(0, cap)]
        pages = list(page_texts)
        done = 0
        failed = 0
        if concurrency > 1:
            eff_dpi = dpi or settings.pdf_ocr_dpi
            for off in range(0, len(targets), concurrency):
                batch = targets[off:off + concurrency]
                pngs: list[bytes | None] = [
                    render_page_png(raw, i, dpi=eff_dpi) for i in batch]
                texts = await _gather_ocr_batch(pngs, ocr_page_fn,
                                                ocr_job=_ocr_job)
                for i, text in zip(batch, texts):
                    if (text or "").strip():
                        pages[i] = text
                    else:
                        failed += 1  # 保留原文本层（多为 ""）占位
                    done += 1
                    if on_progress is not None:
                        try:
                            on_progress(done, len(targets))
                        except Exception:
                            pass
            return pages, {"sparse": len(sparse), "ocr_done": done, "ocr_failed": failed}
        for i in targets:
            try:
                png = render_page_png(raw, i, dpi=dpi)
                if png is None:
                    text = ""
                elif _ocr_job is not None:
                    from .ocr_policy import run_page
                    text = await run_page(_ocr_job, lambda: ocr_page_fn(png))
                else:
                    text = await ocr_page_fn(png)
            except Exception as e:
                log.warning("mixed OCR page %d failed: %s", i, e)
                text = ""
            if (text or "").strip():
                pages[i] = text
            else:
                failed += 1  # 保留原文本层（多为 ""）占位
            done += 1
            if on_progress is not None:
                try:
                    on_progress(done, len(targets))
                except Exception:
                    pass
        return pages, {"sparse": len(sparse), "ocr_done": done, "ocr_failed": failed}
    except Exception as e:
        log.warning("mixed OCR failed: %s", e)
        return [], dict(empty_stats)


def ocr_pdf_pages_mixed_sync(
    raw: bytes,
    ocr_page_fn: Callable[[bytes], str],
    *,
    page_texts: list[str] | None = None,
    ocr_indices: list[int] | None = None,
    max_ocr_pages: int | None = None,
    dpi: int | None = None,
    min_chars: int = _SCANNED_MIN_CHARS_PER_PAGE,
) -> tuple[list[str], dict[str, int]]:
    """``ocr_pdf_pages_mixed`` 的同步版（对话/资料库上传段，本地 tesseract）。

    上限默认取 ``settings.pdf_ocr_sync_max_pages``（限制 OCR 页数，保护同步请求）。
    目标页判定与异步版一致（``ocr_indices`` 优先，否则稀疏 ∪ 乱码）。
    """
    empty_stats = {"sparse": 0, "ocr_done": 0, "ocr_failed": 0}
    try:
        if page_texts is None:
            page_texts = pdf_page_texts(raw)
        if not page_texts:
            return [], dict(empty_stats)
        if ocr_indices is None:
            ocr_indices = pages_needing_ocr(page_texts)
        sparse = [i for i in ocr_indices if 0 <= i < len(page_texts)]
        cap = max_ocr_pages if max_ocr_pages is not None else settings.pdf_ocr_sync_max_pages
        targets = sparse[:max(0, cap)]
        pages = list(page_texts)
        done = 0
        failed = 0
        for i in targets:
            try:
                png = render_page_png(raw, i, dpi=dpi)
                text = (ocr_page_fn(png) or "") if png is not None else ""
            except Exception as e:
                log.warning("sync mixed OCR page %d failed: %s", i, e)
                text = ""
            if text.strip():
                pages[i] = text
            else:
                failed += 1
            done += 1
        return pages, {"sparse": len(sparse), "ocr_done": done, "ocr_failed": failed}
    except Exception as e:
        log.warning("sync mixed OCR failed: %s", e)
        return [], dict(empty_stats)
