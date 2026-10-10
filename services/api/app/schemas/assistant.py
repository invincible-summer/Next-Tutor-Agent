"""站内学习助手 Pydantic 契约（P1 基础协议）。

分层纪律：
- 本文件是助手全部对外契约的唯一事实源；前端类型由
  scripts/contracts/generate_types.py 生成，禁止手工漂移（GAP-09）。
- 全部模型 extra="forbid"；判别联合用 Literal kind/type 判别，
  不接受任意字典或字符串函数名。
- 助手会话与教学链路隔离：这些模型不进入学习证据账本。

ID 规则（§19.1）：
  astc_ 会话 / astt_ 轮 / astm_ 消息 / asta_ 动作 / astd_ 草稿 /
  astx_ 命令 / asts_ 来源引用，后接 32 位小写十六进制（UUID4 hex）。
  client_message_id / invocation_id 为客户端 crypto.randomUUID()。
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """助手契约基类：未知字段拒绝、赋值时同样校验。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# 基础常量与 ID 模式（§19.1）
# ---------------------------------------------------------------------------

def _id_pattern(prefix: str) -> str:
    return rf"^{prefix}_[0-9a-f]{{32}}$"


ConversationId = Annotated[str, Field(pattern=_id_pattern("astc"))]
TurnId = Annotated[str, Field(pattern=_id_pattern("astt"))]
MessageId = Annotated[str, Field(pattern=_id_pattern("astm"))]
ActionId = Annotated[str, Field(pattern=_id_pattern("asta"))]
DraftId = Annotated[str, Field(pattern=_id_pattern("astd"))]
CommandId = Annotated[str, Field(pattern=_id_pattern("astx"))]
SourceRefId = Annotated[str, Field(pattern=_id_pattern("asts"))]

ClientUUID = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]

MAX_TEXT_CHARS = 4000            # 用户输入与 guide 问题上限（Unicode 字符）
MAX_PAGE_CONTEXT_BYTES = 8192    # PageContext JSON 序列化上限
MAX_ENTITY_ID_CHARS = 128        # PageContext entity id 上限
MAX_SELECTION_CHARS = 1200       # 显式引用选区文本上限
ACTION_DEFAULT_TTL_SECONDS = 600  # action 默认 10 分钟有效
DRAFT_DEFAULT_TTL_SECONDS = 1800  # 草稿默认 30 分钟有效
ACK_TIMEOUT_SECONDS = 15          # 页面 ack 超时

ASSISTANT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# 路由与导航目标（§8.2）
# ---------------------------------------------------------------------------

class AssistantRouteId(str, Enum):
    HOME = "home"
    CHAT = "chat"
    COURSE = "course"
    NOTES = "notes"
    DASHBOARD = "dashboard"
    KNOWLEDGE = "knowledge"
    ORCHESTRATION = "orchestration"
    ASSESSMENT = "assessment"
    TOOLS = "tools"
    TOOLS_ILLUSTRATION = "tools_illustration"
    TOOLS_LAB = "tools_lab"
    MEMORY = "memory"
    RESOURCES_FILES = "resources_files"
    RESOURCES_TEXTBOOKS = "resources_textbooks"
    ARCHIVE = "archive"
    PROFILE = "profile"
    ACCOUNT = "account"
    SETTINGS = "settings"
    INSIGHTS = "insights"
    DOCS = "docs"
    ADMIN = "admin"
    LOGIN = "login"
    REGISTER = "register"


EntityId = Annotated[str, Field(min_length=1, max_length=MAX_ENTITY_ID_CHARS)]


class ModuleTarget(_StrictModel):
    kind: Literal["module"] = "module"
    route_id: AssistantRouteId


class WorkspaceChatTarget(_StrictModel):
    kind: Literal["workspace_chat"] = "workspace_chat"
    workspace_id: EntityId


class ChatSessionTarget(_StrictModel):
    kind: Literal["chat_session"] = "chat_session"
    session_id: EntityId


class WorkspaceCoursesTarget(_StrictModel):
    kind: Literal["workspace_courses"] = "workspace_courses"
    workspace_id: EntityId


class LessonTarget(_StrictModel):
    kind: Literal["lesson"] = "lesson"
    workspace_id: EntityId
    lesson_id: EntityId
    # §8.3：默认课程介绍页；edit 打开课件编辑视图（?edit=1）。
    view: Literal["overview", "edit"] = "overview"
    # 固定预览版本（?revision=N）；缺省用课程当前发布版本。
    revision: int | None = Field(default=None, ge=1)


class ClassroomRunTarget(_StrictModel):
    kind: Literal["classroom_run"] = "classroom_run"
    workspace_id: EntityId
    lesson_id: EntityId
    run_id: EntityId


class NoteTarget(_StrictModel):
    kind: Literal["note"] = "note"
    note_id: EntityId


class LearningArchiveTab(str, Enum):
    CHANGES = "changes"
    SESSIONS = "sessions"
    CONCEPTS = "concepts"


class LearningArchiveTarget(_StrictModel):
    kind: Literal["learning_archive"] = "learning_archive"
    workspace_id: EntityId
    tab: LearningArchiveTab = LearningArchiveTab.CHANGES
    concept_key: str | None = Field(default=None, max_length=256)
    source_id: str | None = Field(default=None, max_length=256)


class ConceptTarget(_StrictModel):
    kind: Literal["concept"] = "concept"
    concept_id: EntityId
    workspace_id: EntityId | None = None


class TaskTarget(_StrictModel):
    kind: Literal["task"] = "task"
    task_id: EntityId


class TeachingProposalTarget(_StrictModel):
    kind: Literal["teaching_proposal"] = "teaching_proposal"
    proposal_id: EntityId


# --- §20.3 完整 NavigationTarget 扩展（B01） -------------------------------

class DashboardRange(str, Enum):
    LAST_7D = "7d"
    LAST_30D = "30d"
    THIS_WEEK = "this_week"


class DashboardViewTarget(_StrictModel):
    kind: Literal["dashboard_view"] = "dashboard_view"
    workspace_id: EntityId | None = None
    range: DashboardRange = DashboardRange.LAST_7D


class ChatMessageTarget(_StrictModel):
    kind: Literal["chat_message"] = "chat_message"
    session_id: EntityId
    message_id: EntityId


class FileTarget(_StrictModel):
    kind: Literal["file"] = "file"
    file_id: EntityId
    folder_id: EntityId | None = None
    page: int | None = Field(default=None, ge=1)


class FileFolderTarget(_StrictModel):
    kind: Literal["file_folder"] = "file_folder"
    folder_id: EntityId


class TextbookTarget(_StrictModel):
    kind: Literal["textbook"] = "textbook"
    textbook_id: EntityId
    volume_id: EntityId | None = None
    chapter_id: EntityId | None = None


class AssessmentViewKind(str, Enum):
    START = "start"
    ACTIVE = "active"
    REPORT = "report"
    ERRORS = "errors"
    RECENT = "recent"


class AssessmentViewTarget(_StrictModel):
    kind: Literal["assessment_view"] = "assessment_view"
    view: AssessmentViewKind = AssessmentViewKind.START
    assessment_id: EntityId | None = None
    source_id: EntityId | None = None
    question_id: EntityId | None = None

    @model_validator(mode="after")
    def _view_params_match(self) -> "AssessmentViewTarget":
        # §20.3：report 需要 assessment_id；errors 可带 source_id；其他视图
        # 不携带定位参数（active 他人 ID 切换属运行时归属校验，不在 schema）。
        if self.view is AssessmentViewKind.REPORT and not self.assessment_id:
            raise ValueError("assessment_view/report 需要 assessment_id")
        if self.source_id and self.view is not AssessmentViewKind.ERRORS:
            raise ValueError("source_id 只能与 assessment_view/errors 组合")
        if self.question_id and self.view not in (
                AssessmentViewKind.ERRORS, AssessmentViewKind.REPORT):
            raise ValueError("question_id 只能与 errors/report 组合")
        return self


class GoalTarget(_StrictModel):
    kind: Literal["goal"] = "goal"
    goal_id: EntityId


