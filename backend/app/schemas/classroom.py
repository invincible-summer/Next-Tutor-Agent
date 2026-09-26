"""课堂模式 Pydantic 契约（plan.md §10/§14）。

三层模型严格分离：
- 内容契约（Brief/Spec）：LLM draft 与已发布 revision 共用，extra="forbid"、
  枚举闭合，长度与数量上限在 schema 层强制（不只在 UI 限制）。
- 服务端实体（Lesson/GenerationJob/ClassroomRun/AudioClip/AssetRecord/ExportJob）：
  owner、hash、状态、路径、答案等服务端字段只由 store/worker 写入；客户端
  可写的请求模型单独定义，绝不复用实体模型直接接收外部输入。
- Public DTO：LessonPublic 等白名单投影，不含答案、rubric、密钥、内部路径。

ID 规则（§10.1）：les_/job_/run_/ast_/src_/seg_/blk_/ckp_ + 24 位十六进制；
页 ID s_ + 12 位。schema_version=1。hash 一律 canonical JSON 的 SHA-256。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """全部课堂模型的基础配置：未知字段拒绝、赋值时同样校验。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# ID 模式（§10.1）
# ---------------------------------------------------------------------------

def _id_pattern(prefix: str, hex_len: int) -> str:
    return rf"^{prefix}_[0-9a-f]{{{hex_len}}}$"


LessonId = Annotated[str, Field(pattern=_id_pattern("les", 24))]
JobId = Annotated[str, Field(pattern=_id_pattern("job", 24))]
RunId = Annotated[str, Field(pattern=_id_pattern("run", 24))]
AssetId = Annotated[str, Field(pattern=_id_pattern("ast", 24))]
SourceId = Annotated[str, Field(pattern=_id_pattern("src", 24))]
SegmentId = Annotated[str, Field(pattern=_id_pattern("seg", 24))]
BlockId = Annotated[str, Field(pattern=_id_pattern("blk", 24))]
CheckpointId = Annotated[str, Field(pattern=_id_pattern("ckp", 24))]
SlideId = Annotated[str, Field(pattern=_id_pattern("s", 12))]
ClaimId = Annotated[str, Field(pattern=r"^claim_[0-9a-f]{1,24}$")]
ObjectiveId = Annotated[str, Field(pattern=r"^objective_[0-9a-z_-]{1,32}$")]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

MAX_DURATION_MINUTES = 30
MAX_SLIDES = 24
MAX_SEGMENTS_AUTHORED = 12
MAX_SEGMENTS_AFTER_SPLIT = 24
MAX_BLOCKS_PER_SLIDE = 16
MAX_CLAIMS_PER_SLIDE = 12
MAX_PUBLISHED_REVISIONS = 20


# ---------------------------------------------------------------------------
# 文本长度（§6.3/§9.2：中文按字、英文按词的混合限额）
# ---------------------------------------------------------------------------

_CJK_RANGES = (
    (0x3000, 0x303F), (0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF), (0xFF00, 0xFFEF), (0x20000, 0x2A6DF),
)
_WORD_RE = re.compile(r"[0-9A-Za-z]+(?:['’\-][0-9A-Za-z]+)*")


def _cjk_len(text: str) -> int:
    return sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES))


def _word_len(text: str) -> int:
    return len(_WORD_RE.findall(text))


def check_mixed_text(text: str, cjk_max: int, word_max: int) -> bool:
    """混合文本限额：word_max×CJK字数 + cjk_max×词数 ≤ cjk_max×word_max。

    纯中文 → 字数 ≤ cjk_max；纯英文 → 词数 ≤ word_max；混合按比例折算。
    """
    if cjk_max <= 0 or word_max <= 0:
        return False
    return _word_len(text) * cjk_max + _cjk_len(text) * word_max <= cjk_max * word_max


# ---------------------------------------------------------------------------
# 枚举（全部闭合；§6/§9/§15/§20）
# ---------------------------------------------------------------------------

class SourcePolicy(str, Enum):
    strict_textbook = "strict_textbook"
    textbook_plus = "textbook_plus"
    web_topic = "web_topic"


class ResearchTimeliness(str, Enum):
    basic = "basic"
    recent_year = "recent_year"
    recent_month = "recent_month"


class ImageDensity(str, Enum):
    none = "none"
    balanced = "balanced"
    rich = "rich"


class CheckpointDensity(str, Enum):
    none = "none"
    light = "light"
    standard = "standard"


class LessonLanguage(str, Enum):
    zh = "zh"
    en = "en"


DurationMinutes = Literal[5, 10, 15, 20, 30]
PagePlan = Literal["auto", "4_6", "6_9", "8_12", "10_15", "14_20"]
PedagogyId = Literal[
    "concept_deep@1", "worked_example@1", "exam_review@1",
    "case_inquiry@1", "frontier_briefing@1",
]
ThemeId = Literal[
    "academic_clear@1", "chalk_focus@1", "visual_story@1",
    "lab_notebook@1", "gentle_beginner@1",
]


class SlideLayout(str, Enum):
    title = "title"
    key_points = "key_points"
    image_explain = "image_explain"
    compare = "compare"
    derivation = "derivation"
    worked_example = "worked_example"
    timeline = "timeline"
    checkpoint = "checkpoint"
    summary = "summary"


class SegmentRole(str, Enum):
    motivation = "motivation"
    explain = "explain"
    derive = "derive"
    example = "example"
    misconception = "misconception"
    transition = "transition"
    summary = "summary"


class TransitionKind(str, Enum):
    auto = "auto"
    manual = "manual"


class CalloutTone(str, Enum):
    note = "note"
    warning = "warning"
    summary = "summary"


class ImageFit(str, Enum):
    contain = "contain"
    cover = "cover"


class SourceKind(str, Enum):
    textbook = "textbook"
    workspace_file = "workspace_file"
    session_file = "session_file"
    web = "web"


class ClaimKind(str, Enum):
    textbook_fact = "textbook_fact"
    web_fact = "web_fact"
    author_explanation = "author_explanation"
    constructed_example = "constructed_example"


class ObjectiveEvidenceStatus(str, Enum):
    supported = "supported"
    partial = "partial"
    uncovered = "uncovered"


class AssetRole(str, Enum):
    scene = "scene"
    object = "object"
    process = "process"
    diagram = "diagram"
    data = "data"
    decoration = "decoration"


class AssetStatus(str, Enum):
    ready = "ready"
    unavailable = "unavailable"


class AssetProvider(str, Enum):
    pexels = "pexels"
    pixabay = "pixabay"
    upload = "upload"


class LessonLifecycle(str, Enum):
    active = "active"
    archiving = "archiving"
    archived = "archived"
    purging = "purging"


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    awaiting_outline = "awaiting_outline"
    needs_input = "needs_input"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class JobPhase(str, Enum):
    resolve_sources = "resolve_sources"
    research = "research"
    outline = "outline"
    visual_assets = "visual_assets"
    author_slides = "author_slides"
    checkpoints = "checkpoints"
    review = "review"
    render = "render"
    publish = "publish"


