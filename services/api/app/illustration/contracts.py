"""Closed v2 contracts. Public projections never expose hidden readings or gold."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, JsonValue, model_validator

from app.diagrams.catalog import digest
from app.diagrams.schema import StrictModel

VisualRole = Literal["none", "supplemental", "essential"]
Capability = Literal["liquid_fill", "open_top", "supports_submersion", "heating",
    "support", "rope_attach", "wire_terminal", "tube_terminal", "readable_scale",
    "reading_binding", "geometry_construction", "data_binding", "function_binding",
    "complete_apparatus", "sidearm", "static_illustration"]
RelationType = Literal["connected", "supported_by", "suspended_from", "inside",
    "immersed_in", "series", "parallel_to", "perpendicular", "ordered_left_to_right"]
Layer = Literal["background", "support", "body", "content", "connection",
    "measurement_marks", "geometry_marks", "labels", "emphasis"]
FailureCode = Literal["invalid_contract", "no_meaningful_visual", "missing_material",
    "candidate_not_found", "candidate_capability_mismatch", "scene_schema_invalid",
    "scene_asset_not_authorized", "missing_fact_binding", "parameter_unbound",
    "geometry_out_of_bounds", "relation_unrealizable", "collision_unresolved",
    "text_not_legible", "visual_review_failed", "question_material_incomplete",
    "budget_exhausted", "provider_unavailable", "preview_unavailable", "policy_disabled",
    "patch_conflict", "joint_review_failed", "unsupported_domain", "run_interrupted"]


class IllustrationError(ValueError):
    def __init__(self, code: FailureCode, *, target: str = "canvas", repairable=False):
        self.code, self.target, self.repairable = code, target, repairable
        super().__init__(code)


def literal_number_supported(value, quote):
    """Accept explicit numerals only; never infer a count from a scientific name."""
    if re.search(rf"(?<![\d.−-]){re.escape(f'{value:g}')}(?![\d.])", quote):
        return True
    if value != int(value) or not 0 <= value <= 99:
        return False
    digits = {char: i for i, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    # A bounded Chinese integer followed by an actual count classifier.
    # 二氧化碳, 单位圆, 一半 and compound formulas do not establish a scalar.
    for match in re.finditer(r"(?<![零〇一二两三四五六七八九十百千万])([零〇一二两三四五六七八九十]{1,3})(?=个|条|只|根|组|步|片|对|颗|份|层|次|项|种|块|端|点)", quote):
        token = match[1]
        if token.count("十") == 1:
            left, right = token.split("十")
            if len(left) > 1 or len(right) > 1:
                continue
            number = (digits[left] if left else 1)*10 + (digits[right] if right else 0)
        elif len(token) == 1:
            number = digits[token]
        else:
            continue
        if number == value:
            return True
    return False


class Entity(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][\w:-]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    source_ref: str = Field(default="stem", max_length=80)
    source_quote: str = Field(default="", max_length=240)


class MaterialFact(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][\w:-]{0,63}$")
    type: Literal["scalar", "state", "data", "function", "range", "label", "geometry"]
    entity_id: str = Field(default="", max_length=64)
    value: JsonValue
    unit: str = Field(default="", max_length=24)
    precision: int = Field(default=2, ge=0, le=8)
    source_ref: str = Field(min_length=1, max_length=80)
    source_quote: str = Field(default="", max_length=600)
    display_policy: Literal["explicit", "depict_only", "symbol_only", "hidden"]
    symbol: str = Field(default="", max_length=40)
    predicate: str = Field(default="", max_length=40, pattern=r"^(?:[a-z][a-z0-9_]{0,39})?$")

    @model_validator(mode="after")
    def value_type(self):
        import math
        def finite(value):
            if isinstance(value, float) and not math.isfinite(value):
                return False
            if isinstance(value, list):
                return all(finite(item) for item in value)
            if isinstance(value, dict):
                return all(finite(item) for item in value.values())
            return True
        if not finite(self.value):
            raise ValueError("nonfinite fact")
        if self.type == "scalar" and (not isinstance(self.value, (int, float)) or isinstance(self.value, bool)):
            raise ValueError("scalar must be numeric")
        if self.type == "state" and not isinstance(self.value, bool) or (
                self.predicate in {"liquid_present", "lit", "closed"} and self.type != "state"):
            raise ValueError("state predicate must be boolean")
        return self


class MaterialRelation(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][\w:-]{0,63}$")
    type: RelationType
    from_entity: str = Field(max_length=64)
    to_entity: str = Field(max_length=64)
    fact_refs: list[str] = Field(default_factory=list, max_length=12)
    source_ref: str = Field(default="stem", max_length=80)
    source_quote: str = Field(default="", max_length=240)


class Unknown(StrictModel):
    id: str = Field(max_length=64)
    symbol: str = Field(default="?", max_length=40)
    do_not_show_value: Literal[True] = True


class PublicQuestion(StrictModel):
    type: str = Field(default="short_answer", max_length=40)
    stem: str = Field(min_length=1, max_length=4000)
    options: dict[str, str] = Field(default_factory=dict, max_length=12)
    subject: str = Field(default="", max_length=40)
    grade: str = Field(default="", max_length=40)


Profile = Literal["question_landscape", "question_square", "coordinate_plane",
    "comparison_split", "tabletop"]
PROFILES = {"question_landscape": (960, 560), "question_square": (640, 640),
    "coordinate_plane": (760, 560), "comparison_split": (960, 520), "tabletop": (840, 520)}


class Presentation(StrictModel):
    profile: Profile = "question_landscape"
    style: Literal["textbook_line", "monochrome_line"] = "textbook_line"
    target_width: int = Field(default=640, ge=320, le=960)
    to_scale: bool = False
    preferred_material_names: list[str] = Field(default_factory=list, max_length=4)


def literal_source_span(quote: str, source: str) -> str:
    """Bounded alignment that can only insert two grammatical particles."""
    if not quote or len(quote) < 6 or len(quote) > 600:
        return quote
    starts = [index for index, char in enumerate(source) if char == quote[0]]
    if len(starts) > 32:
        return quote
    matches = []
    for start in starts:
        for added in (1, 2):
            span = source[start:start+len(quote)+added]
            if len(span) != len(quote)+added:
                continue
            states = {(0, 0)}
            for char in span:
                following = set()
                for index, skipped in states:
                    if index < len(quote) and quote[index] == char:
                        following.add((index+1, skipped))
                    if skipped < added and char in "的要应需须可":
                        following.add((index, skipped+1))
                states = following
                if not states:
                    break
            if (len(quote), added) in states:
                matches.append(span)
                if len(matches) > 1:
                    return quote
    return matches[0] if matches else quote


class QuestionMaterialContract(StrictModel):
    schema_version: Literal[2] = 2
    question_ref: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(default=1, ge=1, le=1_000_000)
    material_revision: int = Field(default=1, ge=1)
    contract_hash: str = ""
    public_question: PublicQuestion
    visual_role: VisualRole = "supplemental"
    entities: list[Entity] = Field(default_factory=list, max_length=24)
    facts: list[MaterialFact] = Field(default_factory=list, max_length=48)
    required_relations: list[MaterialRelation] = Field(default_factory=list, max_length=48)
    required_marks: list[str] = Field(default_factory=list, max_length=24)
    unknowns: list[Unknown] = Field(default_factory=list, max_length=12)
    prohibited_additions: list[str] = Field(default_factory=list, max_length=24)
    presentation_constraints: Presentation = Field(default_factory=Presentation)
    authoring_gold: dict[str, JsonValue] = Field(default_factory=dict)
    frozen_question: bool = False

    @model_validator(mode="after")
    def validate_contract(self):
        for rows in (self.entities, self.facts, self.required_relations, self.unknowns):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("duplicate material id")
        entities = {row.id for row in self.entities}
        facts = {row.id for row in self.facts}
        if any(f.entity_id and f.entity_id not in entities for f in self.facts):
            raise ValueError("unknown fact entity")
        for relation in self.required_relations:
            if {relation.from_entity, relation.to_entity} - entities or set(relation.fact_refs) - facts:
                raise ValueError("unknown relation reference")
        if self.visual_role == "none" and (self.entities or self.required_relations):
            raise ValueError("none has visual material")
        for text in self.required_marks + self.prohibited_additions:
            if not text.strip() or len(text) > 120:
                raise ValueError("unbounded mark")
        if self.frozen_question and self.visual_role == "essential":
            raise IllustrationError("question_material_incomplete")
        if self.frozen_question or self.visual_role == "supplemental":
            for row in [*self.entities, *self.facts, *self.required_relations]:
                source = self.source_text(row.source_ref)
                if row.source_quote and row.source_quote not in source and len(row.source_quote) >= 6:
                    # Resolve a unique literal span when the model omitted a
                    # grammatical particle. Only these particles may be
                    # inserted, never negations, digits, units or conditions.
                    row.source_quote = literal_source_span(row.source_quote, source)
                if not row.source_quote or row.source_quote not in source:
                    raise IllustrationError("invalid_contract", target=row.id+":source_quote")
                if isinstance(row, MaterialFact) and row.predicate == "liquid_present":
                    if not re.search(r"水|液|liquid|water|solution", row.source_quote, re.I) or (
                        row.value is True and re.search(r"无水|无[^，。；]{0,8}液|空烧杯|没有|不含|未加|not|empty|without", row.source_quote, re.I)):
                        raise IllustrationError("invalid_contract")
                if isinstance(row, MaterialFact) and row.type in {"scalar", "label", "function", "range", "data"}:
                    def supported(value):
                        if isinstance(value, list):
                            return all(supported(item) for item in value)
                        if isinstance(value, dict):
                            return all(supported(item) for item in value.values())
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            return literal_number_supported(value, row.source_quote)
                        return str(value) in row.source_quote
                    if not supported(row.value):
                        raise IllustrationError("invalid_contract", target=row.id+":value")
        expected = digest(self.model_dump(mode="json", exclude={"contract_hash"}))
        if self.contract_hash and self.contract_hash != expected:
            raise ValueError("contract hash mismatch")
        self.contract_hash = expected
        return self

    def source_text(self, ref: str) -> str:
        if ref == "stem":
            return self.public_question.stem
        if ref.startswith("options:"):
            return self.public_question.options.get(ref.split(":", 1)[1], "")
        return ""

    def composer_view(self) -> dict:
        value = self.model_dump(mode="json", exclude={"authoring_gold", "contract_hash"})
        value["facts"] = []
        for fact in self.facts:
            if fact.display_policy == "hidden":
                continue
            row = fact.model_dump(mode="json")
            if fact.display_policy in {"depict_only", "symbol_only"}:
                row.pop("value")
                row.pop("source_quote")
                row["server_binding"] = True
            value["facts"].append(row)
        return value


class MaterialNeed(StrictModel):
    need_id: str = Field(pattern=r"^[A-Za-z][\w-]{0,39}$")
    entity_ids: list[str] = Field(default_factory=list, max_length=24)
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=40)
    capabilities: list[Capability] = Field(default_factory=list, max_length=12)
    quantity: int = Field(default=1, ge=1, le=12)
    fact_bindings: list[str] = Field(default_factory=list, max_length=24)
    priority: Literal["required", "optional"] = "required"


class BriefRelation(StrictModel):
    relation_id: str = Field(max_length=64)
    type: RelationType
    from_needs: list[str] = Field(max_length=12)
    to_needs: list[str] = Field(max_length=12)
    fact_refs: list[str] = Field(default_factory=list, max_length=24)


class VisualBriefV2(StrictModel):
    schema_version: Literal[2] = 2
    visual_role: VisualRole
    purpose: str = Field(default="", max_length=240)
    view: Literal["front_orthographic", "side_orthographic", "top_orthographic",
        "section", "coordinate_plane", "tabletop"] = "front_orthographic"
    style: Literal["textbook_line", "monochrome_line"] = "textbook_line"
    needs: list[MaterialNeed] = Field(default_factory=list, max_length=12)
    relations_to_express: list[BriefRelation] = Field(default_factory=list, max_length=48)
    labels_to_show: list[str] = Field(default_factory=list, max_length=24)
    labels_forbidden: list[str] = Field(default_factory=list, max_length=24)
    layout_intent: str = Field(default="", max_length=400)
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    rationale_codes: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_brief(self):
        ids = {need.need_id for need in self.needs}
        if len(ids) != len(self.needs) or sum(n.quantity for n in self.needs) > 24:
            raise ValueError("duplicate or excessive needs")
        if self.visual_role == "none" and (self.needs or self.relations_to_express):
            raise ValueError("none has needs")
        if self.visual_role != "none" and (not self.needs or not self.purpose):
            raise ValueError("visual purpose and needs required")
        for row in self.relations_to_express:
            if set(row.from_needs + row.to_needs) - ids:
                raise ValueError("unknown need reference")
        return self


class CandidateNeed(StrictModel):
    need_id: str
    matched: bool
    candidate_ids: list[str] = Field(max_length=6)
    missing_capabilities: list[Capability] = Field(default_factory=list)


class CandidateBundleV2(StrictModel):
    bundle_version: Literal[2] = 2
    catalog_version: str
    metadata_version: str
    retrieval_trace: dict[str, JsonValue]
    needs: list[CandidateNeed]
    assets: list[dict[str, JsonValue]] = Field(max_length=72)

    def allowed(self, need_id: str, asset_id: str, version: int) -> bool:
        return any(n.need_id == need_id and asset_id in n.candidate_ids for n in self.needs) and any(
            a["asset_id"] == asset_id and a["version"] == version for a in self.assets)


class Canvas(StrictModel):
    profile: Profile = "question_landscape"
    width: int | None = None
    height: int | None = None
    background: Literal["paper"] = "paper"

    @model_validator(mode="after")
    def fixed_dimensions(self):
        w, h = PROFILES[self.profile]
        if self.width not in (None, w) or self.height not in (None, h):
            raise ValueError("invalid canvas profile size")
        self.width, self.height = w, h
        return self


class AssetInstance(StrictModel):
    instance_id: str = Field(pattern=r"^[A-Za-z][\w-]{0,39}$")
    need_id: str = Field(max_length=40)
    entity_id: str = Field(default="", max_length=64)
    entity_map: dict[str, str] = Field(default_factory=dict, max_length=12)
    asset_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,95}$")
    version: int = Field(ge=1, le=1000)
    params: dict[str, JsonValue] = Field(default_factory=dict, max_length=24)
    fact_bindings: dict[str, str] = Field(default_factory=dict, max_length=24)
    non_quantitative: list[str] = Field(default_factory=list, max_length=24)
    anchor_intent: str = Field(min_length=1, max_length=240)
    x: float = Field(ge=0, le=960)
    y: float = Field(ge=0, le=640)
    scale: float = Field(default=1, ge=0.25, le=4)
    rotation: float = Field(default=0, ge=-180, le=180)
    layer: Layer = "body"


class Target(StrictModel):
    instance: str = Field(max_length=96)
    port: str = Field(default="", max_length=40)
    region: str = Field(default="", max_length=40)


class SceneRelation(StrictModel):
    relation_id: str = Field(pattern=r"^[A-Za-z][\w:-]{0,63}$")
    contract_relation_id: str = Field(default="", max_length=64)
    type: RelationType
    start: Target
    end: Target
    fact_refs: list[str] = Field(default_factory=list, max_length=24)
    route: Literal["straight", "orthogonal"] = "straight"
    medium: Literal["wire", "rope", "tube", "line"] = "line"
    layer: Literal["connection", "support"] = "connection"


Placement = Literal["outside_top", "outside_bottom", "outside_right", "outside_left",
    "outside_top_right", "outside_right_lower"]


class Annotation(StrictModel):
    annotation_id: str = Field(pattern=r"^[A-Za-z][\w-]{0,39}$")
    text: str = Field(min_length=1, max_length=100)
    target: Target
    placement: Placement = "outside_right"
    fact_refs: list[str] = Field(default_factory=list, max_length=12)


class SceneGroup(StrictModel):
    group_id: str = Field(max_length=40)
    members: list[str] = Field(max_length=24)
    semantic_role: Literal["apparatus", "comparison", "detail"] = "apparatus"


class OcclusionRule(StrictModel):
    top: str = Field(max_length=96)
    bottom: str = Field(max_length=96)
    reason: Literal["inside_container", "supported_contact", "recipe_parts"]


class SceneDraftV2(StrictModel):
    schema_version: Literal[2] = 2
    action: Literal["compose"] = "compose"
    canvas: Canvas = Field(default_factory=Canvas)
    asset_instances: list[AssetInstance] = Field(min_length=1, max_length=24)
    relations: list[SceneRelation] = Field(default_factory=list, max_length=48)
    annotations: list[Annotation] = Field(default_factory=list, max_length=24)
    groups: list[SceneGroup] = Field(default_factory=list, max_length=8)
    occlusion_rules: list[OcclusionRule] = Field(default_factory=list, max_length=24)
    alt: str = Field(min_length=1, max_length=600)
    caption: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def unique_ids(self):
        for rows, name in ((self.asset_instances, "instance_id"), (self.relations, "relation_id"),
                           (self.annotations, "annotation_id"), (self.groups, "group_id")):
            if len({getattr(row, name) for row in rows}) != len(rows):
                raise ValueError("duplicate scene id")
        return self


class PatchOperation(StrictModel):
    op: Literal["move_instance", "scale_instance", "rotate_instance", "set_param",
        "replace_asset", "move_annotation", "replace_relation_route"]
    instance_id: str = ""
    annotation_id: str = ""
    relation_id: str = ""
    x: float | None = None
    y: float | None = None
    scale: float | None = None
    rotation: float | None = None
    key: str = ""
    fact_id: str = ""
    asset_id: str = ""
    version: int | None = None
    placement: Placement | None = None
    route: Literal["straight", "orthogonal"] | None = None


class ScenePatchV2(StrictModel):
    schema_version: Literal[2] = 2
    action: Literal["patch"] = "patch"
    base_scene_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    operations: list[PatchOperation] = Field(min_length=1, max_length=12)
    reason_codes: list[str] = Field(default_factory=list, max_length=12)
    unchanged_claims: list[str] = Field(default_factory=list, max_length=48)


class ReviewIssue(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    severity: Literal["error", "warning"] = "error"
    target: str = Field(max_length=96)
    description: str = Field(default="", max_length=100)
    repairable: bool = False
    suggested_operation: str = Field(default="", max_length=40)


class ReviewResult(StrictModel):
    status: Literal["passed", "failed", "needs_question_revision"]
    score: dict[str, float] = Field(default_factory=dict, max_length=5)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=24)
    verified_facts: list[str] = Field(default_factory=list, max_length=48)
    rationale_codes: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def errors_block_pass(self):
        if any(not 0 <= score <= 100 for score in self.score.values()):
            raise ValueError("invalid review score")
        if self.status == "passed" and any(i.severity == "error" for i in self.issues):
            raise ValueError("review contradicts errors")
        return self


class LayoutReport(StrictModel):
    status: Literal["passed", "failed"] = "passed"
    bounds: dict[str, list[float]] = Field(default_factory=dict)
    ports: dict[str, dict[str, list[float]]] = Field(default_factory=dict)
    adjustments: list[dict[str, JsonValue]] = Field(default_factory=list)
    violations: list[ReviewIssue] = Field(default_factory=list)
    verified_relations: list[str] = Field(default_factory=list)
    verified_facts: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    remaining_slack: float = 0


class DiagramSourceV2(StrictModel):
    schema_version: Literal[2] = 2
    catalog_version: str
    renderer_version: str
    metadata_version: str
    scene_hash: str
    asset_versions: dict[str, int]
    guidance_versions: dict[str, str] = Field(default_factory=dict)
    scene: SceneDraftV2
    fact_bindings: dict[str, JsonValue]
    layout_report: LayoutReport
    contract_hash: str = ""
    review_gates: dict[str, Literal["passed", "unreviewed", "failed"]] = Field(default_factory=dict)
    review_evidence: dict[str, ReviewResult] = Field(default_factory=dict)


class MaterialRequest(StrictModel):
    schema_version: Literal[2] = 2
    action: Literal["request_materials"]
    needs: list[MaterialNeed] = Field(min_length=1, max_length=12)


class CannotComplete(StrictModel):
    schema_version: Literal[2] = 2
    action: Literal["cannot_complete"]
    code: FailureCode


def public_question(question: dict, *, grade="") -> PublicQuestion:
    return PublicQuestion(type=question.get("type", question.get("q_type", "short_answer")),
        stem=question["stem"], options=question.get("options") or {}, grade=grade)


def material_contract(question: dict, *, question_ref: str, revision=1, frozen=False,
                      grade="") -> QuestionMaterialContract:
    """Bind identity/gold on the server; never trust model hashes or review metadata."""
    raw = question.get("material_contract") or {}
    fields = {"visual_role", "entities", "facts", "required_relations", "required_marks",
        "unknowns", "prohibited_additions", "presentation_constraints"}
    projected = {key: value for key, value in raw.items() if key in fields}
    if not frozen and not projected.get("presentation_constraints", {}).get("to_scale", False):
        # Some providers put explicitly non-quantitative pixel dimensions in
        # facts. They are presentation proposals, never teaching conditions.
        # Drop only this provable class, retaining every physical/unit fact.
        cosmetic = {fact["id"] for fact in projected.get("facts", []) if isinstance(fact, dict)
            and fact.get("unit") in {"diagram_px", "height_fraction"} and (
                fact.get("source_ref") == "blueprint" and (
                    fact.get("unit") == "diagram_px" and not fact.get("source_quote") or re.search(
                    r"非定量|示意|not.to.scale|qualitative", fact.get("source_quote", ""), re.I)) or
                fact.get("type") == "geometry" and isinstance(fact.get("value"), str) and re.search(
                    r"非定量|示意|not.to.scale|qualitative", fact["value"], re.I))}
        if cosmetic:
            projected["facts"] = [fact for fact in projected["facts"] if fact["id"] not in cosmetic]
            projected["required_relations"] = [{**relation, "fact_refs": [ref for ref in relation.get("fact_refs", [])
                if ref not in cosmetic]} for relation in projected.get("required_relations", [])]
    if not raw:
        dependent = bool(re.search(r"如图|图中|下图|读图|看图|as shown|figure below|read.*graph",
                                  question["stem"], re.I))
        projected["visual_role"] = "essential" if dependent else "supplemental"
    return QuestionMaterialContract(**projected, question_ref=question_ref,
        question_revision=revision, public_question=public_question(question, grade=grade),
        frozen_question=frozen, authoring_gold={key: question.get(key) for key in
            ("answer", "explanation", "rubric_criteria", "rubric") if question.get(key) is not None})
