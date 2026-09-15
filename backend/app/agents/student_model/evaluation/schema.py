"""统一学生评价领域协议（plan.md §4 领域协议 / §6.4 事务 envelope）。

本文件是唯一 schema 事实源：持久化、API DTO 与 LLM 输出校验都从这里取
严格模型（`extra='forbid'`）。LLM 永不生成 owner、workspace、时间、原始
判分或服务端版本字段——这些由服务端写入。

长度/条数边界（§4.3）在模型层强制；机会、引用、owner、revision 等语义
资格在 `validator.py` 校验，不在 schema 里重复。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import (computed_field, BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

# ---------------------------------------------------------------------------
# 基础约定
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
POLICY_VERSION = "1.0.0"
THEORY_VERSION = "ecd-1.0"          # ECDL + RBT + CLT 的工程映射版本（§3）
UTC_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")

# §4.3 字段边界
MAX_STATEMENT_CHARS = 600
MAX_LIMIT_ITEMS = 5
MAX_LIMIT_CHARS = 300
# 单次模型输出批次上限（拆组由 child job 承担，§4.3）
MAX_CONCEPT_UPDATES_PER_BATCH = 3
MAX_NEW_CLAIMS_PER_BATCH = 8
# §8.3 单答案上限
MAX_ANSWER_BYTES = 32 * 1024


def utc_now_iso() -> str:
    """UTC ISO-8601（秒级截断）。逻辑顺序用 journal seq / observed_at。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonicalize_text(text: str) -> str:
    """原始 evidence 仅统一 CRLF→LF（§4.3），不做 NFKC 等语义转换。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _check_utc(v: str) -> str:
    if not isinstance(v, str) or not UTC_ISO_RE.match(v):
        raise ValueError("time must be UTC ISO-8601 like 2026-09-13T08:00:00Z")
    return v


class StrictModel(BaseModel):
    """公开协议模型基类：未知字段一律拒绝（§4.1）。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 词汇表：认知过程 / 知识类型 / 证据条件（§3）
# ---------------------------------------------------------------------------

class BloomProcess(str, Enum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"
    EVALUATE = "evaluate"
    CREATE = "create"


class KnowledgeType(str, Enum):
    FACTUAL = "factual"
    CONCEPTUAL = "conceptual"
    PROCEDURAL = "procedural"
    METACOGNITIVE = "metacognitive"


class EvidenceConditionKind(str, Enum):
    ORDINARY = "ordinary"
    TRANSFER = "transfer"
    RETENTION = "retention"
    SELF_CHECK = "self_check"


class EvidenceCondition(StrictModel):
    """证据条件对象（§4.3）：时间间隔、提示时序由服务端补充，不能自报。"""
    kind: EvidenceConditionKind
    basis_refs: list[str] = Field(default_factory=list, max_length=8)
    description: str = Field(default="", max_length=MAX_LIMIT_CHARS)
    # 服务端补充的条件事实（如间隔小时数）；模型输出不含此字段时为 None。
    server_facts: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 概念身份与范围（§2.1 / §5）
# ---------------------------------------------------------------------------

class ConceptRef(StrictModel):
    """LearnerConceptKey 的载体（§2.1）。student_id 不在此处——它由 journal
    归属（每用户一个 journal），concept_key 另含 concept_revision。"""
    graph_owner_namespace: str = Field(min_length=1, max_length=64)
    textbook_id: str = Field(min_length=1, max_length=128)
    file_ids: list[str] = Field(default_factory=list, max_length=32)
    concept_id: str = Field(min_length=1, max_length=192)
    concept_revision: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=192)

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_key(cls, data: Any) -> Any:
        """computed `key` 会随 model_dump 写进 journal 行与 API 响应；
        读回时它必须被忽略（由四元组重算），否则 StrictModel 按 extra
        拒绝——含 ConceptJudgment 的事务会在重放时被判损坏（G7 恢复
        演练发现：重启后评价判断被静默截尾或整本 journal 拒写）。"""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k != "key"}
        return data

    @computed_field
    @property
    def key(self) -> str:
        """服务端稳定编码，用于路径 token / 索引键（序列化进 API 响应）。"""
        raw = "\u0001".join([self.graph_owner_namespace, self.textbook_id,
                             self.concept_id, self.concept_revision])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class VolumeSelection(StrictModel):
    """已选教材卷（§5.1）：scope 只含实际选择的卷。"""
    textbook_id: str = Field(min_length=1, max_length=128)
    graph_owner_namespace: str = Field(min_length=1, max_length=64)
    topic_key: str = Field(min_length=1, max_length=192)
    file_ids: list[str] = Field(max_length=64)
    graph_revision: str = Field(min_length=1, max_length=128)


class GraphRevisionInfo(StrictModel):
    graph_owner_namespace: str = Field(min_length=1, max_length=64)
    textbook_id: str = Field(min_length=1, max_length=128)
    graph_revision: str = Field(min_length=1, max_length=128)


class EvaluationScope(StrictModel):
    """ScopeResolver 的输出（§4.2 / §5.1）。"""
    workspace_id: str = Field(min_length=1, max_length=96)
    scope_revision: str = Field(min_length=1, max_length=128)
    selected_volumes: list[VolumeSelection] = Field(max_length=64)
    # 上限只防序列化体积失控：公用教材库全选（60 卷 ≈ 8218 概念）是合法
    # 学习区配置，上限必须覆盖它，否则 scope 解析直接 500。
    allowed_concepts: list[ConceptRef] = Field(max_length=16384)
    graph_revisions: list[GraphRevisionInfo] = Field(default_factory=list,
                                                     max_length=64)
    unresolved_graph_count: int = Field(default=0, ge=0)

    def concept_by_ref(self, ref: str) -> ConceptRef | None:
        """pack 短引用 c1/c2/… 由 ContextPack 维护映射；这里按稳定 key 查。"""
        for c in self.allowed_concepts:
            if c.concept_id == ref or c.key == ref:
                return c
        return None


# ---------------------------------------------------------------------------
# 帮助事件（§7.3）
# ---------------------------------------------------------------------------

class AssistanceEventKind(str, Enum):
    HINT_REQUESTED = "hint_requested"
    ANSWER_REVEALED = "answer_revealed"
    WORKED_EXAMPLE = "worked_example"
    TEACHER_PROBE = "teacher_probe"
    PRIOR_EXPOSURE = "prior_exposure"      # 同任务族此前接触


class AssistanceEvent(StrictModel):
    kind: AssistanceEventKind
    at: str
    detail: str = Field(default="", max_length=600)
    client_entry: str = Field(default="", max_length=64)

    @field_validator("at")
    @classmethod
    def _utc(cls, v: str) -> str:
        return _check_utc(v)


