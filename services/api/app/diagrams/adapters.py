"""Uniform semantic cards for every registered, reviewed catalogue renderer.

Nominal geometry is gallery-only. Runtime geometry always resolves the
parameters supplied by the material contract; this adapter adds no facts.
"""
from __future__ import annotations

from dataclasses import dataclass

from .catalog import catalog
from .schema import DiagramError

DOMAIN = {"vessel": "chemistry", "apparatus": "chemistry", "mechanics": "physics",
    "waves": "physics", "function": "chart", "graph": "graph",
    "geometry": "geometry", "statistics": "chart", "biology": "biology", "earth": "earth"}
APPEARANCE = {"color", "liquid_color", "stroke", "line_width", "font_size"}
QUALITATIVE = {"radius", "length", "fill", "columns", "x_label", "y_label", "show_ticks"}
NO_MARKS = {"show_labels", "show_values", "show_angle", "show_scale", "scale_labels", "show_poles"}


def semantic_domain(asset):
    if asset.renderer.endswith("_extended"):
        prefix = asset.renderer.removesuffix("_extended")
        return {"statistics": "chart", "math": "geometry", "mathematics": "geometry",
            "systems": "graph"}.get(prefix, prefix)
    return DOMAIN.get(asset.renderer, asset.category or "diagram")


def parameter_semantics(asset_id):
    asset = catalog()[1][asset_id]
    result = {}
    for key, spec in asset.parameter_schema().items():
        result[key] = {**spec, "condition_bearing": key not in APPEARANCE and spec["type"] != "color",
            "non_quantitative_allowed": spec.get("non_quantitative_allowed", key in QUALITATIVE),
            "affects_geometry": spec["type"] != "color",
            "unit": spec.get("unit", "diagram_px" if key in {"radius", "length"} else
                "height_fraction" if key == "fill" else "")}
    return result


def capabilities(asset_id):
    asset = catalog()[1][asset_id]
    schema = asset.parameter_schema()
    result = {"static_illustration"}
    if "reading" in schema:
        result |= {"reading_binding", "readable_scale"}
    if "function" in schema:
        result |= {"function_binding", "data_binding"}
    if any(key in schema for key in {"values", "points", "items", "matrix", "edges"}):
        result.add("data_binding")
    if "fill" in schema:
        result.add("liquid_fill")
    if semantic_domain(asset) == "geometry":
        result.add("geometry_construction")
    if asset.renderer == "template":
        result.add("complete_apparatus")
    return result


@dataclass
class AdaptedGeometry:
    asset_id: str
    version: int
    drawing: object
    params: dict
    domain: str
    ports: dict
    regions: dict
    parts: dict
    intrinsic_marks: list[str]
    derived_facts: dict


def instantiate_asset(asset_id, version, params, *, monochrome=False, audit=False):
    asset = catalog()[1].get(asset_id)
    if asset is None or asset.version != version:
        raise DiagramError("diagram_unknown_asset")
    if not audit and asset.review.get("status") != "passed":
        raise DiagramError("diagram_asset_not_reviewed")
    resolved = asset.parameters(params)
    drawing = asset.draw(resolved, monochrome=monochrome)
    ports = {}
    # Only actual renderer anchors may be used. Generic edge/center anchors
    # are positioning aids, not evidence of an electrical/rope terminal.
    for name, point in drawing.anchors.items():
        medium = "wire" if name.startswith("terminal_") else "rope" if name in {
            "attach_start", "attach_end", "rope_attach", "hook"} else "tube" if name in {
            "tube_inlet", "tube_outlet", "mouth", "tip"} else "support" if name in {
            "support_top", "support_bottom", "clamp"} else "position"
        ports[name] = {"point": list(point), "kind": medium}
    regions = {"body": {"bounds": [0, 0, drawing.width, drawing.height], "occlusion": "forbidden"}}
    marks = [element.text for part in drawing.parts for element in part.iter() if element.text]
    return AdaptedGeometry(asset_id, version, drawing, resolved, semantic_domain(asset), ports,
        regions, {"body": drawing.parts}, marks, drawing.facts)


def asset_card(asset_id, *, audit=False):
    asset = catalog()[1][asset_id]
    # Gallery parameters are used exclusively for a thumbnail/card, never
    # returned as bindings or copied by runtime instantiation.
    geometry = instantiate_asset(asset_id, asset.version, asset.sample_params, audit=audit)
    domain = geometry.domain
    return {"asset_id": asset_id, "version": asset.version, "title": asset.title,
        "kind": "construction" if domain in {"geometry", "chart", "graph"} or not any(
            port["kind"] != "position" for port in geometry.ports.values()) else "component",
        "semantic_type": domain, "capabilities": sorted(capabilities(asset_id)),
        "supported_views": ["coordinate_plane"] if asset.renderer == "function" else
            ["front_orthographic", "top_orthographic"] if domain == "geometry" else
            ["front_orthographic", "section"] if asset.renderer in {"biology", "biology_extended"} else ["front_orthographic"],
        "style_family": "textbook_line", "parameters": parameter_semantics(asset_id),
        "nominal_geometry": {"size": [geometry.drawing.width, geometry.drawing.height],
            "ports": geometry.ports, "regions": geometry.regions},
        "parts": list(geometry.parts), "intrinsic_marks": geometry.intrinsic_marks,
        "rotation_allowed": domain not in {"measurement", "chart", "chemistry", "graph"},
        "review": {**asset.review, "geometry_provider": "registered_catalogue_adapter"},
        "thumbnail_ref": f"artifact://diagram-preview/{asset_id}@{asset.version}",
        "limitations": ["Only exposed parameter controls and actual anchors are supported",
            "Fixed marks must be permitted by the question; visual and joint reviews remain mandatory"]}


def compact_capability_guide():
    families = {}
    for asset in catalog()[1].values():
        if asset.review.get("status") != "passed":
            continue
        row = families.setdefault(asset.renderer, {"family": asset.renderer, "subject": asset.category,
            "count": 0, "example_names": [], "capabilities": set()})
        row["count"] += 1
        row["capabilities"] |= capabilities(asset.id)
        if len(row["example_names"]) < 6:
            row["example_names"].append(asset.title)
    return [{**row, "capabilities": sorted(row["capabilities"])} for row in families.values()]
