"""Embedded-figure discovery and cropping.

Discovery runs on pdfplumber image placements (bbox in points, top-left
origin). Cropping renders each page **at most once** via pypdfium2 and cuts
all selected regions out of that single raster with Pillow — never one
document open + full-page render per figure.
"""
from __future__ import annotations

import io
import logging

from .models import FigureCrop, FigureRegion
from .render import render_page_png

log = logging.getLogger(__name__)


def figure_regions(raw: bytes) -> list[FigureRegion]:
    """Deduped embedded-bitmap placements; ``[]`` when pdfplumber cannot parse.

    Geometric filtering (icon/scan-page thresholds, per-volume caps) is the
    caller's policy — this returns raw candidates with page geometry so the
    policy layer never re-opens the document.
    """
    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover - dependency regression guard
        log.warning("pdfplumber unavailable: %s", exc)
        return []
    out: list[FigureRegion] = []
    seen: set[tuple[int, float, float, float, float]] = set()
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                try:
                    page_area = abs(float(page.width) * float(page.height)) or 1.0
                    images = page.images or []
                except Exception:
                    continue
                for img in images:
                    try:
                        x0, top = float(img["x0"]), float(img["top"])
                        x1, bottom = float(img["x1"]), float(img["bottom"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    region = FigureRegion(
                        page_index=page_index,
                        bbox=(x0, top, x1, bottom),
                        width_pt=x1 - x0,
                        height_pt=bottom - top,
                        area_ratio=abs((x1 - x0) * (bottom - top)) / page_area,
                    )
                    key = region.dedupe_key
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(region)
        return out
    except Exception as exc:
        log.debug("figure region discovery failed: %s", exc)
        return out


def render_figure_crops(raw: bytes, regions: list[FigureRegion],
                        *, dpi: int | None = None) -> dict[int, FigureCrop]:
    """Render selected regions → ``{index_in_input_list: FigureCrop}``.

    Pages without selected regions are never rasterized; a failed page or
    crop simply omits its index (callers count misses as skipped figures).
    """
    if not regions:
        return {}
    from PIL import Image

    by_page: dict[int, list[tuple[int, FigureRegion]]] = {}
    for index, region in enumerate(regions):
        by_page.setdefault(region.page_index, []).append((index, region))

    crops: dict[int, FigureCrop] = {}
    scale = (dpi or 200) / 72.0
    for page_index, items in by_page.items():
        png = render_page_png(raw, page_index, dpi=dpi)
        if png is None:
            continue
        try:
            image = Image.open(io.BytesIO(png))
            for index, region in items:
                x0, top, x1, bottom = region.bbox
                crop_box = (max(0, int(x0 * scale)), max(0, int(top * scale)),
                            int(x1 * scale), int(bottom * scale))
                if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                    continue
                out = io.BytesIO()
                image.crop(crop_box).save(out, format="PNG")
                crops[index] = FigureCrop(index, page_index, region.bbox, out.getvalue())
        except Exception as exc:
            log.debug("figure crop failed on page %d: %s", page_index, exc)
    return crops
