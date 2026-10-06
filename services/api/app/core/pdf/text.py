"""Per-page text-layer extraction: pypdf primary, PDFium fallback.

Both engines are tried without any global lock (each call owns its reader /
document). The ``\\f`` page-boundary join itself lives in callers — this
module returns the per-page list only, keeping the chunker contract intact.
"""
from __future__ import annotations

import logging

from . import metadata
from .models import PdfParseError
from .render import PDFIUM_LOCK

log = logging.getLogger(__name__)


def _pdfium_page_texts(raw: bytes) -> list[str]:
    """Tolerant fallback engine (Chrome's PDFium) — fits files pypdf rejects."""
    import pypdfium2 as pdfium

    pages: list[str] = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(raw)
        try:
            for index in range(len(doc)):
                page = doc[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range() or ""
                    finally:
                        textpage.close()
                finally:
                    page.close()
                pages.append(text.replace("\r\n", "\n").replace("\r", "\n"))
            return pages
        finally:
            doc.close()


def page_texts(raw: bytes) -> list[str]:
    """Per-page text layer; ``[]`` when neither engine can read the document.

    A page whose extraction fails individually degrades to ``""`` (a scanned
    page is legitimately empty); only a whole-document open failure falls back
    to the tolerant PDFium lane.
    """
    try:
        reader = metadata.open_reader(raw)
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception as exc:
                log.debug("pypdf page extraction failed (%s); page -> ''", exc)
                pages.append("")
        return pages
    except PdfParseError as exc:
        log.debug("pypdf open failed (%s); trying pdfium", exc.code)
    except Exception as exc:
        log.debug("pypdf extraction failed: %s; trying pdfium", exc)
    try:
        return _pdfium_page_texts(raw)
    except Exception as exc:
        log.debug("pdfium text extraction failed: %s", exc)
        return []