# ---------------------------------------------------------------------------
# 证据与主张（§4.3）
# ---------------------------------------------------------------------------

class EvidenceSpan(StrictModel):
    """canonical_text 上的 Unicode code point 区间 [start, end)。"""
    ref: str = Field(min_length=1, max_length=48)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=2000)

    @field_validator("end")
    @classmethod
    def _order(cls, v: int, info) -> int:
        start = info.data.get("start")
        if isinstance(start, int) and v < start:
            raise ValueError("evidence span end must be >= start")
        return v


class ClaimStance(str, Enum):
    SUPPORTS = "supports"
    CHALLENGES = "challenges"
    INCONCLUSIVE = "inconclusive"


class ObservationClaim(StrictModel):
    """单次观察主张：学生“在何任务/条件下能做什么”。"""
    local_id: str = Field(min_length=1, max_length=48)
    concept_ref: str = Field(min_length=1, max_length=192)
    statement: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)
    stance: ClaimStance
    current_evidence: list[EvidenceSpan] = Field(default_factory=list,
                                                 max_length=12)
    opportunity_ref: str = Field(min_length=1, max_length=192)
    warrant: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    limits: list[str] = Field(default_factory=list,
                              max_length=MAX_LIMIT_ITEMS)
    cognitive_processes: list[BloomProcess] = Field(default_factory=list,
                                                    max_length=6)
    knowledge_types: list[KnowledgeType] = Field(default_factory=list,
                                                 max_length=4)
    evidence_conditions: list[EvidenceCondition] = Field(
        default_factory=list, max_length=4)
    alternatives: list[str] = Field(default_factory=list,
                                    max_length=MAX_LIMIT_ITEMS)

    @field_validator("limits", "alternatives")
    @classmethod
    def _limit_chars(cls, v: list[str]) -> list[str]:
        for item in v:
            if len(item) > MAX_LIMIT_CHARS:
                raise ValueError(f"limit/alternative item exceeds "
                                 f"{MAX_LIMIT_CHARS} chars")
        return v


class ClaimStatus(str, Enum):
    SUPPORTED = "supported"
    TENTATIVE = "tentative"
    CHALLENGED = "challenged"
    UNOBSERVED = "unobserved"


class ClaimView(StrictModel):
    """物化后的当前主张视图（持久化在 ConceptJudgment 内）。"""
    claim_id: str = Field(min_length=1, max_length=64)
    concept_ref: ConceptRef
    statement: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)
    status: ClaimStatus
    support_refs: list[str] = Field(default_factory=list, max_length=32)
    challenge_refs: list[str] = Field(default_factory=list, max_length=32)
    assistance_scope: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    limits: list[str] = Field(default_factory=list, max_length=MAX_LIMIT_ITEMS)
    created_by_observation: str = Field(default="", max_length=64)
    updated_at_observation: str = Field(default="", max_length=64)


class ChangeDirection(str, Enum):
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    MIXED = "mixed"
    STABLE = "stable"
    UNKNOWN = "unknown"


class ChangeComparison(str, Enum):
    COMPARABLE = "comparable"
    PARTIALLY_COMPARABLE = "partially_comparable"
    NOT_COMPARABLE = "not_comparable"
    NO_PRIOR = "no_prior"


class LearningChange(StrictModel):
    direction: ChangeDirection
    comparison: ChangeComparison
    prior_refs: list[str] = Field(default_factory=list, max_length=16)
    current_refs: list[str] = Field(default_factory=list, max_length=16)
    statement: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    alternative_explanations: list[str] = Field(
        default_factory=list, max_length=MAX_LIMIT_ITEMS)


class ProbeKind(str, Enum):
    EXPLAIN = "explain"
    PRACTICE = "practice"
    VARIANT = "variant"
    TRANSFER = "transfer"
    DELAYED_RECHECK = "delayed_recheck"
    SELF_CHECK = "self_check"


class AssistanceLevel(str, Enum):
    FULL_DEMO = "full_demo"
    KEY_HINTS = "key_hints"
    INDEPENDENT = "independent"


class NextProbe(StrictModel):
    kind: ProbeKind
    concept_ref: str = Field(min_length=1, max_length=192)
    target_claim: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    instruction: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)
    rationale: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    expected_observation: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    assistance: AssistanceLevel
    stop_condition: str = Field(default="", max_length=MAX_STATEMENT_CHARS)


# ---------------------------------------------------------------------------
# 概念判断（§4.3 / §4.4）
# ---------------------------------------------------------------------------

class ConceptEvalState(str, Enum):
    """类别不是等级阶梯；禁止映射 0/25/50/75/100（§4.4）。"""
    NOT_OBSERVED = "not_observed"
    EMERGING = "emerging"
    SUPPORTED_IN_SCOPE = "supported_in_scope"
    FRAGILE = "fragile"
    CONFLICTING = "conflicting"


CONCEPT_STATE_LABELS_ZH: dict[ConceptEvalState, str] = {
    ConceptEvalState.NOT_OBSERVED: "尚无学习证据",
    ConceptEvalState.EMERGING: "已有局部证据",
    ConceptEvalState.SUPPORTED_IN_SCOPE: "在这些条件下已有支持",
    ConceptEvalState.FRAGILE: "有明确待解决点",
    ConceptEvalState.CONFLICTING: "证据尚待核对",
}


class EvaluationStatus(str, Enum):
    """运行状态（§4.4），与 job state 分离。"""
    READY = "ready"
    PENDING = "pending"
    RECONCILING = "reconciling"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class ScopeStatus(str, Enum):
    CURRENT = "current"
    OUT_OF_SCOPE = "out_of_scope"
    SOURCE_REMOVED = "source_removed"
    NEEDS_MAPPING = "needs_mapping"


class ReviseClaim(StrictModel):
    claim_id: str = Field(min_length=1, max_length=64)
    new_statement: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)
    new_status: ClaimStatus
    support_refs: list[str] = Field(default_factory=list, max_length=32)
    challenge_refs: list[str] = Field(default_factory=list, max_length=32)
    limits: list[str] = Field(default_factory=list, max_length=MAX_LIMIT_ITEMS)
    reason: str = Field(default="", max_length=MAX_STATEMENT_CHARS)


class CloseClaim(StrictModel):
    claim_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)