class RunStatus(str, Enum):
    active = "active"
    paused = "paused"
    completed = "completed"
    ended = "ended"


class AudioKind(str, Enum):
    narration = "narration"
    qa = "qa"
    feedback = "feedback"


class AudioClipState(str, Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class ExportFormat(str, Enum):
    html_zip = "html_zip"
    notes_md = "notes_md"


class ExportState(str, Enum):
    queued = "queued"
    succeeded = "succeeded"
    failed = "failed"
    expired = "expired"


class CheckpointKind(str, Enum):
    reflect = "reflect"
    question = "question"


class VoicePolicy(str, Enum):
    auto = "auto"
    cloud = "cloud"
    local = "local"
    silent = "silent"


class StartMode(str, Enum):
    automatic = "automatic"
    outline_first = "outline_first"


class RunStartMode(str, Enum):
    resume_or_create = "resume_or_create"
    restart = "restart"


class ProgressAction(str, Enum):
    progress = "progress"
    pause = "pause"
    complete = "complete"
    end = "end"


class CheckpointRunState(str, Enum):
    pending = "pending"
    answered = "answered"
    skipped = "skipped"


class LessonListStatus(str, Enum):
    generating = "generating"
    ready = "ready"
    needs_attention = "needs_attention"
    failed = "failed"


class ImageSearchProvider(str, Enum):
    pexels = "pexels"
    pixabay = "pixabay"


class VisualRole(str, Enum):
    scene = "scene"
    object = "object"
    process = "process"
    diagram = "diagram"
    data = "data"
    decoration = "decoration"


class DiagramDirection(str, Enum):
    horizontal = "horizontal"
    vertical = "vertical"


class RefreshScope(str, Enum):
    missing = "missing"
    all = "all"


class Severity(str, Enum):
    blocker = "blocker"
    major = "major"
    minor = "minor"


# ---------------------------------------------------------------------------
# 内容契约：InlineSpan / SlideBlock / DiagramSpec（§10.3）
# ---------------------------------------------------------------------------

MAX_INLINE_TEXT = 600
MAX_LATEX = 2000
MAX_SPOKEN = 240


class SpanText(_StrictModel):
    kind: Literal["text"] = "text"
    text: str = Field(..., min_length=1, max_length=MAX_INLINE_TEXT)


class SpanEmphasis(_StrictModel):
    kind: Literal["emphasis"] = "emphasis"
    text: str = Field(..., min_length=1, max_length=MAX_INLINE_TEXT)


class SpanMath(_StrictModel):
    kind: Literal["math"] = "math"
    latex: str = Field(..., min_length=1, max_length=MAX_LATEX)
    spoken: str = Field(..., min_length=1, max_length=MAX_SPOKEN)


InlineSpan = Annotated[
    Union[SpanText, SpanEmphasis, SpanMath], Field(discriminator="kind")
]


class FlowNode(_StrictModel):
    id: str = Field(..., min_length=1, max_length=40,
                    pattern=r"^[0-9A-Za-z_\-]+$")
    label: str = Field(..., min_length=1, max_length=40)


class FlowEdge(_StrictModel):
    from_: str = Field(..., alias="from", min_length=1, max_length=40,
                       pattern=r"^[0-9A-Za-z_\-]+$")
    to: str = Field(..., min_length=1, max_length=40,
                    pattern=r"^[0-9A-Za-z_\-]+$")
    label: str | None = Field(None, min_length=1, max_length=40)


class FlowDiagram(_StrictModel):
    type: Literal["flow"] = "flow"
    direction: DiagramDirection = DiagramDirection.horizontal
    nodes: list[FlowNode] = Field(..., min_length=1, max_length=10)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=12)
    alt: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> "FlowDiagram":
        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("flow node id 重复")
        known = set(node_ids)
        for edge in self.edges:
            if edge.from_ not in known or edge.to not in known:
                raise ValueError("flow edge 引用不存在的节点")
        return self


class PlotSeries(_StrictModel):
    label: str = Field(..., min_length=1, max_length=40)
    points: list[tuple[float, float]] = Field(..., min_length=1, max_length=100)


class CartesianPlot(_StrictModel):
    type: Literal["cartesian_plot"] = "cartesian_plot"
    x_label: str = Field(..., min_length=1, max_length=40)
    y_label: str = Field(..., min_length=1, max_length=40)
    x_range: tuple[float, float]
    y_range: tuple[float, float]
    series: list[PlotSeries] = Field(..., min_length=1, max_length=3)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=5)
    constructed: bool = False
    alt: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> "CartesianPlot":
        for name, rng in (("x_range", self.x_range), ("y_range", self.y_range)):
            if not rng[0] < rng[1]:
                raise ValueError(f"{name} 必须严格递增")
            for v in rng:
                if abs(v) > 1e9:
                    raise ValueError(f"{name} 绝对值超限")
        for s in self.series:
            for x, y in s.points:
                if not (self.x_range[0] <= x <= self.x_range[1]):
                    raise ValueError("x 超出 x_range，拒绝无提示裁切")
                if not (self.y_range[0] <= y <= self.y_range[1]):
                    raise ValueError("y 超出 y_range，拒绝无提示裁切")
        return self


class ForceBody(_StrictModel):
    id: str = Field(..., min_length=1, max_length=40,
                    pattern=r"^[0-9A-Za-z_\-]+$")
    shape: Literal["point", "box"]
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    label: str = Field(..., min_length=1, max_length=40)


class ForceArrow(_StrictModel):
    body_id: str = Field(..., min_length=1, max_length=40,
                         pattern=r"^[0-9A-Za-z_\-]+$")
    dx: float = Field(..., ge=-1.0, le=1.0)
    dy: float = Field(..., ge=-1.0, le=1.0)
    label: str = Field(..., min_length=1, max_length=40)


class ForceDiagram(_StrictModel):
    type: Literal["force_diagram"] = "force_diagram"
    bodies: list[ForceBody] = Field(..., min_length=1, max_length=4)
    arrows: list[ForceArrow] = Field(default_factory=list, max_length=8)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=5)
    constructed: bool = False
    alt: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> "ForceDiagram":
        known = {b.id for b in self.bodies}
        for arrow in self.arrows:
            if arrow.body_id not in known:
                raise ValueError("force arrow 引用不存在的 body")
        return self


DiagramSpec = Annotated[
    Union[FlowDiagram, CartesianPlot, ForceDiagram], Field(discriminator="type")
]


class ParagraphBlock(_StrictModel):
    kind: Literal["paragraph"] = "paragraph"
    id: BlockId
    spans: list[InlineSpan] = Field(..., min_length=1, max_length=8)


class BulletsBlock(_StrictModel):
    kind: Literal["bullets"] = "bullets"
    id: BlockId
    items: list[list[InlineSpan]] = Field(..., min_length=1, max_length=5)

    @model_validator(mode="after")
    def _check(self) -> "BulletsBlock":
        for item in self.items:
            if not (1 <= len(item) <= 8):
                raise ValueError("bullets 条目 span 数非法")
            for span in item:
                text = getattr(span, "text", None)
                if text is not None and not check_mixed_text(text, 40, 22):
                    raise ValueError("bullets 条目超长（≤40 中文字或 ≤22 英文词）")
        return self


