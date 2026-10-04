"""V3 binds drawing data to a question, without a component assembly schema."""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import AliasChoices, Field, JsonValue, model_validator

from app.diagrams.catalog import digest
from app.diagrams.schema import StrictModel

from .contracts import (IllustrationError, Presentation, PublicQuestion, ReviewResult,
                        VisualRole, literal_number_supported)


class DrawingInputV3(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][\w:-]{0,63}$")
    description: str = Field(min_length=1, max_length=240)
    value: JsonValue
    unit: str = Field(default="", max_length=40)
    display: Literal["explicit", "depict_only", "symbol_only"] = Field(
        default="explicit", validation_alias=AliasChoices("display", "display_policy"))
    # Required only for inputs first extracted from an already frozen question.
    source_quote: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def bounded_value(self):
        count = 0
        def check(value, depth=0):
            nonlocal count
            count += 1
            if count > 512 or depth > 6:
                raise ValueError("drawing input exceeds JSON budget")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("nonfinite drawing input")
            if isinstance(value, str) and len(value) > 4000:
                raise ValueError("unbounded drawing text")
            if isinstance(value, list):
                for child in value:
                    check(child, depth+1)
            if isinstance(value, dict):
                for key, child in value.items():
                    if len(key) > 80:
                        raise ValueError("unbounded drawing key")
                    check(child, depth+1)
        check(self.value)
        return self


class VisualSpecV3(StrictModel):
    visual_role: VisualRole = "supplemental"
    description: str = Field(default="", max_length=2400)
    drawing_inputs: list[DrawingInputV3] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def unique_inputs(self):
        if len({row.id for row in self.drawing_inputs}) != len(self.drawing_inputs):
            raise ValueError("duplicate drawing input")
        if self.visual_role == "none" and self.drawing_inputs:
            raise ValueError("none has drawing inputs")
        return self


class QuestionVisualContractV3(VisualSpecV3):
    schema_version: Literal[3] = 3
    question_ref: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(default=1, ge=1, le=1_000_000)
    material_revision: int = Field(default=1, ge=1)
    contract_hash: str = ""
    public_question: PublicQuestion
    presentation_constraints: Presentation = Field(default_factory=Presentation)
    illustration_guidance: str = Field(default="", max_length=1200)
    authoring_gold: dict[str, JsonValue] = Field(default_factory=dict)
    frozen_question: bool = False

    @model_validator(mode="after")
    def trusted_identity(self):
        if self.frozen_question and self.visual_role == "essential":
            raise IllustrationError("question_material_incomplete")
        expected = digest(self.model_dump(mode="json", exclude={"contract_hash", "illustration_guidance"}))
        if self.contract_hash and self.contract_hash != expected:
            raise ValueError("contract hash mismatch")
        self.contract_hash = expected
        return self

    @property
    def visual_spec(self) -> VisualSpecV3:
        return VisualSpecV3.model_validate(self.model_dump(mode="json", include={
            "visual_role", "description", "drawing_inputs"}))

    def composer_view(self) -> dict:
        # V3 needs depicted values to draw geometry. They are drawing inputs,
        # explicitly marked against textual disclosure, rather than gold.
        return self.model_dump(mode="json", exclude={"authoring_gold", "contract_hash"})


class MaterialNeedV3(StrictModel):
    need_id: str = Field(pattern=r"^[A-Za-z][\w-]{0,39}$")
    name: str = Field(min_length=1, max_length=100)
    synonyms: list[str] = Field(default_factory=list, max_length=8)
    purpose: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def bounded_synonyms(self):
        if any(not row.strip() or len(row) > 100 for row in self.synonyms):
            raise ValueError("invalid material synonym")
        return self


class VisualRequirementsV3(VisualSpecV3):
    schema_version: Literal[3] = 3
    needs: list[MaterialNeedV3] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_needs(self):
        if len({row.need_id for row in self.needs}) != len(self.needs):
            raise ValueError("duplicate material need")
        if self.visual_role == "none" and self.needs:
            raise ValueError("none has material needs")
        return self


class MaterialReferenceV3(StrictModel):
    asset_id: str = Field(min_length=1, max_length=96)
    version: int = Field(ge=1, le=1_000_000)


class SvgDraftV3(StrictModel):
    schema_version: Literal[3] = 3
    action: Literal["draw"] = "draw"
    svg: str = Field(min_length=1, max_length=128*1024)
    alt: str = Field(min_length=1, max_length=600)
    caption: str = Field(default="", max_length=120)
    used_materials: list[MaterialReferenceV3] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_references(self):
        if len({row.asset_id for row in self.used_materials}) != len(self.used_materials):
            raise ValueError("duplicate material reference")
        return self


class MaterialRequestV3(StrictModel):
    schema_version: Literal[3] = 3
    action: Literal["request_materials"]
    needs: list[MaterialNeedV3] = Field(min_length=1, max_length=12)


class CandidateNeedV3(StrictModel):
    need_id: str
    candidate_ids: list[str] = Field(default_factory=list, max_length=3)