class ConceptUpdate(StrictModel):
    """单概念 patch：active claims 必须在 retain/revise/close 中不重不漏。"""
    concept_ref: str = Field(min_length=1, max_length=192)
    base_judgment_id: str = Field(default="", max_length=64)
    retain_claim_ids: list[str] = Field(default_factory=list, max_length=64)
    add_claim_local_ids: list[str] = Field(default_factory=list, max_length=64)
    revise_claims: list[ReviseClaim] = Field(default_factory=list,
                                             max_length=64)
    close_claims: list[CloseClaim] = Field(default_factory=list, max_length=64)
    proposed_state: ConceptEvalState
    statement: str = Field(default="", max_length=MAX_STATEMENT_CHARS)
    change: LearningChange | None = None
    next_probe: NextProbe | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=32)


class LearnerInterpretation(StrictModel):
    """P3/P4 的 LLM 输出协议（§4.3）。"""
    applicable: bool = True
    abstain_reason: str | None = Field(default=None, max_length=300)
    observation_claims: list[ObservationClaim] = Field(
        default_factory=list, max_length=MAX_NEW_CLAIMS_PER_BATCH)
    concept_updates: list[ConceptUpdate] = Field(
        default_factory=list, max_length=MAX_CONCEPT_UPDATES_PER_BATCH)
    next_probe: NextProbe | None = None
    feedback: str = Field(default="", max_length=1200)
    # §7.3：帮助事件对本批表现的影响解释（服务端强制不低于已知强帮助）。
    assistance_interpretation: str = Field(default="", max_length=600)


class ConceptJudgment(StrictModel):
    """ConceptUpdate 经确定性校验后的物化结果（§4.3）。"""
    judgment_id: str = Field(min_length=1, max_length=64)
    base_judgment_id: str = Field(default="", max_length=64)
    concept_ref: ConceptRef
    workspace_id: str = Field(min_length=1, max_length=96)
    state: ConceptEvalState
    statement: str = Field(default="", max_length=1200)
    claims: list[ClaimView] = Field(default_factory=list, max_length=64)
    change: LearningChange | None = None
    next_probe: NextProbe | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=64)
    evidence_watermark: str = Field(min_length=1, max_length=96)
    policy_version: str = Field(min_length=1, max_length=32)
    theory_version: str = Field(min_length=1, max_length=32)
    prompt_ref: str = Field(min_length=1, max_length=96)
    created_at: str
    source_id: str = Field(default="", max_length=64)
    scope_revision: str = Field(min_length=1, max_length=128)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: str) -> str:
        return _check_utc(v)


# ---------------------------------------------------------------------------
# 任务材料（§4.5）：TaskSnapshot 与公开投影
# ---------------------------------------------------------------------------

class QuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"


class QuestionRef(StrictModel):
    question_id: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(ge=1, le=1_000_000)


class FrozenCriterion(StrictModel):
    """每题 1–12 条；权重有限正数（§4.5）。"""
    id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=600)
    weight: float = Field(gt=0, le=100)
    critical: bool = False
    allow_not_applicable: bool = False
    opportunity_refs: list[str] = Field(default_factory=list, max_length=8)
    accepted_solution_notes: str = Field(default="", max_length=1200)

    @field_validator("weight")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("weight must be finite")
        return v


class EvidenceOpportunity(StrictModel):
    id: str = Field(min_length=1, max_length=48)
    required_product: str = Field(min_length=1, max_length=600)
    permitted_claim: str = Field(default="", max_length=600)
    limits: str = Field(default="", max_length=600)


class TaskVerification(StrictModel):
    """C3 逐题审核结论（§7.4）。未返回的题 = unreviewed。"""
    status: Literal["passed", "revision_required", "rejected",
                    "unreviewed"] = "unreviewed"
    answer_check: Literal["valid", "invalid", "indeterminate"] = "indeterminate"
    grounding_check: Literal["supported", "unsupported", "indeterminate",
                             "not_required"] = "indeterminate"
    actual_required_processes: list[BloomProcess] = Field(default_factory=list,
                                                          max_length=6)
    knowledge_types: list[KnowledgeType] = Field(default_factory=list,
                                                 max_length=4)
    alignment: Literal["aligned", "weaker_than_target", "different_construct",
                       "indeterminate"] = "indeterminate"
    rubric_issue_codes: list[str] = Field(default_factory=list, max_length=12)
    reviewed_at: str = ""
    reviewer: str = Field(default="quiz_critic", max_length=64)


class TaskSnapshot(StrictModel):
    """M4 出题/注册的权威任务材料（§4.2）。question_revision + rubric_hash
    不变即冻结；题目/答案修订必须新 revision。"""
    question_id: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(ge=1, le=1_000_000)
    q_type: QuestionType
    stem: str = Field(min_length=1, max_length=4000)
    options: dict[str, str] = Field(default_factory=dict)
    answer: str = Field(min_length=1, max_length=4000)
    explanation: str = Field(default="", max_length=6000)
    equivalent_solutions: list[str] = Field(default_factory=list,
                                            max_length=8)
    rubric: list[FrozenCriterion] = Field(min_length=1, max_length=12)
    rubric_hash: str = Field(default="", max_length=96)
    verification: TaskVerification = Field(default_factory=TaskVerification)
    # 无工作区/未绑定概念的题目允许空：评价时 against scope 解析；空 →
    # evaluation=unavailable(workspace_required)，任务本身仍可判分反馈。
    concept_refs: list[ConceptRef] = Field(default_factory=list, max_length=3)
    task_family: str = Field(default="", max_length=192)
    novelty: str = Field(default="", max_length=600)
    evidence_opportunities: list[EvidenceOpportunity] = Field(
        default_factory=list, max_length=12)
    grounding_refs: list[str] = Field(default_factory=list, max_length=16)
    source_badge: str = Field(default="", max_length=192)
    origin_question_ref: QuestionRef | None = None
    frozen_at: str = ""
    workspace_id: str = Field(default="", max_length=96)

    @field_validator("frozen_at")
    @classmethod
    def _utc_opt(cls, v: str) -> str:
        return _check_utc(v) if v else v

    @model_validator(mode="after")
    def _compute_rubric_hash(self) -> "TaskSnapshot":
        """量规指纹由服务端从冻结内容计算（§7.4），调用方传入值只在不一致时
        拒绝，防止后台悄悄改量规而不换 revision。"""
        expected = "rh_" + hashlib.sha256(canonical_json(
            [c.model_dump() for c in self.rubric]).encode("utf-8")
        ).hexdigest()[:40]
        if not self.rubric_hash:
            self.rubric_hash = expected
        elif self.rubric_hash != expected:
            raise ValueError("rubric_hash does not match frozen rubric")
        return self

    def public_view(self, *, hints_available: bool = False) -> "QuestionPublic":
        """QuestionPublic 白名单（A07）：答案、完整量规、等价解、critic 输出
        在提交/主动揭晓前不下发。"""
        return QuestionPublic(
            question_id=self.question_id,
            question_revision=self.question_revision,
            q_type=self.q_type,
            stem=self.stem,
            options=dict(self.options),
            input_spec=InputSpec(
                kind="choice" if self.q_type == QuestionType.MULTIPLE_CHOICE
                else "text",
                max_bytes=MAX_ANSWER_BYTES,
                requires_explanation=(
                    self.q_type != QuestionType.MULTIPLE_CHOICE)),
            concept_refs=[c.model_dump() for c in self.concept_refs],
            source_badge=self.source_badge,
            hints_available=hints_available,
        )


