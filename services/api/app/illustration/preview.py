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


def label_clearance(svg: str, width: int, height: int, candidates: list[list[float]]) -> list[bool]:
    """Measure actual ink, so a hollow construction is not a solid obstacle."""
    return browser({"mode": "label_clearance", "svg": svg, "width": width,
        "height": height, "candidates": candidates})["clear"]


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


def material_drawing(card):
    """Complete versioned sample, never taken as the question's conditions."""
    from app.diagrams.catalog import catalog
    from app.diagrams.drawing import Drawing, num
    from app.diagrams.semantics import RECIPES, instantiate_asset
    aid = card["asset_id"]
    if aid not in RECIPES:
        params = {} if aid.startswith("material.") else catalog()[1][aid].sample_params
        return instantiate_asset(aid, card["version"], params).drawing
    drawing = Drawing(640, 520)
    for role, child, x, y, scale in RECIPES[aid].children:
        asset = catalog()[1][child]
        art = instantiate_asset(child, asset.version, asset.sample_params).drawing
        if role in RECIPES[aid].part_selectors:
            art.parts = [art.parts[RECIPES[aid].part_selectors[role]]]
        group = drawing.element("g", transform=f"translate({num(x)} {num(y)}) scale({num(scale)})", data_child=role)
        group.extend(art.parts)
    return drawing


def material_sources(bundle):
    """Only this authorized retrieval result, with full, untruncated SVG code."""
    from app.diagrams.catalog import digest
    result = []
    for card in bundle.assets:
        svg = material_drawing(card).svg()
        result.append({"asset_id": card["asset_id"], "version": card["version"],
            "svg": svg, "source_hash": digest(svg), "purpose": "material_template_not_question_facts"})
    return result


def measurement_crops(compiled) -> list[dict]:
    """Enlarged screenshots of the identical final SVG, not redrawn readings."""
    image, source = compiled.illustration, compiled.source
    messages = []
    for instance, params in source.fact_bindings.items():
        if not any(isinstance(value, dict) and value.get("type") in {"scalar", "data", "range", "function"}
                for value in params.values()) or instance not in source.layout_report.bounds:
            continue
        if len(messages) >= 6:
            break
        x, y, w, h = source.layout_report.bounds[instance]
        left, top = max(0, x-12), max(0, y-12)
        right, bottom = min(image.width, x+w+12), min(image.height, y+h+12)
        result = browser({"mode": "render", "svg": image.svg, "width": image.width, "height": image.height,
            "clip": {"x": left, "y": top, "width": right-left, "height": bottom-top}})
        messages += [{"type": "text", "text": f"同一最终成图的材料局部放大，实例 {instance}（请辨认短刻度和单位）"},
                     image_message(base64.b64decode(result["png"], validate=True))]
    # Port contact is also a read-the-picture obligation. With no numeric
    # facts, a static apparatus previously received no detail image at all.
    from app.diagrams.semantics import RECIPES
    endpoints = [(relation.start.instance, relation.start.port, relation.end.instance, relation.end.port)
        for relation in source.scene.relations if relation.type in {"connected", "supported_by", "suspended_from", "series"}]
    endpoints += [(node.instance_id+":"+start, ap, node.instance_id+":"+end, bp)
        for node in source.scene.asset_instances if node.asset_id in RECIPES
        for kind, start, ap, end, bp, _medium in RECIPES[node.asset_id].relations
        if kind in {"connected", "supported_by", "suspended_from", "series"}]
    seen = set()
    for start, ap, end, bp in endpoints:
        if len(messages) >= 6:
            break
        a = source.layout_report.ports.get(start, {}).get(ap)
        b = source.layout_report.ports.get(end, {}).get(bp)
        if a is None or b is None:
            continue
        marker = tuple(round(value, 1) for value in [*a, *b])
        if marker in seen:
            continue
        seen.add(marker)
        left, top = max(0, min(a[0], b[0])-48), max(0, min(a[1], b[1])-48)
        right, bottom = min(image.width, max(a[0], b[0])+48), min(image.height, max(a[1], b[1])+48)
        result = browser({"mode": "render", "svg": image.svg, "width": image.width, "height": image.height,
            "clip": {"x": left, "y": top, "width": right-left, "height": bottom-top}})
        messages += [{"type": "text", "text": f"同一最终成图的连接局部放大：{start}.{ap} / {end}.{bp}"},
            image_message(base64.b64decode(result["png"], validate=True))]
    return messages
