"""Versioned, immutable project resources and local deterministic retrieval."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schema import DiagramError, VisualRequirements, finite

CATALOG_PATH = Path(__file__).resolve().parents[2] / "assets" / "diagram_library" / "catalog.json"
RENDERER_VERSION = "1.1.0"


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", text).lower())


def _json_values(value, depth=0, max_abs=10000):
    if depth > 4:
        raise DiagramError("diagram_invalid_parameter")
    if isinstance(value, list):
        if len(value) > 100:
            raise DiagramError("diagram_invalid_parameter")
        for child in value:
            _json_values(child, depth+1, max_abs)
    elif isinstance(value, str):
        if len(value) > 100 or any(t in value.lower() for t in ["<svg", "<script", "```"]):
            raise DiagramError("diagram_invalid_parameter")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        finite(value, -max_abs, max_abs)
    else:
        raise DiagramError("diagram_invalid_parameter")


@dataclass(frozen=True)
class AssetSpec:
    id: str
    title: str
    english: str
    category: str
    renderer: str
    variant: str
    aliases: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    version: int = 1
    sample_params: dict = field(default_factory=dict)
    preset: dict = field(default_factory=dict)
    license: str = "original-project-artwork"
    subjects: list[str] = field(default_factory=list)
    education_levels: list[str] = field(default_factory=list)
    asset_kind: str = "object"
    topics: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)

    def parameter_schema(self) -> dict:
        from .registry import extension
        item = extension(self.id)
        if item:
            return item.parameters(self.variant)
        common = {"show_labels": {"type": "boolean", "default": False}}
        if self.renderer == "vessel":
            common.update({"fill": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                           "liquid_color": {"type": "color", "default": "#a9cedb"},
                           "show_scale": {"type": "boolean", "default": self.variant == "cylinder"},
                           "scale_labels": {"type": "boolean", "default": False},
                           "capacity": {"type": "number", "minimum": 1, "maximum": 1000, "default": 100}})
        elif self.renderer == "apparatus":
            common.update({"lit": {"type": "boolean", "default": False},
                           "reading": {"type": "number", "minimum": -20, "maximum": 100, "default": 25},
                           "scale_labels": {"type": "boolean", "default": False}})
        elif self.renderer == "mechanics":
            common.update({"angle": {"type": "number", "minimum": -30 if "pendulum" in self.variant else 0, "maximum": 30 if "pendulum" in self.variant else 40, "default": 25},
                           "radius": {"type": "number", "minimum": 5, "maximum": 50, "default": 34},
                           "length": {"type": "number", "minimum": 20, "maximum": 70 if self.variant == "double_pendulum" else 100, "default": 60 if self.variant == "double_pendulum" else 95},
                           "show_angle": {"type": "boolean", "default": False}})
        elif self.renderer == "geometry":
            common.update({"radius": {"type": "number", "minimum": 10, "maximum": 63, "default": 54},
                           "angle": {"type": "number", "minimum": 1, "maximum": 359, "default": 60},
                           "sides": {"type": "integer", "minimum": 3, "maximum": 12, "default": 6},
                           "count": {"type": "integer", "minimum": 1, "maximum": 10, "default": 4},
                           "filled": {"type": "integer", "minimum": 0, "maximum": 10, "default": 1},
                           "face": {"type": "integer", "minimum": 1, "maximum": 6, "default": 5},
                           "show_values": {"type": "boolean", "default": False},
                           "construction": {"type": "enum", "choices": ["none", "altitude", "median", "bisector"], "default": "none"}})
        elif self.renderer == "function":
            common.update({"function": {"type": "string", "max_length": 160},
                           "x_range": {"type": "list", "default": [-4, 4]},
                           "y_range": {"type": "list", "default": [-3, 5]},
                           "interval": {"type": "list", "default": [0, 2]},
                           "vector": {"type": "list", "default": [2, 2]},
                           "x_label": {"type": "string", "default": "x", "max_length": 30},
                           "y_label": {"type": "string", "default": "y", "max_length": 30},
                           "show_ticks": {"type": "boolean", "default": True}})
            if self.variant == "linear_transform":
                common["matrix"] = {"type": "list", "default": [[1, .5], [.2, 1]]}
        elif self.renderer == "chart":
            common.update({"values": {"type": "list", "required": True},
                           "labels": {"type": "list", "default": []},
                           "points": {"type": "list"}, "errors": {"type": "list"},
                           "bin_edges": {"type": "list"},
                           "density": {"type": "boolean", "default": False},
                           "show_values": {"type": "boolean", "default": False},
                           "df": {"type": "number", "minimum": 1, "maximum": 30, "default": 5}})
        elif self.renderer == "graph":
            common.update({"items": {"type": "list", "required": True},
                           "edges": {"type": "list"},
                           "columns": {"type": "integer", "minimum": 1, "maximum": 8, "default": 3}})
        elif self.renderer == "measurement":
            common.update({"reading": {"type": "number", "minimum": 0, "maximum": 50, "default": 0},
                           "maximum": {"type": "number", "minimum": .1, "maximum": 100, "default": 5},
                           "scale_labels": {"type": "boolean", "default": False}})
        elif self.renderer == "circuit":
            common["closed"] = {"type": "boolean", "default": False}
        elif self.renderer == "template":
            from .templates import parameter_schema
            common.update(parameter_schema(self.variant))
        elif self.renderer in {"waves", "chemistry"}:
            common.update({"count": {"type": "integer", "minimum": 1, "maximum": 16, "default": 12},
                           "displacement": {"type": "number", "minimum": 0, "maximum": 60, "default": 10},
                           "show_poles": {"type": "boolean", "default": False}})
        controls: set[str] = set()
        variant = self.variant
        if self.renderer == "vessel":
            if variant not in {"drying_tube", "bell_jar", "desiccator", "crucible", "evaporating_dish", "mortar", "watch_glass", "petri_dish"}:
                controls.update({"fill", "liquid_color"})
            if variant in {"beaker", "tall_beaker", "measuring_cup", "overflow_cup", "calorimeter", "cup", "bucket", "cylinder", "gas_cylinder"}:
                controls.add("show_scale")
            if variant in {"cylinder", "gas_cylinder"}:
                controls.update({"capacity", "scale_labels"})
        elif self.renderer == "apparatus":
            if variant in {"alcohol_lamp", "bunsen_burner"}:
                controls.add("lit")
            if variant == "thermometer":
                controls.update({"reading", "scale_labels"})
                common["reading"]["maximum"] = 80
        elif self.renderer == "mechanics":
            if variant in {"ball", "hollow_ball", "disc", "ring", "pendulum_bob"}:
                controls.add("radius")
            if variant in {"spring", "vertical_spring", "spring_oscillator", "vertical_oscillator", "pendulum", "physical_pendulum", "double_pendulum"}:
                controls.add("length")
                if variant in {"spring_oscillator", "vertical_oscillator"}:
                    common["length"]["maximum"] = 70 if variant == "spring_oscillator" else 90
                    common["length"]["default"] = 70 if variant == "spring_oscillator" else 90
            if "pendulum" in variant and variant != "pendulum_bob":
                controls.update({"angle", "show_angle"})
            if variant == "incline":
                controls.add("angle")
        elif self.renderer == "geometry":
            if variant in {"circle", "arc", "sector", "segment_area", "annulus", "chord", "tangent", "secant", "central_angle", "inscribed_angle", "incircle", "circumcircle"}:
                controls.add("radius")
            if variant in {"arc", "sector", "segment_area", "central_angle", "inscribed_angle"}:
                controls.add("angle")
            if variant == "regular_polygon":
                controls.add("sides")
            if variant in {"fraction_bar", "fraction_circle", "counting_rods", "grid", "matrix", "base_ten", "dot_array"}:
                controls.add("count")
            if variant in {"fraction_bar", "counting_rods"}:
                controls.add("filled")
            if variant == "dice":
                controls.add("face")
            if variant == "number_line":
                controls.add("show_values")
            if variant in {"triangle", "right_triangle", "isosceles_triangle", "equilateral_triangle"}:
                controls.add("construction")
        elif self.renderer == "function":
            controls.update({"x_range", "y_range", "show_ticks", "x_label", "y_label"})
            if variant not in {"axes", "polar", "complex", "vector", "vector_sum", "projection", "basis", "linear_transform"}:
                controls.add("function")
            if variant in {"tangent_line", "integral", "riemann"}:
                controls.add("interval")
            if variant in {"vector", "vector_sum", "projection", "basis"}:
                controls.add("vector")
            if variant == "linear_transform":
                controls.add("matrix")
        elif self.renderer == "chart":
            controls.update({"values", "labels"})
            if variant in {"bar", "horizontal_bar", "grouped", "stacked", "percent_stacked", "pie", "donut"}:
                controls.add("show_values")
            if variant in {"scatter", "bubble", "errorbar", "confidence"}:
                controls.add("points")
            if variant in {"errorbar", "confidence"}:
                controls.add("errors")
            if variant == "histogram":
                controls.update({"bin_edges", "density"})
            if variant == "t_distribution":
                controls.add("df")
        elif self.renderer == "graph":
            controls.add("items")
            if variant in {"matrix", "table", "payoff", "place_value", "comparison"}:
                controls.add("columns")
            if variant not in {"array", "stack", "queue", "circular_queue", "matrix", "table", "payoff", "place_value", "comparison", "venn2", "venn3", "euler"}:
                controls.add("edges")
        elif self.renderer == "measurement":
            if variant in {"meter", "ammeter_real", "voltmeter_real", "multimeter", "pressure_gauge", "dynamometer"}:
                controls.update({"reading", "maximum", "scale_labels"})
        elif self.renderer == "circuit":
            if variant in {"switch", "double_switch", "relay"}:
                controls.add("closed")
        elif self.renderer == "waves":
            if variant in {"gas_container", "particle_container"}:
                controls.add("count")
            if variant in {"syringe", "piston"}:
                controls.add("displacement")
            if variant in {"magnet", "bar_magnet", "field_lines", "electromagnet"}:
                controls.add("show_poles")
        elif self.renderer == "chemistry":
            if variant in {"proton", "neutron", "electron", "h2", "o2", "n2", "water", "co2", "ammonia", "methane", "hcl", "ethanol", "acetic_acid", "ethylene", "acetylene"}:
                controls.add("show_labels")
            if variant in {"pure", "mixture", "gas_mixture", "powder", "bubbles"}:
                controls.add("count")
        elif self.renderer == "template":
            return parameter_schema(variant)
        return {key: value for key, value in common.items() if key in controls}

    def parameters(self, raw: dict) -> dict:
        schema = self.parameter_schema()
        if not isinstance(raw, dict) or set(raw)-set(schema):
            raise DiagramError("diagram_unknown_parameter")
        if len(json.dumps(raw, ensure_ascii=False, allow_nan=False)) > 8192:
            raise DiagramError("diagram_parameter_budget_exceeded")
        result = {}
        for key, spec in schema.items():
            if key not in raw:
                if spec.get("required") and key not in self.preset:
                    raise DiagramError("diagram_data_required")
                if "default" in spec:
                    result[key] = spec["default"]
                continue
            value = raw[key]
            kind = spec["type"]
            if kind in {"number", "integer"}:
                finite(value, spec["minimum"], spec["maximum"])
                if kind == "integer" and type(value) is not int:
                    raise DiagramError("diagram_invalid_parameter")
            elif kind == "boolean":
                if type(value) is not bool:
                    raise DiagramError("diagram_invalid_parameter")
            elif kind == "color":
                if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                    raise DiagramError("diagram_invalid_parameter")
            elif kind == "enum":
                if value not in spec["choices"]:
                    raise DiagramError("diagram_invalid_parameter")
            elif kind == "string":
                if not isinstance(value, str) or len(value) > spec.get("max_length", 100):
                    raise DiagramError("diagram_invalid_parameter")
            elif kind == "list":
                if not isinstance(value, list):
                    raise DiagramError("diagram_invalid_parameter")
                _json_values(value, max_abs=spec.get("max_abs", 10000))
            result[key] = value
        result = {**result, **self.preset}
        if self.renderer == "function":
            for key in ["x_range", "y_range", "interval", "vector"]:
                if key in result and (len(result[key]) != 2 or any(type(v) not in {int, float} for v in result[key])):
                    raise DiagramError("diagram_invalid_parameter")
        if self.renderer == "geometry" and "filled" in result and result["filled"] > result["count"]:
            raise DiagramError("diagram_invalid_fraction")
        if self.renderer == "chart":
            values = result["values"]
            matrix = values if values and isinstance(values[0], list) else [values]
            if not values or len(matrix) > 6 or any(not row or len(row) != len(matrix[0]) or any(type(v) not in {int, float} for v in row) for row in matrix):
                raise DiagramError("diagram_invalid_chart_data")
            if len(matrix[0]) > (40 if self.variant in {"box", "density", "qq", "dot", "ecdf", "normal", "t_distribution"} else 7 if self.variant in {"pie", "donut", "pictograph"} else 12):
                raise DiagramError("diagram_chart_data_too_dense")
            labels = result.get("labels", [])
            if labels and (len(labels) != len(matrix[0]) or any(not isinstance(v, str) or len(v) > 16 for v in labels)):
                raise DiagramError("diagram_invalid_chart_labels")
            available = 6 if self.variant in {"pie", "donut", "radar"} else 2.5 if self.variant == "pictograph" else 4 if self.variant in {"horizontal_bar", "population"} else min(6, 380/len(matrix[0])/16)
            if any(sum(1 if ord(c) > 255 else .6 for c in label) > available for label in labels):
                raise DiagramError("diagram_chart_labels_too_dense")
            points = result.get("points")
            if points is not None and (len(points) != len(matrix[0]) or any(not isinstance(pt, list) or len(pt) not in {2, 3} or any(type(v) not in {int, float} for v in pt) or len(pt) == 3 and pt[2] <= 0 for pt in points)):
                raise DiagramError("diagram_invalid_chart_points")
            if self.variant == "stem_leaf" and (any(type(v) is not int or v < 0 for v in values) or len({v//10 for v in values}) > 8):
                raise DiagramError("diagram_invalid_stem_leaf_data")
        if self.renderer == "graph" and "items" in result:
            if not result["items"] or len(result["items"]) > 15 or any(type(v) not in {str, int, float} or len(str(v)) > 12 for v in result["items"]):
                raise DiagramError("diagram_invalid_graph_data")
            n = len(result["items"])
            if self.variant == "stack" and n > 7 or self.variant in {"matrix", "table", "payoff", "place_value", "comparison"} and math.ceil(n/result["columns"]) > 6:
                raise DiagramError("diagram_graph_data_too_dense")
            if self.variant not in {"tree", "binary_tree", "heap", "probability_tree", "syntax_tree", "organization", "argument", "paragraph", "matrix", "table", "payoff", "place_value", "comparison", "stack"} and n > 8:
                raise DiagramError("diagram_graph_data_too_dense")
        # Advertised controls correspond to actual drawing behavior; fixed
        # silhouettes must not offer unrelated measurements to the browser.
        return result

    def draw(self, raw: dict | None = None, *, monochrome=False, preview=False):
        from .registry import render
        params = self.parameters({**(self.sample_params if preview else {}), **(raw or {})})
        return render(self.renderer, self.variant, params, monochrome)

    def card(self) -> dict:
        from .registry import extension
        item = extension(self.id)
        drawing = self.draw(preview=True)
        return {"asset_id": self.id, "version": self.version, "name": self.title,
                "english": self.english, "category": self.category,
                "renderer": self.renderer, "variant": self.variant,
                "aliases": list(self.aliases),
                "asset_kind": self.asset_kind, "subjects": list(self.subjects),
                "topics": list(self.topics),
                "size": [drawing.width, drawing.height], "features": list(self.features),
                "anchors": {name: [round(x, 2), round(y, 2)] for name, (x, y) in drawing.anchors.items()},
                "parameters": self.parameter_schema(),
                "rotation_allowed": item.rotation_allowed if item else self.renderer not in {"chart", "graph", "function", "template", "vessel"}}


@lru_cache(maxsize=1)
def catalog() -> tuple[str, dict[str, AssetSpec]]:
    from .provenance import source_record
    raw = json.loads(CATALOG_PATH.read_text("utf-8"))
    assets = {}
    for item in raw["assets"]:
        spec = AssetSpec(**{**item, "aliases": tuple(item.get("aliases", [])), "features": tuple(item.get("features", []))})
        if spec.review.get("status") == "passed":
            actual = source_record(spec.id, spec.renderer, spec.variant)["source_hash"]
            if (spec.review.get("source_hash") != actual or spec.provenance.get("source_hash") != actual
                    or any(spec.review.get(key) != "passed" for key in ["automatic", "structure", "visual"])):
                spec.review["status"] = "stale"
        if spec.id in assets or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,95}", spec.id):
            raise DiagramError("diagram_catalog_invalid")
        assets[spec.id] = spec
    return raw["version"], assets


@lru_cache(maxsize=1)
def _index():
    from app.core.retriever import BM25Index, Chunk
    assets = catalog()[1]
    return BM25Index([Chunk(chunk_id=a.id, source=a.title, index=i,
                            text=" ".join([a.title, a.english, *a.aliases, *a.features]))
                      for i, a in enumerate(assets.values())])


_FEATURE_ALIASES = {
    "opentop": "敞口", "liquidfill": "可显示液面", "liquidcontainer": "可显示液面",
    "lit": "点燃状态", "sidearm": "带侧管", "readoutscale": "刻度",
    "ropeattach": "接绳", "wireterminal": "电接线", "tubeinlet": "接导管",
    "tubeoutlet": "接导管", "可显示液体": "可显示液面", "可加热": "可加热",
}

# High-confidence complete scenes used to enrich retrieval when a declaration
# lists separate parts but omits a useful scene brief.  These are hints only:
# the composition model receives the complete template alongside the parts and
# remains responsible for selecting, positioning, sizing and parameterising it.
_TEMPLATE_HINTS = (
    (("酒精灯", "烧杯"), "加热烧杯"),
    (("酒精灯", "试管"), "试管加热"),
    (("过滤",), "过滤装置"),
    (("滴定",), "滴定装置"),
    (("单摆",), "单摆"),
    (("滑块", "斜面"), "斜面滑块"),
    (("滑块", "水平"), "水平面滑块"),
    (("滑轮", "物体"), "两物体经滑轮连接"),
    (("滑轮", "砝码"), "两物体经滑轮连接"),
    (("弹簧", "滑块"), "水平弹簧"),
    (("弹簧", "振子"), "水平弹簧"),
    (("电路", "串联"), "串联电路"),
    (("电路", "并联"), "并联电路"),
    (("凸透镜",), "透镜成像"),
    (("凹透镜",), "凹透镜光路"),
    (("杠杆", "平衡"), "杠杆平衡"),
    (("碰撞",), "碰撞实验"),
    (("平抛",), "平抛情境"),
    (("浮力",), "浮力实验"),
    (("温度", "受热"), "物体受热"),
    (("种子", "萌发"), "种子萌发对照"),
    (("食物", "网"), "食物网关系"),
    (("供应", "需求"), "供需曲线"),
    (("三角形", "角"), "三角形角度关系"),
    (("散点", "拟合"), "散点图与拟合"),
    (("柱状图",), "柱状图构型"),
    (("扇形图",), "饼图构型"),
)


def search(name: str, *, features: list[str] | None = None, category="", top_k=3,
           gallery: bool = False, education_level="") -> list[AssetSpec]:
    """Local lexical retrieval. Functional constraints are hard filters."""
    assets = catalog()[1]
    query = _normalize(name)
    if not query:
        return []
    required = {_normalize(_FEATURE_ALIASES.get(_normalize(v), v)) for v in (features or [])}
    hits = {chunk.chunk_id: score for chunk, score in _index().search(name, top_k=len(assets))}
    ranked = []
    for asset in assets.values():
        if asset.review.get("status") != "passed":
            continue
        available = {_normalize(v) for v in asset.features}
        if not required <= available:
            continue
        names = [_normalize(v) for v in [asset.title, asset.english, *asset.aliases]]
        exact = query in names
        contained = any(len(n) >= 2 and n in query for n in names)
        similarity = max(SequenceMatcher(None, query, n).ratio() for n in names) if len(query) >= 4 else 0
        score = hits.get(asset.id, 0)
        # CJK unigram overlap alone is not a useful instrument match.
        lexical = any(len(n) >= 2 and (n in query or query in n) for n in names)
        if not (exact or contained or lexical or similarity >= .8):
            continue
        category_bonus = int(category in {asset.category, asset.renderer, *asset.subjects}) if category else 0
        level_bonus = int(education_level in asset.education_levels) if education_level else 0
        ranked.append(((int(exact), int(contained), category_bonus, level_bonus, similarity, score), asset.id, asset))
    ranked.sort(key=lambda item: (tuple(-v for v in item[0]), item[1]))
    return [item[2] for item in ranked[:max(1, min(len(assets) if gallery else 5, top_k))]]


@dataclass
class CandidateBundle:
    requirements: list[VisualRequirements]
    assets: dict[str, AssetSpec]
    needs: list[dict]

    def prompt_data(self) -> dict:
        return {"catalog_version": catalog()[0],
                "requirements": [requirement.model_dump(mode="json")
                                 for requirement in self.requirements],
                "needs": self.needs,
                "assets": [asset.card() for asset in self.assets.values()]}


def retrieve(requirements: list[VisualRequirements], *, education_level="") -> CandidateBundle:
    assets, needs = {}, []
    for requirement in requirements:
        if not requirement.illustration_needed:
            continue
        for need in requirement.needs:
            candidates = search(need.name, features=need.features, category=need.category,
                                education_level=education_level, top_k=5)
            for candidate in candidates:
                assets[candidate.id] = candidate
            needs.append({"question_slot": requirement.question_slot, "key": need.key,
                          "name": need.name, "category": need.category,
                          "features": list(need.features),
                          "quantity": need.quantity, "candidates": [a.id for a in candidates],
                          "status": "matched" if candidates else "missing"})
        # Whole apparatus templates may satisfy the complete declared scene.
        # A short need list often has no useful scene brief (for example,
        # "烧杯 + 酒精灯").  Infer only high-confidence textbook structures;
        # the model still chooses the final scene and parameters.
        template_queries = []
        if requirement.scene_brief.strip():
            template_queries.append(requirement.scene_brief.strip())
        need_text = " ".join(need.name for need in requirement.needs)
        normalized = _normalize(requirement.scene_brief + " " + need_text)
        for tokens, query in _TEMPLATE_HINTS:
            if all(_normalize(token) in normalized for token in tokens) and query not in template_queries:
                template_queries.append(query)
        for query in template_queries[:4]:
            for candidate in search(query, education_level=education_level, top_k=10):
                if candidate.renderer == "template":
                    assets[candidate.id] = candidate
                    needs.append({"question_slot": requirement.question_slot,
                                  "key": "scene_template", "name": query,
                                  "category": "template", "features": [],
                                  "scene_brief": requirement.scene_brief,
                                  "quantity": 1, "candidates": [candidate.id],
                                  "status": "matched"})
    return CandidateBundle(requirements, assets, needs)


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