class FormulaBlock(_StrictModel):
    kind: Literal["formula"] = "formula"
    id: BlockId
    latex: str = Field(..., min_length=1, max_length=MAX_LATEX)
    spoken: str = Field(..., min_length=1, max_length=MAX_SPOKEN)
    label: str | None = Field(None, min_length=1, max_length=40)


class ImageBlock(_StrictModel):
    kind: Literal["image"] = "image"
    id: BlockId
    asset_id: AssetId
    alt: str = Field(..., min_length=1, max_length=500)
    caption: str = Field(..., min_length=1, max_length=300)
    fit: ImageFit = ImageFit.contain


class TableBlock(_StrictModel):
    kind: Literal["table"] = "table"
    id: BlockId
    headers: list[str] = Field(..., min_length=1, max_length=5)
    rows: list[list[str]] = Field(..., max_length=5)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=5)
    constructed: bool = False

    @model_validator(mode="after")
    def _check(self) -> "TableBlock":
        for h in self.headers:
            if not (1 <= len(h) <= 60):
                raise ValueError("表头长度非法")
        for row in self.rows:
            if len(row) != len(self.headers):
                raise ValueError("表格行列不匹配")
            for cell in row:
                if not (1 <= len(cell) <= 120):
                    raise ValueError("表格单元格长度非法")
        return self


class StepItem(_StrictModel):
    label: str = Field(..., min_length=1, max_length=40)
    spans: list[InlineSpan] = Field(..., min_length=1, max_length=6)


class StepsBlock(_StrictModel):
    kind: Literal["steps"] = "steps"
    id: BlockId
    steps: list[StepItem] = Field(..., min_length=1, max_length=5)


class DiagramBlock(_StrictModel):
    kind: Literal["diagram"] = "diagram"
    id: BlockId
    diagram: DiagramSpec


class CheckpointBlock(_StrictModel):
    kind: Literal["checkpoint"] = "checkpoint"
    id: BlockId
    checkpoint_id: CheckpointId


class CalloutBlock(_StrictModel):
    kind: Literal["callout"] = "callout"
    id: BlockId
    tone: CalloutTone = CalloutTone.note
    spans: list[InlineSpan] = Field(..., min_length=1, max_length=8)


SlideBlock = Annotated[
    Union[
        ParagraphBlock, BulletsBlock, FormulaBlock, ImageBlock, TableBlock,
        StepsBlock, DiagramBlock, CheckpointBlock, CalloutBlock,
    ],
    Field(discriminator="kind"),
]


class TeachingClaim(_StrictModel):
    claim_id: ClaimId
    text: str = Field(..., min_length=1, max_length=600)
    kind: ClaimKind
    block_ids: list[BlockId] = Field(default_factory=list, max_length=16)
    segment_ids: list[SegmentId] = Field(default_factory=list, max_length=24)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=8)
    as_of: date | None = None


class NarrationSegment(_StrictModel):
    segment_id: SegmentId
    role: SegmentRole
    display_text: str = Field(..., min_length=1, max_length=480)
    spoken_text: str = Field(..., min_length=1, max_length=MAX_SPOKEN)
    show_block_ids: list[BlockId] = Field(default_factory=list, max_length=16)
    focus_block_ids: list[BlockId] = Field(default_factory=list, max_length=16)
    pause_after_ms: int = Field(0, ge=0, le=3000)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=8)
    estimated_ms: int = Field(0, ge=0, le=600_000)


class SlideSpec(_StrictModel):
    slide_id: SlideId
    order: int = Field(..., ge=1, le=MAX_SLIDES)
    title: str = Field(..., min_length=1)
    learning_objective_ids: list[ObjectiveId] = Field(
        default_factory=list, max_length=8)
    layout: SlideLayout
    blocks: list[SlideBlock] = Field(..., min_length=1, max_length=MAX_BLOCKS_PER_SLIDE)
    segments: list[NarrationSegment] = Field(
        ..., min_length=1, max_length=MAX_SEGMENTS_AFTER_SPLIT)
    claims: list[TeachingClaim] = Field(default_factory=list,
                                        max_length=MAX_CLAIMS_PER_SLIDE)
    source_ids: list[SourceId] = Field(default_factory=list, max_length=12)
    transition: TransitionKind = TransitionKind.auto
    estimated_seconds: int = Field(0, ge=0, le=600)

    @model_validator(mode="after")
    def _check(self) -> "SlideSpec":
        if not check_mixed_text(self.title, 36, 12):
            raise ValueError("页面标题超长（≤36 中文字或 ≤12 英文词）")
        return self


# ---------------------------------------------------------------------------
# 目标 / 术语 / 检查点 / 大纲
# ---------------------------------------------------------------------------

MAX_EXCERPT_CHARS = 4000


class Objective(_StrictModel):
    objective_id: ObjectiveId
    text: str = Field(..., min_length=1, max_length=200)
    evidence_status: ObjectiveEvidenceStatus = ObjectiveEvidenceStatus.uncovered


class GlossaryEntry(_StrictModel):
    term: str = Field(..., min_length=1, max_length=60)
    definition: str = Field(..., min_length=1, max_length=300)
    spoken_hint: str = Field("", max_length=120)


class CheckpointTemplate(_StrictModel):
    """服务端私有模板；verified_question_template 绝不出现在 public 投影。"""

    checkpoint_id: CheckpointId
    slide_id: SlideId
    kind: CheckpointKind
    prompt: str = Field(..., min_length=1, max_length=500)
    verified_question_template: dict[str, Any] | None = None
    reflection_seconds: int | None = Field(None, ge=15, le=60)
    optional: bool = True

    @model_validator(mode="after")
    def _check(self) -> "CheckpointTemplate":
        if self.kind == CheckpointKind.reflect and self.verified_question_template:
            raise ValueError("reflect 检查点不能携带题目模板")
        if self.kind == CheckpointKind.question and not self.optional \
                and self.verified_question_template is None:
            raise ValueError("必需的 question 检查点必须有题目模板")
        return self


class OutlinePage(_StrictModel):
    """大纲页计划（awaiting_outline 审核对象与 author_slides 输入）。"""

    order: int = Field(..., ge=1, le=MAX_SLIDES)
    title: str = Field(..., min_length=1, max_length=120)
    layout: SlideLayout
    objective_ids: list[ObjectiveId] = Field(default_factory=list, max_length=8)
    budget_seconds: int = Field(0, ge=0, le=600)
    key_points: list[str] = Field(default_factory=list, max_length=6)
    visual_intent: "VisualIntent | None" = None


class OutlinePlan(_StrictModel):
    objectives: list[Objective] = Field(..., min_length=1, max_length=12)
    pages: list[OutlinePage] = Field(..., min_length=1, max_length=MAX_SLIDES)
    glossary: list[GlossaryEntry] = Field(default_factory=list, max_length=40)
    scope_note: str = Field("", max_length=600)
    uncovered_note: str = Field("", max_length=600)
    total_budget_seconds: int = Field(0, ge=0, le=3600)


