"""Native-PDF table harvesting via pdfplumber (MIT, pdfminer.six engine).

Returns *candidates* only — deduplication, anti-fake gating and markdown
assembly stay in ``core.figure_harvest`` where the knowledge-pipeline
contracts (``[表|表格]`` blocks, false-positive thresholds) live.
"""
from __future__ import annotations

import io
import logging
import re

from .models import TableBlock

log = logging.getLogger(__name__)


def _cell_text(value) -> str:
    return re.sub(r"\s*\n\s*", " ", str(value or "")).strip()


def harvest_tables(raw: bytes, *, max_rows: int = 200) -> list[TableBlock]:
    """Extract table row grids per page; ``[]`` when pdfplumber cannot parse.

    ``max_rows`` caps pathological row explosions from decorative grids.
    """
    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover - dependency regression guard
        log.warning("pdfplumber unavailable: %s", exc)
        return []
    out: list[TableBlock] = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                try:
                    found = page.find_tables()
                except Exception:
                    continue
                for table in found or []:
                    try:
                        rows = [[_cell_text(c) for c in row]
                                for row in (table.extract() or [])][:max_rows]
                    except Exception:
                        continue
                    if rows:
                        bbox = table.bbox
                        out.append(TableBlock(
                            page_1based=page_index + 1,
                            rows=rows,
                            bbox=(float(bbox[0]), float(bbox[1]),
                                  float(bbox[2]), float(bbox[3])) if bbox else None,
                        ))
        return out
    except Exception as exc:
        log.debug("table harvest failed: %s", exc)
        return out