class InputSpec(StrictModel):
    kind: Literal["choice", "text"]
    max_bytes: int = Field(ge=1, le=MAX_ANSWER_BYTES)
    requires_explanation: bool = False


class QuestionPublic(StrictModel):
    """答前公开投影（§4.5 / A07）。"""
    question_id: str
    question_revision: int
    q_type: QuestionType
    stem: str
    options: dict[str, str] = Field(default_factory=dict)
    input_spec: InputSpec
    concept_refs: list[dict[str, Any]] = Field(default_factory=list,
                                               max_length=3)
    source_badge: str = ""
    hints_available: bool = False


# ---------------------------------------------------------------------------
# 来源受理（§4.2 / §2.2）
# ---------------------------------------------------------------------------

class SourceKind(str, Enum):
    DIALOGUE = "dialogue"
    ASSESSMENT = "assessment"


class SourceProvenance(str, Enum):
    LIVE = "live"
    MIGRATION = "migration"
    DEMO_FIXTURE = "demo_fixture"


class EvidenceSpanOwnership(StrictModel):
    """§7.2 evidence_ownership：(message_id, source_revision, span) 单一属主。"""
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    owner: Literal["dialogue", "assessment"]


class SourceReceipt(StrictModel):
    """两种且仅两种学习观察来源的受理档案（§2.2 / §4.2）。"""
    source_id: str = Field(min_length=1, max_length=64)
    source_revision: int = Field(ge=1, le=1_000_000)
    kind: SourceKind
    observed_at: str
    workspace_id_at_observation: str = Field(default="", max_length=96)
    canonical_text: str = Field(min_length=1, max_length=2_000_000)
    assistance_events: list[AssistanceEvent] = Field(default_factory=list,
                                                     max_length=64)
    task_ref: QuestionRef | None = None
    attempt_id: str = Field(default="", max_length=64)
    spans: list[EvidenceSpanOwnership] = Field(default_factory=list,
                                               max_length=64)
    source_session_ref: str = Field(default="", max_length=96)
    message_ref: str = Field(default="", max_length=96)
    reply_message_ref: str = Field(default="", max_length=96)
    assessment_id: str = Field(default="", max_length=96)
    scope_revision: str = Field(default="", max_length=128)
    provenance: SourceProvenance = SourceProvenance.LIVE
    assistance_floor: AssistanceLevel = Field(default=AssistanceLevel.INDEPENDENT)
    created_at: str = ""

    @field_validator("observed_at", "created_at")
    @classmethod
    def _utc_fields(cls, v: str) -> str:
        return _check_utc(v) if v else v


# ---------------------------------------------------------------------------
# 判分（§4.5）
# ---------------------------------------------------------------------------

class GradingStatus(str, Enum):
    PENDING = "pending"
    GRADED = "graded"
    INDETERMINATE = "indeterminate"
    UNVERIFIED = "unverified"


class Verdict(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    WRONG = "wrong"


class CriterionResultKind(str, Enum):
    MET = "met"
    PARTIAL = "partial"
    NOT_MET = "not_met"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"


class CriterionResult(StrictModel):
    criterion_id: str = Field(min_length=1, max_length=64)
    result: CriterionResultKind
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    comment: str = Field(default="", max_length=600)


class FirstError(StrictModel):
    location_ref: str | None = None
    description: str = Field(min_length=1, max_length=600)
    preceding_correct_refs: list[str] = Field(default_factory=list,
                                              max_length=12)


class Hypothesis(StrictModel):
    statement: str = Field(min_length=1, max_length=600)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    distinguishing_probe: str = Field(default="", max_length=600)


class TaskFeedback(StrictModel):
    strengths: list[str] = Field(default_factory=list, max_length=6)
    improvement: str = Field(default="", max_length=600)
    next_step: str = Field(default="", max_length=600)


class TaskResult(StrictModel):
    """本题成绩：仅由 frozen weights 服务端本地计算；score=null 贯穿
    API/UI/CAT（A04）。模型输出 schema 不含 score。"""
    question_ref: QuestionRef
    grading_status: GradingStatus
    verdict: Verdict | None = None
    task_score: float | None = None
    criterion_results: list[CriterionResult] = Field(default_factory=list,
                                                     max_length=12)
    first_error: FirstError | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=8)
    feedback: TaskFeedback | None = None
    answer_fingerprint: str = Field(default="", max_length=96)
    computed_at: str = ""
    rubric_hash: str = Field(default="", max_length=96)

    @field_validator("computed_at")
    @classmethod
    def _utc_opt(cls, v: str) -> str:
        return _check_utc(v) if v else v


class ContinuationAction(StrictModel):
    """P3 continuation（§9.5）。"""
    action: Literal["continue", "probe", "finish"]
    reason: str = Field(default="", max_length=600)
    remaining_claims: list[str] = Field(default_factory=list, max_length=8)
    next_probe: NextProbe | None = None


class AssessmentInterpretationOutput(StrictModel):
    """P3 完整输出（§9.5）。"""
    criterion_results: list[CriterionResult] = Field(default_factory=list,
                                                     max_length=12)
    first_error: FirstError | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=8)
    rubric_issues: list[str] = Field(default_factory=list, max_length=12)
    task_feedback: TaskFeedback | None = None
    learner: LearnerInterpretation
    continuation: ContinuationAction | None = None


# ---------------------------------------------------------------------------
# 复核（§9.7）
# ---------------------------------------------------------------------------