class VisualIntent(_StrictModel):
    role: VisualRole
    purpose: str = Field(..., min_length=1, max_length=300)
    required_objects: list[str] = Field(default_factory=list, max_length=6)
    exclude: list[str] = Field(default_factory=list, max_length=6)
    orientation: Literal["landscape", "portrait", "square"] | None = None
    query_terms: list[str] = Field(default_factory=list, max_length=5)
    alt: str = Field("", max_length=500)


# ---------------------------------------------------------------------------
# 来源记录（§7/§10.2）
# ---------------------------------------------------------------------------

class FileLocator(_StrictModel):
    kind: Literal["file"] = "file"
    namespace: str = Field(..., min_length=1, max_length=64)
    file_id: str = Field(..., min_length=1, max_length=128)
    chunk_ids: list[str] = Field(..., min_length=1, max_length=32)
    page: int | None = Field(None, ge=1)
    printed_page: str | None = Field(None, min_length=1, max_length=20)
    section_path: list[str] = Field(default_factory=list, max_length=8)
    content_hash: str = Field(..., min_length=8, max_length=128)


class WebLocator(_StrictModel):
    kind: Literal["web"] = "web"
    url: str = Field(..., min_length=8, max_length=2048,
                     pattern=r"^https://[^\s]+$")
    canonical_url: str = Field(..., min_length=8, max_length=2048,
                               pattern=r"^https://[^\s]+$")
    domain: str = Field(..., min_length=1, max_length=200)
    publisher: str | None = Field(None, min_length=1, max_length=120)
    retrieved_at: datetime


SourceLocator = Annotated[Union[FileLocator, WebLocator], Field(discriminator="kind")]


class SourceRecord(_StrictModel):
    source_id: SourceId
    kind: SourceKind
    title: str = Field(..., min_length=1, max_length=200)
    locator: SourceLocator
    excerpt: str = Field("", max_length=MAX_EXCERPT_CHARS)
    excerpt_hash: Sha256Hex
    retrieved_at: datetime
    published_at: date | None = None
    as_of: date | None = None
    verification: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "SourceRecord":
        if self.kind == SourceKind.web and not isinstance(self.locator, WebLocator):
            raise ValueError("web 来源必须使用 web locator")
        if self.kind != SourceKind.web and not isinstance(self.locator, FileLocator):
            raise ValueError("文件来源必须使用 file locator")
        return self


class AssetProvenance(_StrictModel):
    provider: AssetProvider
    provider_asset_id: str = Field("", max_length=128)
    source_url: str = Field("", max_length=2048)
    creator: str = Field("", max_length=200)
    creator_url: str = Field("", max_length=2048)
    license_url: str = Field("", max_length=2048)
    fetched_at: datetime


class AssetRecord(_StrictModel):
    asset_id: AssetId
    sha256: Sha256Hex
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    width: int = Field(..., ge=1, le=20000)
    height: int = Field(..., ge=1, le=20000)
    provenance: AssetProvenance
    alt: str = Field(..., min_length=1, max_length=500)
    caption: str = Field("", max_length=300)
    role: AssetRole
    bytes: int = Field(..., ge=0, le=1_572_864)
    status: AssetStatus = AssetStatus.ready


# ---------------------------------------------------------------------------
# LessonBrief（§4.1/§10.2）
# ---------------------------------------------------------------------------

class ChapterSelection(_StrictModel):
    section_path: list[str] = Field(default_factory=list, max_length=8)
    title: str = Field(..., min_length=1, max_length=120)


class SourceFileSelection(_StrictModel):
    file_id: str = Field(..., min_length=1, max_length=128)
    chapters: list[ChapterSelection] = Field(default_factory=list, max_length=12)


class ExtraSessionSelection(_StrictModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    attachment_file_ids: list[str] = Field(default_factory=list, max_length=10)


class SourceSelection(_StrictModel):
    files: list[SourceFileSelection] = Field(default_factory=list, max_length=8)
    extra_sessions: list[ExtraSessionSelection] = Field(
        default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _check(self) -> "SourceSelection":
        total_chapters = sum(len(f.chapters) for f in self.files)
        if total_chapters > 12:
            raise ValueError("章节选择总数超过 12")
        return self


class ResearchBrief(_StrictModel):
    enabled: bool = True
    timeliness: ResearchTimeliness = ResearchTimeliness.basic


class VoicePreferences(_StrictModel):
    policy: VoicePolicy = VoicePolicy.auto
    voice_id: str = Field("", max_length=64)
    allow_local_fallback: bool = True
    playback_speed: float = Field(0.9, ge=0.5, le=1.5)


class LessonBrief(_StrictModel):
    topic: str = Field(..., min_length=2, max_length=120)
    goals: list[str] = Field(default_factory=list, max_length=5)
    source_selection: SourceSelection = Field(default_factory=SourceSelection)
    source_policy: SourcePolicy = SourcePolicy.textbook_plus
    research: ResearchBrief = Field(default_factory=ResearchBrief)
    duration_minutes: DurationMinutes = 15
    page_plan: PagePlan = "auto"
    language: LessonLanguage = LessonLanguage.zh
    grade: str = Field("", max_length=16)
    pedagogy_id: PedagogyId = "concept_deep@1"
    theme_id: ThemeId = "academic_clear@1"
    image_density: ImageDensity = ImageDensity.balanced
    checkpoint_density: CheckpointDensity = CheckpointDensity.standard
    voice_preferences: VoicePreferences = Field(default_factory=VoicePreferences)
    custom_requirements: str = Field("", max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> "LessonBrief":
        for g in self.goals:
            if not (1 <= len(g) <= 120):
                raise ValueError("学习目标每项 ≤120 字")
        if self.source_policy == SourcePolicy.strict_textbook \
                and not self.source_selection.files:
            raise ValueError("strict_textbook 必须选择教材/本区资料")
        return self


# ---------------------------------------------------------------------------
# 服务端实体（§10.2；owner/状态/hash 仅服务端写入）
# ---------------------------------------------------------------------------

class ReviewIssue(_StrictModel):
    code: str = Field(..., min_length=1, max_length=64)
    severity: Severity = Severity.minor
    slide_id: SlideId | None = None
    field_path: str | None = Field(None, max_length=120)
    reason: str = Field(..., min_length=1, max_length=600)


class ReviewReport(_StrictModel):
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=64)
    summary: str = Field("", max_length=600)


class LessonRevision(_StrictModel):
    revision: int = Field(..., ge=1)
    schema_version: Literal[1] = 1
    brief: LessonBrief
    source_snapshot: list[SourceRecord] = Field(
        default_factory=list, max_length=64)
    slides: list[SlideSpec] = Field(..., min_length=1, max_length=MAX_SLIDES)
    checkpoint_templates: list[CheckpointTemplate] = Field(
        default_factory=list, max_length=3)
    objectives: list[Objective] = Field(..., min_length=1, max_length=12)
    glossary: list[GlossaryEntry] = Field(default_factory=list, max_length=40)
    assets: list[AssetRecord] = Field(default_factory=list, max_length=12)
    renderer_version: str = Field("", max_length=64)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    review_report: ReviewReport | None = None
    content_hash: Sha256Hex
    created_at: datetime


class Lesson(_StrictModel):
    lesson_id: LessonId
    owner_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=120)
    created_at: datetime
    updated_at: datetime
    latest_ready_revision: int | None = None
    published_revisions: list[int] = Field(
        default_factory=list, max_length=MAX_PUBLISHED_REVISIONS)
    next_revision: int = Field(1, ge=1)
    latest_job_id: JobId | None = None
    lifecycle: LessonLifecycle = LessonLifecycle.active


class JobBudget(_StrictModel):
    llm_calls_used: int = 0
    llm_input_tokens_used: int = 0
    llm_output_tokens_used: int = 0
    search_calls_used: int = 0
    extract_calls_used: int = 0
    image_searches_used: int = 0
    image_downloads_used: int = 0
    tts_chars_used: int = 0
    active_seconds_used: float = 0.0
    unknown_external_outcomes: int = 0


class GenerationJob(_StrictModel):
    job_id: JobId
    owner_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=128)
    lesson_id: LessonId
    base_revision: int | None = None
    target_revision: int = Field(..., ge=1)
    state: JobState = JobState.queued
    phase: JobPhase | None = None
    state_revision: int = Field(1, ge=1)
    brief_hash: Sha256Hex
    stage_inputs: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    budget: JobBudget = Field(default_factory=JobBudget)
    attempts: int = Field(1, ge=1)
    recovery_count: int = Field(0, ge=0)
    cancel_requested: bool = False
    last_error: str | None = Field(None, max_length=2000)
    next_retry_at: datetime | None = None
    epoch: int = Field(1, ge=1)
    start_mode: StartMode = StartMode.automatic
    operation: "RevisionOperation | None" = None
    created_at: datetime
    updated_at: datetime


