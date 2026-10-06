"""Facade over the permissive PDF engines (ADR-0015).

Business modules import from here (or from :mod:`app.core.pdf`), never the
engine packages directly. All facade entry points keep the legacy
never-raise contracts: page count ``0``, texts/outline ``[]``, render
``None``, harvest ``[]`` on unreadable documents — the reason codes live in
``PdfParseError`` for callers that need explicit diagnostics.
"""
from __future__ import annotations

from . import figures, metadata, render, tables, text
from .models import FigureCrop, FigureRegion, OutlineEntry, PdfParseError, TableBlock

__all__ = [
    "FigureCrop", "FigureRegion", "OutlineEntry", "PdfParseError", "TableBlock",
    "PDFIUM_LOCK", "page_count", "page_texts", "outline", "page_labels",
    "render_page_png", "harvest_tables", "harvest_figure_regions",
    "render_figure_crops", "parse_failure_reason",
]


def page_count(raw: bytes) -> int:
    """Total page count (0 when neither engine can open the document)."""
    try:
        return len(metadata.open_reader(raw).pages)
    except Exception:
        return render.pdfium_page_count(raw)


def page_texts(raw: bytes) -> list[str]:
    """Per-page text layer (``[]`` on unreadable documents)."""
    return text.page_texts(raw)


def outline(raw: bytes) -> list[OutlineEntry]:
    """Bookmark/TOC rows ``[{level, title, page_1based}]`` (``[]`` on failure)."""
    return metadata.outline(raw)


def page_labels(raw: bytes) -> list[str]:
    """Display label per page (``""`` rows when the PDF has no label tree)."""
    return metadata.page_labels(raw)


def render_page_png(raw: bytes, page_index: int, *, dpi: int | None = None) -> bytes | None:
    """Rasterize one page to PNG bytes under the PDFium lock (``None`` on failure)."""
    return render.render_page_png(raw, page_index, dpi=dpi)


def harvest_tables(raw: bytes) -> list[TableBlock]:
    """Native table candidates per page (gating stays in ``figure_harvest``)."""
    return tables.harvest_tables(raw)


def harvest_figure_regions(raw: bytes) -> list[FigureRegion]:
    """Deduped embedded-figure placements (filtering stays in ``figure_harvest``)."""
    return figures.figure_regions(raw)


def render_figure_crops(raw: bytes, regions: list[FigureRegion],
                        *, dpi: int | None = None) -> dict[int, FigureCrop]:
    """Crop the selected regions (each page rasterized at most once)."""
    return figures.render_figure_crops(raw, regions, dpi=dpi)


def parse_failure_reason(raw: bytes) -> str | None:
    """``"pdf_corrupt"`` / ``"pdf_encrypted"`` for unreadable input, else ``None``."""
    try:
        metadata.open_reader(raw)
        return None
    except PdfParseError as exc:
        return exc.code
    except Exception:
        return "pdf_corrupt"
