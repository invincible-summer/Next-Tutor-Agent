"""Permissive PDF backend package (ADR-0015): pypdf + pdfplumber + pypdfium2.

Engine assignments: pypdf → page count / outline / page labels / base text;
pdfplumber (pdfminer.six) → tables and figure bboxes; pypdfium2 → page
rasterization (global ``PDFIUM_LOCK``, never held across ``await``); Pillow →
crop/resize/encode. Import from :mod:`app.core.pdf` only — the ``service``
facade preserves the legacy never-raise result contracts.
"""
from .models import FigureCrop, FigureRegion, OutlineEntry, PdfParseError, TableBlock
from .render import PDFIUM_LOCK
from .service import (
    harvest_figure_regions,
    harvest_tables,
    outline,
    page_count,
    page_labels,
    page_texts,
    parse_failure_reason,
    render_figure_crops,
    render_page_png,
)

__all__ = [
    "FigureCrop", "FigureRegion", "OutlineEntry", "PdfParseError", "TableBlock",
    "PDFIUM_LOCK", "harvest_figure_regions", "harvest_tables", "outline",
    "page_count", "page_labels", "page_texts", "parse_failure_reason",
    "render_figure_crops", "render_page_png",
]
