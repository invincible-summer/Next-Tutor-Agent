"""Scene requests have their own identity, independent of assessment questions."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.diagrams.catalog import digest
from app.diagrams.schema import StrictModel
from .contracts import (Entity, MaterialFact, MaterialRelation, Presentation,
                        Unknown, VisualBriefV2, validate_scientific_material)
from .v3_contracts import DrawingInputV3, MaterialNeedV3, input_supported


class MaterialSelection(StrictModel):
    asset_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    version: int = Field(ge=1, le=1_000_000)


class CreateSession(StrictModel):
    title: str = Field(default="", max_length=120)


class SceneTurn(StrictModel):
    message: str = Field(min_length=1, max_length=2400)
    mode: Literal["v1", "v2", "v3", "v4"] = "v1"
    selected_materials: list[MaterialSelection] = Field(default_factory=list, max_length=12)
    base_revision: int = Field(default=0, ge=0, le=1_000_000)
    source_revision: int | None = Field(default=None, ge=0, le=1_000_000)
    request_id: str | None = Field(default=None, max_length=96, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

    @model_validator(mode="after")
    def valid_selection(self):
        if not self.message.strip():
            raise ValueError("empty scene request")
        if len({row.asset_id for row in self.selected_materials}) != len(self.selected_materials):
            raise ValueError("duplicate material selection")
        if self.mode == "v1" and self.selected_materials:
            raise ValueError("V1 does not accept selected materials")
        return self


class SceneText(StrictModel):
    content: str = Field(min_length=1, max_length=24000)
    subject: str = Field(default="", max_length=40)
    grade: str = Field(default="", max_length=40)

    @property
    def stem(self):
        """Compatibility with scientific validators that read a literal source."""
        return self.content

    @property
    def options(self):
        return {}


class ScientificProjection(StrictModel):
    entities: list[Entity] = Field(default_factory=list, max_length=24)
    facts: list[MaterialFact] = Field(default_factory=list, max_length=48)
    required_relations: list[MaterialRelation] = Field(default_factory=list, max_length=48)
    internal_relations: list[MaterialRelation] = Field(default_factory=list, max_length=48)
    required_marks: list[str] = Field(default_factory=list, max_length=24)
    unknowns: list[Unknown] = Field(default_factory=list, max_length=12)
    prohibited_additions: list[str] = Field(default_factory=list, max_length=24)
    presentation_constraints: Presentation = Field(default_factory=Presentation)


class SceneMaterialContract(ScientificProjection):
    schema_version: Literal[2] = 2
    task_kind: Literal["scenario"] = "scenario"
    source: SceneText
    visual_role: Literal["supplemental"] = "supplemental"
    contract_hash: str = ""

    @property
    def public_question(self):
        return self.source

    @property
    def frozen_question(self):
        return False

    def source_text(self, ref):
        return self.source.content if ref in {"stem", "source"} else ""

    @model_validator(mode="after")
    def scientific_sources(self):
        # Shared scientific/source validation; no question reference or gold.
        return validate_scientific_material(self)

    def composer_view(self):
        return self.model_dump(mode="json", exclude={"contract_hash"})


class SceneRequirementsV2(StrictModel):
    material: ScientificProjection
    brief: VisualBriefV2


class SceneRequirementsV3(StrictModel):
    description: str = Field(min_length=1, max_length=2400)
    drawing_inputs: list[DrawingInputV3] = Field(default_factory=list, max_length=48)
    needs: list[MaterialNeedV3] = Field(default_factory=list, max_length=12)
    presentation_constraints: Presentation = Field(default_factory=Presentation)


class SceneVisualContract(SceneRequirementsV3):
    schema_version: Literal[3] = 3
    task_kind: Literal["scenario"] = "scenario"
    source: SceneText
    visual_role: Literal["supplemental"] = "supplemental"
    contract_hash: str = ""

    @model_validator(mode="after")
    def source_identity(self):
        if any(not input_supported(row, self.source) for row in self.drawing_inputs):
            raise ValueError("drawing input not supported by user source")
        if len({row.id for row in self.drawing_inputs}) != len(self.drawing_inputs):
            raise ValueError("duplicate drawing input")
        self.contract_hash = digest(self.model_dump(mode="json", exclude={"contract_hash"}))
        return self

    @property
    def public_question(self):
        return self.source

    def composer_view(self):
        return self.model_dump(mode="json", exclude={"contract_hash"})
