"""Safe declarative controls for uploaded SVGs; no executable expressions."""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from pydantic import Field, JsonValue, model_validator
from typing import Literal

from .schema import StrictModel, DiagramError
from .interface import INTERFACE_VERSION, material_interface, parameter_contract

NUMERIC = {"rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"}, "ellipse": {"cx", "cy", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2"}, "text": {"x", "y", "font-size"},
    "tspan": {"x", "y"}}


class SvgParameter(StrictModel):
    type: Literal["number", "integer", "string", "color"]
    role: Literal["quantity", "text", "schematic", "appearance"]
    description: str = Field(min_length=1, max_length=160)
    unit: str = Field(default="", max_length=24)
    default: JsonValue
    minimum: float | None = Field(default=None, ge=-4096, le=4096)
    maximum: float | None = Field(default=None, ge=-4096, le=4096)
    max_length: int = Field(default=40, ge=1, le=80)

    @model_validator(mode="after")
    def valid(self):
        if self.role == "text" and self.type != "string" or self.type == "string" and self.role != "text":
            raise ValueError("text role requires string")
        if self.type == "color" and self.role != "appearance":
            raise ValueError("colors are appearance controls")
        if self.type in {"number", "integer"} and (
                self.minimum is None or self.maximum is None or self.minimum >= self.maximum):
            raise ValueError("numeric controls require a finite interval")
        if self.role in {"appearance", "schematic"} and self.unit not in {"", "diagram_px"}:
            raise ValueError("display controls cannot declare physical units")
        if self.type == "integer" and (self.minimum != int(self.minimum) or self.maximum != int(self.maximum)):
            raise ValueError("integer interval requires integer boundaries")
        self.check(self.default)
        return self

    def check(self, value):
        if self.type in {"number", "integer"}:
            if type(value) not in {int, float} or not math.isfinite(value) or not self.minimum <= value <= self.maximum:
                raise ValueError("numeric control outside interval")
            if self.type == "integer" and type(value) is not int:
                raise ValueError("integer control requires integer")
        elif self.type == "color":
            if not isinstance(value, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", value):
                raise ValueError("invalid color")
        elif not isinstance(value, str) or len(value) > self.max_length or any(c in value for c in "<>\n\r"):
            raise ValueError("invalid text control")


class SvgBinding(StrictModel):
    parameter: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    element_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    attribute: Literal["x", "y", "width", "height", "rx", "ry", "cx", "cy", "r",
        "x1", "x2", "y1", "y2", "font-size", "text", "fill", "stroke"]
    factor: float = Field(default=1, ge=-4096, le=4096)
    offset: float = Field(default=0, ge=-4096, le=4096)


class SvgParameterization(StrictModel):
    schema_version: Literal["1.0.0"] = INTERFACE_VERSION
    parameters: dict[str, SvgParameter] = Field(default_factory=dict, max_length=12)
    bindings: list[SvgBinding] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def declarations(self):
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key) for key in self.parameters):
            raise ValueError("invalid parameter key")
        if {row.parameter for row in self.bindings} != set(self.parameters):
            raise ValueError("every parameter requires an SVG binding")
        targets = [(row.element_id, row.attribute) for row in self.bindings]
        if len(targets) != len(set(targets)):
            raise ValueError("ambiguous SVG binding")
        return self

    def interface(self):
        specs = {key: {**spec.model_dump(exclude_none=True), "condition_bearing": spec.role != "appearance",
            "non_quantitative_allowed": spec.role == "schematic",
            "svg_bindings": [binding.model_dump() for binding in self.bindings if binding.parameter == key]}
            for key, spec in self.parameters.items()}
        return material_interface(parameter_contract(specs))

    def resolve(self, values):
        if not isinstance(values, dict) or set(values) - set(self.parameters):
            raise DiagramError("diagram_unknown_parameter")
        resolved = {}
        try:
            for key, spec in self.parameters.items():
                value = values.get(key, spec.default)
                spec.check(value)
                resolved[key] = value
        except ValueError:
            raise DiagramError("diagram_invalid_parameter") from None
        return resolved

    def apply(self, svg, values=None):
        resolved = self.resolve(values or {})
        root = ET.fromstring(svg)
        ids = {}
        for node in root.iter():
            if node.get("id"):
                if node.get("id") in ids:
                    raise DiagramError("diagram_invalid_parameter")
                ids[node.get("id")] = node
        for binding in self.bindings:
            node = ids.get(binding.element_id)
            if node is None:
                raise DiagramError("diagram_invalid_parameter")
            tag = node.tag.rsplit("}", 1)[-1]
            spec, value = self.parameters[binding.parameter], resolved[binding.parameter]
            if spec.type in {"number", "integer"} and binding.attribute in NUMERIC.get(tag, set()):
                number = value * binding.factor + binding.offset
                if not math.isfinite(number) or not -4096 <= number <= 4096:
                    raise DiagramError("diagram_invalid_parameter")
                if binding.attribute in {"r", "rx", "ry", "width", "height"} and number <= 0:
                    raise DiagramError("diagram_invalid_parameter")
                node.set(binding.attribute, f"{number:g}")
            elif spec.type == "string" and binding.attribute == "text" and tag in {"text", "tspan"} and not list(node):
                if binding.factor != 1 or binding.offset != 0:
                    raise DiagramError("diagram_invalid_parameter")
                node.text = value
            elif spec.type == "color" and binding.attribute in {"fill", "stroke"} and tag in {*NUMERIC, "path", "polygon", "polyline"}:
                if binding.factor != 1 or binding.offset != 0:
                    raise DiagramError("diagram_invalid_parameter")
                node.set(binding.attribute, value)
            else:
                raise DiagramError("diagram_invalid_parameter")
        return ET.tostring(root, encoding="unicode"), resolved

    def validate_template(self, svg):
        """Declared defaults must reproduce the actual source, not change it."""
        rendered, _ = self.apply(svg)
        before = {node.get("id"): node for node in ET.fromstring(svg).iter() if node.get("id")}
        after = {node.get("id"): node for node in ET.fromstring(rendered).iter() if node.get("id")}
        mismatches = []
        for index, binding in enumerate(self.bindings):
            old, new = before[binding.element_id], after[binding.element_id]
            source = old.text or "" if binding.attribute == "text" else old.get(binding.attribute, "0")
            value = new.text or "" if binding.attribute == "text" else new.get(binding.attribute)
            spec = self.parameters[binding.parameter]
            if spec.type in {"number", "integer"}:
                # Hand-authored SVGs often round coordinates to tenths of a
                # pixel. Such rounding must not look like a broken binding.
                match = abs(float(source)-float(value)) <= .05 + 1e-9
            else:
                def color(text):
                    return "#"+"".join(c*2 for c in text[1:]) if re.fullmatch(r"#[0-9a-fA-F]{3}", text) else text
                match = color(source).lower() == color(value).lower() if spec.type == "color" else source == value
            if not match:
                mismatches.append({"location": ["bindings", index], "type": "default_mismatch",
                    "message": f"{binding.element_id}.{binding.attribute}: 默认值 {spec.default} × factor {binding.factor} + offset {binding.offset} = {value}，SVG原值是 {source}；修正映射。固定坐标应不绑定，或factor=0且offset=原值。"})
        if mismatches:
            error = DiagramError("material_parameter_default_mismatch")
            error.binding_errors = mismatches
            raise error


def program(value=None):
    return value if isinstance(value, SvgParameterization) else SvgParameterization.model_validate(value or {})
