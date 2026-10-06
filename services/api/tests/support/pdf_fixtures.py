"""Synthetic PDF fixtures built with ReportLab + pypdf (ADR-0015).

Replaces every test-side ``import fitz`` document builder. All content here
is project-authored synthetic test data (content policy); fonts use the
non-embedding CID reference (STSong-Light) so no font bytes are bundled.

Conventions kept compatible with the legacy fitz builders:
- default page size A4 (595×842 pt), text drawn from the bottom-left origin
  at sensible positions (exact coordinates never asserted downstream);
- ``scanned=True`` pages are truly blank — empty text layer, renderable.
"""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from PIL import Image as PILImage

_CJK_FONT = "STSong-Light"
_font_registered = False


def _ensure_font() -> None:
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
        _font_registered = True


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _draw_text(c: canvas.Canvas, text: str, x: float, y_top: float) -> None:
    """Draw one text line; ``y_top`` is measured from the page top (fitz style)."""
    if _has_cjk(text):
        _ensure_font()
        c.setFont(_CJK_FONT, 12)
    else:
        c.setFont("Helvetica", 12)
    c.drawString(x, A4[1] - y_top, text)


def make_pdf(pages_text: list[str], *, scanned: bool = False) -> bytes:
    """Text-layer pages (or blank pages when ``scanned``) — legacy shape."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i, page_text in enumerate(pages_text):
        if not scanned:
            _draw_text(c, page_text or f"page {i+1}", 72, 72)
        c.showPage()
    c.save()
    return buf.getvalue()


def blank_pdf(n_pages: int = 1) -> bytes:
    """Renderable pages with an empty text layer (scanned-style fixture)."""
    return make_pdf([""] * n_pages, scanned=True)


def page_with_image(text_lines: list[str], *, image_rect: tuple[float, float, float, float],
                    image_size: tuple[int, int] = (200, 150),
                    color: tuple[int, int, int] = (128, 128, 128)) -> bytes:
    """One page with a text layer plus an embedded bitmap at ``image_rect`` (pt)."""
    img = PILImage.new("RGB", image_size, color)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y_top = 90.0
    for line in text_lines:
        _draw_text(c, line, 72, y_top)
        y_top += 20.0
    x0, y0, x1, y1 = image_rect  # fitz-style top-left origin rect
    c.drawImage(ImageReader(io.BytesIO(img_bytes.getvalue())),
                x0, A4[1] - y1, width=x1 - x0, height=y1 - y0)
    c.showPage()
    c.save()
    return buf.getvalue()


def with_page_labels(raw: bytes, *, start_page: int = 0, prefix: str = "",
                     style: str = "D", first_page_num: int = 1) -> bytes:
    """Attach a page-label number tree via pypdf (``style``: D/d/R/r/A/a)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    writer.append(reader)
    writer.set_page_label(start_page, len(reader.pages) - 1,
                          prefix=prefix, style=f"/{style}", start=first_page_num)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def with_outline(raw: bytes, toc: list[tuple[int, str, int]]) -> bytes:
    """Attach bookmarks ``[(level, title, page_1based), ...]`` via pypdf."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import Fit

    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    writer.append(reader)
    parents: dict[int, object] = {}
    for level, title, page_1based in toc:
        page_index = max(0, min(page_1based - 1, len(reader.pages) - 1))
        parent = parents.get(level - 1) if level > 1 else None
        item = writer.add_outline_item(title, page_index, parent=parent,
                                       fit=Fit.fit_horizontally(top=800))
        parents[level] = item
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def table_pdf(rows: list[list[str]], *, grid: bool = True) -> bytes:
    """One page containing a ruled table (pdfplumber needs ruling lines)."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    flat = [str(cell) for row in rows for cell in row]
    if any(_has_cjk(text) for text in flat):
        _ensure_font()
        font_name = _CJK_FONT
    else:
        font_name = "Helvetica"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    col_width = max(90.0, max((len(text) for text in flat), default=8) * 12)
    table = Table([[str(cell) for cell in row] for row in rows],
                  colWidths=[col_width] * max((len(r) for r in rows), default=1))
    if grid:
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("FONTNAME", (0, 0), (-1, -1), font_name),
        ]))
    table.wrapOn(c, 460, 600)
    table.drawOn(c, 72, 500)
    c.showPage()
    c.save()
    return buf.getvalue()


def multipage_text_pdf(pages: list[str], *, page_size: tuple[float, float] = A4) -> bytes:
    """Explicit page-size control for layout-sensitive tests."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    for i, text in enumerate(pages):
        _draw_text(c, text or f"page {i+1}", 72, 72)
        c.showPage()
    c.save()
    return buf.getvalue()
