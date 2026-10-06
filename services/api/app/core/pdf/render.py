"""Page rasterization via pypdfium2 (Apache-2.0/BSD-3, PDFium engine).

PDFium is **not** thread safe: every pypdfium2 call in the process must be
serialized through :data:`PDFIUM_LOCK` (same discipline as the legacy MuPDF
lock — the 2026-08 uvicorn SIGSEGV under concurrent textbook OCR). The lock
is never held across ``await``: callers wrap these sync helpers in
``asyncio.to_thread`` and re-acquire per page.

Documents are opened per call with short lifetimes; the input ``bytes``
buffer stays referenced by the local scope for the whole document lifetime,
as pypdfium2 borrows rather than copies it. Parallel rendering at scale
belongs in worker processes, not threads.
"""
from __future__ import annotations

import io
import logging
import threading

log = logging.getLogger(__name__)

#: Global PDFium serialization lock. Only PDFium calls may take it; the pypdf
#: and pdfplumber lanes keep their own independent readers and never wait on it.
PDFIUM_LOCK = threading.Lock()

_DEFAULT_DPI = 200


def render_page_png(raw: bytes, page_index: int, *, dpi: int | None = None) -> bytes | None:
    """Render one page to PNG bytes; ``None`` on any failure/out-of-range page."""
    import pypdfium2 as pdfium

    effective_dpi = dpi or _DEFAULT_DPI
    try:
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(raw)
            try:
                if page_index < 0 or page_index >= len(doc):
                    return None
                page = doc[page_index]
                try:
                    bitmap = page.render(scale=effective_dpi / 72.0)
                    pil = bitmap.to_pil()
                    out = io.BytesIO()
                    pil.save(out, format="PNG")
                    return out.getvalue()
                finally:
                    page.close()
            finally:
                doc.close()
    except Exception as exc:
        log.debug("pdfium render page %d failed: %s", page_index, exc)
        return None


def pdfium_page_count(raw: bytes) -> int:
    """Page count via the tolerant engine (0 on failure) — metadata fallback."""
    import pypdfium2 as pdfium

    try:
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(raw)
            try:
                return len(doc)
            finally:
                doc.close()
    except Exception:
        return 0