class Cursor(_StrictModel):
    slide_id: SlideId
    segment_id: SegmentId
    chunk_index: int = Field(0, ge=0, le=63)
    offset_ms: int = Field(0, ge=0, le=3_600_000)
    last_completed_segment_id: SegmentId | None = None


class LeaseInfo(_StrictModel):
    client_id: str = Field(..., min_length=8, max_length=64)
    lease_epoch: int = Field(..., ge=1)
    expires_at: datetime
    heartbeat_at: datetime


class RunCheckpointRef(_StrictModel):
    checkpoint_id: CheckpointId
    slide_id: SlideId
    kind: CheckpointKind
    question_ref: str | None = Field(None, max_length=128)
    state: CheckpointRunState = CheckpointRunState.pending
    assistance_events: list[str] = Field(default_factory=list, max_length=16)


class AudioProfile(_StrictModel):
    policy: VoicePolicy = VoicePolicy.auto
    provider: str = Field("", max_length=32)
    voice_id: str = Field("", max_length=64)
    language: LessonLanguage = LessonLanguage.zh
    allow_local_fallback: bool = True
    playback_speed: float = Field(0.9, ge=0.5, le=1.5)
    version: int = Field(1, ge=1)


class RunAnnotation(_StrictModel):
    annotation_id: str = Field(..., min_length=1, max_length=64)
    slide_id: SlideId
    segment_id: SegmentId | None = None
    user_text: str = Field("", max_length=4000)
    auto_excerpt: str = Field("", max_length=2000)
    created_at: datetime


class ClassroomRun(_StrictModel):
    run_id: RunId
    owner_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=128)
    lesson_id: LessonId
    lesson_revision: int = Field(..., ge=1)
    content_hash: Sha256Hex
    status: RunStatus = RunStatus.active
    state_revision: int = Field(1, ge=1)
    cursor: Cursor
    resume_anchor: Cursor | None = None
    checkpoint_refs: list[RunCheckpointRef] = Field(default_factory=list,
                                                    max_length=3)
    audio_profile: AudioProfile = Field(default_factory=AudioProfile)
    audio_profile_version_note: str = Field("", max_length=200)
    qa_session_id: str | None = Field(None, min_length=1, max_length=128)
    lease: LeaseInfo | None = None
    visited_slides: list[SlideId] = Field(default_factory=list, max_length=MAX_SLIDES)
    listened_segments: list[SegmentId] = Field(default_factory=list, max_length=576)
    skipped_slides: list[SlideId] = Field(default_factory=list, max_length=MAX_SLIDES)
    audio_refs: dict[str, str] = Field(default_factory=dict)
    # 阶段 F（§11.5）：云端失败后该 run 后续音色锁本地，只提示一次；
    # run 级云合成字符计数（TTS_CHARS_PER_RUN 预算）。
    tts_local_locked: bool = False
    tts_fallback_notified: bool = False
    tts_chars_used: int = Field(0, ge=0)
    # 进度事件去重（§12.3：最近 PROGRESS_DEDUP_EVENTS 个 client_event_id）
    progress_event_ids: list[str] = Field(default_factory=list,
                                          max_length=256)
    annotations: list[RunAnnotation] = Field(default_factory=list, max_length=100)
    completed_kind: Literal["listened", "browsed", ""] = ""
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> "ClassroomRun":
        if len(self.audio_refs) > 1000:
            raise ValueError("audio_refs 超过上限 1000")
        return self


class AudioClip(_StrictModel):
    clip_id: str = Field(..., min_length=8, max_length=64)
    owner_id: str = Field(..., min_length=1, max_length=128)
    lesson_id: LessonId
    revision: int = Field(..., ge=1)
    kind: AudioKind
    content_ref: dict[str, str] = Field(default_factory=dict)
    segment_id: SegmentId | None = None
    chunk_index: int = Field(0, ge=0, le=63)
    synthesis_key: Sha256Hex
    provider: str = Field("", max_length=32)
    voice_id: str = Field("", max_length=64)
    language: LessonLanguage = LessonLanguage.zh
    sample_rate: int = Field(0, ge=0)
    sample_count: int = Field(0, ge=0)
    bytes: int = Field(0, ge=0)
    sha256: Sha256Hex | None = None
    state: AudioClipState = AudioClipState.pending
    error: str | None = Field(None, max_length=500)


class ExportJob(_StrictModel):
    job_id: JobId
    owner_id: str = Field(..., min_length=1, max_length=128)
    lesson_id: LessonId
    revision: int = Field(..., ge=1)
    format: ExportFormat
    state: ExportState = ExportState.queued
    artifact_id: str | None = Field(None, min_length=1, max_length=128)
    created_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# 修订操作（§14.1 五种 operation 判别联合）
# ---------------------------------------------------------------------------

class ReplaceSlideChange(_StrictModel):
    op: Literal["replace_slide"] = "replace_slide"
    slide_id: SlideId
    slide: SlideSpec


class DeleteSlideChange(_StrictModel):
    op: Literal["delete_slide"] = "delete_slide"
    slide_id: SlideId