class WeekTaskTarget(_StrictModel):
    kind: Literal["week_task"] = "week_task"
    week_index: int = Field(ge=0)
    week_task_id: EntityId
    subtask_id: EntityId | None = None


class MemoryPreferencesTarget(_StrictModel):
    kind: Literal["memory_preferences"] = "memory_preferences"


class NoteRevisionTarget(_StrictModel):
    kind: Literal["note_revision"] = "note_revision"
    note_id: EntityId
    revision: int = Field(ge=1)


# §20.3：resource_type 复用 core/trash 的 TrashResourceType 闭集（枚举
# 展开闭合集合，生成结果不出现任意 string）。
class ArchiveResourceType(str, Enum):
    SESSION = "session"
    LIBRARY_FILE = "library_file"
    LIBRARY_FOLDER = "library_folder"
    TEXTBOOK = "textbook"
    TEXTBOOK_VOLUME = "textbook_volume"
    WORKSPACE = "workspace"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    NOTES_NOTE = "notes_note"
    CLASSROOM_LESSON = "classroom_lesson"


class ArchiveItemTarget(_StrictModel):
    kind: Literal["archive_item"] = "archive_item"
    item_id: EntityId
    resource_type: ArchiveResourceType


class ProfileSectionKind(str, Enum):
    ACCOUNT = "account"
    LEARNING = "learning"
    VOICE = "voice"
    ASSISTANT = "assistant"


class ProfileSectionTarget(_StrictModel):
    kind: Literal["profile_section"] = "profile_section"
    section: ProfileSectionKind = ProfileSectionKind.ACCOUNT


class SettingsSectionKind(str, Enum):
    GENERAL = "general"
    LEARNING = "learning"
    VOICE = "voice"
    ASSISTANT = "assistant"
    PROCESSING = "processing"
    ACCOUNT = "account"
    ABOUT = "about"


class SettingsSectionTarget(_StrictModel):
    kind: Literal["settings_section"] = "settings_section"
    section: SettingsSectionKind = SettingsSectionKind.GENERAL


class TeachingGuidanceTarget(_StrictModel):
    kind: Literal["teaching_guidance"] = "teaching_guidance"
    guidance_id: EntityId


class DocsSectionTarget(_StrictModel):
    kind: Literal["docs_section"] = "docs_section"
    section_id: EntityId


class AdminSectionKind(str, Enum):
    USERS = "users"
    ORPHAN_DATA = "orphan_data"
    CLASSROOM_HEALTH = "classroom_health"
    OCR = "ocr"
    TEXTBOOK_PIPELINE = "textbook_pipeline"
    LLM = "llm"
    LEARNER_EVALUATION = "learner_evaluation"
    PROMPT_MEMORY = "prompt_memory"


class AdminSectionTarget(_StrictModel):
    kind: Literal["admin_section"] = "admin_section"
    section: AdminSectionKind


NavigationTarget = Annotated[
    Union[
        ModuleTarget,
        WorkspaceChatTarget,
        ChatSessionTarget,
        WorkspaceCoursesTarget,
        LessonTarget,
        ClassroomRunTarget,
        NoteTarget,
        LearningArchiveTarget,
        ConceptTarget,
        TaskTarget,
        TeachingProposalTarget,
        DashboardViewTarget,
        ChatMessageTarget,
        FileTarget,
        FileFolderTarget,
        TextbookTarget,
        AssessmentViewTarget,
        GoalTarget,
        WeekTaskTarget,
        MemoryPreferencesTarget,
        NoteRevisionTarget,
        ArchiveItemTarget,
        ProfileSectionTarget,
        SettingsSectionTarget,
        TeachingGuidanceTarget,
        DocsSectionTarget,
        AdminSectionTarget,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# 查询范围与页面上下文（§6）
# ---------------------------------------------------------------------------

Lang = Literal["zh", "en"]


class FollowPageScope(_StrictModel):
    mode: Literal["follow_page"] = "follow_page"


class WorkspaceScope(_StrictModel):
    mode: Literal["workspace"] = "workspace"
    workspace_id: EntityId


class AllWorkspacesScope(_StrictModel):
    mode: Literal["all_workspaces"] = "all_workspaces"


ScopeSelection = Annotated[
    Union[FollowPageScope, WorkspaceScope, AllWorkspacesScope],
    Field(discriminator="mode"),
]


class ResolvedScope(_StrictModel):
    mode: Literal["public", "workspace", "all_workspaces", "account"]
    workspace_ids: list[EntityId] = Field(default_factory=list, max_length=50)
    scope_revisions: dict[str, str] = Field(default_factory=dict)


class PageContextEntity(_StrictModel):
    kind: Literal["chat", "lesson", "run", "note", "concept", "task"]
    id: EntityId
    parent_id: EntityId | None = None
    revision: str | None = Field(default=None, max_length=64)


class PageContextSelection(_StrictModel):
    text: str = Field(min_length=1, max_length=MAX_SELECTION_CHARS)
    label: str = Field(min_length=1, max_length=64)


class PageContext(_StrictModel):
    """发送时冻结的页面上下文快照；整个 JSON 不超过 8 KiB。"""

    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    route_id: AssistantRouteId
    route_epoch: int = Field(ge=0)
    workspace_id: EntityId | None = None
    entity: PageContextEntity | None = None
    view: str | None = Field(default=None, max_length=64)
    selection: PageContextSelection | None = None

    @model_validator(mode="after")
    def _check_size(self) -> "PageContext":
        size = len(self.model_dump_json(exclude_none=True).encode("utf-8"))
        if size > MAX_PAGE_CONTEXT_BYTES:
            raise ValueError(f"page_context 超过 {MAX_PAGE_CONTEXT_BYTES} 字节上限")
        return self


# ---------------------------------------------------------------------------
# 时间窗口与来源（§6.4 / §7.7）
# ---------------------------------------------------------------------------

class TimeWindow(_StrictModel):
    start_at: datetime
    end_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=64)


SourceAvailability = Literal["available", "archived", "removed", "unavailable"]


class AssistantSource(_StrictModel):
    source_id: SourceRefId
    kind: Literal[
        "learning_evidence",
        "concept_evaluation",
        "teaching_report",
        "teaching_proposal",
        "task",
        "lesson",
        "product_help",
        "site_search",
    ]
    title: str = Field(min_length=1, max_length=200)
    workspace_id: EntityId | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime
    revision: str | None = Field(default=None, max_length=64)
    locator: NavigationTarget
    availability: SourceAvailability = "available"


AssistantNoticeCode = Annotated[str, Field(min_length=1, max_length=64)]


class AssistantNotice(_StrictModel):
    code: AssistantNoticeCode
    message: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# 统计口径与展示块（§7.3 / §11.4）
# ---------------------------------------------------------------------------

MetricKey = Literal[
    "answer_attempt_count",
    "graded_answer_count",
    "pending_answer_count",
    "recorded_learning_days",
    "completed_task_count",
    "observed_concept_count",
    "course_position",
    "learning_duration_minutes",
]

MetricCompleteness = Literal["complete", "partial", "unavailable"]


class MetricItem(_StrictModel):
    key: MetricKey
    label: str = Field(min_length=1, max_length=120)
    value: float | None = None
    unit: str = Field(default="", max_length=32)
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)
    completeness: MetricCompleteness = "complete"
    known_minimum: float | None = None
    unavailable_reason: str | None = Field(default=None, max_length=200)


class EvaluationStatement(_StrictModel):
    """已有学习评价主张：文本、条件与限制都来自现有评价域。"""

    text: str = Field(min_length=1, max_length=1000)
    status: str | None = Field(default=None, max_length=64)
    scope_note: str | None = Field(default=None, max_length=500)
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)


class PendingCounts(_StrictModel):
    """评价作业状态统计（≠作答判分状态，§19.8）。"""

    evaluation_pending: int = Field(default=0, ge=0)
    active_reviews: int = Field(default=0, ge=0)
    failed_jobs: int = Field(default=0, ge=0)


class WorkspaceCoverage(_StrictModel):
    observed_concepts: int | None = Field(default=None, ge=0)
    coverage_note: str | None = Field(default=None, max_length=300)


