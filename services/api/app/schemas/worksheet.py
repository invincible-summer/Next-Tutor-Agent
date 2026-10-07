"""Public worksheet DTOs shared by Web and Mobile."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

QuestionType = Literal["multiple_choice", "fill_blank", "short_answer"]
WorksheetStatus = Literal["draft", "ready"]
ExportVariant = Literal["student", "teacher"]


class WorksheetImage(BaseModel):
    asset_id: str
    url: str
    data_url: str | None = None
    alt: str = ""
    mime_type: str = "image/png"


class WorksheetQuestion(BaseModel):
    id: str
    number: int = Field(ge=1)
    type: QuestionType
    stem: str
    options: dict[str, str] = Field(default_factory=dict)
    answer: str = ""
    explanation: str = ""
    score: int = Field(default=5, ge=0, le=100)
    difficulty: int = Field(default=3, ge=1, le=5)
    knowledge_points: list[str] = Field(default_factory=list, max_length=8)
    image: WorksheetImage | None = None
    version: int = Field(default=1, ge=1)
    updated_at: float = 0


class WorksheetDocument(BaseModel):
    id: str
    title: str
    # 学习区由创建/保存流程强制选择；旧草稿没有该字段时保持兼容，
    # 重新保存或生成前必须补齐。
    learning_area: str = ""
    workspace_id: str = ""
    subject: str = ""
    grade: str = ""
    unit: str = ""
    duration_minutes: int = Field(default=45, ge=1, le=600)
    total_score: int = Field(default=0, ge=0, le=10000)
    instructions: str = ""
    guidance_prompt: str = ""
    goal: str = ""
    knowledge_points: list[str] = Field(default_factory=list, max_length=12)
    reference_textbook: bool = False
    status: WorksheetStatus = "draft"
    questions: list[WorksheetQuestion] = Field(default_factory=list)
    etag: str
    created_at: float
    updated_at: float


class WorksheetSummary(BaseModel):
    id: str
    title: str
    subject: str = ""
    question_count: int = Field(ge=0)
    total_score: int = Field(ge=0)
    status: WorksheetStatus
    updated_at: float


class WorksheetList(BaseModel):
    items: list[WorksheetSummary]
    total: int = Field(ge=0)


class WorksheetCreateRequest(BaseModel):
    title: str = Field(default="未命名试卷", min_length=1, max_length=120)
    learning_area: str = Field(default="", max_length=120)
    workspace_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(default="", max_length=60)
    grade: str = Field(default="", max_length=60)
    unit: str = Field(default="", max_length=120)
    duration_minutes: int = Field(default=45, ge=1, le=600)
    instructions: str = Field(default="", max_length=2000)
    guidance_prompt: str = Field(default="", max_length=4000)
    goal: str = Field(default="", max_length=4000)
    knowledge_points: list[str] = Field(default_factory=list, max_length=12)
    reference_textbook: bool = False


class WorksheetPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    learning_area: str | None = Field(default=None, max_length=120)
    workspace_id: str | None = Field(default=None, max_length=128)
    subject: str | None = Field(default=None, max_length=60)
    grade: str | None = Field(default=None, max_length=60)
    unit: str | None = Field(default=None, max_length=120)
    duration_minutes: int | None = Field(default=None, ge=1, le=600)
    instructions: str | None = Field(default=None, max_length=2000)
    guidance_prompt: str | None = Field(default=None, max_length=4000)
    goal: str | None = Field(default=None, max_length=4000)
    knowledge_points: list[str] | None = Field(default=None, max_length=12)
    reference_textbook: bool | None = None
    etag: str = Field(min_length=1, max_length=128)


class WorksheetGenerateRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=50)
    question_type: QuestionType = "multiple_choice"
    type_distribution: dict[str, int] = Field(default_factory=dict)
    difficulty: int = Field(default=3, ge=1, le=5)
    score: int = Field(default=5, ge=0, le=100)
    knowledge_points: list[str] = Field(default_factory=list, max_length=12)
    guidance_prompt: str = Field(default="", max_length=4000)
    idempotency_key: str = Field(default="", max_length=128)


class WorksheetQuestionPatchRequest(BaseModel):
    stem: str | None = Field(default=None, max_length=12000)
    options: dict[str, str] | None = None
    answer: str | None = Field(default=None, max_length=6000)
    explanation: str | None = Field(default=None, max_length=8000)
    score: int | None = Field(default=None, ge=0, le=100)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    knowledge_points: list[str] | None = Field(default=None, max_length=8)
    etag: str = Field(min_length=1, max_length=128)


class WorksheetRefineRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    etag: str = Field(min_length=1, max_length=128)


class WorksheetImageAttachRequest(BaseModel):
    data_url: str = Field(min_length=20, max_length=16_000_000)
    alt: str = Field(default="", max_length=160)
    # Optional for older clients; new editors send the document ETag so an
    # image cannot silently overwrite a concurrent question edit.
    etag: str | None = Field(default=None, min_length=1, max_length=128)


class WorksheetGenerateResponse(BaseModel):
    worksheet: WorksheetDocument
    generated: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)


class WorksheetExport(BaseModel):
    variant: ExportVariant
    format: Literal["markdown", "html"]
    content: str


PUBLIC_TYPE_MODELS = [
    "WorksheetImage", "WorksheetQuestion", "WorksheetDocument", "WorksheetSummary",
    "WorksheetList", "WorksheetCreateRequest", "WorksheetPatchRequest",
    "WorksheetGenerateRequest", "WorksheetQuestionPatchRequest", "WorksheetRefineRequest",
    "WorksheetImageAttachRequest", "WorksheetGenerateResponse", "WorksheetExport",
]
PUBLIC_TYPE_UNIONS = {}
