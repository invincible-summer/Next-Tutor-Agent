"""Strict schemas for chem-lab content packs (species/rules/equipment/concepts/experiments).

All content is project-authored source (not runtime data). Every model is
``extra="forbid"`` and integer-only for quantities so the two engines stay
in lockstep; validation runs at catalog load time and in scripts/chem_lab.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

PACK_SCHEMA_VERSION = 1
MAX_LABEL_CHARS = 120
MAX_TEXT_CHARS = 4000
MAX_ID_CHARS = 96


class L10n(BaseModel, extra="forbid"):
    zh: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    en: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class _IdModel(BaseModel, extra="forbid"):
    id: str = Field(min_length=1, max_length=MAX_ID_CHARS, pattern=r"^[a-z0-9][a-z0-9_.-]*$")


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------

SpeciesState = Literal["liquid", "solute", "solid", "gas", "indicator"]
SpeciesRole = Literal[
    "solvent", "acid", "base", "salt", "ion", "indicator",
    "tracer", "precipitate", "gas", "product", "spectator",
]


class SolubilityPoint(BaseModel, extra="forbid"):
    temperature_milli_c: int = Field(ge=-50_000, le=200_000)
    # Solubility as milli-(umol/uL) — umol/uL == mol/L, so 3126 == 3.126 mol/L.
    solubility_umol_per_ul_milli: int = Field(ge=0, le=2_000_000)


class SpeciesDef(_IdModel):
    kind: Literal["species"] = "species"
    schema_version: Literal[1] = 1
    name: L10n
    formula: str = Field(default="", max_length=40)
    state: SpeciesState
    role: SpeciesRole
    hazard: Literal["none", "teaching_virtual_irritant"] = "none"
    color_token: str = Field(default="clear", max_length=48)
    color_label: L10n | None = None
    # Colored solutes: concentration (milli umol/uL) at which tint becomes
    # visible, and tint strength used to pick the dominant color.
    visible_conc_umol_per_ul_milli: int = Field(default=0, ge=0, le=2_000_000)
    color_strength_permille: int = Field(default=0, ge=0, le=1000)
    # Solids: amount (umol) at which a precipitate/crystal bed is visible.
    visible_amount_umol: int = Field(default=0, ge=0, le=100_000_000)
    # Indicator pH bands: [max_ph_milli, color_token] rows, ascending; the
    # last row acts as the fallback band.
    ph_bands: list[list[int | str]] = Field(default_factory=list, max_length=8)
    solubility_table: list[SolubilityPoint] = Field(default_factory=list, max_length=16)
    spectator: bool = False
    source: Literal["project_authored"] = "project_authored"
    source_url: str = Field(default="", max_length=200)
    teaching_note: L10n | None = None

    @model_validator(mode="after")
    def _check_tables(self) -> "SpeciesDef":
        temps = [p.temperature_milli_c for p in self.solubility_table]
        if temps != sorted(temps):
            raise ValueError(f"{self.id}: solubility_table 温度点必须升序")
        for band in self.ph_bands:
            if len(band) != 2 or not isinstance(band[0], int) or not isinstance(band[1], str):
                raise ValueError(f"{self.id}: ph_bands 行必须是 [ph_milli:int, token:str]")
        return self


# ---------------------------------------------------------------------------
# Rules (closed-set DSL; validated again by dsl.py)
# ---------------------------------------------------------------------------

RuleTrigger = Literal[
    "on_aspirate", "on_dispense", "on_pour", "on_heat", "on_stir", "on_wait",
]

ConditionOp = Literal[
    "species_at_least", "volume_between", "temperature_between",
    "ph_between", "mix_at_least", "vessel_connected", "equipment_state",
]

EffectOp = Literal[
    "consume_min_ratio", "produce", "transfer_to_solid", "transfer_to_gas",
    "emit_temperature", "mark_observation", "mark_step",
]


class RuleCondition(BaseModel, extra="forbid"):
    op: ConditionOp
    vessel: str = Field(default="$target", max_length=24)
    species: str = Field(default="", max_length=MAX_ID_CHARS)
    amount_umol: int = Field(default=0, ge=0, le=10**12)
    min_value: int = Field(default=0, le=10**12)
    max_value: int = Field(default=0, le=10**12)
    target: str = Field(default="", max_length=MAX_ID_CHARS)
    expected: str = Field(default="", max_length=48)


class RuleEffect(BaseModel, extra="forbid"):
    op: EffectOp
    vessel: str = Field(default="$target", max_length=24)
    # consume_min_ratio: pairs of [species, ratio] with optional per-firing cap.
    pairs: list[list[int | str]] = Field(default_factory=list, max_length=6)
    cap_umol: int = Field(default=0, ge=0, le=10**12)
    species: str = Field(default="", max_length=MAX_ID_CHARS)
    from_consumed: bool = False
    ratio_to_consumed: int = Field(default=1000, ge=0, le=4000)
    amount_umol: int = Field(default=0, ge=0, le=10**12)
    micro_j_per_umol: int = Field(default=0, ge=0, le=10**9)
    key: str = Field(default="", max_length=64)


class RuleLimits(BaseModel, extra="forbid"):
    max_firings_per_evaluation: int = Field(default=8, ge=1, le=64)


class RuleDef(_IdModel):
    kind: Literal["rule"] = "rule"
    schema_version: Literal[1] = 1
    priority: int = Field(ge=0, le=10_000)
    triggers: list[RuleTrigger] = Field(min_length=1, max_length=8)
    when: list[RuleCondition] = Field(default_factory=list, max_length=8)
    then: list[RuleEffect] = Field(min_length=1, max_length=8)
    limits: RuleLimits = RuleLimits()
    model_scope: str = Field(default="teaching_semi_quantitative", max_length=64)
    public_note: L10n


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------

EquipmentCategory = Literal["vessel", "instrument", "device", "tool", "container"]
MeasureQuantity = Literal["temperature", "ph", "volume", "mass"]


class EquipmentPort(BaseModel, extra="forbid"):
    id: str = Field(min_length=1, max_length=32)
    x: int = Field(ge=-1000, le=2000)
    y: int = Field(ge=-1000, le=2000)


class EquipmentDef(_IdModel):
    kind: Literal["equipment"] = "equipment"
    schema_version: Literal[1] = 1
    name: L10n
    category: EquipmentCategory
    capacity_uL: int = Field(default=0, ge=0, le=10_000_000)
    graduations_uL: list[int] = Field(default_factory=list, max_length=24)
    heat_compatible: bool = False
    pourable: bool = True
    measures: list[MeasureQuantity] = Field(default_factory=list, max_length=4)
    instrument_label: L10n | None = None
    # Bench footprint (viewBox units) and connection ports.
    footprint: list[int] = Field(min_length=2, max_length=2)
    ports: list[EquipmentPort] = Field(default_factory=list, max_length=8)
    # Heating devices: rated energy per 500 ms simulation step at full power.
    rated_micro_j_per_step: int = Field(default=0, ge=0, le=10**9)
    gas_tight: bool = False


# ---------------------------------------------------------------------------
# Concepts
# ---------------------------------------------------------------------------

class ConceptDef(_IdModel):
    kind: Literal["concept"] = "concept"
    schema_version: Literal[1] = 1
    title: L10n
    body: L10n
    equation: str = Field(default="", max_length=160)
    misconception: L10n | None = None
    prerequisites: list[str] = Field(default_factory=list, max_length=8)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

LabMode = Literal["guided", "explore", "self_check"]


class BenchSlot(_IdModel):
    x: int = Field(ge=0, le=4000)
    y: int = Field(ge=0, le=4000)
    w: int = Field(ge=8, le=1000)
    h: int = Field(ge=8, le=1000)


class StartVessel(_IdModel):
    kind: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    slot: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    capacity_uL: int = Field(ge=1, le=10_000_000)
    volume_uL: int = Field(default=0, ge=0, le=10_000_000)
    temperature_milli_c: int | None = Field(default=None, ge=-50_000, le=200_000)
    contents: dict[str, int] = Field(default_factory=dict, max_length=24)
    solids: dict[str, int] = Field(default_factory=dict, max_length=12)
    gases: dict[str, int] = Field(default_factory=dict, max_length=6)
    # Inverted gas-collection cylinders accumulate delivered gas instead of
    # venting it to the atmosphere.
    collects_gas: bool = False
    label: L10n | None = None

    @model_validator(mode="after")
    def _check_amounts(self) -> "StartVessel":
        for table, name in ((self.contents, "contents"), (self.solids, "solids"), (self.gases, "gases")):
            for species, amount in table.items():
                if amount <= 0:
                    raise ValueError(f"{self.id}: {name}.{species} 必须为正整数")
        if self.volume_uL > self.capacity_uL:
            raise ValueError(f"{self.id}: 初始体积超过容量")
        return self


class StartEquipment(_IdModel):
    kind: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    slot: str = Field(min_length=1, max_length=MAX_ID_CHARS)


class StartingState(BaseModel, extra="forbid"):
    room_temperature_milli_c: int = Field(default=25_000, ge=-40_000, le=60_000)
    slots: list[BenchSlot] = Field(min_length=1, max_length=24)
    vessels: list[StartVessel] = Field(default_factory=list, max_length=16)
    equipment: list[StartEquipment] = Field(default_factory=list, max_length=24)


class ReagentSpec(_IdModel):
    """A stock bottle made available in the tray (unlimited restock is not
    implied — the bottle has a finite declared volume)."""
    vessel_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    species: list[str] = Field(min_length=1, max_length=8)
    description: L10n | None = None
    concentration_label: L10n | None = None


class ProcedureStep(_IdModel):
    title: L10n
    objective: L10n
    accepted_commands: list[str] = Field(min_length=1, max_length=14)
    expected_observations: list[str] = Field(default_factory=list, max_length=8)
    hint_ladder: list[L10n] = Field(min_length=1, max_length=4)
    concept_ids: list[str] = Field(default_factory=list, max_length=6)


class GoalSpec(_IdModel):
    title: L10n
    requires_observations: list[str] = Field(min_length=1, max_length=8)


class PredictionOption(_IdModel):
    label: L10n
    correct: bool = False


class PredictionSpec(_IdModel):
    step_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    question: L10n
    options: list[PredictionOption] = Field(min_length=2, max_length=5)


class VisualTheme(BaseModel, extra="forbid"):
    id: str = Field(default="paper_lab_v1", max_length=48)
    color_tokens: dict[str, L10n] = Field(default_factory=dict, max_length=32)
    color_hex: dict[str, str] = Field(default_factory=dict, max_length=32)
    textures: dict[str, L10n] = Field(default_factory=dict, max_length=8)


class SafetyProfile(BaseModel, extra="forbid"):
    id: str = Field(min_length=1, max_length=64)
    warn_temperature_milli_c: int = Field(default=60_000, ge=0, le=120_000)
    lock_temperature_milli_c: int = Field(default=95_000, ge=0, le=150_000)
    notes: L10n


class ExperimentPack(_IdModel):
    kind: Literal["experiment"] = "experiment"
    schema_version: Literal[1] = 1
    pack_version: str = Field(min_length=1, max_length=24, pattern=r"^\d+\.\d+\.\d+$")
    title: L10n
    summary: L10n
    audience: list[str] = Field(min_length=1, max_length=4)
    source: Literal["project_authored"] = "project_authored"
    model_fidelity: Literal["teaching_semi_quantitative"] = "teaching_semi_quantitative"
    model_scope: L10n
    modes: list[LabMode] = Field(min_length=1, max_length=3)
    equipment: list[str] = Field(min_length=1, max_length=24)
    species: list[str] = Field(min_length=1, max_length=32)
    rule_ids: list[str] = Field(default_factory=list, max_length=16)
    concept_ids: list[str] = Field(default_factory=list, max_length=16)
    starting_state: StartingState
    reagents: list[ReagentSpec] = Field(default_factory=list, max_length=12)
    procedure: list[ProcedureStep] = Field(default_factory=list, max_length=16)
    goals: list[GoalSpec] = Field(default_factory=list, max_length=8)
    predictions: list[PredictionSpec] = Field(default_factory=list, max_length=8)
    safety_profile: SafetyProfile
    visual_theme: VisualTheme = VisualTheme(id="paper_lab_v1")
    ph_model: Literal["strong_binary_v1", "none"] = "none"
    observation_visibility: dict[str, list[str]] = Field(default_factory=dict, max_length=24)


class ContentManifest(BaseModel, extra="forbid"):
    schema_version: Literal[1] = 1
    content_version: str = Field(min_length=1, max_length=24)
    files: dict[str, str] = Field(min_length=1, max_length=256)
