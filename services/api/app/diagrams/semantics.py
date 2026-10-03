"""Reviewed v2 assembly metadata for a deliberately bounded initial inventory.

The historical catalog/renderers remain immutable. V2 geometry is generated
from resolved parameters; gallery examples are never an instance fact source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from .catalog import catalog, digest
from .drawing import Drawing
from .schema import DiagramError

METADATA_VERSION = "2.0.0"
V2_RENDERER_VERSION = "2.0.0"

# Explicitly registered, no family-wide promise of scientific validation.
COMPONENTS = {
    "vessel.beaker": ("chemistry", ["liquid_fill", "open_top", "supports_submersion"]),
    "vessel.tall_beaker": ("chemistry", ["liquid_fill", "open_top", "supports_submersion"]),
    "vessel.cylinder": ("measurement", ["liquid_fill", "open_top", "readable_scale"]),
    "vessel.test_tube": ("chemistry", ["liquid_fill", "open_top"]),
    "vessel.conical_flask": ("chemistry", ["liquid_fill", "tube_terminal"]),
    "vessel.gas_wash_bottle": ("chemistry", ["liquid_fill", "tube_terminal"]),
    "apparatus.alcohol_lamp": ("chemistry", ["heating"]),
    "apparatus.tripod": ("chemistry", ["support"]),
    "apparatus.wire_mesh": ("chemistry", ["support"]),
    "apparatus.stand": ("chemistry", ["support"]),
    "apparatus.funnel": ("chemistry", ["tube_terminal"]),
    "apparatus.thermometer": ("measurement", ["reading_binding", "readable_scale"]),
    "mechanics.ball": ("physics", ["rope_attach"]),
    "mechanics.block": ("physics", ["rope_attach", "support"]),
    "mechanics.weight": ("physics", ["rope_attach"]),
    "mechanics.plane": ("physics", ["support"]),
    "mechanics.wall": ("physics", ["support"]),
    "mechanics.spring": ("physics", ["rope_attach"]),
    "mechanics.vertical_spring": ("physics", ["rope_attach"]),
    "measurement.dynamometer": ("measurement", ["rope_attach", "reading_binding", "readable_scale"]),
    "measurement.ammeter_real": ("measurement", ["wire_terminal", "reading_binding", "readable_scale"]),
    "measurement.voltmeter_real": ("measurement", ["wire_terminal", "reading_binding", "readable_scale"]),
    "circuit.battery": ("circuit", ["wire_terminal"]),
    "circuit.switch": ("circuit", ["wire_terminal"]),
    "circuit.resistor": ("circuit", ["wire_terminal"]),
    "circuit.ammeter": ("circuit", ["wire_terminal"]),
    "geometry.triangle": ("geometry", ["geometry_construction"]),
    "geometry.circumcircle": ("geometry", ["geometry_construction"]),
    "chart.bar": ("chart", ["data_binding"]),
    "chart.line": ("chart", ["data_binding"]),
    "function.quadratic": ("chart", ["function_binding", "data_binding"]),
}


@dataclass(frozen=True)
class Recipe:
    title: str
    aliases: tuple[str, ...]
    children: tuple[tuple[str, str, float, float, float], ...]
    # (type, child, port/region, other child, port/region, medium)
    relations: tuple[tuple[str, str, str, str, str, str], ...] = ()
    bindings: dict = field(default_factory=dict)
    allowed_overlaps: tuple[tuple[str, str], ...] = ()
    part_selectors: dict[str, int] = field(default_factory=dict)


RECIPES = {
    "recipe.heating_beaker": Recipe("加热烧杯", ("烧杯加热装置", "酒精灯加热烧杯"),
        (("beaker", "vessel.beaker", 130, 4, 2), ("mesh", "apparatus.wire_mesh", 130, 174, 2),
         ("tripod", "apparatus.tripod", 130, 224, 2), ("lamp", "apparatus.alcohol_lamp", 150, 253.75, 1.75)),
        (("supported_by", "beaker", "support_bottom", "mesh", "support_top", "line"),
         ("supported_by", "mesh", "support_bottom", "tripod", "support_top", "line")),
        {"fill": ("beaker", "fill"), "lit": ("lamp", "lit")}, (("tripod", "lamp"),)),
    "recipe.thermal": Recipe("烧杯温度测量", ("加热测温", "液体温度计"),
        (("beaker", "vessel.beaker", 100, 140, 2), ("thermometer", "apparatus.thermometer", 80, 100, 2)),
        (("immersed_in", "thermometer", "bulb", "beaker", "liquid", "line"),),
        {"fill": ("beaker", "fill"), "reading": ("thermometer", "reading"),
             "scale_labels": ("thermometer", "scale_labels")}),
    "recipe.buoyancy_measurement": Recipe("浸没测力", ("浮力实验", "测力计悬挂小球", "完全浸没"),
        (("beaker", "vessel.beaker", 152, 180, 2), ("ball", "mechanics.ball", 232, 305, 1),
         ("dynamometer", "measurement.dynamometer", 152, 5, 2)),
        (("suspended_from", "ball", "rope_attach", "dynamometer", "hook", "rope"),
         ("immersed_in", "ball", "body", "beaker", "liquid", "line")),
        {"fill": ("beaker", "fill"), "reading": ("dynamometer", "reading"),
         "maximum": ("dynamometer", "maximum"), "radius": ("ball", "radius"),
         "scale_labels": ("dynamometer", "scale_labels")}, (("beaker", "dynamometer"),)),
    "recipe.spring_vertical": Recipe("竖直弹簧悬挂物体", ("弹簧悬挂砝码",),
        (("spring", "mechanics.vertical_spring", 120, 5, 2), ("weight", "mechanics.weight", 120, 222, 2)),
        (("suspended_from", "weight", "rope_attach", "spring", "spring_end", "rope"),),
        {"length": ("spring", "length")}),
    "recipe.spring_horizontal": Recipe("水平弹簧滑块", ("弹簧连接滑块",),
        (("wall", "mechanics.wall", -73, 241.8, 1.6),
         ("spring", "mechanics.spring", 15, 241.8, 1.6), ("block", "mechanics.block", 227, 246.6, 1.6),
         ("plane", "mechanics.plane", 115, 175, 3)),
        (("supported_by", "block", "support_bottom", "plane", "support_top", "line"),
         ("connected", "spring", "spring_end", "block", "rope_attach", "rope"),
         ("connected", "spring", "spring_start", "wall", "fixed_end", "rope")),
        {"length": ("spring", "length")}),
    "recipe.horizontal_block": Recipe("水平面滑块", ("水平面上的物体",),
        (("block", "mechanics.block", 150, 218, 2), ("plane", "mechanics.plane", 70, 190, 3)),
        (("supported_by", "block", "support_bottom", "plane", "support_top", "line"),)),
    "recipe.series_circuit": Recipe("电池开关电阻电流表串联", ("串联电路",),
        (("battery", "circuit.battery", 205, 245, 1.3), ("switch", "circuit.switch", 12, 15, 1.1),
         ("resistor", "circuit.resistor", 212, 15, 1.1), ("ammeter", "circuit.ammeter", 412, 15, 1.1)),
        (("series", "battery", "terminal_right", "ammeter", "terminal_right", "wire"),
         ("series", "ammeter", "terminal_left", "resistor", "terminal_right", "wire"),
         ("series", "resistor", "terminal_left", "switch", "terminal_right", "wire"),
         ("series", "switch", "terminal_left", "battery", "terminal_left", "wire")),
        {"closed": ("switch", "closed")}),
    "recipe.filtration": Recipe("过滤装置", ("漏斗过滤",),
        (("funnel", "apparatus.funnel", 54.75, 121.8, 1.8), ("beaker", "vessel.beaker", 120, 210, 1.8)),
        (("connected", "funnel", "wall_contact", "beaker", "inner_wall", "tube"),),
        {"fill": ("beaker", "fill")}, (("funnel", "beaker"),)),
    "recipe.inscribed_triangle": Recipe("圆内接三角形", ("三角形外接圆", "圆内接正三角形", "圆内接等边三角形"),
        (("circle", "geometry.circumcircle", 90, 8, 2.6), ("triangle", "geometry.circumcircle", 90, 8, 2.6)),
        (("inside", "triangle", "body", "circle", "body", "line"),),
        {"radius": ("circle", "radius")}, (), {"circle": 0, "triangle": 1}),
    "recipe.bar_table": Recipe("题目数据柱状图", ("柱状图", "柱状统计图"),
        (("chart", "chart.bar", 10, 5, 1.3),), (),
        {"values": ("chart", "values"), "labels": ("chart", "labels"), "show_values": ("chart", "show_values")}),
}

# Every condition-bearing parameter must be bound. Qualitative geometric
# parameters can use explicitly declared non-quantitative defaults only.
PHYSICAL = {"fill", "reading", "maximum", "capacity", "lit", "closed", "radius",
    "length", "angle", "values", "labels", "points", "function", "x_range", "y_range",
    "construction", "interval", "show_angle", "show_values", "show_ticks", "show_scale",
    "scale_labels", "x_label", "y_label"}
QUALITATIVE = {"fill", "radius", "length", "show_scale", "scale_labels", "show_ticks",
    "x_range", "y_range", "x_label", "y_label", "show_angle", "show_values", "construction"}


def parameter_semantics(asset_id: str) -> dict:
    if asset_id.startswith("material."):
        return {}
    if asset_id not in COMPONENTS and asset_id not in RECIPES:
        from .adapters import parameter_semantics as adapt
        return adapt(asset_id)
    if asset_id in RECIPES:
        result = {key: parameter_semantics(child)[param] for key, (name, param) in RECIPES[asset_id].bindings.items()
                for role, child, *_ in RECIPES[asset_id].children if role == name}
        if asset_id in {"recipe.buoyancy_measurement", "recipe.thermal"}:
            result["fill"] = {**result["fill"], "default": .7}
        if asset_id == "recipe.filtration":
            result["fill"] = {**result["fill"], "default": .2}
        return result
    asset = catalog()[1][asset_id]
    out = {}
    for key, spec in asset.parameter_schema().items():
        out[key] = {**spec, "condition_bearing": key in PHYSICAL,
            "non_quantitative_allowed": key in QUALITATIVE,
            "affects_geometry": key in PHYSICAL,
            "unit": "N" if key in {"reading", "maximum"} and "dynamometer" in asset_id else
                    "A" if key == "reading" and "ammeter" in asset_id else
                    "V" if key == "reading" and "voltmeter" in asset_id else
                    "°C" if key == "reading" and "thermometer" in asset_id else
                    "height_fraction" if key == "fill" else "diagram_px" if key in {"radius", "length"} else ""}
    return out


@dataclass
class InstanceGeometry:
    asset_id: str
    version: int
    drawing: Drawing
    params: dict
    domain: str
    ports: dict
    regions: dict
    parts: dict
    intrinsic_marks: list[str]
    derived_facts: dict


def instantiate_asset(asset_id: str, version: int, params: dict, *, monochrome=False) -> InstanceGeometry:
    if asset_id.startswith("material."):
        from .materials import instantiate
        return instantiate(asset_id, version, params, monochrome=monochrome)
    if asset_id not in COMPONENTS:
        from .adapters import instantiate_asset as adapt
        return adapt(asset_id, version, params, monochrome=monochrome)
    asset = catalog()[1].get(asset_id)
    if asset is None or asset.version != version or asset.review.get("status") != "passed":
        raise DiagramError("diagram_asset_not_reviewed")
    resolved = asset.parameters(params)
    drawing = asset.draw(resolved, monochrome=monochrome)
    if asset.renderer == "function" and resolved.get("show_ticks", True):
        ymin, ymax = resolved["y_range"]
        xmin, xmax = resolved["x_range"]
        ox = 50+(max(xmin, min(xmax, 0))-xmin)*395/(xmax-xmin)
        oy = 250-(max(ymin, min(ymax, 0))-ymin)*220/(ymax-ymin)
        # Preserve calibrated ticks, omit only labels colliding with x=0.
        drawing.parts = [part for part in drawing.parts if not (
            part.tag.rsplit("}", 1)[-1] == "text" and part.get("text-anchor") == "end" and
            abs(float(part.get("x", 0))-max(28, ox-9)) < .01 and
            abs(float(part.get("y", 0))-(oy+23)) < 14)]
        for part in drawing.parts:
            if part.text == "0" and abs(float(part.get("x", -1000))-ox) < .01:
                part.set("x", str(ox-12))
    ports, regions = {}, {"body": {"bounds": [0, 0, drawing.width, drawing.height], "occlusion": "forbidden"}}
    ports.update({k: {"point": list(v), "kind": "wire"} for k, v in drawing.anchors.items()
                  if k.startswith("terminal_")})
    if asset.renderer == "circuit":
        critical = {"battery": [57, 49, 46, 62], "switch": [49, 47, 65, 35],
                    "resistor": [43, 66, 74, 28], "ammeter": [49, 49, 62, 62]}[asset.variant]
        regions["symbol"] = {"bounds": critical, "occlusion": "never_cover"}
    for key in ("rope_attach", "spring_start", "spring_end", "hook"):
        if key in drawing.anchors:
            ports[key] = {"point": list(drawing.anchors[key]), "kind": "rope"}
    if asset.variant in {"spring", "vertical_spring"}:
        for name, anchor in [("spring_start", "attach_start"), ("spring_end", "attach_end")]:
            ports[name] = {"point": list(drawing.anchors[anchor]), "kind": "rope"}
    if asset_id == "mechanics.wall":
        ports["fixed_end"] = {"point": list(drawing.anchors["fixed_end"]), "kind": "rope"}
        regions["body"]["bounds"] = [63, 15, 18, 130]
    for key in ("tube_inlet", "tube_outlet", "mouth", "stem"):
        if key in drawing.anchors:
            ports[key] = {"point": list(drawing.anchors[key]), "kind": "tube"}
    for key in ("support_top", "support_bottom", "clamp"):
        if key in drawing.anchors:
            ports[key] = {"point": list(drawing.anchors[key]), "kind": "support"}
    if asset_id == "apparatus.funnel":
        ports["wall_contact"] = {"point": [73, 143], "kind": "tube"}
        regions["paint_cone"] = {"bounds": [28, 28, 104, 58], "occlusion": "paint"}
        regions["paint_neck"] = {"bounds": [68, 86, 24, 3], "occlusion": "paint"}
        regions["paint_stem"] = {"bounds": [73, 89, 14, 54], "occlusion": "paint"}
    if asset.renderer == "mechanics" and asset.variant in {"ball", "block", "weight"}:
        if asset.variant == "ball":
            radius = resolved.get("radius", 34)
            body = [80-radius, 85-radius, radius*2, radius*2]
            attach = [80, 85-radius]
        elif asset.variant == "block":
            body, attach = [30, 45, 100, 64], [30, 77]
        else:
            body, attach = [42, 50, 76, 82], [80, 23]
        regions["body"]["bounds"] = body
        ports["rope_attach"] = {"point": attach, "kind": "rope"}
        ports["support_bottom"] = {"point": [body[0]+body[2]/2, body[1]+body[3]], "kind": "support"}
    if asset_id == "measurement.dynamometer":
        drawing.text("N", 80, 37, size=11)
        regions["dial"] = {"bounds": [65, 38, 30, 82], "occlusion": "never_cover"}
    elif asset.renderer == "measurement":
        regions["dial"] = {"bounds": [36, 35, 88, 74], "occlusion": "never_cover"}
    elif asset_id == "apparatus.thermometer":
        drawing.text("°C", 80, 10, size=11)
        for i in range(10):
            drawing.line(88, 112-i*8, 92, 112-i*8, width=.8)
        regions["dial"] = {"bounds": [64, 14, 44, 130], "occlusion": "never_cover"}
        regions["bulb"] = {"bounds": [68, 120, 24, 24], "occlusion": "forbidden"}
    if asset.renderer == "vessel":
        if asset.variant in {"beaker", "tall_beaker"}:
            x, y, w, h = (49, 25, 62, 108) if asset.variant == "tall_beaker" else (45, 46, 70, 87)
            # Liquid is height, not volume. Bind only a height_fraction fact.
            bottom = 135
            top = 24 if asset.variant == "tall_beaker" else 45
            surface = bottom - (bottom-top)*resolved.get("fill", 0)
            regions["cavity"] = {"bounds": [x, y, w, h], "occlusion": "container"}
            regions["liquid"] = {"bounds": [x, surface, w, bottom-surface], "occlusion": "container"}
            left, right = (48, 112) if asset.variant == "tall_beaker" else (35, 125)
            regions["rim_left"] = {"bounds": [left-5, top-2, 7, 4], "occlusion": "never_cover"}
            regions["rim_right"] = {"bounds": [right-2, top-6, 12, 12], "occlusion": "never_cover"}
            if asset.variant == "beaker":
                ports["inner_wall"] = {"point": [36.75, 94], "kind": "tube"}
        elif asset.variant == "cylinder":
            regions["scale"] = {"bounds": [58, 18, 40, 110], "occlusion": "never_cover"}
    if asset_id == "apparatus.wire_mesh":
        # Consistent front view for assembled heating apparatus.
        drawing = Drawing(monochrome=monochrome)
        drawing.rect(22, 50, 116, 6, fill=drawing.surface, width=1)
        for x in range(24, 138, 6):
            drawing.line(x, 50, x, 56, width=.7, color=drawing.muted)
        ports["support_top"] = {"point": [80, 50], "kind": "support"}
        ports["support_bottom"] = {"point": [80, 56], "kind": "support"}
        regions["body"]["bounds"] = [22, 50, 116, 6]
    # Background/content/front parts let submerged objects remain visible.
    parts = {"body": drawing.parts}
    if asset.renderer == "vessel" and asset.variant in {"beaker", "tall_beaker"}:
        back_count = 3 if resolved.get("fill", 0) else 1
        parts = {"background": drawing.parts[:back_count], "front": drawing.parts[back_count:]}
    if asset_id == "apparatus.thermometer":
        labels = [part for part in drawing.parts if part.tag.rsplit("}", 1)[-1] == "text"]
        label_art = Drawing(monochrome=monochrome)
        for label in labels:
            width = len(label.text or "")*7+4
            x, y = float(label.get("x")), float(label.get("y"))
            left = x-width/2 if label.get("text-anchor") == "middle" else x-2
            label_art.rect(left, y-12, width, 14, fill="#fff", color="#fff", width=0)
            label_art.parts.append(label)
        parts = {"body": [part for part in drawing.parts if part not in labels], "labels": label_art.parts}
    marks = [e.text for p in drawing.parts for e in p.iter() if e.text]
    return InstanceGeometry(asset_id, version, drawing, resolved, COMPONENTS[asset_id][0],
        ports, regions, parts, marks, drawing.facts)


def recipe_version(asset_id: str) -> int:
    return 1


def capabilities(asset_id: str) -> set[str]:
    if asset_id.startswith("material."):
        return {"static_illustration"}
    if asset_id in COMPONENTS:
        return set(COMPONENTS[asset_id][1])
    if asset_id in RECIPES:
        return {"complete_apparatus"} | set().union(*(capabilities(child) for _, child, *_ in RECIPES[asset_id].children))
    from .adapters import capabilities as adapt
    return adapt(asset_id)


def asset_card(asset_id: str) -> dict:
    from .guidance import for_asset
    return {**_asset_card(asset_id), "usage_guidance": for_asset(asset_id)}


def _asset_card(asset_id: str) -> dict:
    if asset_id.startswith("material."):
        from .materials import card, detail, current_owner
        return card(detail(current_owner(), asset_id.removeprefix("material."), enabled_only=True))
    if asset_id not in COMPONENTS and asset_id not in RECIPES:
        from .adapters import asset_card as adapt
        return adapt(asset_id)
    if asset_id in RECIPES:
        recipe = RECIPES[asset_id]
        children = [asset_card(child) for _, child, *_ in recipe.children]
        return {"asset_id": asset_id, "version": recipe_version(asset_id), "title": recipe.title,
            "kind": "recipe", "capabilities": sorted(capabilities(asset_id)), "semantic_type": "apparatus",
            "supported_views": ["front_orthographic", "top_orthographic"] if asset_id == "recipe.inscribed_triangle" else ["front_orthographic"], "style_family": "textbook_line",
            "parameters": parameter_semantics(asset_id),
            "calibration": {"thermometer": {"range": [-20, 80], "unit": "°C", "smallest_division": 5}}
                if asset_id == "recipe.thermal" else {},
            "parameter_bindings": {key: list(value) for key, value in recipe.bindings.items()},
            "children": [{"child_id": role, "asset_id": child, "version": catalog()[1][child].version,
                "layout": {"x": x, "y": y, "scale": scale},
                "geometry": asset_card(child)["nominal_geometry"],
                "parameters": parameter_semantics(child)}
                for role, child, x, y, scale in recipe.children],
            "relations": [list(row) for row in recipe.relations],
            "allowed_overlaps": [list(row) for row in recipe.allowed_overlaps],
            "nominal_geometry": {"size": [640, 520]}, "rotation_allowed": False,
            "review": {"status": "passed", "metadata_version": METADATA_VERSION,
                "source_hash": semantic_hash()}, "thumbnail_ref": f"artifact://diagram-preview/{asset_id}@1"}
    asset = catalog()[1][asset_id]
    sample = instantiate_asset(asset_id, asset.version, asset.sample_params)
    return {"asset_id": asset_id, "version": asset.version, "title": asset.title,
        "kind": "construction" if sample.domain in {"geometry", "chart"} else "component",
        "semantic_type": sample.domain, "capabilities": sorted(capabilities(asset_id)),
        "supported_views": ["coordinate_plane"] if asset.renderer == "function" else
            ["front_orthographic", "top_orthographic"] if sample.domain == "geometry" else ["front_orthographic"],
        "style_family": "textbook_line", "parameters": parameter_semantics(asset_id),
        "calibration": {"range": [-20, 80], "unit": "°C", "smallest_division": 5}
            if asset_id == "apparatus.thermometer" else {},
        "nominal_geometry": {"size": [sample.drawing.width, sample.drawing.height],
            "ports": sample.ports, "regions": sample.regions},
        "parts": list(sample.parts), "intrinsic_marks": sample.intrinsic_marks,
        "rotation_allowed": sample.domain not in {"measurement", "chart", "chemistry"},
        "review": {**asset.review, "metadata_version": METADATA_VERSION, "geometry_provider": "registered_v2"},
        "thumbnail_ref": f"artifact://diagram-preview/{asset_id}@{asset.version}"}


@lru_cache(maxsize=1)
def semantic_hash() -> str:
    from pathlib import Path
    from .guidance import GUIDE_DIR
    return digest({str(path.relative_to(Path(__file__).resolve().parents[2])): path.read_text("utf-8") for path in
        [Path(__file__), Path(__file__).with_name("adapters.py"), Path(__file__).with_name("guidance.py"),
         *sorted(GUIDE_DIR.glob("*/usage_guide.json"))]})


def catalog_version() -> str:
    return f"{catalog()[0]}+{METADATA_VERSION}+{semantic_hash()[7:19]}"
