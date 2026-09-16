"""Bounded, monochrome SVG question material. No storage or model calls.

Rebuild XML from a closed element/attribute grammar. Never pass through the
original model string, and never silently remove a meaningful drawing part.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field, model_validator

SVG_NS = "http://www.w3.org/2000/svg"
MAX_SVG_BYTES = 24 * 1024
MAX_NODES = 180
MAX_DEPTH = 10
MAX_SEGMENTS = 800
ET.register_namespace("", SVG_NS)

PRESENTATION = {"stroke", "fill", "stroke-width", "stroke-linecap",
                "stroke-linejoin", "stroke-dasharray", "transform"}
STYLE_PRESENTATION = PRESENTATION - {"transform"}
MARKER_LINKS = {"marker-start", "marker-mid", "marker-end"}
ROOT_METADATA = {"version", "role", "aria-label", "aria-labelledby", "focusable"}
ELEMENT_ATTRIBUTES = {
    "svg": {"viewBox", "width", "height", "preserveAspectRatio"} | ROOT_METADATA,
    "defs": set(),
    "marker": {"id", "markerWidth", "markerHeight", "refX", "refY",
               "orient", "markerUnits", "viewBox", "preserveAspectRatio"},
    "g": set(), "line": {"x1", "y1", "x2", "y2"},
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"}, "ellipse": {"cx", "cy", "rx", "ry"},
    "polyline": {"points"}, "polygon": {"points"}, "path": {"d"},
    "text": {"x", "y", "text-anchor", "font-size"},
    "tspan": {"x", "y", "dx", "dy", "font-size", "baseline-shift"},
    "title": set(), "desc": set(),
}
for _tag in set(ELEMENT_ATTRIBUTES) - {"svg", "defs", "marker", "title", "desc"}:
    ELEMENT_ATTRIBUTES[_tag] |= PRESENTATION | {"style"}
for _tag in {"line", "polyline", "polygon", "path"}:
    ELEMENT_ATTRIBUTES[_tag] |= MARKER_LINKS

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_NUM_RE = re.compile(_NUMBER)
_PATH_TOKEN = re.compile(rf"[A-Za-z]|{_NUMBER}")
_PATH_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6,
               "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
_COLORS = {"none": "none", "black": "#000", "#000": "#000",
           "#000000": "#000", "currentColor": "#000", "white": "#fff",
           "#fff": "#fff", "#ffffff": "#fff"}
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_LOCAL_MARKER_RE = re.compile(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.-]{0,63})\s*\)")


class IllustrationValidationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _reject(code: str = "svg_invalid_geometry") -> None:
    raise IllustrationValidationError(code)


def _number(raw: str, low: float = -4096, high: float = 4096) -> float:
    if not _NUM_RE.fullmatch(raw):
        _reject()
    value = float(raw)
    if not math.isfinite(value) or not low <= value <= high:
        _reject()
    return value


def _numbers(raw: str, *, limit: int = 400) -> list[float]:
    matches = list(_NUM_RE.finditer(raw))
    if not matches or len(matches) > limit:
        _reject("svg_budget_exceeded" if matches else "svg_invalid_geometry")
    end = 0
    for match in matches:
        if raw[end:match.start()].strip(" \r\n\t,"):
            _reject()
        end = match.end()
    if raw[end:].strip(" \r\n\t,"):
        _reject()
    return [_number(m.group()) for m in matches]


def _path(raw: str) -> int:
    if not raw or len(raw) > 2048:
        _reject("svg_budget_exceeded")
    tokens: list[str] = []
    end = 0
    for match in _PATH_TOKEN.finditer(raw):
        if raw[end:match.start()].strip(" \r\n\t,"):
            _reject()
        tokens.append(match.group())
        end = match.end()
    if raw[end:].strip(" \r\n\t,") or not tokens or tokens[0] not in ("M", "m"):
        _reject()
    i = segments = 0
    while i < len(tokens):
        command = tokens[i].upper()
        if command not in _PATH_ARITY:
            _reject()
        i += 1
        start = i
        while i < len(tokens) and not tokens[i].isalpha():
            i += 1
        values = [_number(t) for t in tokens[start:i]]
        arity = _PATH_ARITY[command]
        if not arity:
            if values:
                _reject()
            segments += 1
            continue
        if not values or len(values) % arity:
            _reject()
        if command == "A":
            for offset in range(0, len(values), arity):
                if (values[offset] < 0 or values[offset + 1] < 0
                        or values[offset + 3] not in (0, 1)
                        or values[offset + 4] not in (0, 1)):
                    _reject()
        segments += len(values) // arity
    return segments


def _transform(raw: str) -> None:
    matches = list(re.finditer(r"(translate|scale|rotate)\s*\(([^()]*)\)", raw))
    if not 1 <= len(matches) <= 4:
        _reject()
    end = 0
    for match in matches:
        if raw[end:match.start()].strip(" ,\t\r\n"):
            _reject()
        end = match.end()
        kind, body = match.groups()
        values = _numbers(body, limit=3)
        if len(values) not in ({1, 3} if kind == "rotate" else {1, 2}):
            _reject()
        if kind == "scale" and any(not 0.1 <= abs(v) <= 10 for v in values):
            _reject()
        if kind == "rotate" and not -360 <= values[0] <= 360:
            _reject()
    if raw[end:].strip(" ,\t\r\n"):
        _reject()


def _marker_link(value: str) -> tuple[str, str]:
    match = _LOCAL_MARKER_RE.fullmatch(value.strip())
    if not match:
        _reject("svg_forbidden_attribute")
    return f"url(#{match.group(1)})", match.group(1)


def _attribute(name: str, raw: str) -> str:
    value = raw.strip()
    if len(value) > 2048:
        _reject("svg_forbidden_attribute")
    if name in MARKER_LINKS:
        return _marker_link(value)[0]
    if "url(" in value.lower():
        _reject("svg_forbidden_attribute")
    if name in ("fill", "stroke"):
        if value not in _COLORS:
            _reject("svg_forbidden_attribute")
        return _COLORS[value]
    enums = {"stroke-linecap": {"butt", "round", "square"},
             "stroke-linejoin": {"miter", "round", "bevel"},
             "text-anchor": {"start", "middle", "end"},
             "baseline-shift": {"sub", "super", "0"}}
    if name in enums:
        if value not in enums[name]:
            _reject("svg_forbidden_attribute")
    elif name == "transform":
        _transform(value)
    elif name == "stroke-dasharray":
        values = _numbers(value, limit=8)
        if min(values) < 0 or not any(values):
            _reject()
    elif name == "points":
        if len(_numbers(value)) % 2:
            _reject()
    elif name == "d":
        _path(value)
    else:
        limits = {"stroke-width": (0.5, 4), "font-size": (12, 28)}
        low, high = limits.get(name, (0 if name in {
            "width", "height", "rx", "ry", "r"} else -4096, 4096))
        _number(value, low, high)
    return value


def _style(raw: str) -> dict[str, str]:
    if len(raw) > 1024 or any(token in raw for token in ("{", "}", "@", "/*", "*/")):
        _reject("svg_forbidden_attribute")
    out: dict[str, str] = {}
    for declaration in raw.split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            _reject("svg_forbidden_attribute")
        name, value = declaration.split(":", 1)
        name = name.strip().lower()
        if name not in STYLE_PRESENTATION:
            _reject("svg_forbidden_attribute")
        out[name] = _attribute(name, value)
    if not out:
        _reject("svg_forbidden_attribute")
    return out


@dataclass(frozen=True)
class NormalizedSvg:
    svg: str
    width: int
    height: int


def normalize_svg(raw_svg: str, *, alt: str = "", caption: str = "") -> NormalizedSvg:
    if not isinstance(raw_svg, str) or len(raw_svg.encode("utf-8")) > MAX_SVG_BYTES:
        _reject("svg_too_large")
    if re.search(r"<!|<\?", raw_svg):
        _reject("svg_forbidden_node")
    try:
        from defusedxml.ElementTree import fromstring
        source = fromstring(raw_svg, forbid_dtd=True, forbid_entities=True,
                            forbid_external=True)
    except ImportError:
        _reject("svg_parser_unavailable")
    except Exception:
        _reject("svg_invalid_xml")
    if source.tag != f"{{{SVG_NS}}}svg":
        _reject("svg_forbidden_node")
    box = _numbers(source.attrib.get("viewBox", ""), limit=4)
    if len(box) != 4 or box[:2] != [0, 0]:
        _reject()
    width, height = box[2:]
    if (not width.is_integer() or not height.is_integer()
            or not 320 <= width <= 960 or not 200 <= height <= 720
            or not 0.75 <= width / height <= 3):
        _reject()
    counters = {"nodes": 0, "text": 0, "segments": 0, "shapes": 0}
    marker_ids: set[str] = set()
    marker_refs: set[str] = set()

    def copy(node: ET.Element, depth: int, parent: str,
             in_defs: bool = False) -> ET.Element | None:
        counters["nodes"] += 1
        if counters["nodes"] > MAX_NODES or depth > MAX_DEPTH:
            _reject("svg_budget_exceeded")
        if not isinstance(node.tag, str) or not node.tag.startswith(f"{{{SVG_NS}}}"):
            _reject("svg_forbidden_node")
        tag = node.tag[len(SVG_NS) + 2:]
        if tag not in ELEMENT_ATTRIBUTES or (tag == "svg" and depth != 1):
            _reject("svg_forbidden_node")
        if tag == "defs" and parent not in {"svg", "g"}:
            _reject("svg_forbidden_node")
        if tag == "marker" and parent != "defs":
            _reject("svg_forbidden_node")
        if parent == "defs" and tag != "marker":
            _reject("svg_forbidden_node")
        if tag == "tspan" and parent not in {"text", "tspan"}:
            _reject("svg_forbidden_node")
        if parent in {"text", "tspan"} and tag != "tspan":
            _reject("svg_forbidden_node")
        if parent not in {"", "svg", "g", "defs", "marker", "text", "tspan"}:
            _reject("svg_forbidden_node")
        out = ET.Element(node.tag)
        style_raw = ""
        for key, value in node.attrib.items():
            if key not in ELEMENT_ATTRIBUTES[tag]:
                _reject("svg_forbidden_attribute")
            if tag == "svg":
                continue  # canonical root properties are set below
            if key == "style":
                style_raw = value
                continue
            if tag == "marker":
                if key == "id":
                    if not _ID_RE.fullmatch(value):
                        _reject("svg_forbidden_attribute")
                    if value in marker_ids:
                        _reject("svg_forbidden_attribute")
                    marker_ids.add(value)
                    out.set("id", value)
                    continue
                if key in {"markerWidth", "markerHeight"}:
                    _number(value, 1, 32)
                    out.set(key, value.strip())
                    continue
                if key in {"refX", "refY"}:
                    _number(value, -64, 64)
                    out.set(key, value.strip())
                    continue
                if key == "markerUnits":
                    if value not in {"strokeWidth", "userSpaceOnUse"}:
                        _reject("svg_forbidden_attribute")
                    out.set(key, value)
                    continue
                if key == "orient":
                    if value not in {"auto", "auto-start-reverse"}:
                        _number(value, -360, 360)
                    out.set(key, value.strip())
                    continue
                if key == "viewBox":
                    values = _numbers(value, limit=4)
                    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                        _reject()
                    out.set(key, " ".join(str(int(v)) if v.is_integer() else str(v) for v in values))
                    continue
                if key == "preserveAspectRatio":
                    if value not in {"xMidYMid meet", "xMidYMid slice", "none"}:
                        _reject("svg_forbidden_attribute")
                    out.set(key, value)
                    continue
            normalized = _attribute(key, value)
            out.set(key, normalized)
            if key in MARKER_LINKS:
                marker_refs.add(_marker_link(normalized)[1])
        if style_raw:
            for key, value in _style(style_raw).items():
                out.set(key, value)
        if tag == "marker" and "id" not in out.attrib:
            _reject("svg_forbidden_attribute")
        if tag in {"title", "desc"}:
            if len(node):
                _reject("svg_forbidden_node")
            return None  # regenerate accessible text from the audited fields
        nested_defs = in_defs or tag in {"defs", "marker"}
        if tag not in {"svg", "g", "defs", "marker", "text", "tspan"} and not nested_defs:
            counters["shapes"] += 1
        if tag == "path":
            counters["segments"] += _path(node.attrib.get("d", ""))
        if tag in {"polyline", "polygon"}:
            points = _numbers(node.attrib.get("points", ""))
            if len(points) < (6 if tag == "polygon" else 4):
                _reject()
            counters["segments"] += len(points) // 2
        if counters["segments"] > MAX_SEGMENTS:
            _reject("svg_budget_exceeded")
        if tag in {"text", "tspan"}:
            out.text = node.text
            counters["text"] += len(node.text or "")
        elif (node.text or "").strip():
            _reject("svg_forbidden_node")
        for child in node:
            rebuilt = copy(child, depth + 1, tag, nested_defs)
            if rebuilt is not None:
                out.append(rebuilt)
                if tag in {"text", "tspan"}:
                    rebuilt.tail = child.tail
                    counters["text"] += len(child.tail or "")
            if tag not in {"text", "tspan"} and (child.tail or "").strip():
                _reject("svg_forbidden_node")
        if counters["text"] > 600:
            _reject("svg_budget_exceeded")
        return out

    root = copy(source, 1, "")
    if root is None or not counters["shapes"]:
        _reject("svg_invalid_geometry")
    if not marker_refs.issubset(marker_ids):
        _reject("svg_forbidden_attribute")
    root.attrib = {"viewBox": f"0 0 {int(width)} {int(height)}",
                   "width": str(int(width)), "height": str(int(height)),
                   "preserveAspectRatio": "xMidYMid meet"}
    # Explicit shape/text defaults, without accepting arbitrary CSS or fonts.
    def defaults(node: ET.Element, inherited: dict[str, str]) -> None:
        tag = node.tag.split("}")[-1]
        if tag in {"text", "tspan"}:
            node.attrib.setdefault("fill", inherited.get("fill", "#000"))
            node.attrib.setdefault("font-size", "18")
        elif tag not in {"svg", "g", "defs", "marker"}:
            for key, default in (("fill", "none"), ("stroke", "#000"),
                                 ("stroke-width", "2")):
                node.attrib.setdefault(key, inherited.get(key, default))
        effective = {**inherited, **{k: v for k, v in node.attrib.items()
                                    if k in PRESENTATION and k != "transform"}}
        for child in node:
            defaults(child, effective)
    defaults(root, {})
    if caption:
        ET.SubElement(root, f"{{{SVG_NS}}}desc").text = caption
    if alt:
        ET.SubElement(root, f"{{{SVG_NS}}}title").text = alt
    svg = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES or len(list(root.iter())) > MAX_NODES:
        _reject("svg_budget_exceeded")
    return NormalizedSvg(svg, int(width), int(height))


class GeneratedIllustration(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    kind: Literal["svg"]
    alt: str = Field(min_length=1, max_length=600)
    caption: str = Field(default="", max_length=120)
    svg: str = Field(max_length=MAX_SVG_BYTES)


def _hash(svg: str, alt: str, caption: str) -> str:
    payload = json.dumps({"svg": svg, "alt": alt, "caption": caption,
                          "schema_version": 1}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class QuestionIllustration(GeneratedIllustration):
    schema_version: Literal[1] = 1
    sanitizer_version: Literal[1, 2] = 2
    content_hash: str
    width: int
    height: int

    @model_validator(mode="after")
    def canonical(self) -> "QuestionIllustration":
        if not self.alt.strip():
            _reject("illustration_alt_missing")
        normalized = normalize_svg(self.svg, alt=self.alt, caption=self.caption)
        if ((normalized.svg, normalized.width, normalized.height) !=
                (self.svg, self.width, self.height)
                or self.content_hash != _hash(self.svg, self.alt, self.caption)):
            raise ValueError("illustration_snapshot_not_canonical")
        return self


def normalize_illustration(raw: Any) -> QuestionIllustration:
    if isinstance(raw, QuestionIllustration):
        return QuestionIllustration.model_validate(raw.model_dump())
    if isinstance(raw, dict) and "sanitizer_version" in raw:
        return QuestionIllustration.model_validate(raw)
    try:
        generated = GeneratedIllustration.model_validate(raw)
        if not generated.alt.strip():
            _reject("illustration_alt_missing")
        normalized = normalize_svg(generated.svg, alt=generated.alt,
                                   caption=generated.caption)
        return QuestionIllustration(
            kind="svg", alt=generated.alt, caption=generated.caption,
            svg=normalized.svg, width=normalized.width, height=normalized.height,
            content_hash=_hash(normalized.svg, generated.alt, generated.caption))
    except IllustrationValidationError:
        raise
    except (ValueError, TypeError):
        _reject("illustration_invalid_schema")


def normalize_question_illustration(question: dict, *, policy: str) -> dict:
    q = dict(question)
    raw = q.get("illustration")
    if raw is None:
        if policy == "required":
            _reject("illustration_required_missing")
        if re.search(r"如图|图中|下图|上图|见图|as shown|diagram below|figure below",
                     str(q.get("stem") or ""), re.I):
            _reject("illustration_missing_dependency")
        q["illustration"] = None
    elif policy == "off":
        _reject("illustration_disabled")
    else:
        q["illustration"] = normalize_illustration(raw).model_dump()
    return q


def illustration_grammar() -> str:
    """The prompt and validator share one element/attribute allowlist."""
    return json.dumps({"elements": {k: sorted(v) for k, v in ELEMENT_ATTRIBUTES.items()},
                       "local_marker_refs_only": True,
                       "style_properties": sorted(STYLE_PRESENTATION),
                       "max_utf8_bytes": MAX_SVG_BYTES, "max_nodes": MAX_NODES,
                       "max_depth": MAX_DEPTH, "max_path_segments": MAX_SEGMENTS},
                      ensure_ascii=False, sort_keys=True)


def illustration_telemetry(quiz_data: dict) -> dict:
    """Trace only bounded operational metadata, never SVG/alt/student text."""
    verification = quiz_data.get("verification") or {}
    return {
        "policy": verification.get("illustration_policy", "off"),
        "generation_calls": verification.get("generation_calls", 0),
        "completion_tokens": verification.get("completion_tokens", 0),
        "elapsed_ms": verification.get("generation_elapsed_ms", 0),
        "diagrams": [{"hash": q["illustration"]["content_hash"],
                      "bytes": len(q["illustration"]["svg"].encode("utf-8")),
                      "sanitizer_version": q["illustration"]["sanitizer_version"]}
                     for q in (quiz_data.get("questions") or [])[:5]
                     if isinstance(q, dict) and isinstance(q.get("illustration"), dict)],
    }
