"""Shared value types for the permissive PDF backend (ADR-0015).

Business modules import these (and the :mod:`service` facade) only — direct
imports of ``pypdf`` / ``pdfplumber`` / ``pypdfium2`` outside this package are
forbidden so the engine choice stays swappable and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class PdfParseError(RuntimeError):
    """Raised by engine-facing internals with a stable machine code.

    Codes: ``pdf_corrupt`` (cannot open/parse), ``pdf_encrypted`` (password
    required and empty-password auth failed). The ``service`` facade catches
    these and degrades to 0/[]/None results to keep the legacy
    never-raise contracts of the upload/OCR paths.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OutlineEntry:
    """One bookmark/TOC row, mirroring the legacy ``fitz.get_toc()`` shape."""

    level: int                 # 1-based nesting depth
    title: str
    page_1based: int           # 0 → unknown/invalid destination

    def as_row(self) -> tuple[int, str, int]:
        return (self.level, self.title, self.page_1based)


@dataclass(frozen=True)
class TableBlock:
    """One harvested table region (candidates only — no anti-fake gating here)."""

    page_1based: int
    rows: list[list[str]] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None  # (x0, top, x1, bottom) pt

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass(frozen=True)
class FigureRegion:
    """One embedded-bitmap placement (pdfplumber coordinates, points)."""

    page_index: int            # 0-based
    bbox: tuple[float, float, float, float]  # (x0, top, x1, bottom) pt, top-left origin
    width_pt: float
    height_pt: float
    area_ratio: float          # fraction of the page area

    @property
    def dedupe_key(self) -> tuple[int, float, float, float, float]:
        return (self.page_index, *(round(v, 1) for v in self.bbox))


@dataclass(frozen=True)
class FigureCrop:
    """A rendered PNG crop for one selected :class:`FigureRegion`."""

    region_index: int          # index into the caller-provided region list
    page_index: int
    bbox: tuple[float, float, float, float]
    png: bytes
