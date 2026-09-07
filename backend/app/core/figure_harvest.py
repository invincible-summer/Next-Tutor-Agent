"""原生 PDF（文本层）图表与印刷页码收割（RAG_FIGURE_HARVEST，默认开）。

文本层良好的原生教材 PDF 不会经过视觉模型，导致：表格只剩散乱文字、
插图完全不可见、印刷页码无从谈起。本模块在教材构建路径上做确定性收割：

- **表格**：``page.find_tables()``（PyMuPDF 内置表格识别，零 LLM）→
  markdown 行块 ``[表|上下文]``；
- **插图**：``page.get_images()`` + ``get_image_rects()`` 提取位图区域
  （过滤小图标/整页扫描图/重复区域），裁剪渲染 PNG → 复用多模态通道
  （``ocr.describe_figure_image``）生成 ``[图|...] + 图述：`` 块——调用
  受 ``ocr_policy.textbook_ocr_job`` 并发治理；
- **印刷页码**：PDF page label（``page.get_label()``）为纯数字时输出
  ``[页码=N]`` 页首标记，与扫描书 OCR prompt v2 的标记同构，由
  Structured Chunker V2 统一解析为 ``printed_page`` 元数据。

收割块按页并入教材 ``.txt`` 事实源（页序对齐 ``\\f``）；无任何收获时
返回空，文本 hash 不变（rag_graph 刷新零成本跳过）。矢量图形不做聚类
猜测（下划线/边框极易误判为插图——宁缺毋滥，其文字标注已在文本层中）。
仅教材构建路径调用；聊天/资料库同步上传不经过本模块。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .config import settings

log = logging.getLogger(__name__)

# 位图区域过滤阈值（pt / 页面积占比）。
_MIN_FIG_PT = 60.0          # 任一边小于 60pt 视为图标/装饰
_MIN_FIG_AREA_RATIO = 0.01  # 面积 < 1% 页面
_MAX_FIG_AREA_RATIO = 0.90  # 面积 > 90% 视为整页扫描图（OCR 路径的地盘）
_MAX_FIGURES_PER_VOLUME = 40  # 单卷图述 VLM 调用上限（成本护栏）
_MAX_TABLE_ROWS = 60          # 单表 markdown 行数上限
_LABEL_NUMERIC_RE = re.compile(r"^\s*([0-9]{1,4})\s*$")


def _page_label_int(page: Any) -> int | None:
    """PDF page label 为纯数字时返回印刷页码（罗马数字/空等返回 None）。"""
    try:
        label = str(page.get_label() or "").strip()
    except Exception:
        return None
    m = _LABEL_NUMERIC_RE.match(label)
    return int(m.group(1)) if m else None


def _extract_rows(table: Any) -> list[list[str]]:
    """Table → 规整化单元格行（extract 失败返回 []，单元格内换行折叠）。"""
    try:
        return [[re.sub(r"\s*\n\s*", " ", str(c or "")).strip() for c in row]
                for row in (table.extract() or [])]
    except Exception:
        return []


def _rows_look_like_table(rows: list[list[str]]) -> bool:
    """反伪造门槛（P8）：``find_tables`` 会把普通问题框/边框装饰也圈成"表"。

    取证（2026-08）：单卷可产出上百张假表——特征是两列内容逐行重复
    （框内文字被复制进两列）、有效单元格稀疏、伪表头 Col2。真表至少 2 行
    2 列、多行有 ≥2 个非空单元格，且任意两列的非空内容不会大面积一致。
    """
    if len(rows) < 2:
        return False
    width = max((len(r) for r in rows), default=0)
    if width < 2:
        return False
    filled = [c for r in rows for c in r if c]
    if len(filled) < 4:
        return False
    if sum(1 for r in rows if sum(1 for c in r if c) >= 2) < 2:
        return False
    for a in range(width):
        for b in range(a + 1, width):
            both = [(r[a] if a < len(r) else "", r[b] if b < len(r) else "")
                    for r in rows]
            both = [(x, y) for x, y in both if x and y]
            if sum(1 for x, y in both if x == y) >= 2:
                return False
    return True


def _table_markdown(table: Any) -> str:
    """Table → markdown 行（先过 ``_rows_look_like_table`` 反伪造门槛）。"""
    rows = _extract_rows(table)
    if not _rows_look_like_table(rows):
        return ""
    try:
        md = str(table.to_markdown()).strip()
        if md:
            return "\n".join(md.splitlines()[:_MAX_TABLE_ROWS])
    except Exception:
        pass
    lines = []
    for row in rows[:_MAX_TABLE_ROWS]:
        cells = [c for c in row if c]
        line = " | ".join(cells)
        if line:
            lines.append(f"| {line} |" if not line.startswith("|") else line)
    return "\n".join(lines)


def _figure_rects(page: Any) -> list[Any]:
    """本页可收割的位图区域：去重 + 过滤小图标/整页图。"""
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    seen: set[tuple[float, float, float, float]] = set()
    out: list[Any] = []
    try:
        images = page.get_images(full=True) or []
    except Exception:
        return out
    for img in images:
        xref = img[0] if img else 0
        try:
            rects = page.get_image_rects(xref) or []
        except Exception:
            continue
        for rect in rects:
            key = (round(rect.x0, 1), round(rect.y0, 1),
                   round(rect.x1, 1), round(rect.y1, 1))
            if key in seen:
                continue
            seen.add(key)
            if rect.width < _MIN_FIG_PT or rect.height < _MIN_FIG_PT:
                continue
            ratio = abs(rect.width * rect.height) / page_area
            if ratio < _MIN_FIG_AREA_RATIO or ratio > _MAX_FIG_AREA_RATIO:
                continue
            out.append(rect)
    return out


def _render_clip(page: Any, rect: Any, dpi: int) -> bytes | None:
    try:
        pix = page.get_pixmap(clip=rect, dpi=dpi)
        return pix.tobytes("png")
    except Exception:
        return None


def harvest_native_blocks_sync(raw: bytes) -> dict[int, dict[str, Any]]:
    """确定性部分（表格 + 页码 + 图区域裁剪），无任何 LLM 调用。

    返回 ``{page_no(1-based): {"label": int|None, "tables": [str],
    "figure_pngs": [bytes]}}``；figure_pngs 的描述由异步阶段补充。
    永不抛出；任何 fitz 异常返回已收割部分。
    """
    import fitz
    from .pdf_ocr import FITZ_LOCK  # PyMuPDF 非线程安全：收割全程持锁（同步，无 await）
    out: dict[int, dict[str, Any]] = {}
    with FITZ_LOCK:
        try:
            doc = fitz.open(stream=raw, filetype="pdf")
        except Exception:
            return out
        try:
            figures_used = 0
            for idx, page in enumerate(doc, start=1):
                entry: dict[str, Any] = {}
                label = _page_label_int(page)
                if label is not None:
                    entry["label"] = label
                tables: list[str] = []
                try:
                    finder = page.find_tables()
                    for table in (finder.tables or []):
                        md = _table_markdown(table)
                        if md:
                            tables.append(md)
                except Exception:
                    pass
                if tables:
                    entry["tables"] = tables
                if figures_used < _MAX_FIGURES_PER_VOLUME:
                    rects = _figure_rects(page)
                    pngs: list[bytes] = []
                    for rect in rects[: _MAX_FIGURES_PER_VOLUME - figures_used]:
                        png = _render_clip(page, rect, settings.pdf_ocr_dpi)
                        if png:
                            pngs.append(png)
                    if pngs:
                        figures_used += len(pngs)
                        entry["figure_pngs"] = pngs
                if entry:
                    out[idx] = entry
            return out
        except Exception as e:
            log.warning("native figure/table harvest failed: %s", e)
            return out
        finally:
            try:
                doc.close()
            except Exception:
                pass


async def _describe_figures(pngs: list[bytes]) -> list[str]:
    """批量图述（ocr_policy 并发治理 + 多模态通道；失败/空返回 ""）。"""
    if not pngs:
        return []
    from . import ocr as ocr_mod
    from . import ocr_policy

    async def one(png: bytes) -> str:
        try:
            return (await ocr_mod.describe_figure_image(png) or "").strip()
        except Exception:
            return ""

    try:
        async with ocr_policy.textbook_ocr_job() as job:
            # 并发图述（ocr_policy.run_page 本就是全局并发治理器，页上限内
            # 天然限流）；gather 保序，结果与逐图串行完全一致。
            tasks = [ocr_policy.run_page(job, lambda p=png: one(p)) for png in pngs]
            results = list(await asyncio.gather(*tasks, return_exceptions=True))
            return ["" if isinstance(r, BaseException) else (r or "") for r in results]
    except Exception as e:
        log.warning("figure description pass failed: %s", e)
        return ["" for _ in pngs]


async def harvest_native_blocks(raw: bytes, *, describer=None) -> dict[int, dict[str, Any]]:
    """完整收割（表格/页码确定性 + 插图多模态图述）。

    返回 ``{page_no: {"label": int|None, "blocks": [str]}}``——blocks 为
    按序追加到该页文本末尾的标记块。图述全空的插图丢弃（宁缺毋滥）。

    ``describer``: 自定义图述协程（(png) -> str）；缺省走教材通道
    （describe_figure_image + ocr_policy 并发治理）。上传文件路径
    （multimodal_parser）传入 describe_embedded_image 并跳过治理——
    对话上传有自身上限保护，不占教材 OCR 并发额度。
    """
    harvested = harvest_native_blocks_sync(raw)
    if not harvested:
        return {}
    pending: list[tuple[int, int, bytes]] = []  # (page_no, fig_idx, png)
    for page_no, entry in harvested.items():
        for i, png in enumerate(entry.get("figure_pngs") or []):
            pending.append((page_no, i, png))
    descriptions: dict[tuple[int, int], str] = {}
    if pending:
        if describer is not None:
            async def _one(png: bytes) -> str:
                try:
                    return (await describer(png) or "").strip()
                except Exception:
                    return ""
            texts = list(await asyncio.gather(*[_one(p[2]) for p in pending]))
        else:
            texts = await _describe_figures([p[2] for p in pending])
        for (page_no, i, _png), text in zip(pending, texts):
            if text:
                descriptions[(page_no, i)] = text
    return assemble_blocks(harvested, descriptions)


def assemble_blocks(harvested: dict[int, dict[str, Any]],
                    descriptions: dict[tuple[int, int], str]) -> dict[int, dict[str, Any]]:
    """确定性收割 + 图述 → 最终 {page_no: {label, blocks}}（纯函数）。"""
    out: dict[int, dict[str, Any]] = {}
    for page_no, entry in harvested.items():
        blocks: list[str] = []
        for md in entry.get("tables") or []:
            blocks.append(f"[表|表格]\n{md}")
        for i, _png in enumerate(entry.get("figure_pngs") or []):
            desc = descriptions.get((page_no, i))
            if desc and not _is_decoration(desc):
                # 归一：单行化（切块器原子块）+ 补「图述：」前缀（prompt
                # 要求但容错模型输出）。
                desc = re.sub(r"\s*\n\s*", " ", desc).strip()
                if not desc.startswith("图述"):
                    desc = "图述：" + desc.lstrip("：: ")
                blocks.append(f"[图|插图]\n{desc}")
        result: dict[str, Any] = {"blocks": blocks}
        if entry.get("label") is not None:
            result["label"] = entry["label"]
        if blocks or result.get("label") is not None:
            out[page_no] = result
    return out


def _is_decoration(desc: str) -> bool:
    from .ocr import is_decoration_description
    try:
        return is_decoration_description(desc)
    except Exception:
        return False


def merge_harvest_into_text(text: str, harvested: dict[int, dict[str, Any]]) -> str:
    """把收割结果并入 ``\\f`` 分页文本：页首插 [页码=N]，页尾追加标记块。

    页数以文本侧为准（收割页号越界忽略）；无变化返回原文（hash 稳定）。
    """
    if not harvested:
        return text
    pages = text.split("\f")
    changed = False
    for page_no, entry in harvested.items():
        idx = page_no - 1
        if not 0 <= idx < len(pages):
            continue
        page = pages[idx]
        label = entry.get("label")
        blocks = entry.get("blocks") or []
        if label is not None and f"[页码={label}]" not in page:
            stripped = page.lstrip("\n")
            lead = page[:len(page) - len(stripped)]
            page = f"{lead}[页码={label}]\n{stripped}"
            changed = True
        if blocks:
            tail = "\n".join(blocks)
            page = (page.rstrip() + "\n" + tail) if page.strip() else tail
            changed = True
        pages[idx] = page
    return "\f".join(pages) if changed else text