class ReorderSlidesChange(_StrictModel):
    op: Literal["reorder_slides"] = "reorder_slides"
    page_ids: list[SlideId] = Field(..., min_length=1, max_length=MAX_SLIDES)


EditChange = Annotated[
    Union[ReplaceSlideChange, DeleteSlideChange, ReorderSlidesChange],
    Field(discriminator="op"),
]


class EditContentOperation(_StrictModel):
    op: Literal["edit_content"] = "edit_content"
    changes: list[EditChange] = Field(..., min_length=1, max_length=50)


class ChangeThemeOperation(_StrictModel):
    op: Literal["change_theme"] = "change_theme"
    theme_id: ThemeId


class RegenerateSlideOperation(_StrictModel):
    op: Literal["regenerate_slide"] = "regenerate_slide"
    slide_id: SlideId
    instruction: str = Field("", max_length=500)


class ReplaceImageOperation(_StrictModel):
    op: Literal["replace_image"] = "replace_image"
    slide_id: SlideId
    block_id: BlockId
    candidate_id: str | None = Field(None, min_length=1, max_length=128)
    asset_id: AssetId | None = None


class RefreshResearchOperation(_StrictModel):
    op: Literal["refresh_research"] = "refresh_research"
    scope: RefreshScope = RefreshScope.missing


RevisionOperation = Annotated[
    Union[
        EditContentOperation, ChangeThemeOperation, RegenerateSlideOperation,
        ReplaceImageOperation, RefreshResearchOperation,
    ],
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# 错误协议（§14.3）
# ---------------------------------------------------------------------------

class ClassroomErrorCode(str, Enum):
    classroom_disabled = "classroom_disabled"
    source_not_ready = "source_not_ready"
    source_not_found = "source_not_found"
    source_changed = "source_changed"
    research_unavailable = "research_unavailable"
    freshness_unverified = "freshness_unverified"
    image_unavailable = "image_unavailable"
    content_invalid = "content_invalid"
    layout_overflow = "layout_overflow"
    renderer_unavailable = "renderer_unavailable"
    budget_exceeded = "budget_exceeded"
    quota_exceeded = "quota_exceeded"
    generation_failed = "generation_failed"
    job_cancelled = "job_cancelled"
    revision_conflict = "revision_conflict"
    lease_conflict = "lease_conflict"
    scope_changed = "scope_changed"
    audio_busy = "audio_busy"
    tts_unavailable = "tts_unavailable"
    voice_unavailable = "voice_unavailable"
    export_expired = "export_expired"
    storage_unavailable = "storage_unavailable"
    damaged = "damaged"
    idempotency_conflict = "idempotency_conflict"


class ErrorBody(_StrictModel):
    code: ClassroomErrorCode
    message: str = Field(..., min_length=1, max_length=500)
    retryable: bool = False
    phase: JobPhase | None = None
    request_id: str = Field("", max_length=64)


class ErrorResponse(_StrictModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# Public 投影（§10.5）
# ---------------------------------------------------------------------------

class SourcePublic(_StrictModel):
    source_id: SourceId
    kind: SourceKind
    title: str
    status: Literal["available", "revoked"] = "available"
    namespace: str = ""
    section_path: list[str] = Field(default_factory=list)
    page: int | None = None
    printed_page: str | None = None
    url: str | None = None
    domain: str | None = None
    publisher: str | None = None
    published_at: date | None = None
    as_of: date | None = None
    retrieved_at: datetime


class AssetPublic(_StrictModel):
    asset_id: AssetId
    mime: str
    width: int
    height: int
    alt: str
    caption: str
    role: AssetRole
    status: AssetStatus
    provider: AssetProvider
    creator: str = ""
    source_url: str = ""
    license_url: str = ""


class CheckpointPublic(_StrictModel):
    checkpoint_id: CheckpointId
    slide_id: SlideId
    kind: CheckpointKind
    prompt: str
    reflection_seconds: int | None = None
    optional: bool = True
    question: dict[str, Any] | None = None
    run_state: CheckpointRunState | None = None


class BriefPublic(_StrictModel):
    topic: str
    goals: list[str]
    source_policy: SourcePolicy
    duration_minutes: int
    language: LessonLanguage
    grade: str
    pedagogy_id: str
    theme_id: str
    image_density: ImageDensity
    checkpoint_density: CheckpointDensity
    research_enabled: bool
    research_timeliness: ResearchTimeliness
    custom_requirements: str = ""


class RevisionPublic(_StrictModel):
    revision: int
    schema_version: int
    brief: BriefPublic
    slides: list[SlideSpec]
    objectives: list[Objective]
    glossary: list[GlossaryEntry]
    source_records: list[SourcePublic]
    assets: list[AssetPublic]
    checkpoints: list[CheckpointPublic]
    renderer_version: str
    content_hash: str
    created_at: datetime
    estimated_total_seconds: int = 0


class JobProgress(_StrictModel):
    completed_slides: int = 0
    total_slides: int = 0


class JobPublic(_StrictModel):
    job_id: JobId
    lesson_id: LessonId
    state: JobState
    phase: JobPhase | None
    state_revision: int
    progress: JobProgress
    warnings: list[str] = Field(default_factory=list, max_length=32)
    last_error: str | None = None
    cancel_requested: bool = False
    start_mode: StartMode
    created_at: datetime
    updated_at: datetime
    next_actions: list[str] = Field(default_factory=list, max_length=8)


class LessonBriefProgress(_StrictModel):
    """未完成课程的 brief/进度（GET L 未 ready 时返回，不伪造 slides）。"""

    brief: BriefPublic
    progress: JobProgress
    phase: JobPhase | None
    state: JobState


class RunPublic(_StrictModel):
    run_id: RunId
    lesson_id: LessonId
    lesson_revision: int
    status: RunStatus
    state_revision: int
    cursor: Cursor
    resume_anchor: Cursor | None = None
    audio_profile: AudioProfile
    qa_session_id: str | None = None
    visited_slide_count: int = 0
    completed_kind: str = ""
    created_at: datetime
    updated_at: datetime


class LessonListStatusExtra(_StrictModel):
    chapter_label: str = ""
    slide_count: int = 0
    active_job_count: int = 0
    last_run: RunPublic | None = None


class LessonSummaryPublic(_StrictModel):
    lesson_id: LessonId
    workspace_id: str
    title: str
    status: LessonListStatus
    latest_ready_revision: int | None
    latest_job: JobPublic | None = None
    brief: BriefPublic | None = None
    extra: LessonListStatusExtra = Field(default_factory=LessonListStatusExtra)
    updated_at: datetime


class LessonDetailPublic(_StrictModel):
    lesson_id: LessonId
    workspace_id: str
    title: str
    lifecycle: LessonLifecycle
    latest_ready_revision: int | None
    published_revisions: list[int]
    latest_job: JobPublic | None = None
    revision: RevisionPublic | None = None
    pending: LessonBriefProgress | None = None
    recent_run: RunPublic | None = None


class RevisionListItem(_StrictModel):
    revision: int
    created_at: datetime
    source_changed: bool = False
    available: bool = True


class RevisionListResponse(_StrictModel):
    items: list[RevisionListItem]
    total: int
    page: int
    page_size: int


class LessonListResponse(_StrictModel):
    items: list[LessonSummaryPublic]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# capabilities / templates（§14.1）
# ---------------------------------------------------------------------------

class RendererCapability(_StrictModel):
    available: bool = False
    reason: str = ""
    renderer_version: str = ""


class ServiceCapability(_StrictModel):
    configured: bool = False
    available: bool = False
    reason: str = ""


class VoiceInfo(_StrictModel):
    voice_id: str
    language: str
    display_name: str


class TtsCapability(_StrictModel):
    configured: bool = False
    available: bool = False
    reason: str = ""
    policy: VoicePolicy = VoicePolicy.auto
    local_enabled: bool = False
    voices: list[VoiceInfo] = Field(default_factory=list, max_length=32)


class ClassroomLimits(_StrictModel):
    max_pages: int
    max_duration_minutes: int
    max_published_revisions: int
    max_image_searches: int
    max_images: int
    daily_tts_chars: int
    generation_daily_limit: int
    audio_cache_mb: int


class ClassroomCapabilities(_StrictModel):
    enabled: bool = False
    allow_guest: bool = False
    renderer: RendererCapability
    research: ServiceCapability
    images: ServiceCapability
    tts: TtsCapability
    limits: ClassroomLimits


class PedagogyTemplateInfo(_StrictModel):
    pedagogy_id: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    version: str


class ThemeTemplateInfo(_StrictModel):
    theme_id: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    version: str


class TemplateDefaults(_StrictModel):
    duration_minutes: int = 15
    language: LessonLanguage = LessonLanguage.zh
    pedagogy_id: str = "concept_deep@1"
    theme_id: str = "academic_clear@1"
    image_density: ImageDensity = ImageDensity.balanced
    checkpoint_density: CheckpointDensity = CheckpointDensity.standard
    source_policy: SourcePolicy = SourcePolicy.textbook_plus


class ClassroomTemplates(_StrictModel):
    pedagogy: list[PedagogyTemplateInfo]
    themes: list[ThemeTemplateInfo]
    defaults: TemplateDefaults


# ---------------------------------------------------------------------------
# API 请求/响应（§14）
# ---------------------------------------------------------------------------

class CreateLessonRequest(_StrictModel):
    brief: LessonBrief
    start_mode: StartMode = StartMode.automatic


class CreateLessonResponse(_StrictModel):
    lesson_id: LessonId
    job_id: JobId
    target_revision: int
    status_url: str
    events_url: str


class CreateRevisionRequest(_StrictModel):
    """POST L/revisions：五种修订 operation 之一（§14.1）。"""

    base_revision: int = Field(..., ge=1)
    operation: RevisionOperation


class CreateRevisionResponse(_StrictModel):
    lesson_id: LessonId
    job_id: JobId
    revision: int
    status_url: str
    events_url: str


class JobSnapshotEvent(_StrictModel):
    """SSE snapshot 事件体（§14.4）。"""

    job_id: JobId
    state_revision: int
    state: JobState
    phase: JobPhase | None
    completed_slides: int = 0
    total_slides: int = 0
    warnings: list[str] = Field(default_factory=list)


class CancelJobRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)


class JobPreviewResponse(_StrictModel):
    """GET J/preview：只读草稿（§4.2/§14.1）。

    slides 为已生成页的结构化 DTO（不含答案；题模板绝不进入本投影）；
    html 是可安全编译完成时的草稿整课 HTML（含「草稿」水印），否则 None
    由前端展示结构化预览。
    """

    state: JobState
    phase: JobPhase | None
    slides: list[SlideSpec] = Field(default_factory=list)
    outline: OutlinePlan | None = None
    html: str | None = Field(None, max_length=4_000_000)


class RetryJobRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)


class OutlinePatchRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)
    outline: OutlinePlan


class BriefPatchRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)
    brief_patch: LessonBrief


class ContinueJobRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)


class RevisionOpRequest(_StrictModel):
    base_revision: int = Field(..., ge=1)
    operation: RevisionOperation


class ImageSearchRequest(_StrictModel):
    lesson_id: LessonId
    visual_intent: VisualIntent
    provider: ImageSearchProvider | None = None


class ImageCandidate(_StrictModel):
    candidate_id: str
    provider: AssetProvider
    provider_asset_id: str
    thumbnail_url: str = ""
    width: int = 0
    height: int = 0
    creator: str = ""
    source_url: str = ""
    license_url: str = ""


class ImageSearchResponse(_StrictModel):
    candidates: list[ImageCandidate] = Field(default_factory=list, max_length=8)


class AssetUploadResponse(_StrictModel):
    asset: AssetPublic


class CreateRunRequest(_StrictModel):
    lesson_revision: int | None = Field(None, ge=1)
    mode: RunStartMode = RunStartMode.resume_or_create
    voice_preferences: VoicePreferences | None = None


class RunCreateResponse(_StrictModel):
    run_id: RunId
    lesson_revision: int
    status: RunStatus
    resumed: bool = False


class LeaseAcquireRequest(_StrictModel):
    client_id: str = Field(..., min_length=8, max_length=64)
    takeover: bool = False


class LeaseRenewRequest(_StrictModel):
    client_id: str = Field(..., min_length=8, max_length=64)
    lease_epoch: int = Field(..., ge=1)


class LeaseResponse(_StrictModel):
    lease_epoch: int
    expires_at: datetime


class ProgressRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)
    client_event_id: str = Field(..., min_length=8, max_length=128)
    client_seq: int = Field(..., ge=1)
    lease_epoch: int = Field(..., ge=1)
    action: ProgressAction
    cursor: Cursor | None = None
    played_segment_ids: list[SegmentId] = Field(default_factory=list, max_length=48)
    skipped_slide_ids: list[SlideId] = Field(default_factory=list, max_length=24)


class ProgressResponse(_StrictModel):
    state_revision: int


class AudioRequest(_StrictModel):
    segment_ids: list[SegmentId] = Field(..., min_length=1, max_length=3)
    lease_epoch: int = Field(..., ge=1)


class ClipStatus(_StrictModel):
    clip_id: str
    state: AudioClipState
    status_url: str
    content_url: str | None = None


class AudioResponse(_StrictModel):
    clips: list[ClipStatus] = Field(default_factory=list, max_length=3)


class AudioProfileRequest(_StrictModel):
    expected_state_revision: int = Field(..., ge=1)
    voice_preferences: VoicePreferences


class QaSessionResponse(_StrictModel):
    session_id: str


class QaAudioRequest(_StrictModel):
    reply_message_id: str = Field(..., min_length=1, max_length=128)
    lease_epoch: int = Field(..., ge=1)


class CheckpointSubmitRequest(_StrictModel):
    question_ref: str = Field(..., min_length=1, max_length=128)
    student_answer: str = Field(..., min_length=1, max_length=20000)
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    expected_scope_revision: str | None = Field(None, max_length=64)


