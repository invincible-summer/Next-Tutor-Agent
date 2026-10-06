"""PDF metadata via pypdf: page count, outline (TOC), page labels.

pypdf readers are cheap and independent per call; no cross-call state is
shared, so unlike the PDFium render lane these helpers need no global lock.
Corrupt/encrypted documents raise :class:`PdfParseError` for callers that
need the reason; the ``service`` facade degrades to empty results.
"""
from __future__ import annotations

import io
import logging

from .models import OutlineEntry, PdfParseError

log = logging.getLogger(__name__)


def open_reader(raw: bytes):
    """Open a PdfReader, authenticating with an empty password when possible."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            # Match the legacy engine's tolerance: many producer files use an
            # empty user password with owner-only restrictions.
            if reader.decrypt("") is None:
                raise PdfParseError("pdf_encrypted", "empty-password auth failed")
        return reader
    except PdfParseError:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise PdfParseError("pdf_corrupt", str(exc)[:200]) from exc
    except Exception as exc:  # defensive: parser edge cases
        raise PdfParseError("pdf_corrupt", f"{type(exc).__name__}: {exc}") from exc


def has_page_labels(raw: bytes) -> bool:
    try:
        reader = open_reader(raw)
        return "/PageLabels" in (reader.trailer["/Root"] or {})
    except Exception:
        return False


def outline(raw: bytes) -> list[OutlineEntry]:
    """Flatten the bookmark tree into ``[{level, title, page_1based}]`` rows.

    Mirrors the legacy ``fitz.get_toc()`` contract: 1-based levels, invalid
    destinations keep ``page_1based=0``. No labels → ``[]``; failures → ``[]``.
    """
    try:
        reader = open_reader(raw)
        entries: list[OutlineEntry] = []

        def walk(items, depth: int) -> None:
            for item in items or []:
                if isinstance(item, list):
                    walk(item, depth + 1)
                    continue
                title = str(getattr(item, "title", "") or "")
                try:
                    page_0 = reader.get_destination_page_number(item)
                except Exception:
                    page_0 = -1
                entries.append(OutlineEntry(depth, title, page_0 + 1))

        walk(reader.outline, 1)
        return entries
    except PdfParseError as exc:
        log.debug("outline parse failed (%s)", exc.code)
        return []
    except Exception as exc:
        log.debug("outline parse failed: %s", exc)
        return []


def page_labels(raw: bytes) -> list[str]:
    """Display label per page; ``""`` when the PDF carries no /PageLabels tree.

    pypdf synthesises default labels (``"1", "2", ...``) for unlabelled files;
    we suppress those to preserve the legacy engine's behaviour — printed-page
    markers were only emitted for PDFs that actually declare page labels.
    """
    try:
        reader = open_reader(raw)
        n = len(reader.pages)
        root = reader.trailer.get("/Root") or {}
        if "/PageLabels" not in root:
            return ["" for _ in range(n)]
        try:
            labels = list(reader.page_labels)
        except Exception as exc:
            log.debug("page label tree unreadable: %s", exc)
            return ["" for _ in range(n)]
        if len(labels) < n:  # defensive: pad short number trees
            labels += [""] * (n - len(labels))
        return [str(lbl) if lbl is not None else "" for lbl in labels[:n]]
    except PdfParseError as exc:
        log.debug("page label parse failed (%s)", exc.code)
        return []
    except Exception as exc:
        log.debug("page label parse failed: %s", exc)
        return []