class ReviewDecisionKind(str, Enum):
    UPHOLD = "uphold"
    REVISE = "revise"
    INVALIDATE = "invalidate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ReviewDecisionOutput(StrictModel):
    """P5 输出；scope/批量撤销/生命周期操作由服务端决定。"""
    decision: ReviewDecisionKind
    issue_kind: Literal["question_defect", "rubric_defect", "misinterpretation",
                        "citation_error", "concept_binding", "comparison_error",
                        "insufficient_evidence", "other"] = "other"
    reason: str = Field(min_length=1, max_length=1200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    affected_claim_ids: list[str] = Field(default_factory=list, max_length=64)
    replacement_interpretation: LearnerInterpretation | None = None
    replacement_criterion_results: list[CriterionResult] = Field(
        default_factory=list, max_length=12)
    question_revision_issue: QuestionRef | None = None


class ReviewRequestRecord(StrictModel):
    review_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=64)
    interpretation_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1200)
    issue_kind: str = Field(default="other", max_length=64)
    requested_at: str
    requested_revision: int = Field(ge=1, le=1_000_000)
    status: Literal["active", "resolved", "dismissed"] = "active"
    # R06：复核历史要能展示真实决定（"复核中"不是终态文案）。insufficient
    # 保持 active（待确定）但记录决定；空串 = 尚未判定。
    decided_kind: str = Field(default="", max_length=64)
    decided_at: str = Field(default="", max_length=40)
    resolution_note: str = Field(default="", max_length=600)

    @field_validator("requested_at")
    @classmethod
    def _utc(cls, v: str) -> str:
        return _check_utc(v)

    @field_validator("decided_at")
    @classmethod
    def _utc_opt(cls, v: str) -> str:
        return _check_utc(v) if v else v


# ---------------------------------------------------------------------------
# 综合（§9.11）
# ---------------------------------------------------------------------------

class ScopeType(str, Enum):
    CONCEPT = "concept"
    SESSION = "session"
    WORKSPACE = "workspace"


class ThemeSummary(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=1200)
    claim_refs: list[str] = Field(default_factory=list, max_length=32)


class SynthesisChange(StrictModel):
    statement: str = Field(min_length=1, max_length=600)
    prior_refs: list[str] = Field(default_factory=list, max_length=16)
    current_refs: list[str] = Field(default_factory=list, max_length=16)
    comparison: ChangeComparison


class OpenQuestion(StrictModel):
    statement: str = Field(min_length=1, max_length=600)
    claim_refs: list[str] = Field(default_factory=list, max_length=16)


class ScopeSynthesisOutput(StrictModel):
    """P9 输出。coverage counts、水位、生成时间由代码加。"""
    scope_type: ScopeType
    statement: str = Field(min_length=1, max_length=2000)
    claim_refs: list[str] = Field(default_factory=list, max_length=64)
    theme_summaries: list[ThemeSummary] = Field(default_factory=list,
                                                max_length=16)
    changes: list[SynthesisChange] = Field(default_factory=list, max_length=16)
    open_questions: list[OpenQuestion] = Field(default_factory=list,
                                               max_length=16)
    priority_probe: NextProbe | None = None
    limits: list[str] = Field(default_factory=list, max_length=8)


class ScopeSynthesis(StrictModel):
    """synthesis_committed 的持久化形态：LLM 内容 + 服务端水位。"""
    synthesis_id: str = Field(min_length=1, max_length=64)
    scope_type: ScopeType
    workspace_id: str = Field(min_length=1, max_length=96)
    concept_ref: ConceptRef | None = None
    source_session_ref: str = Field(default="", max_length=96)
    statement: str = Field(min_length=1, max_length=2000)
    claim_refs: list[str] = Field(default_factory=list, max_length=64)
    theme_summaries: list[ThemeSummary] = Field(default_factory=list,
                                                max_length=16)
    changes: list[SynthesisChange] = Field(default_factory=list, max_length=16)
    open_questions: list[OpenQuestion] = Field(default_factory=list,
                                               max_length=16)
    priority_probe: NextProbe | None = None
    limits: list[str] = Field(default_factory=list, max_length=8)
    scope_revision: str = Field(min_length=1, max_length=128)
    evidence_watermark: str = Field(min_length=1, max_length=96)
    pending_source_count: int = Field(default=0, ge=0)
    # R08：依赖失效后正文隐藏（引用失效即隐藏正文，不删历史行）。
    revoked: bool = False
    generated_at: str

    @field_validator("generated_at")
    @classmethod
    def _utc(cls, v: str) -> str:
        return _check_utc(v)


# ---------------------------------------------------------------------------
# 作业（§10.3）
# ---------------------------------------------------------------------------

class JobKind(str, Enum):
    ASSESSMENT_EVALUATION = "assessment_evaluation"
    DIALOGUE_EVALUATION = "dialogue_evaluation"
    REVIEW = "review"
    SYNTHESIS_CONCEPT = "synthesis_concept"
    SYNTHESIS_SESSION = "synthesis_session"
    SYNTHESIS_WORKSPACE = "synthesis_workspace"
    CLT_REVIEW = "clt_review"
    BACKFILL = "backfill"
    CHILD_GROUP = "child_group"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    ABSTAINED = "abstained"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobPriority(int, Enum):
    """数值越小越先执行（§10.3 优先级）。"""
    AWAITING_FEEDBACK = 0
    CURRENT_DIALOGUE = 1
    REVIEW = 2
    SYNTHESIS_CONCEPT = 3
    SYNTHESIS_SESSION_WORKSPACE = 4
    CLT_SAMPLE = 5
    BACKFILL = 6


class EvaluationJob(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)
    kind: JobKind
    source_id: str = Field(default="", max_length=64)
    source_revision: int = Field(default=1, ge=1, le=1_000_000)
    workspace_id: str = Field(default="", max_length=96)
    scope_revision: str = Field(default="", max_length=128)
    state: JobState = JobState.QUEUED
    priority: int = Field(default=JobPriority.CURRENT_DIALOGUE.value, ge=0,
                          le=100)
    parent_job_id: str = Field(default="", max_length=64)
    lease_token: str = Field(default="", max_length=96)
    lease_expires_at: str = Field(default="", max_length=40)
    attempt_count: int = Field(default=0, ge=0, le=100)
    transport_attempts: int = Field(default=0, ge=0, le=64)
    repair_used: bool = False
    deadline_seconds: int = Field(default=120, ge=1, le=600)
    wall_deadline_at: str = Field(default="", max_length=40)
    error_code: str = Field(default="", max_length=64)
    input_hash: str = Field(default="", max_length=96)
    prompt_binding: str = Field(default="", max_length=192)
    created_at: str = ""
    updated_at: str = ""

    @field_validator("created_at", "updated_at", "lease_expires_at",
                     "wall_deadline_at")
    @classmethod
    def _utc_opt(cls, v: str) -> str:
        return _check_utc(v) if v else v


# ---------------------------------------------------------------------------
# ContextPack（§8）
# ---------------------------------------------------------------------------

class PackManifest(StrictModel):
    included_refs: list[str] = Field(default_factory=list, max_length=256)
    omitted_refs: list[str] = Field(default_factory=list, max_length=256)
    truncations: list[str] = Field(default_factory=list, max_length=32)
    input_hash: str = Field(min_length=8, max_length=96)
    schema_version: int = SCHEMA_VERSION
    prompt_ref: str = Field(default="", max_length=192)
    evidence_watermark: str = Field(default="", max_length=96)