class CandidateMaterialV3(StrictModel):
    asset_id: str
    version: int
    title: str
    svg: str
    source_hash: str
    guidance_version: str
    usage_guidance: str = ""
    review_guidance: str = ""
    allowed_for_needs: list[str] = Field(default_factory=list, max_length=24)


class CandidateBundleV3(StrictModel):
    schema_version: Literal[3] = 3
    catalog_version: str
    needs: list[CandidateNeedV3] = Field(default_factory=list, max_length=24)
    assets: list[CandidateMaterialV3] = Field(default_factory=list, max_length=12)
    retrieval_trace: dict[str, JsonValue] = Field(default_factory=dict)

    def allowed(self, asset_id: str, version: int) -> bool:
        return any(row.asset_id == asset_id and row.version == version for row in self.assets)


class ReviewResultV3(ReviewResult):
    # A checked ID alone is insufficient for essential numeric stimuli. Keep
    # the independently reconstructed visible value as private audit evidence.
    observed_values: dict[str, JsonValue] = Field(default_factory=dict)


class DiagramSourceV3(StrictModel):
    schema_version: Literal[3] = 3
    pipeline_mode: Literal["v3"] = "v3"
    catalog_version: str
    renderer_version: str
    scene_hash: str
    content_hash: str
    svg_source_hash: str
    contract_hash: str
    asset_versions: dict[str, int] = Field(default_factory=dict)
    guidance_versions: dict[str, str] = Field(default_factory=dict)
    material_source_hashes: dict[str, str] = Field(default_factory=dict)
    draft: SvgDraftV3
    rendered_bounds: list[float] = Field(default_factory=list, max_length=4)
    review_gates: dict[str, Literal["passed", "unreviewed", "failed"]] = Field(default_factory=dict)
    review_evidence: dict[str, ReviewResultV3] = Field(default_factory=dict)


def input_supported(row: DrawingInputV3, public: PublicQuestion) -> bool:
    """Extraction may quote public data, never recover it from an answer."""
    sources = [public.stem, *public.options.values()]
    quote = row.source_quote
    if not quote or not any(quote in text for text in sources):
        return False
    def supported(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return literal_number_supported(value, quote)
        if isinstance(value, list):
            return all(supported(item) for item in value)
        if isinstance(value, dict):
            return all(supported(item) for item in value.values())
        if isinstance(value, bool):
            # Boolean meaning belongs in a quoted description, not a number.
            return row.description in quote
        return value is not None and str(value) in quote
    return supported(row.value)


def material_contract(question: dict, *, question_ref: str, revision=1, frozen=False,
                      grade="", illustration_guidance="", **_kwargs) -> QuestionVisualContractV3:
    """Bind authoritative question/gold; permit old text questions to use V3."""
    raw = question.get("visual_spec") or question.get("material_contract") or {}
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if isinstance(raw.get("visual_spec"), dict):
        raw = {**raw, **raw["visual_spec"]}
    public = PublicQuestion(type=question.get("type", question.get("q_type", "short_answer")),
        stem=question["stem"], options=question.get("options") or {},
        subject=question.get("subject", ""), grade=grade or question.get("grade", ""))
    projected = {key: raw[key] for key in ("visual_role", "description", "drawing_inputs",
        "presentation_constraints") if key in raw}
    if not raw:
        dependent = bool(re.search(r"如图|图中|下图|读图|看图|as shown|figure below|read.*graph",
                                   public.stem, re.I))
        projected["visual_role"] = "essential" if dependent and not frozen else "supplemental"
    if "drawing_inputs" not in projected and raw.get("facts"):
        projected["drawing_inputs"] = [{"id": row["id"],
            "description": row.get("predicate") or row.get("symbol") or row["id"],
            "value": row["value"], "unit": row.get("unit", ""),
            "display": row.get("display_policy", "explicit"),
            "source_quote": row.get("source_quote", "")} for row in raw["facts"]
            if row.get("display_policy") != "hidden" and (not frozen or
                row.get("source_ref") == "stem" or str(row.get("source_ref", "")).startswith("options:"))]
    if frozen:
        # A frozen text question cannot turn into a required read-the-picture
        # question, nor gain an answer-derived reading on a later enrichment.
        projected["visual_role"] = "supplemental" if question.get("visual_role") != "none" else "none"
        # An old authoring description may contain non-public blueprint values.
        # Later CAT enrichment takes its drawing conditions from frozen text.
        projected["description"] = public.stem
        projected["drawing_inputs"] = [row.model_dump(mode="json") for data in projected.get("drawing_inputs", [])
            if input_supported(row := DrawingInputV3.model_validate(data), public)]
    return QuestionVisualContractV3(question_ref=question_ref, question_revision=revision,
        public_question=public, frozen_question=frozen, **projected,
        illustration_guidance=illustration_guidance[:1200],
        authoring_gold={key: question.get(key) for key in
            ("answer", "correct_answer", "explanation", "rubric_criteria", "rubric")
            if question.get(key) is not None})