class WorkspaceSummary(_StrictModel):
    workspace_id: EntityId
    workspace_name: str = Field(min_length=1, max_length=200)
    scope_revision: str = Field(default="", max_length=64)
    evidence_watermark: str = Field(default="", max_length=64)
    evaluation_status: str = Field(default="pending", max_length=64)
    coverage: WorkspaceCoverage = Field(default_factory=WorkspaceCoverage)
    statements: list[EvaluationStatement] = Field(default_factory=list, max_length=3)
    pending_counts: PendingCounts = Field(default_factory=PendingCounts)
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)


class UnscopedActivity(_StrictModel):
    """全局查询下未能归入工作区的真实活动，单独成组不参与跨区评价。"""

    included: bool = False
    summary: str = Field(default="", max_length=500)
    recorded_learning_days: int | None = Field(default=None, ge=0)
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)


class NextStep(_StrictModel):
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=500)
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)
    action_id: ActionId | None = None


class LearningReport(_StrictModel):
    window: TimeWindow
    scope: ResolvedScope
    facts: list[MetricItem] = Field(default_factory=list, max_length=6)
    workspace_summaries: list[WorkspaceSummary] = Field(
        default_factory=list, max_length=20)
    unscoped_activity: UnscopedActivity = Field(default_factory=UnscopedActivity)
    next_steps: list[NextStep] = Field(default_factory=list, max_length=3)
    generated_at: datetime
    complete: bool = True
    display_truncated: bool = False
    notices: list[AssistantNotice] = Field(default_factory=list, max_length=10)


class FailureCount(_StrictModel):
    category: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=0)
    label: str | None = Field(default=None, max_length=200)


class StrategyStat(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)
    attempts: int = Field(ge=0)
    successes: int = Field(ge=0)


class ProposalSummary(_StrictModel):
    proposal_id: EntityId
    title: str | None = Field(default=None, max_length=200)
    status: Literal["proposed", "approved", "applied", "rejected"]
    summary: str | None = Field(default=None, max_length=500)


class GuidanceSummary(_StrictModel):
    guidance_id: EntityId
    title: str | None = Field(default=None, max_length=200)
    impact_turns: int = Field(default=0, ge=0)
    applied_at: datetime | None = None


class TeachingCoverage(_StrictModel):
    complete: bool = True
    inspected_count: int = Field(default=0, ge=0)
    invalid_count: int = Field(default=0, ge=0)
    earliest_available_at: datetime | None = None


class TeachingReport(_StrictModel):
    window: TimeWindow
    scope: ResolvedScope
    total_turns: int = Field(default=0, ge=0)
    failure_distribution: list[FailureCount] = Field(
        default_factory=list, max_length=20)
    top_strategies: list[StrategyStat] = Field(default_factory=list, max_length=10)
    proposals: list[ProposalSummary] = Field(default_factory=list, max_length=20)
    active_guidance: list[GuidanceSummary] = Field(default_factory=list, max_length=20)
    pending_proposals: int = Field(default=0, ge=0)
    coverage: TeachingCoverage = Field(default_factory=TeachingCoverage)
    generated_at: datetime
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)
    notices: list[AssistantNotice] = Field(default_factory=list, max_length=10)


# ---------------------------------------------------------------------------
# 动作与页面命令（§9 / §19.3 / §19.4）
# ---------------------------------------------------------------------------

class NavigatePayload(_StrictModel):
    kind: Literal["navigate"] = "navigate"
    target: NavigationTarget


class OpenWorkspaceFormPayload(_StrictModel):
    kind: Literal["open_workspace_form"] = "open_workspace_form"
    workspace_id: EntityId | Literal["new"] = "new"


class PrepareLessonDraft(_StrictModel):
    topic: str = Field(min_length=1, max_length=500)
    objectives: str | None = Field(default=None, max_length=2000)
    # §19.6：不重新定义课程时长范围，与 CreateLessonRequest 一致（离散枚举）。
    duration_minutes: Literal[5, 10, 15, 20, 30] | None = None


class PrepareLessonPayload(_StrictModel):
    kind: Literal["prepare_lesson"] = "prepare_lesson"
    workspace_id: EntityId
    draft: PrepareLessonDraft


class ResumeLessonPayload(_StrictModel):
    kind: Literal["resume_lesson"] = "resume_lesson"
    workspace_id: EntityId
    lesson_id: EntityId
    lesson_revision: int = Field(ge=1)
    run_id: EntityId | None = None


class LaunchTaskPayload(_StrictModel):
    kind: Literal["launch_task"] = "launch_task"
    task_id: EntityId


class HandoffChatPayload(_StrictModel):
    kind: Literal["handoff_chat"] = "handoff_chat"
    workspace_id: EntityId | None = None
    draft_id: DraftId


class HandoffNotePayload(_StrictModel):
    kind: Literal["handoff_note"] = "handoff_note"
    draft_id: DraftId


class OpenClassroomQuestionPayload(_StrictModel):
    kind: Literal["open_classroom_question"] = "open_classroom_question"
    workspace_id: EntityId
    lesson_id: EntityId
    run_id: EntityId
    question: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


# --- §21 领域写动作（B03 协议 + B05–B10 逐操作补齐） ------------------------
# operation 闭集先登记 §21.3 全表；input 模型按任务包逐步实现，未实现的
# operation 由 previews/domain_actions 拒绝（capability_disabled）。input
# 是按 operation 配对的严格模型联合（validator 强制配对），不出现 any。

DomainOperation = Literal[
    "workspace.create", "workspace.update_sources",
    "chat.rename", "chat.move_workspace", "chat.archive",
    "note.create", "note.append", "note.replace", "note.move",
    "note.set_review", "note.restore_revision",
    "goal.create", "goal.update", "plan.regenerate",
    "task.create", "task.update", "task.complete", "subtask.create",
    "schedule.update",
    "assessment.start", "assessment.practice",
    "evaluation.request_review", "evaluation.retry", "evaluation.synthesize",
    "memory.set_window",
    "library.create_folder", "library.rename_file", "library.move_file",
    "textbook.cancel", "textbook.rebuild",
    "archive.restore",
    "profile.update", "assistant.preferences",
    "teaching.approve", "teaching.apply", "teaching.revoke",
    "lesson.generate", "lesson.retry", "lesson.cancel", "lesson.export",
]


class TaskCreateInput(_StrictModel):
    title: str = Field(min_length=1, max_length=200)
    day: str = Field(default="", max_length=10)  # YYYY-MM-DD；空=今天
    estimate_minutes: int | None = Field(default=None, ge=1, le=480)
    workspace_id: EntityId | None = None


class TaskCompleteInput(_StrictModel):
    task_id: EntityId
    expected_status: Literal["pending", "in_progress", "overdue"] = "pending"


class NoteCreateInput(_StrictModel):
    title: str = Field(default="", max_length=120)
    content: str = Field(min_length=1, max_length=12000)
    folder_id: EntityId | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class ChatRenameInput(_StrictModel):
    session_id: EntityId
    title: str = Field(min_length=1, max_length=60)


class ScheduleUpdateInput(_StrictModel):
    daily_minutes: int = Field(ge=5, le=480)


# --- B05：聊天 / 资料 / 归档动作（§21.3） ------------------------------------
# P6-C3 起工作区来源只保留教材（folder_ids 已废弃），update_sources 因此
# 只收 add/remove_file_ids；folder 选择不再进入助手契约。

