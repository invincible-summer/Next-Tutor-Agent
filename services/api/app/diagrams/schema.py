"""Bounded declarations: models request objects and compose references, never files."""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DiagramError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}


class VisualNeed(StrictModel):
    key: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(default=1, ge=1, le=24)
    features: list[str] = Field(default_factory=list, max_length=8)
    category: str = Field(default="", max_length=40)

    @field_validator("features")
    @classmethod
    def bounded_features(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 60 for value in values):
            raise ValueError("invalid feature")
        return values


class VisualRequirements(StrictModel):
    question_slot: str = Field(default="q1", pattern=r"^q[1-5]$")
    illustration_needed: bool = False
    scene_brief: str = Field(default="", max_length=600)
    needs: list[VisualNeed] = Field(default_factory=list, max_length=12)
    visible_labels: list[str] = Field(default_factory=list, max_length=16)
    layout_intent: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def bounded(self):
        if len({n.key for n in self.needs}) != len(self.needs):
            raise ValueError("duplicate need")
        if sum(n.quantity for n in self.needs) > 24:
            raise ValueError("too many objects")
        if any(len(v) > 80 for v in self.visible_labels):
            raise ValueError("label too long")
        return self


class SceneNode(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    asset_id: str = Field(min_length=1, max_length=96)
    version: int = Field(default=1, ge=1, le=1000)
    x: float = Field(default=0, ge=0, le=960)
    y: float = Field(default=0, ge=0, le=720)
    scale: float = Field(default=1, ge=0.15, le=6)
    rotation: float = Field(default=0, ge=-360, le=360)
    params: dict[str, Any] = Field(default_factory=dict)
    label: str = Field(default="", max_length=80)


class Endpoint(StrictModel):
    node: str = Field(max_length=40)
    anchor: str = Field(default="center", max_length=40)


class Connection(StrictModel):
    start: Endpoint
    end: Endpoint
    kind: Literal["line", "wire", "rope", "tube", "arrow", "dashed"] = "line"
    route: Literal["straight", "orthogonal"] = "straight"
    label: str = Field(default="", max_length=60)


class SceneLabel(StrictModel):
    text: str = Field(min_length=1, max_length=100)
    x: float = Field(ge=0, le=960)
    y: float = Field(ge=0, le=720)
    anchor: Literal["start", "middle", "end"] = "middle"


class RegionTarget(StrictModel):
    node: str = Field(max_length=40)
    region: str = Field(default="", max_length=60)
    anchor: str = Field(default="", max_length=60)

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.region) == bool(self.anchor):
            raise ValueError("target one registered region or anchor")
        return self


class LayoutRelation(StrictModel):
    type: Literal["inside", "immersed_in", "supported_by", "suspended_from"]
    source: RegionTarget
    target: RegionTarget
    source_quote: str = Field(min_length=1, max_length=400)


class SceneSpec(StrictModel):
    schema_version: Literal[1] = 1
    width: int = Field(default=640, ge=320, le=960)
    height: int = Field(default=400, ge=200, le=720)
    nodes: list[SceneNode] = Field(default_factory=list, max_length=24)
    connections: list[Connection] = Field(default_factory=list, max_length=48)
    labels: list[SceneLabel] = Field(default_factory=list, max_length=24)
    layout_relations: list[LayoutRelation] = Field(default_factory=list, max_length=12)
    alt: str = Field(min_length=1, max_length=600)
    caption: str = Field(default="", max_length=120)
    profile: Literal["textbook", "monochrome"] = "textbook"

    @model_validator(mode="after")
    def valid_scene(self):
        if not 0.75 <= self.width / self.height <= 3:
            raise ValueError("invalid aspect ratio")
        if not self.nodes:
            raise ValueError("empty scene")
        if not self.alt.strip():
            raise ValueError("empty alt")
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("duplicate node")
        for connection in self.connections:
            if connection.start.node not in ids or connection.end.node not in ids:
                raise ValueError("unknown endpoint")
        for relation in self.layout_relations:
            if relation.source.node not in ids or relation.target.node not in ids or relation.source.node == relation.target.node:
                raise ValueError("unknown layout relation endpoint")
        return self


class DiagramSource(StrictModel):
    catalog_version: str
    renderer_version: str
    scene_hash: str
    asset_versions: dict[str, int]
    scene: SceneSpec


def finite(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagramError("diagram_invalid_parameter")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise DiagramError("diagram_invalid_parameter")
    return number
