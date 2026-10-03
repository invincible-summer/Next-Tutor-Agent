"""Offline preview with real browser fonts; failures never count as reviews."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

from .contracts import IllustrationError

RENDER_SCRIPT = Path(__file__).resolve().parents[4] / "apps/web/scripts/render-illustration.mjs"


def browser(request: dict) -> dict:
    try:
        result = subprocess.run([shutil.which("node") or "node", str(RENDER_SCRIPT)],
            input=json.dumps(request, ensure_ascii=False, allow_nan=False), capture_output=True,
            text=True, timeout=15, check=True)
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError("preview budget exceeded")
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IllustrationError("preview_unavailable") from exc


def measure(svgs: list[str], texts: list[dict]) -> dict:
    return browser({"mode": "measure", "svgs": svgs, "texts": texts})


def render(illustration) -> bytes:
    result = browser({"mode": "render", "svg": illustration.svg,
                      "width": illustration.width, "height": illustration.height})
    x, y, w, h = result["bounds"]
    if x < -1 or y < -1 or x+w > illustration.width+1 or y+h > illustration.height+1:
        raise IllustrationError("geometry_out_of_bounds", repairable=True)
    png = base64.b64decode(result["png"], validate=True)
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise IllustrationError("preview_unavailable")
    return png


def image_message(png: bytes) -> dict:
    return {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"), "detail": "high"}}


def candidate_thumbnails(bundle) -> list[dict]:
    """Real local catalog previews, explicitly independent of question facts.

    Contact sheets have stable card IDs; gallery samples only help selection.
    They are never consulted by parameter resolution or publication checks.
    """
    from app.diagrams.catalog import catalog
    from app.diagrams.drawing import Drawing, num
    from app.diagrams.semantics import RECIPES, instantiate_asset
    messages = []
    for offset in range(0, len(bundle.assets), 12):
        cards = bundle.assets[offset:offset+12]
        sheet = Drawing(960, ((len(cards)+3)//4)*200)
        for index, card in enumerate(cards):
            aid = card["asset_id"]
            drawing = Drawing(640, 520) if aid in RECIPES else None
            if aid in RECIPES:
                for role, child, x, y, scale in RECIPES[aid].children:
                    asset = catalog()[1][child]
                    art = instantiate_asset(child, asset.version, asset.sample_params).drawing
                    if role in RECIPES[aid].part_selectors:
                        art.parts = [art.parts[RECIPES[aid].part_selectors[role]]]
                    group = drawing.element("g", transform=f"translate({num(x)} {num(y)}) scale({num(scale)})")
                    group.extend(art.parts)
            elif aid.startswith("material."):
                drawing = instantiate_asset(aid, card["version"], {}).drawing
            else:
                asset = catalog()[1][aid]
                drawing = instantiate_asset(aid, asset.version, asset.sample_params).drawing
            scale = min(215/drawing.width, 156/drawing.height)
            x, y = (index % 4)*240, (index//4)*200
            group = sheet.element("g", transform=f"translate({num(x+12)} {num(y+10)}) scale({num(scale)})")
            group.extend(drawing.parts)
            sheet.text(aid, x+12, y+184, size=11, anchor="start")
        result = browser({"mode": "render", "svg": sheet.svg(), "width": sheet.width, "height": sheet.height})
        messages += [{"type": "text", "text": "素材库外观预览（样例刻度/数据不是本题事实，禁止复制样例参数）"},
                     image_message(base64.b64decode(result["png"], validate=True))]
    return messages


def measurement_crops(compiled) -> list[dict]:
    """Enlarged screenshots of the identical final SVG, not redrawn readings."""
    image, source = compiled.illustration, compiled.source
    messages = []
    for instance, params in source.fact_bindings.items():
        if not {"reading", "capacity"} & set(params) or instance not in source.layout_report.bounds:
            continue
        if len(messages) >= 6:
            break
        x, y, w, h = source.layout_report.bounds[instance]
        left, top = max(0, x-12), max(0, y-12)
        right, bottom = min(image.width, x+w+12), min(image.height, y+h+12)
        result = browser({"mode": "render", "svg": image.svg, "width": image.width, "height": image.height,
            "clip": {"x": left, "y": top, "width": right-left, "height": bottom-top}})
        messages += [{"type": "text", "text": f"同一最终成图的仪器局部放大，实例 {instance}（请辨认短刻度和单位）"},
                     image_message(base64.b64decode(result["png"], validate=True))]
    return messages