class PackConceptRefEntry(StrictModel):
    """pack 内短引用（c1/c2/…）到 ConceptRef 的映射。"""
    short_ref: str = Field(min_length=1, max_length=16)
    concept: ConceptRef


class EvaluationContextPack(StrictModel):
    """§8.1 输入分层。内容序列化为 user message 的 JSON；system 只从
    版本化 registry 装配（§8.1）。"""
    pack_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(default="", max_length=64)
    source_id: str = Field(min_length=1, max_length=64)
    prompt_binding: str = Field(min_length=1, max_length=192)
    scenarios: list[str] = Field(default_factory=list, max_length=8)
    output_language: str = Field(default="zh", max_length=8)
    allowlist: list[PackConceptRefEntry] = Field(default_factory=list,
                                                 max_length=12)
    current_student_evidence: dict[str, Any] = Field(default_factory=dict)
    task: dict[str, Any] = Field(default_factory=dict)
    assistance_before_response: list[AssistanceEvent] = Field(
        default_factory=list, max_length=64)
    textbook_reference: dict[str, Any] = Field(default_factory=dict)
    prior_same_concept: dict[str, Any] = Field(default_factory=dict)
    related_concepts: dict[str, Any] = Field(default_factory=dict)
    session_context: dict[str, Any] = Field(default_factory=dict)
    workspace_context: dict[str, Any] = Field(default_factory=dict)
    learner_preferences: dict[str, Any] = Field(default_factory=dict)
    review_context: dict[str, Any] = Field(default_factory=dict)
    manifest: PackManifest


# ---------------------------------------------------------------------------
# 事务 journal（§6.4）
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def compute_checksum(envelope: dict[str, Any]) -> str:
    payload = {k: v for k, v in envelope.items() if k != "checksum"}
    return "sha256:" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()


class _OpBase(StrictModel):
    """操作 discriminated union 基类；禁止任意动态 op（§6.4）。"""


class OpQuestionRegistered(_OpBase):
    op: Literal["question_registered"] = "question_registered"
    task: TaskSnapshot


class OpAssistanceRecorded(_OpBase):
    op: Literal["assistance_recorded"] = "assistance_recorded"
    source_id: str = Field(default="", max_length=64)
    question_ref: QuestionRef | None = None
    source_session_ref: str = Field(default="", max_length=96)
    message_ref: str = Field(default="", max_length=96)
    assistance: AssistanceEvent


class OpSourceRegistered(_OpBase):
    op: Literal["source_registered"] = "source_registered"
    source: SourceReceipt
    job_id: str = Field(default="", max_length=64)


class OpSourceRevised(_OpBase):
    op: Literal["source_revised"] = "source_revised"
    source_id: str = Field(min_length=1, max_length=64)
    source_revision: int = Field(ge=2, le=1_000_000)
    canonical_text: str = Field(min_length=1, max_length=2_000_000)
    reason: str = Field(default="", max_length=300)


class OpJobRequested(_OpBase):
    op: Literal["job_requested"] = "job_requested"
    job: EvaluationJob


class OpJobLeased(_OpBase):
    op: Literal["job_leased"] = "job_leased"
    job_id: str = Field(min_length=1, max_length=64)
    lease_token: str = Field(min_length=1, max_length=96)
    lease_expires_at: str
    worker: str = Field(default="", max_length=96)

    @field_validator("lease_expires_at")
    @classmethod
    def _utc(cls, v: str) -> str:
        return _check_utc(v)


class OpJobInputPrepared(_OpBase):
    op: Literal["job_input_prepared"] = "job_input_prepared"
    job_id: str = Field(min_length=1, max_length=64)
    input_hash: str = Field(min_length=8, max_length=96)
    prompt_binding: str = Field(default="", max_length=192)
    generation: str = Field(min_length=1, max_length=64)
    # R16：最小可复现输入清单（引用 ID/裁剪记录，不含正文副本）——
    # 满足"复盘当时模型读了哪些依据"，敏感原文不重复落账
    included_refs: list[str] = Field(default_factory=list, max_length=32)
    truncations: list[str] = Field(default_factory=list, max_length=16)


class OpJobSubresultStaged(_OpBase):
    op: Literal["job_subresult_staged"] = "job_subresult_staged"
    job_id: str = Field(min_length=1, max_length=64)
    group_index: int = Field(ge=0, le=64)
    subresult: dict[str, Any]


class OpJobFailed(_OpBase):
    op: Literal["job_failed"] = "job_failed"
    job_id: str = Field(min_length=1, max_length=64)
    error_code: str = Field(min_length=1, max_length=64)
    retryable: bool = False
    attempt_count: int = Field(default=1, ge=1, le=100)
    transport_attempts: int = Field(default=1, ge=0, le=64)
    retry_after_seconds: int = Field(default=0, ge=0, le=3600)
    # R17：绝对重试时刻（fail 事务冻结；重放/重启不再按当前时间重算，
    # 旧 journal 无此字段时才回退 retry_after_seconds 推导）
    retry_not_before: str = Field(default="", max_length=40)

    @field_validator("retry_not_before")
    @classmethod
    def _utc_opt(cls, v: str) -> str:
        return _check_utc(v) if v else v


class OpJobCancelled(_OpBase):
    op: Literal["job_cancelled"] = "job_cancelled"
    job_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=300)


class OpResultCommitted(_OpBase):
    op: Literal["result_committed"] = "result_committed"
    job_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=64)
    source_revision: int = Field(ge=1, le=1_000_000)
    scope_revision: str = Field(min_length=1, max_length=128)
    task_result: TaskResult | None = None
    interpretation_id: str = Field(default="", max_length=64)
    interpretation: LearnerInterpretation | None = None
    judgments: list[ConceptJudgment] = Field(default_factory=list,
                                             max_length=8)
    continuation: ContinuationAction | None = None
    abstained: bool = False
    # R10：local_id → {obs_id, concept_key, source_id} 稳定映射——claim 到
    # 真实概念/来源的精确寻址不依赖 pack 短引用。
    observation_map: list[dict[str, str]] = Field(default_factory=list,
                                                  max_length=32)
    outbox: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


class OpReviewRequested(_OpBase):
    op: Literal["review_requested"] = "review_requested"
    review: ReviewRequestRecord
    job_id: str = Field(default="", max_length=64)


class OpReviewResolved(_OpBase):
    op: Literal["review_resolved"] = "review_resolved"
    review_id: str = Field(min_length=1, max_length=64)
    decision: ReviewDecisionOutput
    replacement_interpretation_id: str = Field(default="", max_length=64)
    job_id: str = Field(default="", max_length=64)
    outbox: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


