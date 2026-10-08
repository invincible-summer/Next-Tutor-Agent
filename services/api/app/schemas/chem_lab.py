"""Public chem-lab DTOs for web/mobile clients.

This module is the generation source for the shared TypeScript contracts
(`scripts/contracts/generate_types.py` -> `packages/contracts`). It covers
the virtual chemistry bench REST family (`api/v1/tool_chem_lab.py`).

Private material — rule internals beyond their public note, moderation
annotations, internal storage paths, owner ids of other users — must never
appear in these models.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChemLabMode = Literal["guided", "explore", "self_check"]
ChemLabPhase = Literal["setup", "ready", "running", "safety_locked", "completed"]
ChemLabCommandKind = Literal[
    "pick_up", "place", "aspirate", "dispense", "pour", "heat", "stir",
    "wait", "measure", "connect", "filter", "wash", "dispose", "checkpoint",
    "move", "release",
]
ChemLabGuidanceLevel = Literal[
    "on_track", "try_again", "hint", "explain", "safety", "complete",
]


# --- Catalog -----------------------------------------------------------------


class ChemLabExperimentSummary(BaseModel):
    id: str
    pack_version: str
    pack_hash: str
    title: dict[str, str]
    summary: dict[str, str]
    audience: list[str]
    modes: list[str]
    model_fidelity: str
    model_scope: dict[str, str]


class ChemLabCatalog(BaseModel):
    experiments: list[ChemLabExperimentSummary]
    capabilities: dict[str, bool] = Field(default_factory=dict)


class ChemLabExperimentDetail(BaseModel):
    """Public experiment projection: display info, procedure titles, reagents,
    safety — rule internals and prediction answers are already stripped."""
    id: str
    pack_version: str
    pack_hash: str
    title: dict[str, str]
    summary: dict[str, str]
    audience: list[str]
    modes: list[str]
    model_fidelity: str
    model_scope: dict[str, str]
    language: str = ""
    goals: list[dict[str, Any]] = Field(default_factory=list)
    procedure: list[dict[str, Any]] = Field(default_factory=list)
    reagents: list[dict[str, Any]] = Field(default_factory=list)
    predictions: list[dict[str, Any]] = Field(default_factory=list)
    starting_state: dict[str, Any] = Field(default_factory=dict)
    safety_profile: dict[str, Any] = Field(default_factory=dict)
    observation_visibility: dict[str, list[str]] = Field(default_factory=dict)
    visual_theme: dict[str, Any] = Field(default_factory=dict)
    equipment_defs: dict[str, Any] = Field(default_factory=dict)
    species_defs: dict[str, Any] = Field(default_factory=dict)
    concept_defs: dict[str, Any] = Field(default_factory=dict)


class ChemLabEnginePack(BaseModel):
    """Client-side engine pack for the Worker mirror (browser prediction).

    Same versioned pack the server interprets — prediction answers are
    stripped; the server re-validates and re-computes every command, so this
    projection is never an authority.
    """
    pack_hash: str
    pack: dict[str, Any]


# --- Commands ------------------------------------------------------------------


class LabCommand(BaseModel):
    """Closed command union: one kind + its optional params.

    Unknown fields are rejected outright so a client can never smuggle
    owner/session overrides through the command body.
    """
    model_config = ConfigDict(extra="forbid")

    kind: ChemLabCommandKind
    object_id: str | None = None
    slot_id: str | None = None
    source_id: str | None = None
    target_id: str | None = None
    instrument_id: str | None = None
    vessel_id: str | None = None
    device_id: str | None = None
    apparatus_id: str | None = None
    amount_uL: int | None = None
    rate: str | None = None
    power_permille: int | None = None
    speed_permille: int | None = None
    duration_ms: int | None = None
    quantity: str | None = None
    label: str | None = None


class ChemLabCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1, max_length=96)
    client_seq: int = Field(ge=0)
    base_revision: int = Field(ge=0)
    pack_hash: str = Field(min_length=1, max_length=128)
    command: LabCommand


# --- Public projections ---------------------------------------------------------


class ChemLabEvent(BaseModel):
    seq: int = Field(ge=0)
    kind: str
    command_id: str = ""
    sim_time_ms: int = Field(ge=0)
    vessel_id: str = ""
    rule_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ChemLabObservation(BaseModel):
    seq: int = Field(ge=0)
    key: str
    vessel_id: str = ""
    rule_id: str = ""
    sim_time_ms: int = Field(ge=0)
    concept_ids: list[str] = Field(default_factory=list)


class ChemLabGuidance(BaseModel):
    level: ChemLabGuidanceLevel
    text: str
    evidence_event_seq: int = Field(ge=0)
    concept_ids: list[str] = Field(default_factory=list)
    # 引擎公开形状里这个键承载的是 model_fidelity 标签（teaching_* 字符串）。
    model_scope: str = ""
    step_id: str | None = None
    hint_index: int | None = None


class ChemLabVesselFrame(BaseModel):
    fill_ratio_permille: int = Field(ge=0, le=1000)
    liquid_color_token: str = "clear"
    liquid_label: str = ""
    opacity_permille: int = Field(ge=0, le=1000)
    turbidity_permille: int = Field(ge=0, le=1000)
    precipitate: dict[str, Any] | None = None
    bubbles: dict[str, Any] | None = None
    steam_permille: int = Field(ge=0, le=1000)
    temperature_band: str = "room"


class ChemLabRenderFrame(BaseModel):
    vessels: dict[str, ChemLabVesselFrame] = Field(default_factory=dict)
    instruments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    highlights: list[dict[str, Any]] = Field(default_factory=list)


class ChemLabGoalStatus(BaseModel):
    id: str
    status: Literal["pending", "met"]


class ChemLabCheckpoint(BaseModel):
    checkpoint_id: str
    label: str
    revision: int = Field(ge=0)
    seq: int = Field(ge=0)
    sim_time_ms: int = Field(ge=0)
    created_at: float = 0.0


# --- Sessions ---------------------------------------------------------------------


class ChemLabCreateSession(BaseModel):
    """Client pins experiment/version/hash/mode; owner comes only from auth."""
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=96)
    pack_version: str | None = None
    mode: ChemLabMode = "guided"
    language: str = Field(default="zh", min_length=1, max_length=16)
    session_seed: int = Field(default=0, ge=0)


class ChemLabSessionSummary(BaseModel):
    session_id: str
    experiment_id: str
    pack_version: str
    pack_hash: str
    mode: ChemLabMode
    phase: ChemLabPhase
    revision: int = Field(ge=0)
    finished: bool = False
    title: dict[str, str] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class ChemLabSessionList(BaseModel):
    items: list[ChemLabSessionSummary]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class ChemLabSessionSnapshot(BaseModel):
    session_id: str
    experiment_id: str
    pack_version: str
    pack_hash: str
    mode: ChemLabMode
    language: str
    phase: ChemLabPhase
    revision: int = Field(ge=0)
    seq: int = Field(ge=0)
    sim_time_ms: int = Field(ge=0)
    state_hash: str
    goals: list[ChemLabGoalStatus] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    observations: list[ChemLabObservation] = Field(default_factory=list)
    guidance: ChemLabGuidance | None = None
    render_frame: ChemLabRenderFrame | None = None
    recent_events: list[ChemLabEvent] = Field(default_factory=list)
    checkpoints: list[ChemLabCheckpoint] = Field(default_factory=list)
    sync_required: bool = False
    finished: bool = False
    branch: dict[str, Any] | None = None
    # 引擎全量状态（仅会话属主可见）：Worker reset(snapshot) 的种子。
    # 内容均为公开投影的派生（物质账本/可见现象），不含规则原文之外的信息。
    engine_state: dict[str, Any] | None = None
    created_at: float
    updated_at: float


class ChemLabCommandAck(BaseModel):
    accepted: bool
    command_id: str
    revision: int = Field(ge=0)
    seq_from: int = Field(ge=0)
    seq_to: int = Field(ge=0)
    state_hash: str
    error_code: str = ""
    events: list[ChemLabEvent] = Field(default_factory=list)
    observations: list[ChemLabObservation] = Field(default_factory=list)
    guidance: ChemLabGuidance | None = None
    render_frame: ChemLabRenderFrame | None = None
    snapshot: ChemLabSessionSnapshot | None = None


class ChemLabEventPage(BaseModel):
    items: list[ChemLabEvent]
    next_after_seq: int = Field(ge=0)
    truncated: bool = False


class ChemLabCheckpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)


class ChemLabForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str | None = None
    at_revision: int | None = Field(default=None, ge=0)


class ChemLabForkResult(BaseModel):
    session: ChemLabSessionSnapshot
    source_session_id: str
    fork_revision: int = Field(ge=0)


class ChemLabResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str | None = None


class ChemLabResultCard(BaseModel):
    session_id: str
    experiment_id: str
    pack_version: str
    mode: ChemLabMode
    goals: list[ChemLabGoalStatus] = Field(default_factory=list)
    observations: list[ChemLabObservation] = Field(default_factory=list)
    concepts: list[dict[str, Any]] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    sim_time_ms: int = Field(ge=0)
    revision: int = Field(ge=0)
    state_hash: str
    model_fidelity: str = ""
    model_scope: dict[str, str] = Field(default_factory=dict)
    finished_at: float


class ChemLabDeletedAck(BaseModel):
    deleted: bool


# --- Historical revision view (read-only) ----------------------------------------


class ChemLabSceneVessel(BaseModel):
    """Minimal vessel facts the scene needs to place and animate objects."""
    kind: str
    slot: str
    volume_uL: int = Field(default=0, ge=0)
    capacity_uL: int = Field(default=0, ge=0)
    temperature_milli_c: int = 0
    mix_permille: int = Field(default=0, ge=0, le=1000)
    heat: dict[str, Any] | None = None


class ChemLabSceneEquipment(BaseModel):
    kind: str
    slot: str
    load_volume_uL: int = Field(default=0, ge=0)
    connected: dict[str, Any] | None = None
    reading: dict[str, Any] | None = None


class ChemLabSceneState(BaseModel):
    vessels: dict[str, ChemLabSceneVessel] = Field(default_factory=dict)
    equipment: dict[str, ChemLabSceneEquipment] = Field(default_factory=dict)
    held: str | None = None


class ChemLabRevisionView(BaseModel):
    """Read-only projection of one historical revision: replayed from the
    stored script without touching the session (no writes, no branch, no
    updated_at bump), filtered with the session mode's visibility rules —
    exactly what the live snapshot would hide stays hidden here."""
    session_id: str
    experiment_id: str
    pack_version: str
    pack_hash: str
    mode: ChemLabMode
    language: str
    read_only: Literal[True] = True
    revision: int = Field(ge=0)
    tip_revision: int = Field(ge=0)
    sim_time_ms: int = Field(ge=0)
    state_hash: str
    phase: ChemLabPhase
    render_frame: ChemLabRenderFrame
    scene_state: ChemLabSceneState
    goals: list[ChemLabGoalStatus] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    observations: list[ChemLabObservation] = Field(default_factory=list)
    recent_events: list[ChemLabEvent] = Field(default_factory=list)
    guidance: ChemLabGuidance | None = None


PUBLIC_TYPE_MODELS: list[str] = [
    # catalog
    "ChemLabExperimentSummary",
    "ChemLabCatalog",
    "ChemLabExperimentDetail",
    "ChemLabEnginePack",
    # commands
    "LabCommand",
    "ChemLabCommandRequest",
    # projections
    "ChemLabEvent",
    "ChemLabObservation",
    "ChemLabGuidance",
    "ChemLabVesselFrame",
    "ChemLabRenderFrame",
    "ChemLabGoalStatus",
    "ChemLabCheckpoint",
    # sessions
    "ChemLabCreateSession",
    "ChemLabSessionSummary",
    "ChemLabSessionList",
    "ChemLabSessionSnapshot",
    "ChemLabCommandAck",
    "ChemLabEventPage",
    "ChemLabCheckpointCreate",
    "ChemLabForkRequest",
    "ChemLabForkResult",
    "ChemLabResetRequest",
    "ChemLabResultCard",
    "ChemLabDeletedAck",
    # historical revision view
    "ChemLabSceneVessel",
    "ChemLabSceneEquipment",
    "ChemLabSceneState",
    "ChemLabRevisionView",
]

PUBLIC_TYPE_UNIONS: dict[str, tuple[str, ...]] = {}