class RunNoteRequest(_StrictModel):
    slide_id: SlideId
    segment_id: SegmentId | None = None
    user_text: str = Field("", max_length=4000)


class RunNoteResponse(_StrictModel):
    annotation_id: str


class SaveNoteRequest(_StrictModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    include_user_notes: bool = True


class SaveNoteResponse(_StrictModel):
    note_id: str


class ClassroomSummaryCheckpoint(_StrictModel):
    checkpoint_id: CheckpointId
    slide_id: SlideId
    kind: CheckpointKind
    state: CheckpointRunState


class ClassroomSummaryQuestion(_StrictModel):
    question_ref: str
    state: str
    pending_evaluation: bool = False


class ClassroomSummary(_StrictModel):
    run_id: RunId
    lesson_id: LessonId
    lesson_revision: int
    visited_slide_count: int
    listened_segment_count: int
    question_count: int
    checkpoints: list[ClassroomSummaryCheckpoint]
    questions: list[ClassroomSummaryQuestion]
    annotations: list[RunAnnotation]
    completed_kind: str = ""
    ask_follow_up_available: bool = True


class ExportCreateRequest(_StrictModel):
    revision: int = Field(..., ge=1)
    format: ExportFormat


class ExportCreateResponse(_StrictModel):
    export_id: JobId
    status_url: str
    content_url: str | None = None


class VoicePreviewRequest(_StrictModel):
    language: LessonLanguage = LessonLanguage.zh
    voice_preferences: VoicePreferences


class VoicePreviewResponse(_StrictModel):
    clip_id: str
    content_url: str


class OperationStatus(_StrictModel):
    operation_id: str
    kind: str
    state: Literal["pending", "running", "succeeded", "failed"] = "pending"
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# 供类型生成脚本消费的公开模型清单（确定性顺序）
# ---------------------------------------------------------------------------

PUBLIC_TYPE_UNIONS: dict[str, tuple[str, ...]] = {
    "InlineSpan": ("SpanText", "SpanEmphasis", "SpanMath"),
    "DiagramSpec": ("FlowDiagram", "CartesianPlot", "ForceDiagram"),
    "SlideBlock": (
        "ParagraphBlock", "BulletsBlock", "FormulaBlock", "ImageBlock",
        "TableBlock", "StepsBlock", "DiagramBlock", "CheckpointBlock",
        "CalloutBlock",
    ),
    "SourceLocator": ("FileLocator", "WebLocator"),
    "EditChange": ("ReplaceSlideChange", "DeleteSlideChange", "ReorderSlidesChange"),
    "RevisionOperation": (
        "EditContentOperation", "ChangeThemeOperation",
        "RegenerateSlideOperation", "ReplaceImageOperation",
        "RefreshResearchOperation",
    ),
}

PUBLIC_TYPE_MODELS: list[str] = [
    "ClassroomErrorCode",
    "SourcePolicy", "ResearchTimeliness", "ImageDensity", "CheckpointDensity",
    "LessonLanguage", "SlideLayout", "SegmentRole", "TransitionKind",
    "CalloutTone", "ImageFit", "SourceKind", "ClaimKind",
    "ObjectiveEvidenceStatus", "AssetRole", "AssetStatus", "AssetProvider",
    "LessonLifecycle", "JobState", "JobPhase", "RunStatus", "AudioKind",
    "AudioClipState", "ExportFormat", "ExportState", "CheckpointKind",
    "VoicePolicy", "StartMode", "RunStartMode", "ProgressAction",
    "CheckpointRunState", "LessonListStatus", "ImageSearchProvider",
    "VisualRole", "DiagramDirection", "RefreshScope", "Severity",
    "SpanText", "SpanEmphasis", "SpanMath",
    "FlowNode", "FlowEdge", "FlowDiagram", "PlotSeries",
    "CartesianPlot", "ForceBody", "ForceArrow", "ForceDiagram",
    "ParagraphBlock", "BulletsBlock", "FormulaBlock", "ImageBlock",
    "TableBlock", "StepItem", "StepsBlock", "DiagramBlock",
    "CheckpointBlock", "CalloutBlock",
    "TeachingClaim", "NarrationSegment", "SlideSpec",
    "Objective", "GlossaryEntry", "CheckpointTemplate", "OutlinePage",
    "OutlinePlan", "VisualIntent",
    "FileLocator", "WebLocator", "SourceRecord", "AssetProvenance",
    "AssetRecord",
    "ChapterSelection", "SourceFileSelection", "ExtraSessionSelection",
    "SourceSelection", "ResearchBrief", "VoicePreferences", "LessonBrief",
    "ReviewIssue", "ReviewReport", "LessonRevision", "Lesson", "JobBudget",
    "GenerationJob", "JobPreviewResponse", "Cursor", "LeaseInfo", "RunCheckpointRef",
    "AudioProfile", "RunAnnotation", "ClassroomRun", "AudioClip", "ExportJob",
    "ReplaceSlideChange", "DeleteSlideChange", "ReorderSlidesChange",
    "EditContentOperation", "ChangeThemeOperation",
    "RegenerateSlideOperation", "ReplaceImageOperation",
    "RefreshResearchOperation",
    "ErrorBody", "ErrorResponse",
    "SourcePublic", "AssetPublic", "CheckpointPublic", "BriefPublic",
    "RevisionPublic", "JobProgress", "JobPublic", "LessonBriefProgress",
    "RunPublic", "LessonListStatusExtra", "LessonSummaryPublic",
    "LessonDetailPublic", "RevisionListItem", "RevisionListResponse",
    "LessonListResponse",
    "RendererCapability", "ServiceCapability", "VoiceInfo", "TtsCapability",
    "ClassroomLimits", "ClassroomCapabilities",
    "PedagogyTemplateInfo", "ThemeTemplateInfo", "TemplateDefaults",
    "ClassroomTemplates",
    "CreateLessonRequest", "CreateLessonResponse",
    "CreateRevisionRequest", "CreateRevisionResponse", "JobSnapshotEvent",
    "CancelJobRequest", "RetryJobRequest", "OutlinePatchRequest",
    "BriefPatchRequest", "ContinueJobRequest", "RevisionOpRequest",
    "ImageSearchRequest", "ImageCandidate", "ImageSearchResponse",
    "AssetUploadResponse", "CreateRunRequest", "RunCreateResponse",
    "LeaseAcquireRequest", "LeaseRenewRequest", "LeaseResponse",
    "ProgressRequest", "ProgressResponse", "AudioRequest", "ClipStatus",
    "AudioResponse", "AudioProfileRequest", "QaSessionResponse",
    "QaAudioRequest", "CheckpointSubmitRequest", "RunNoteRequest",
    "RunNoteResponse", "SaveNoteRequest", "SaveNoteResponse",
    "ClassroomSummaryCheckpoint", "ClassroomSummaryQuestion",
    "ClassroomSummary", "ExportCreateRequest", "ExportCreateResponse",
    "VoicePreviewRequest", "VoicePreviewResponse", "OperationStatus",
]