class OpReviewDismissed(_OpBase):
    """复核不再可执行（解释/来源已删除等）——受理侧关闭并允许新异议。"""
    op: Literal["review_dismissed"] = "review_dismissed"
    review_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=300)
    job_id: str = Field(default="", max_length=64)


class OpInterpretationRevoked(_OpBase):
    op: Literal["interpretation_revoked"] = "interpretation_revoked"
    interpretation_id: str = Field(min_length=1, max_length=64)
    outbox: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    source_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=600)
    affected_judgment_ids: list[str] = Field(default_factory=list,
                                             max_length=64)
    # R08：引用了受影响主张/判断的同步综合一并隐藏正文（不删历史）。
    affected_synthesis_ids: list[str] = Field(default_factory=list,
                                              max_length=64)
    question_revision_issue: QuestionRef | None = None


class OpSynthesisCommitted(_OpBase):
    op: Literal["synthesis_committed"] = "synthesis_committed"
    synthesis: ScopeSynthesis
    replaces_synthesis_id: str = Field(default="", max_length=64)
    # R01/R08：综合与 job 终态同一事务（此前只写 ScopeSynthesis，job 永
    # 远 RUNNING 直到 lease 过期再被认领，形成无限重执行）
    job_id: str = Field(default="", max_length=64)
    # R08：递归失效后的确定性重放——依据仍存活的旧判断恢复为当前判断
    # （全部存活原样恢复；部分存活降级 fragile；零存活不恢复）
    restored_judgments: list[ConceptJudgment] = Field(default_factory=list,
                                                      max_length=32)


class OpScopeChanged(_OpBase):
    op: Literal["scope_changed"] = "scope_changed"
    workspace_id: str = Field(min_length=1, max_length=96)
    scope_revision: str = Field(min_length=1, max_length=128)
    change: str = Field(default="", max_length=600)
    # 与 EvaluationScope.allowed_concepts 同一概念全集，上限同步（见其注释）。
    affected_concept_keys: list[str] = Field(default_factory=list,
                                             max_length=16384)


class OpSourceArchived(_OpBase):
    op: Literal["source_archived"] = "source_archived"
    source_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=300)


class OpSourceRestored(_OpBase):
    op: Literal["source_restored"] = "source_restored"
    source_id: str = Field(min_length=1, max_length=64)


class OpConsumerAck(_OpBase):
    op: Literal["consumer_ack"] = "consumer_ack"
    event_id: str = Field(min_length=1, max_length=96)
    consumer: str = Field(min_length=1, max_length=64)
    consumer_version: int = Field(default=1, ge=1, le=1000)


class OpAssessmentSessionChanged(_OpBase):
    op: Literal["assessment_session_changed"] = "assessment_session_changed"
    assessment_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(default="", max_length=96)
    change: str = Field(default="", max_length=300)
    detail: dict[str, Any] = Field(default_factory=dict)


JournalOperation = Annotated[
    Union[
        OpQuestionRegistered, OpAssistanceRecorded, OpSourceRegistered,
        OpSourceRevised, OpJobRequested, OpJobLeased, OpJobInputPrepared,
        OpJobSubresultStaged, OpJobFailed, OpJobCancelled, OpResultCommitted,
        OpReviewRequested, OpReviewResolved, OpReviewDismissed,
        OpInterpretationRevoked,
        OpSynthesisCommitted, OpScopeChanged, OpSourceArchived,
        OpSourceRestored, OpConsumerAck, OpAssessmentSessionChanged,
    ],
    Field(discriminator="op"),
]

JOURNAL_OPERATION_TYPES: dict[str, type[_OpBase]] = {
    "question_registered": OpQuestionRegistered,
    "assistance_recorded": OpAssistanceRecorded,
    "source_registered": OpSourceRegistered,
    "source_revised": OpSourceRevised,
    "job_requested": OpJobRequested,
    "job_leased": OpJobLeased,
    "job_input_prepared": OpJobInputPrepared,
    "job_subresult_staged": OpJobSubresultStaged,
    "job_failed": OpJobFailed,
    "job_cancelled": OpJobCancelled,
    "result_committed": OpResultCommitted,
    "review_requested": OpReviewRequested,
    "review_resolved": OpReviewResolved,
    "review_dismissed": OpReviewDismissed,
    "interpretation_revoked": OpInterpretationRevoked,
    "synthesis_committed": OpSynthesisCommitted,
    "scope_changed": OpScopeChanged,
    "source_archived": OpSourceArchived,
    "source_restored": OpSourceRestored,
    "consumer_ack": OpConsumerAck,
    "assessment_session_changed": OpAssessmentSessionChanged,
}


class JournalTransaction(StrictModel):
    """每行一个完整事务（§6.4）。checksum 覆盖除自身外的 canonical envelope。"""
    schema_version: int = SCHEMA_VERSION
    generation: str = Field(min_length=4, max_length=64)
    seq: int = Field(ge=1, le=10**12)
    transaction_id: str = Field(min_length=4, max_length=64)
    created_at: str
    operations: list[JournalOperation] = Field(min_length=1, max_length=32)
    checksum: str = Field(default="", max_length=96)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: str) -> str:
        return _check_utc(v)

    def resolved_checksum(self) -> str:
        return compute_checksum(self.model_dump(exclude={"checksum"}))

    def verify_checksum(self) -> bool:
        return bool(self.checksum) and self.checksum == self.resolved_checksum()


# ---------------------------------------------------------------------------
# 读取侧投影（§12 / §11.3）
# ---------------------------------------------------------------------------

class ConceptEvaluationView(StrictModel):
    """概念当前投影：未观察节点由 scope 左连接生成（§11.2）。"""
    concept_ref: ConceptRef
    state: ConceptEvalState | None
    evaluation_status: EvaluationStatus
    judgment_id: str = Field(default="", max_length=64)
    statement: str = Field(default="", max_length=1200)
    claims: list[ClaimView] = Field(default_factory=list, max_length=64)
    change: LearningChange | None = None
    next_probe: NextProbe | None = None
    scope_status: ScopeStatus = ScopeStatus.CURRENT
    evidence_count: int = Field(default=0, ge=0)
    # R10：来源时间取 observed_at（表现发生时刻），评价完成时间单列。
    last_observed_at: str = Field(default="", max_length=40)
    evaluated_at: str = Field(default="", max_length=40)
    updated_at: str = Field(default="", max_length=40)


class CoverageCounts(StrictModel):
    observed_concepts: int = Field(ge=0)
    not_observed_concepts: int = Field(ge=0)
    by_state: dict[str, int] = Field(default_factory=dict)
    reconciling_concepts: int = Field(default=0, ge=0)


