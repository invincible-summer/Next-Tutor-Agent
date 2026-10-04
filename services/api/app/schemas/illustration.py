"""Public illustration DTOs for web/mobile clients.

This module is the generation source for the shared TypeScript contracts
(`scripts/contracts/generate_types.py` -> `packages/contracts`). It covers the
two public API families that consume the shared illustration engine:

- tool assistant scenario sessions/turns/jobs (`api/v1/tool_illustration.py`);
- quiz illustration jobs and frozen artifacts
  (`api/v1/assessment_illustration.py`, `api/v1/illustration_jobs.py`).

Private authoring material — `DiagramSourceV2/V3`, `authoring_gold`, review
bodies and provider diagnostics — must never appear in these models.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IllustrationMode = Literal["v1", "v2", "v3"]

ToolJobStatus = Literal["queued", "running", "ready", "failed"]
ToolJobStage = Literal[
    "preparing", "retrieving", "composing", "rendering", "reviewing", "ready", "failed",
]
TurnStatus = Literal["queued", "running", "ready", "failed"]


class QuestionIllustration(BaseModel):
    """Sanitized public SVG artifact; identical for web and mobile consumers."""
    kind: Literal["svg"]
    schema_version: int
    sanitizer_version: int
    svg: str
    alt: str = ""
    caption: str = ""
    width: int = 0
    height: int = 0
    content_hash: str = ""


class PublicIllustrationFailure(BaseModel):
    """Closed failure projection: public code + retry flag only."""
    code: str
    retryable: bool = False


class SelectedMaterialRef(BaseModel):
    """Material references clients may submit: opaque asset id + version only."""
    asset_id: str
    version: int = Field(ge=1)


# --- Tool assistant: scenario sessions -------------------------------------


class IllustrationSessionSummary(BaseModel):
    session_id: str
    title: str
    revision: int = Field(ge=0)
    active_job_id: str | None = None
    created_at: float
    updated_at: float


class IllustrationSessionList(BaseModel):
    items: list[IllustrationSessionSummary]
    total: int = Field(ge=0)


class ScenarioTurn(BaseModel):
    turn_id: str
    message: str
    mode: IllustrationMode
    selected_materials: list[SelectedMaterialRef]
    job_id: str
    status: TurnStatus
    revision: int | None = None
    created_at: float
    request_id: str | None = None
    source_revision: int | None = None


class ScenarioRevision(BaseModel):
    revision: int = Field(ge=1)
    artifact_id: str
    mode: IllustrationMode
    illustration: QuestionIllustration | None = None
    created_at: float


class IllustrationSession(BaseModel):
    session_id: str
    title: str
    revision: int = Field(ge=0)
    active_job_id: str | None = None
    created_at: float
    updated_at: float
    turns: list[ScenarioTurn]
    revisions: list[ScenarioRevision]


class DeletedSessionAck(BaseModel):
    deleted: bool


class ToolIllustrationJob(BaseModel):
    job_id: str
    session_id: str
    turn_id: str
    mode: IllustrationMode
    status: ToolJobStatus
    stage: ToolJobStage
    base_revision: int = Field(ge=0)
    revision: int | None = None
    artifact_id: str | None = None
    selected_materials: list[SelectedMaterialRef]
    failure: PublicIllustrationFailure | None = None
    created_at: float
    updated_at: float
    source_revision: int | None = None
    progress: int = Field(ge=0, le=100)
    illustration: QuestionIllustration | None = None


# --- Quiz illustration (assessment) ----------------------------------------


class QuizIllustrationProgress(BaseModel):
    stage: str
    percent: int = Field(ge=0, le=100)


class QuizIllustrationJob(BaseModel):
    """V2/V3 job projection from `app/illustration/events.public_job`."""
    status: str
    job_id: str
    question_id: str
    question_revision: int = Field(ge=1)
    visual_role: str
    artifact_id: str | None = None
    illustration: QuestionIllustration | None = None
    failure: PublicIllustrationFailure | None = None
    code: str = ""
    retryable: bool = False
    progress: QuizIllustrationProgress


class QuizIllustrationStatus(BaseModel):
    """V1 direct-enrich envelope (legacy route on `/questions/{id}/illustration`).

    The same legacy route proxies a running V2/V3 job by returning the
    `QuizIllustrationJob` envelope instead; the route declares both models as
    a union so each response keeps its exact public field set.
    """
    status: str
    question_id: str
    question_revision: int = Field(ge=1)
    illustration: QuestionIllustration | None = None
    code: str = ""
    retryable: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)


PUBLIC_TYPE_MODELS: list[str] = [
    # shared artifact projections
    "QuestionIllustration",
    "PublicIllustrationFailure",
    "SelectedMaterialRef",
    # tool assistant
    "IllustrationSessionSummary",
    "IllustrationSessionList",
    "ScenarioTurn",
    "ScenarioRevision",
    "IllustrationSession",
    "DeletedSessionAck",
    "ToolIllustrationJob",
    # quiz illustration
    "QuizIllustrationProgress",
    "QuizIllustrationJob",
    "QuizIllustrationStatus",
]

PUBLIC_TYPE_UNIONS: dict[str, tuple[str, ...]] = {}