class WorkspaceCreateInput(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    file_ids: list[EntityId] = Field(default_factory=list, max_length=50)


class WorkspaceUpdateSourcesInput(_StrictModel):
    workspace_id: EntityId
    add_file_ids: list[EntityId] = Field(default_factory=list, max_length=50)
    remove_file_ids: list[EntityId] = Field(default_factory=list, max_length=50)


class ChatMoveWorkspaceInput(_StrictModel):
    session_id: EntityId
    workspace_id: EntityId


class ChatArchiveInput(_StrictModel):
    session_id: EntityId


class LibraryCreateFolderInput(_StrictModel):
    name: str = Field(min_length=1, max_length=60)


class LibraryRenameFileInput(_StrictModel):
    file_id: EntityId
    filename: str = Field(min_length=1, max_length=240)  # 与 FileRename 对齐


class LibraryMoveFileInput(_StrictModel):
    file_id: EntityId
    folder_id: str = Field(default="", max_length=64)  # "" = 根目录/未分类


class TextbookCancelInput(_StrictModel):
    textbook_id: EntityId


class TextbookRebuildInput(_StrictModel):
    textbook_id: EntityId
    # §21.3 闭集；quality_ocr 属原页面手动刷新入口，不进助手契约。
    mode: Literal["rag_graph", "full_ocr", "graph_only"] = "rag_graph"


class ArchiveRestoreInput(_StrictModel):
    item_id: EntityId
    workspace_ids: list[EntityId] = Field(default_factory=list, max_length=10)


# --- B06：笔记 / 学习编排动作（§21.3） --------------------------------------
# note.append/replace 携带精确 base_revision（乐观并发，冲突 409）；
# goal/plan 写操作经预览确认后提交同一候选（§21.6.3/21.6.4）。

class NoteAppendInput(_StrictModel):
    note_id: EntityId
    base_revision: int = Field(ge=1, le=10_000)
    append_markdown: str = Field(min_length=1, max_length=8000)


class NoteReplaceInput(_StrictModel):
    note_id: EntityId
    base_revision: int = Field(ge=1, le=10_000)
    title: str = Field(default="", max_length=120)   # 空 = 不改标题
    content: str = Field(min_length=1, max_length=12000)
    summary: str = Field(default="", max_length=200)


class NoteMoveInput(_StrictModel):
    note_ids: list[EntityId] = Field(min_length=1, max_length=20)
    folder_id: str = Field(default="", max_length=64)  # "" = 未分类


class NoteSetReviewInput(_StrictModel):
    note_id: EntityId
    enabled: bool


class NoteRestoreRevisionInput(_StrictModel):
    note_id: EntityId
    revision: int = Field(ge=1, le=10_000)
    expected_current_revision: int = Field(ge=1, le=10_000)


class GoalCreateInput(_StrictModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    goal_type: Literal["ability", "exam", "interest"] = "ability"
    subjects: list[str] = Field(default_factory=list, max_length=8)
    target_concept_ids: list[str] = Field(default_factory=list, max_length=100)
    workspace_id: str = Field(default="", max_length=64)
    deadline: float = Field(default=0.0, ge=0.0)


class GoalUpdateInput(_StrictModel):
    goal_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    goal_type: Literal["ability", "exam", "interest"] | None = None
    subjects: list[str] | None = Field(default=None, max_length=8)
    target_concept_ids: list[str] | None = Field(
        default=None, max_length=100)
    workspace_id: str | None = Field(default=None, max_length=64)
    deadline: float | None = Field(default=None, ge=0.0)


class PlanRegenerateInput(_StrictModel):
    expected_plan_revision: str = Field(min_length=1, max_length=128)
    explicit_goal_ids: list[str] = Field(default_factory=list, max_length=8)
    num_weeks: int = Field(default=4, ge=1, le=12)


class TaskUpdateInput(_StrictModel):
    task_id: EntityId
    title: str | None = Field(default=None, min_length=1, max_length=200)
    day: str | None = Field(default=None, max_length=10)
    kind: str | None = Field(default=None, max_length=20)
    phase: str | None = Field(default=None, max_length=30)
    estimate_minutes: int | None = Field(default=None, ge=1, le=480)
    priority: int | None = Field(default=None, ge=1, le=5)
    milestone_id: str | None = Field(default=None, max_length=64)


class SubtaskCreateInput(_StrictModel):
    week_index: int = Field(ge=0, le=52)
    week_task_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    estimate_minutes: int = Field(default=15, ge=1, le=480)


# --- B07：测评 / 评价 / 教学指导动作（§21.3） --------------------------------
# 测评与评价写操作一律 review_required（消耗生成资源 / 生命周期门控）；
# 教学指导批准/应用/撤销彼此独立确认（FULL-14：应用必须核对实际生效）。

class AssessmentStartInput(_StrictModel):
    workspace_id: str = Field(default="", max_length=96)
    concept_keys: list[str] = Field(min_length=1, max_length=20)
    count: int = Field(default=5, ge=1, le=20)
    q_type: str = Field(default="", max_length=32)
    grade: str = Field(default="本科", max_length=16)
    subject: str = Field(default="", max_length=32)
    expected_scope_revision: str = Field(default="", max_length=128)


class AssessmentPracticeInput(_StrictModel):
    question_id: str = Field(min_length=1, max_length=64)
    question_revision: int = Field(ge=1, le=1_000_000)
    mode: Literal["same", "variant"]
    expected_scope_revision: str = Field(default="", max_length=128)


class EvaluationRequestReviewInput(_StrictModel):
    source_id: str = Field(min_length=1, max_length=64)
    interpretation_id: str = Field(min_length=4, max_length=64)
    reason: str = Field(min_length=4, max_length=1200)
    issue_kind: str = Field(default="other", max_length=64)
    expected_revision: int = Field(ge=1)


class EvaluationRetryInput(_StrictModel):
    job_id: str = Field(min_length=1, max_length=64)


class EvaluationSynthesizeInput(_StrictModel):
    workspace_id: str = Field(min_length=1, max_length=96)
    expected_scope_revision: str = Field(default="", max_length=128)


class TeachingApproveInput(_StrictModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    expected_status: Literal["proposed", "approved", "rejected",
                             "applied"] = "proposed"


class TeachingApplyInput(_StrictModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    expected_status: Literal["approved", "applied"] = "approved"


class TeachingRevokeInput(_StrictModel):
    guidance_id: str = Field(min_length=1, max_length=64)
    expected_active: Literal[True] = True


# --- B09：画像 / 记忆 / 助手偏好（§21.3/§22.4） ------------------------------
# 主题/字号/语言是本地 UI 偏好（useUIStore），走 set_local_preference
# 客户端执行；服务端偏好只含回答风格白名单。

class MemorySetWindowInput(_StrictModel):
    window: int = Field(ge=5, le=200)


class SetLocalPreferencePayload(_StrictModel):
    """§21.1/§22.4 本地 UI 偏好：客户端直接写 useUIStore，不经服务端。"""
    kind: Literal["set_local_preference"] = "set_local_preference"
    key: Literal["theme", "font_scale", "lang"]
    value: str = Field(min_length=1, max_length=32)


# --- C01/C03：跨模块工作流（§21.1/§23.1/§23.5） ------------------------------

WorkflowTemplateId = Literal[
    "setup_learning_space", "weekly_review_to_plan",
    "weak_point_to_practice", "material_to_course",
    "organize_materials", "continue_learning_session",
]


class StartWorkflowPayload(_StrictModel):
    """§21.1/§23.5 发起工作流：execute 只创建 draft（不写业务），后续经
    办理事项视图批准计划并启动（§23.3 多步授权）。"""
    kind: Literal["start_workflow"] = "start_workflow"
    template: WorkflowTemplateId
    objective: str = Field(min_length=4, max_length=2000)
    scope: dict = Field(default_factory=dict)
    selection_ids: dict[str, list[str]] = Field(default_factory=dict)


class SubscriptionSubscribeInput(_StrictModel):
    """§25.1 订阅输入：时间缺失用默认建议时间（表单预填语义）。"""
    kind: Literal["weekly_brief", "daily_tasks", "due_reviews",
                  "unfinished_course"]
    local_time: str = Field(default="", max_length=5)
    timezone: str = Field(default="UTC", max_length=64)


class ManageSubscriptionPayload(_StrictModel):
    """§21.1/§25.1 订阅动作：execute 建订阅；退订/编辑走设置卡原页。"""
    kind: Literal["manage_subscription"] = "manage_subscription"
    operation: Literal["subscribe"]
    input: SubscriptionSubscribeInput


class ProfileUpdateInput(_StrictModel):
    # §21.3 白名单 patch；avatar/prefs 不经助手（原页面能力）。
    name: str | None = Field(default=None, min_length=1, max_length=40)
    grade: str | None = Field(default=None, max_length=16)
    school: str | None = Field(default=None, max_length=80)
    subjects: list[str] | None = Field(default=None, max_length=8)


class AssistantPreferencesInput(_StrictModel):
    """§24.6 偏好白名单（局部更新 + base_revision 版本合并）。"""
    response_length: Literal["short", "standard", "detailed"] | None = None
    tone: Literal["neutral", "encouraging"] | None = None
    default_scope: Literal["follow_page", "all_workspaces"] | None = None
    proactive_enabled: bool | None = None
    voice_input_mode: Literal["hold", "toggle"] | None = None
    send_after_recording: bool | None = None
    auto_read: bool | None = None
    voice_policy: Literal["auto", "cloud", "local", "silent"] | None = None
    voice_id: str | None = Field(default=None, max_length=128)
    allow_local_fallback: bool | None = None
    playback_rate: Literal[0.75, 1, 1.25, 1.5] | None = None
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    base_revision: int | None = Field(default=None, ge=1)


# --- B10：课程高级动作（§21.3；对齐 2026-09 课堂重构与自主构图） --------------
# 助手预览与参数均不含构图（composition/密度/表面）字段：构图由模型在
# schema 枚举内自选（slide@2.3.0 起）；组件编辑/播放控制经原页面交接。

class LessonGenerateInput(_StrictModel):
    workspace_id: str = Field(min_length=1, max_length=96)
    topic: str = Field(min_length=2, max_length=120)
    goals: list[str] = Field(default_factory=list, max_length=5)
    duration_minutes: Literal[5, 10, 15, 20, 30] = 15
    start_mode: Literal["automatic", "outline_first"] = "automatic"
    source_file_ids: list[str] = Field(default_factory=list, max_length=8)
    grade: str = Field(default="", max_length=16)
    language: Literal["zh", "en"] = "zh"


class LessonJobInput(_StrictModel):
    workspace_id: str = Field(min_length=1, max_length=96)
    lesson_id: str = Field(min_length=1, max_length=96)
    job_id: str = Field(min_length=1, max_length=96)
    expected_state_revision: int = Field(ge=1)


class LessonExportInput(_StrictModel):
    workspace_id: str = Field(min_length=1, max_length=96)
    lesson_id: str = Field(min_length=1, max_length=96)
    revision: int = Field(ge=1)
    fmt: Literal["html_zip", "notes_md"] = "html_zip"


_INPUT_BY_OPERATION: dict[str, type[BaseModel]] = {
    "task.create": TaskCreateInput,
    "task.complete": TaskCompleteInput,
    "note.create": NoteCreateInput,
    "chat.rename": ChatRenameInput,
    "schedule.update": ScheduleUpdateInput,
    "workspace.create": WorkspaceCreateInput,
    "workspace.update_sources": WorkspaceUpdateSourcesInput,
    "chat.move_workspace": ChatMoveWorkspaceInput,
    "chat.archive": ChatArchiveInput,
    "library.create_folder": LibraryCreateFolderInput,
    "library.rename_file": LibraryRenameFileInput,
    "library.move_file": LibraryMoveFileInput,
    "textbook.cancel": TextbookCancelInput,
    "textbook.rebuild": TextbookRebuildInput,
    "archive.restore": ArchiveRestoreInput,
    # B06（§21.3）
    "note.append": NoteAppendInput,
    "note.replace": NoteReplaceInput,
    "note.move": NoteMoveInput,
    "note.set_review": NoteSetReviewInput,
    "note.restore_revision": NoteRestoreRevisionInput,
    "goal.create": GoalCreateInput,
    "goal.update": GoalUpdateInput,
    "plan.regenerate": PlanRegenerateInput,
    "task.update": TaskUpdateInput,
    "subtask.create": SubtaskCreateInput,
    # B07（§21.3）
    "assessment.start": AssessmentStartInput,
    "assessment.practice": AssessmentPracticeInput,
    "evaluation.request_review": EvaluationRequestReviewInput,
    "evaluation.retry": EvaluationRetryInput,
    "evaluation.synthesize": EvaluationSynthesizeInput,
    "teaching.approve": TeachingApproveInput,
    "teaching.apply": TeachingApplyInput,
    "teaching.revoke": TeachingRevokeInput,
    # B09（§21.3）
    "memory.set_window": MemorySetWindowInput,
    "profile.update": ProfileUpdateInput,
    "assistant.preferences": AssistantPreferencesInput,
    # B10（§21.3）
    "lesson.generate": LessonGenerateInput,
    "lesson.retry": LessonJobInput,
    "lesson.cancel": LessonJobInput,
    "lesson.export": LessonExportInput,
}


class DomainWritePayload(_StrictModel):
    kind: Literal["domain_write"] = "domain_write"
    operation: DomainOperation
    input: TaskCreateInput | TaskCompleteInput | NoteCreateInput \
        | ChatRenameInput | ScheduleUpdateInput | WorkspaceCreateInput \
        | WorkspaceUpdateSourcesInput | ChatMoveWorkspaceInput \
        | ChatArchiveInput | LibraryCreateFolderInput | LibraryRenameFileInput \
        | LibraryMoveFileInput | TextbookCancelInput | TextbookRebuildInput \
        | ArchiveRestoreInput | NoteAppendInput | NoteReplaceInput \
        | NoteMoveInput | NoteSetReviewInput | NoteRestoreRevisionInput \
        | GoalCreateInput | GoalUpdateInput | PlanRegenerateInput \
        | TaskUpdateInput | SubtaskCreateInput | AssessmentStartInput \
        | AssessmentPracticeInput | EvaluationRequestReviewInput \
        | EvaluationRetryInput | EvaluationSynthesizeInput \
        | TeachingApproveInput | TeachingApplyInput | TeachingRevokeInput \
        | MemorySetWindowInput | ProfileUpdateInput \
        | AssistantPreferencesInput | LessonGenerateInput | LessonJobInput \
        | LessonExportInput
    expected_revision: str | None = Field(default=None, max_length=128)
    preview_id: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def _input_matches_operation(self) -> "DomainWritePayload":
        # §21.1：按 operation 判别的严格联合——operation 与 input 类型必须
        # 配对；未实现 operation 的 input 模型尚不存在，同样拒绝。
        expected = _INPUT_BY_OPERATION.get(self.operation)
        if expected is None or not isinstance(self.input, expected):
            raise ValueError(
                f"operation {self.operation} 与 input 模型不匹配或未实现")
        return self


class PreviewChange(_StrictModel):
    field: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    before: str | None = Field(default=None, max_length=500)
    after: str | None = Field(default=None, max_length=500)


class ActionPreview(_StrictModel):
    preview_id: str = Field(min_length=8, max_length=64)
    action_id: ActionId
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    affected_entities: list[NavigationTarget] = Field(
        default_factory=list, max_length=10)
    changes: list[PreviewChange] = Field(
        default_factory=list, max_length=20)
    side_effects: list[str] = Field(default_factory=list, max_length=10)
    reversible: bool = False
    parameter_hash: str = Field(min_length=16, max_length=128)
    source_revisions: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime
    approval: Literal["intent_sufficient", "review_required",
                      "original_page_required"]
    # §21.6.3：plan.regenerate 预览绑定的候选 id；其余操作为空。
    candidate_id: str | None = Field(default=None, max_length=64)


class ActionApproveRequest(_StrictModel):
    preview_id: str = Field(min_length=8, max_length=64)
    parameter_hash: str = Field(min_length=16, max_length=128)
    decision: Literal["approve", "reject"]


class ActionApproveResponse(_StrictModel):
    approval_id: str = Field(min_length=8, max_length=64)
    decision: Literal["approve", "reject"]
    expires_at: datetime


class ActionUndoRequest(_StrictModel):
    client_request_id: ClientUUID
    expected_result_revision: str = Field(min_length=1, max_length=128)


class ActionUndoResponse(_StrictModel):
    action: AssistantAction
    undone: bool
    reason: str | None = Field(default=None, max_length=300)


AssistantActionPayload = Annotated[
    Union[
        NavigatePayload,
        OpenWorkspaceFormPayload,
        PrepareLessonPayload,
        ResumeLessonPayload,
        LaunchTaskPayload,
        HandoffChatPayload,
        HandoffNotePayload,
        OpenClassroomQuestionPayload,
        SetLocalPreferencePayload,
        StartWorkflowPayload,
        DomainWritePayload,
    ],
    Field(discriminator="kind"),
]


ActionExecution = Literal["automatic", "user_click"]

ActionState = Literal[
    "proposed",
    "executing",
    "awaiting_ack",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
    "needs_attention",
]


class BusinessUndoSnapshot(_StrictModel):
    """§21.5 执行时记录的撤销前值快照；字段按 operation 取用。

    由 previews 各执行分支在补偿发生前写入 business_result.undo；
    客户端只读，不参与模型上下文。
    """
    task_id: str | None = None
    day: str | None = None
    title: str | None = None
    note_id: str | None = None
    content_sha: str | None = None
    session_id: str | None = None
    previous_title: str | None = None
    previous_minutes: int | None = None
    workspace_id: str | None = None
    selected_file_ids: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    result_updated_at: str | None = None
    previous_workspace_id: str | None = None
    trash_item: str | None = None
    folder_id: str | None = None
    previous_filename: str | None = None
    file_id: str | None = None
    previous_folder_id: str | None = None
    resource_type: str | None = None
    original_id: str | None = None
    # B06（笔记/学习编排）
    base_revision: int | None = None
    result_revision: int | None = None
    moves: list[dict[str, str]] = Field(default_factory=list)
    previous_enabled: bool | None = None
    restored_from_current: int | None = None
    previous: dict[str, str] | None = None
    applied: dict[str, str] | None = None
    week_index: int | None = None
    week_task_id: str | None = None
    subtask_id: str | None = None


class BusinessResult(_StrictModel):
    kind: Literal["none", "classroom_run", "task_launch", "draft", "note",
                  "task", "chat", "workspace", "library_folder",
                  "library_file", "trash_item", "textbook", "textbook_job",
                  "restore", "goal", "assessment", "evaluation_job",
                  "teaching_proposal", "profile", "classroom_job",
                  "classroom_export", "workflow", "subscription"] = "none"
    entity_id: EntityId | None = None
    related_ids: dict[str, EntityId] = Field(default_factory=dict)
    result_revision: str | None = Field(default=None, max_length=128)
    # §21.6.1 幂等复用标记（failed→retry 复用同一实体时为 true）。
    idempotent_reuse: bool = False
    # §21.5 可逆操作的前值快照；撤销端点据此执行真实补偿。
    undo: BusinessUndoSnapshot | None = None


class ActionClientResult(_StrictModel):
    status: Literal["succeeded", "failed", "cancelled"]
    code: str | None = Field(default=None, max_length=64)
    detail: str | None = Field(default=None, max_length=500)


class ActionUndoResult(_StrictModel):
    """§21.5 撤销回执：补偿已真实执行；message 为面向用户的说明。"""
    client_request_id: str = Field(min_length=1, max_length=64)
    undone_at: datetime
    message: str = Field(min_length=1, max_length=300)


class AssistantAction(_StrictModel):
    action_id: ActionId
    conversation_id: ConversationId
    turn_id: TurnId
    label: str = Field(min_length=1, max_length=200)
    payload: AssistantActionPayload
    execution: ActionExecution = "user_click"
    state: ActionState = "proposed"
    undo_result: ActionUndoResult | None = None
    created_at: datetime
    expires_at: datetime
    business_result: BusinessResult | None = None
    client_result: ActionClientResult | None = None
    return_target: NavigationTarget | None = None


class NavigateCommand(_StrictModel):
    kind: Literal["navigate"] = "navigate"
    target: NavigationTarget


class WorkspaceFormCommand(_StrictModel):
    kind: Literal["workspace_form"] = "workspace_form"
    workspace_id: EntityId | Literal["new"] = "new"


class LessonFormCommand(_StrictModel):
    kind: Literal["lesson_form"] = "lesson_form"
    workspace_id: EntityId
    draft_id: DraftId


class ChatDraftCommand(_StrictModel):
    kind: Literal["chat_draft"] = "chat_draft"
    draft_id: DraftId


class NoteDraftCommand(_StrictModel):
    kind: Literal["note_draft"] = "note_draft"
    draft_id: DraftId


class ClassroomQuestionCommand(_StrictModel):
    kind: Literal["classroom_question"] = "classroom_question"
    workspace_id: EntityId
    lesson_id: EntityId
    run_id: EntityId
    draft_id: DraftId


PageCommand = Annotated[
    Union[
        NavigateCommand,
        WorkspaceFormCommand,
        LessonFormCommand,
        ChatDraftCommand,
        NoteDraftCommand,
        ClassroomQuestionCommand,
    ],
    Field(discriminator="kind"),
]

PageCommandStatusCode = Literal[
    "entity_not_found",
    "draft_expired",
    "user_stayed",
    "page_not_ready",
    "capability_disabled",
    "save_failed",
]


class PageCommandResult(_StrictModel):
    status: Literal["succeeded", "failed", "cancelled"]
    code: PageCommandStatusCode | None = None


class ActionExecuteRequest(_StrictModel):
    invocation_id: ClientUUID
    client_instance_id: str = Field(min_length=8, max_length=64)
    route_epoch: int = Field(ge=0)
    approval_id: str | None = Field(default=None, min_length=8, max_length=64)


class ActionExecutionResponse(_StrictModel):
    action: AssistantAction
    conversation_revision: int = Field(ge=1)
    business_result: BusinessResult
    command: PageCommand | None = None
    command_id: CommandId | None = None
    ack_token: str | None = Field(default=None, max_length=128)
    command_expires_at: datetime | None = None
    retryable: bool = False
    ready_to_deliver: bool = False


class ActionAckRequest(_StrictModel):
    command_id: CommandId
    ack_token: str = Field(min_length=16, max_length=128)
    result: Literal["succeeded", "failed", "cancelled"]
    error_code: PageCommandStatusCode | None = None


# ---------------------------------------------------------------------------
# 交接草稿（§9.5 / §19.6）
# ---------------------------------------------------------------------------

class LessonPrefill(_StrictModel):
    kind: Literal["lesson"] = "lesson"
    workspace_id: EntityId
    topic: str = Field(min_length=1, max_length=500)
    objectives: str | None = Field(default=None, max_length=2000)
    duration_minutes: int | None = Field(default=None, ge=1, le=600)


class ChatPrefill(_StrictModel):
    kind: Literal["chat"] = "chat"
    workspace_id: EntityId | None = None
    session_id: EntityId | None = None
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class NotePrefill(_StrictModel):
    kind: Literal["note"] = "note"
    title: str = Field(min_length=1, max_length=120)
    markdown: str = Field(min_length=0, max_length=12000)
    folder_id: EntityId | None = None


class ClassroomQuestionPrefill(_StrictModel):
    kind: Literal["classroom_question"] = "classroom_question"
    workspace_id: EntityId
    lesson_id: EntityId
    run_id: EntityId
    question: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


AssistantDraftPrefill = Annotated[
    Union[LessonPrefill, ChatPrefill, NotePrefill, ClassroomQuestionPrefill],
    Field(discriminator="kind"),
]


class DraftResultEntity(_StrictModel):
    kind: Literal["lesson", "chat", "note", "classroom_question"]
    id: EntityId


class AssistantDraft(_StrictModel):
    draft_id: DraftId
    conversation_id: ConversationId
    action_id: ActionId
    prefill: AssistantDraftPrefill
    source_ids: list[SourceRefId] = Field(default_factory=list, max_length=30)
    created_at: datetime
    expires_at: datetime
    consumed: bool = False
    result_entity: DraftResultEntity | None = None


# ---------------------------------------------------------------------------
# 消息与展示块（§11.4）
# ---------------------------------------------------------------------------

MessageStatus = Literal["streaming", "complete", "cancelled", "failed", "interrupted"]


class MarkdownBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["markdown"] = "markdown"
    text: str = Field(max_length=20000)


class MetricsBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["metrics"] = "metrics"
    items: list[MetricItem] = Field(min_length=1, max_length=12)


class LearningReportBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["learning_report"] = "learning_report"
    report: LearningReport


class TeachingReportBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["teaching_report"] = "teaching_report"
    report: TeachingReport


class ActionsBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["actions"] = "actions"
    items: list[AssistantAction] = Field(min_length=1, max_length=3)


class ChoiceOption(_StrictModel):
    option_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class ChoicesBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["choices"] = "choices"
    prompt: str = Field(min_length=1, max_length=500)
    options: list[ChoiceOption] = Field(min_length=2, max_length=6)


class NoticeBlock(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    type: Literal["notice"] = "notice"
    tone: Literal["info", "warning", "error"]
    code: AssistantNoticeCode
    text: str = Field(min_length=1, max_length=1000)


AssistantBlock = Annotated[
    Union[
        MarkdownBlock,
        MetricsBlock,
        LearningReportBlock,
        TeachingReportBlock,
        ActionsBlock,
        ChoicesBlock,
        NoticeBlock,
    ],
    Field(discriminator="type"),
]


class AssistantMessage(_StrictModel):
    message_id: MessageId
    seq: int = Field(ge=1)
    role: Literal["user", "assistant"]
    created_at: datetime
    turn_id: TurnId
    status: MessageStatus
    scope: ResolvedScope
    blocks: list[AssistantBlock] = Field(default_factory=list, max_length=24)
    sources: list[AssistantSource] = Field(default_factory=list, max_length=30)


# ---------------------------------------------------------------------------
# 轮请求 / 快照 / SSE 事件（§11.3 / §11.5 / §19.2）
# ---------------------------------------------------------------------------

class TurnChoice(_StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    option_id: str = Field(min_length=1, max_length=64)


class AssistantTurnRequest(_StrictModel):
    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    client_message_id: ClientUUID
    expected_conversation_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    lang: Lang = "zh"
    timezone: str = Field(min_length=1, max_length=64)
    scope: ScopeSelection
    page_context: PageContext
    choice: TurnChoice | None = None


class AssistantError(_StrictModel):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    request_id: str = Field(default="", max_length=64)


ServerTurnState = Literal["running", "completed", "cancelled", "failed", "interrupted"]


class TurnSnapshot(_StrictModel):
    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    turn_id: TurnId
    conversation_id: ConversationId
    conversation_revision: int = Field(ge=1)
    client_message_id: ClientUUID
    state: ServerTurnState
    cancel_requested: bool = False
    created_at: datetime
    updated_at: datetime
    last_event_seq: int = Field(default=0, ge=0)
    user_message: AssistantMessage
    assistant_message: AssistantMessage
    actions: list[AssistantAction] = Field(default_factory=list, max_length=10)
    error: AssistantError | None = None


class TurnAcceptedResponse(_StrictModel):
    turn_id: TurnId
    conversation_id: ConversationId
    conversation_revision: int = Field(ge=1)
    state: ServerTurnState
    events_path: str = Field(min_length=1, max_length=200)
    duplicate: bool = False


class TurnCancelRequest(_StrictModel):
    client_request_id: ClientUUID


class TurnCancelResponse(_StrictModel):
    turn_id: TurnId
    state: ServerTurnState
    cancel_requested: bool


class _SseEventBase(_StrictModel):
    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    turn_id: TurnId
    event_seq: int = Field(ge=1)
    emitted_at: datetime


class SnapshotSseEvent(_SseEventBase):
    event: Literal["snapshot"] = "snapshot"
    snapshot: TurnSnapshot
    last_event_seq: int = Field(ge=0)


class StatusSseEvent(_SseEventBase):
    event: Literal["status"] = "status"
    stage: Literal["understanding", "reading", "composing"]
    label: str = Field(min_length=1, max_length=120)


class TextDeltaSseEvent(_SseEventBase):
    event: Literal["text_delta"] = "text_delta"
    message_id: MessageId
    block_id: str = Field(min_length=1, max_length=64)
    delta: str = Field(min_length=1, max_length=4000)


class BlockUpsertSseEvent(_SseEventBase):
    event: Literal["block_upsert"] = "block_upsert"
    message_id: MessageId
    block: AssistantBlock


class MessageDoneSseEvent(_SseEventBase):
    event: Literal["message_done"] = "message_done"
    message: AssistantMessage


class TurnDoneSseEvent(_SseEventBase):
    event: Literal["turn_done"] = "turn_done"
    state: ServerTurnState
    conversation_revision: int = Field(ge=1)


class ErrorSseEvent(_SseEventBase):
    event: Literal["error"] = "error"
    error: AssistantError


AssistantSseEvent = Annotated[
    Union[
        SnapshotSseEvent,
        StatusSseEvent,
        TextDeltaSseEvent,
        BlockUpsertSseEvent,
        MessageDoneSseEvent,
        TurnDoneSseEvent,
        ErrorSseEvent,
    ],
    Field(discriminator="event"),
]


# ---------------------------------------------------------------------------
# 会话 API 载荷（§11.2）
# ---------------------------------------------------------------------------

class ConversationCreateRequest(_StrictModel):
    client_request_id: ClientUUID
    title: str | None = Field(default=None, max_length=120)


class ConversationCreated(_StrictModel):
    conversation_id: ConversationId
    revision: int = Field(ge=1)
    created_at: datetime


class ConversationSummary(_StrictModel):
    conversation_id: ConversationId
    title: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(_StrictModel):
    items: list[ConversationSummary] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class ActiveTurnInfo(_StrictModel):
    turn_id: TurnId
    state: ServerTurnState
    created_at: datetime


class ConversationDetailResponse(_StrictModel):
    conversation_id: ConversationId
    title: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    messages: list[AssistantMessage] = Field(default_factory=list)
    has_more: bool = False
    active_turn: ActiveTurnInfo | None = None


# ---------------------------------------------------------------------------
# capabilities 与访客导览（§19.9 / §11.2）
# ---------------------------------------------------------------------------

class ModuleCapability(_StrictModel):
    route_id: AssistantRouteId
    available: bool
    disabled_reason: str | None = Field(default=None, max_length=200)


class AssistantCapabilities(_StrictModel):
    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    enabled: bool
    catalog_version: str = Field(min_length=1, max_length=32)
    identity_mode: Literal["guest", "authenticated"]
    conversation_enabled: bool
    model_available: bool
    modules: list[ModuleCapability] = Field(default_factory=list, max_length=32)
    tools: list[str] = Field(default_factory=list, max_length=32)
    action_kinds: list[str] = Field(default_factory=list, max_length=32)
    disabled_reasons: dict[str, str] = Field(default_factory=dict)


class GuideHistoryItem(_StrictModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class GuideRequest(_StrictModel):
    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    question: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    lang: Lang = "zh"
    route_id: AssistantRouteId | None = None
    history: list[GuideHistoryItem] = Field(default_factory=list, max_length=6)


class GuideModuleEntry(_StrictModel):
    route_id: AssistantRouteId
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)


class GuideResponse(_StrictModel):
    schema_version: Literal[1] = ASSISTANT_SCHEMA_VERSION
    text: str = Field(min_length=1, max_length=6000)
    module_entries: list[GuideModuleEntry] = Field(default_factory=list, max_length=3)
    followups: list[str] = Field(default_factory=list, max_length=4)


# ---------------------------------------------------------------------------
# 错误 envelope（§11.1 / §11.7 / §19.10）
# ---------------------------------------------------------------------------

class AssistantErrorCode(str, Enum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    ENTITY_NOT_FOUND = "entity_not_found"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    CONVERSATION_CHANGED = "conversation_changed"
    CONVERSATION_BUSY = "conversation_busy"
    CONVERSATION_FULL = "conversation_full"
    SCOPE_CHANGED = "scope_changed"
    REVISION_CONFLICT = "revision_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    ACTION_IN_PROGRESS = "action_in_progress"
    ACTION_EXPIRED = "action_expired"
    DRAFT_EXPIRED = "draft_expired"
    TARGET_CHANGED = "target_changed"
    DRAFT_ALREADY_CONSUMED = "draft_already_consumed"
    INVALID_CONTEXT = "invalid_context"
    INVALID_TARGET = "invalid_target"
    RATE_LIMITED = "rate_limited"
    CONCURRENCY_LIMIT = "concurrency_limit"
    CAPABILITY_DISABLED = "capability_disabled"
    MODEL_UNAVAILABLE = "model_unavailable"
    SOURCE_UNAVAILABLE = "source_unavailable"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    PREVIEW_STALE = "preview_stale"
    INTERNAL_ERROR = "internal_error"


class ErrorBody(_StrictModel):
    code: AssistantErrorCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    request_id: str = Field(default="", max_length=64)


class ErrorResponse(_StrictModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# 类型生成导出（供 scripts/contracts/generate_types.py 使用）
# ---------------------------------------------------------------------------

PUBLIC_TYPE_UNIONS: dict[str, tuple[str, ...]] = {
    "NavigationTarget": (
        "ModuleTarget", "WorkspaceChatTarget", "ChatSessionTarget",
        "WorkspaceCoursesTarget", "LessonTarget", "ClassroomRunTarget",
        "NoteTarget", "LearningArchiveTarget", "ConceptTarget", "TaskTarget",
        "TeachingProposalTarget",
        "DashboardViewTarget", "ChatMessageTarget", "FileTarget",
        "FileFolderTarget", "TextbookTarget", "AssessmentViewTarget",
        "GoalTarget", "WeekTaskTarget", "MemoryPreferencesTarget",
        "NoteRevisionTarget", "ArchiveItemTarget", "ProfileSectionTarget", "SettingsSectionTarget",
        "TeachingGuidanceTarget", "DocsSectionTarget", "AdminSectionTarget",
    ),
    "ScopeSelection": (
        "FollowPageScope", "WorkspaceScope", "AllWorkspacesScope",
    ),
    "AssistantActionPayload": (
        "NavigatePayload", "OpenWorkspaceFormPayload", "PrepareLessonPayload",
        "ResumeLessonPayload", "LaunchTaskPayload", "HandoffChatPayload",
        "HandoffNotePayload", "OpenClassroomQuestionPayload",
        "SetLocalPreferencePayload", "StartWorkflowPayload",
    "SubscriptionSubscribeInput", "ManageSubscriptionPayload",
        "ManageSubscriptionPayload", "DomainWritePayload",
    ),
    "PageCommand": (
        "NavigateCommand", "WorkspaceFormCommand", "LessonFormCommand",
        "ChatDraftCommand", "NoteDraftCommand", "ClassroomQuestionCommand",
    ),
    "AssistantDraftPrefill": (
        "LessonPrefill", "ChatPrefill", "NotePrefill", "ClassroomQuestionPrefill",
    ),
    "AssistantBlock": (
        "MarkdownBlock", "MetricsBlock", "LearningReportBlock",
        "TeachingReportBlock", "ActionsBlock", "ChoicesBlock", "NoticeBlock",
    ),
    "AssistantSseEvent": (
        "SnapshotSseEvent", "StatusSseEvent", "TextDeltaSseEvent",
        "BlockUpsertSseEvent", "MessageDoneSseEvent", "TurnDoneSseEvent",
        "ErrorSseEvent",
    ),
}

PUBLIC_TYPE_MODELS: list[str] = [
    "AssistantRouteId", "LearningArchiveTab",
    "ModuleTarget", "WorkspaceChatTarget", "ChatSessionTarget",
    "WorkspaceCoursesTarget", "LessonTarget", "ClassroomRunTarget",
    "NoteTarget", "LearningArchiveTarget", "ConceptTarget", "TaskTarget",
    "TeachingProposalTarget",
    "DashboardRange", "DashboardViewTarget", "ChatMessageTarget",
    "FileTarget", "FileFolderTarget", "TextbookTarget",
    "AssessmentViewKind", "AssessmentViewTarget", "GoalTarget",
    "WeekTaskTarget", "MemoryPreferencesTarget", "NoteRevisionTarget",
    "ArchiveResourceType", "ArchiveItemTarget", "ProfileSectionKind",
    "ProfileSectionTarget", "SettingsSectionKind", "SettingsSectionTarget", "TeachingGuidanceTarget", "DocsSectionTarget",
    "AdminSectionKind", "AdminSectionTarget",
    "FollowPageScope", "WorkspaceScope", "AllWorkspacesScope", "ResolvedScope",
    "PageContextEntity", "PageContextSelection", "PageContext",
    "TimeWindow", "AssistantSource", "AssistantNotice",
    "MetricItem", "EvaluationStatement", "PendingCounts", "WorkspaceCoverage",
    "WorkspaceSummary", "UnscopedActivity", "NextStep", "LearningReport",
    "FailureCount", "StrategyStat", "ProposalSummary", "GuidanceSummary",
    "TeachingCoverage", "TeachingReport",
    "NavigatePayload", "OpenWorkspaceFormPayload", "PrepareLessonDraft",
    "PrepareLessonPayload", "ResumeLessonPayload", "LaunchTaskPayload",
    "HandoffChatPayload", "HandoffNotePayload", "OpenClassroomQuestionPayload",
    "SetLocalPreferencePayload", "StartWorkflowPayload",
    "SubscriptionSubscribeInput", "ManageSubscriptionPayload",
    "TaskCreateInput", "TaskCompleteInput", "NoteCreateInput",
    "ChatRenameInput", "ScheduleUpdateInput",
    "WorkspaceCreateInput", "WorkspaceUpdateSourcesInput",
    "ChatMoveWorkspaceInput", "ChatArchiveInput",
    "LibraryCreateFolderInput", "LibraryRenameFileInput",
    "LibraryMoveFileInput", "TextbookCancelInput", "TextbookRebuildInput",
    "ArchiveRestoreInput", "DomainWritePayload",
    "NoteAppendInput", "NoteReplaceInput", "NoteMoveInput",
    "NoteSetReviewInput", "NoteRestoreRevisionInput",
    "GoalCreateInput", "GoalUpdateInput", "PlanRegenerateInput",
    "TaskUpdateInput", "SubtaskCreateInput",
    "AssessmentStartInput", "AssessmentPracticeInput",
    "EvaluationRequestReviewInput", "EvaluationRetryInput",
    "EvaluationSynthesizeInput", "TeachingApproveInput",
    "TeachingApplyInput", "TeachingRevokeInput",
    "MemorySetWindowInput", "SetLocalPreferencePayload",
    "ProfileUpdateInput", "AssistantPreferencesInput",
    "LessonGenerateInput", "LessonJobInput", "LessonExportInput",
    "PreviewChange", "ActionPreview", "ActionApproveRequest",
    "ActionApproveResponse", "ActionUndoRequest", "ActionUndoResponse",
    "BusinessUndoSnapshot", "BusinessResult", "ActionUndoResult",
    "ActionClientResult", "AssistantAction",
    "NavigateCommand", "WorkspaceFormCommand", "LessonFormCommand",
    "ChatDraftCommand", "NoteDraftCommand", "ClassroomQuestionCommand",
    "PageCommandResult", "ActionExecuteRequest", "ActionExecutionResponse",
    "ActionAckRequest",
    "LessonPrefill", "ChatPrefill", "NotePrefill", "ClassroomQuestionPrefill",
    "DraftResultEntity", "AssistantDraft",
    "MarkdownBlock", "MetricsBlock", "LearningReportBlock",
    "TeachingReportBlock", "ActionsBlock", "ChoiceOption", "ChoicesBlock",
    "NoticeBlock", "AssistantMessage",
    "TurnChoice", "AssistantTurnRequest", "AssistantError",
    "TurnSnapshot", "TurnAcceptedResponse", "TurnCancelRequest",
    "TurnCancelResponse",
    "SnapshotSseEvent", "StatusSseEvent", "TextDeltaSseEvent",
    "BlockUpsertSseEvent", "MessageDoneSseEvent", "TurnDoneSseEvent",
    "ErrorSseEvent",
    "ConversationCreateRequest", "ConversationCreated",
    "ConversationSummary", "ConversationListResponse", "ActiveTurnInfo",
    "ConversationDetailResponse",
    "ModuleCapability", "AssistantCapabilities",
    "GuideHistoryItem", "GuideRequest", "GuideModuleEntry", "GuideResponse",
    "AssistantErrorCode", "ErrorBody", "ErrorResponse",
]