class WorkspaceEvaluationSummary(StrictModel):
    workspace_id: str
    scope_revision: str
    evaluation_status: EvaluationStatus
    evaluated_through: str = Field(default="", max_length=96)
    pending_source_count: int = Field(default=0, ge=0)
    allowed_concept_count: int = Field(default=0, ge=0)
    coverage: CoverageCounts
    synthesis: ScopeSynthesis | None = None
    workspace_name: str = Field(default="", max_length=192)
    updated_at: str = Field(default="", max_length=40)


class SourceTimelineItem(StrictModel):
    source_id: str
    kind: SourceKind
    observed_at: str
    scope_status: ScopeStatus
    availability: Literal["available", "archived", "deleted"] = "available"
    concept_refs: list[str] = Field(default_factory=list, max_length=12)
    summary: str = Field(default="", max_length=600)
    source_session_ref: str = Field(default="", max_length=96)
    interpretation_id: str = Field(default="", max_length=64)
    review_status: str = Field(default="", max_length=32)


# ---------------------------------------------------------------------------
# C8 教学设计审核（§9.10）
# ---------------------------------------------------------------------------

class CLTDimension(str, Enum):
    GUIDANCE_FIT = "guidance_fit"
    ELEMENT_INTERACTIVITY_CONTROL = "element_interactivity_control"
    SPLIT_ATTENTION_RISK = "split_attention_risk"
    REDUNDANCY_RISK = "redundancy_risk"
    TRANSIENCE_SEGMENTATION = "transience_segmentation"
    FADING_READINESS = "fading_readiness"


class CLTItemVerdict(str, Enum):
    PASS = "pass"
    CONCERN = "concern"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"


class CLTReviewItem(StrictModel):
    dimension: CLTDimension
    verdict: CLTItemVerdict
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)
    reason: str = Field(default="", max_length=600)


class CLTReviewPriorityAdjustment(StrictModel):
    description: str = Field(min_length=1, max_length=600)
    expected_observation: str = Field(default="", max_length=600)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)


class TeachingDesignReview(StrictModel):
    """P8 输出：只审核教学设计，不评价学生能力（§9.10）。"""
    items: list[CLTReviewItem] = Field(default_factory=list, max_length=6)
    priority_adjustment: CLTReviewPriorityAdjustment | None = None
    limits: list[str] = Field(default_factory=list, max_length=8)


# ---------------------------------------------------------------------------
# P1 任务蓝图 / P2 审核输出（§9.3 / §9.4）
# ---------------------------------------------------------------------------

class BlueprintNovelty(StrictModel):
    kind: Literal["same_form", "changed_context", "changed_representation",
                  "new_structure", "indeterminate"] = "indeterminate"
    baseline_refs: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=600)


class EvidenceOpportunityDraft(StrictModel):
    id: str = Field(min_length=1, max_length=48)
    required_product: str = Field(min_length=1, max_length=600)
    permitted_claim: str = Field(default="", max_length=600)
    limits: str = Field(default="", max_length=600)


class TaskBlueprintItem(StrictModel):
    local_question_id: str = Field(min_length=1, max_length=48)
    target_concept_refs: list[str] = Field(min_length=1, max_length=3)
    target_claims: list[str] = Field(min_length=1, max_length=8)
    intended_processes: list[BloomProcess] = Field(min_length=1, max_length=6)
    knowledge_types: list[KnowledgeType] = Field(default_factory=list,
                                                 max_length=4)
    q_type: QuestionType
    difficulty_design: str = Field(default="", max_length=300)
    task_family: str = Field(default="", max_length=192)
    novelty: BlueprintNovelty | None = None
    assistance_plan: str = Field(default="", max_length=600)
    evidence_opportunities: list[EvidenceOpportunityDraft] = Field(
        default_factory=list, max_length=12)
    rubric_draft: list[FrozenCriterion] = Field(default_factory=list,
                                                max_length=12)
    grounding_refs: list[str] = Field(default_factory=list, max_length=16)
    construction_brief: str = Field(default="", max_length=2000)


class TaskBlueprint(StrictModel):
    items: list[TaskBlueprintItem] = Field(min_length=1, max_length=20)


class OpportunityCheck(StrictModel):
    opportunity_ref: str = Field(min_length=1, max_length=48)
    observable: bool = False
    limits: str = Field(default="", max_length=600)


class RubricIssue(StrictModel):
    criterion_ref: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=600)


class QuestionAudit(StrictModel):
    """P2 单题审核：每题恰好一次，遗漏即 unreviewed（A06）。"""
    question_ref: str = Field(min_length=1, max_length=96)
    answer_check: Literal["valid", "invalid", "indeterminate"]
    grounding_check: Literal["supported", "unsupported", "indeterminate",
                             "not_required"]
    actual_required_processes: list[BloomProcess] = Field(default_factory=list,
                                                          max_length=6)
    knowledge_types: list[KnowledgeType] = Field(default_factory=list,
                                                 max_length=4)
    alignment: Literal["aligned", "weaker_than_target", "different_construct",
                       "indeterminate"]
    opportunity_checks: list[OpportunityCheck] = Field(default_factory=list,
                                                       max_length=12)
    rubric_issues: list[RubricIssue] = Field(default_factory=list, max_length=12)
    brief_basis: str = Field(default="", max_length=600)
    grounding_refs: list[str] = Field(default_factory=list, max_length=16)
    recommended_revision: str = Field(default="", max_length=600)
    proposed_status: Literal["passed", "revision_required", "rejected",
                             "unreviewed"]


class QuestionAuditBatch(StrictModel):
    items: list[QuestionAudit] = Field(min_length=1, max_length=20)


# ---------------------------------------------------------------------------
# P6 教学决策（§9.8）
# ---------------------------------------------------------------------------

class TeachingAction(str, Enum):
    EXPLAIN = "explain"
    PRACTICE = "practice"
    QUIZ = "quiz"
    REVIEW = "review"
    CLARIFY = "clarify"
    SUMMARIZE = "summarize"


class TeachingDecisionOutput(StrictModel):
    action: TeachingAction
    target_concept_ref: str = Field(default="", max_length=192)
    assistance: AssistanceLevel = Field(default=AssistanceLevel.KEY_HINTS)
    rationale: str = Field(default="", max_length=600)
    evaluation_refs: list[str] = Field(default_factory=list, max_length=16)
    expected_observation: str = Field(default="", max_length=600)
    stop_condition: str = Field(default="", max_length=600)
    presentation_hints: list[str] = Field(default_factory=list, max_length=8)
