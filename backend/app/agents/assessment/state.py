"""Assessment state: read-only input projection + result/goal types.

The assessment engine's ONLY view of the student. A flat, plain-data
projection (plain str/float/list, NO reference to student_model types)
assembled by the caller from live student_model + teaching_engine state.
Keeping it plain keeps this package import-clean (it never imports
student_model at runtime), same contract as teaching_engine.state.

Every field defaults to empty so a caller that only knows the concept name
still gets a valid context (the evaluator degrades to a neutral grade).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScoreLevel(str, Enum):
    """Three-level mastery of a single answer (replaces binary right/wrong).

    A chatbot says right or wrong. A tutor says "right idea, missed a step"
    (PARTIAL), a qualitatively different signal that REMEDIATION can act on.
    """
    NONE = "none"        # 0.0 -- no usable understanding
    PARTIAL = "partial"  # 0.5 -- right direction, missing step / minor slip
    FULL = "full"        # 1.0 -- complete mastery of this question

    @classmethod
    def from_score(cls, score: float) -> "ScoreLevel":
        if score >= 0.75:
            return cls.FULL
        if score >= 0.25:
            return cls.PARTIAL
        return cls.NONE

    @property
    def score(self) -> float:
        return {"none": 0.0, "partial": 0.5, "full": 1.0}[self.value]


# Verdict values kept backward-compatible with the existing /quiz/grade SSE
# contract ("correct" | "wrong"). M4 adds "partial"; older frontends ignore it.
VERDICT_CORRECT = "correct"
VERDICT_PARTIAL = "partial"
VERDICT_WRONG = "wrong"
VERDICT_UNKNOWN = "unknown"


@dataclass
class AssessmentContext:
    """One assessment target's worth of read-only context.

    Callers assemble this from live teaching_engine state:
      base_difficulty  <- teaching_engine 难度引擎（中性种子=2，band 兜底）
      recent_outcomes  <- teaching_engine teaching_log entries
    G4：current/target 数值掌握字段已删（统一评价语义化，§13）。
    """
    concept: str = ""
    subject: str = ""
    grade: str = "本科"
    skill_id: str = ""
    base_difficulty: int = 2
    recent_outcomes: list[str] = field(default_factory=list)
    # 统一 Quiz Grounding（plan.md §5.1）：additive 字段，plain data only。
    # grounding_sources 只放序列化后的 QuizSourceRef plain dict，保持本
    # 包 import-clean —— Assessment 不依赖 KnowledgeStore，证据由 API 层
    # 经 QuizGroundingProvider 解析后投影进来。to_dict/from_dict 全支持，
    # 保证 CAT session 持久化和重启恢复不丢证据 scope。
    grounding_required: bool = False
    grounding_mode: str = "generic"          # textbook | generic
    grounding_tier: str = "not_found"        # found | partial | not_found
    grounding_query: str = ""
    grounding_sources: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "subject": self.subject,
            "grade": self.grade,
            "skill_id": self.skill_id,
            "base_difficulty": self.base_difficulty,
            "recent_outcomes": list(self.recent_outcomes),
            "grounding_required": self.grounding_required,
            "grounding_mode": self.grounding_mode,
            "grounding_tier": self.grounding_tier,
            "grounding_query": self.grounding_query,
            "grounding_sources": [dict(s) for s in self.grounding_sources],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AssessmentContext":
        d = d or {}
        return cls(
            concept=str(d.get("concept", "") or ""),
            subject=str(d.get("subject", "") or ""),
            grade=str(d.get("grade", "本科") or "本科"),
            skill_id=str(d.get("skill_id", "") or ""),
            base_difficulty=int(d.get("base_difficulty", 2)),
            recent_outcomes=list(d.get("recent_outcomes", []) or []),
            grounding_required=bool(d.get("grounding_required", False)),
            grounding_mode=str(d.get("grounding_mode", "generic") or "generic"),
            grounding_tier=str(d.get("grounding_tier", "not_found") or "not_found"),
            grounding_query=str(d.get("grounding_query", "") or ""),
            grounding_sources=[dict(s) for s in (d.get("grounding_sources") or [])
                               if isinstance(s, dict)],
        )


@dataclass
class AssessmentGoal:
    """What one assessment is trying to accomplish.

    The bridge between the Teaching Engine's advisory next_check ("test
    opening direction at difficulty 3") and a concrete Question.
    """
    concept: str = ""
    purpose: str = "check"   # check | diagnose | practice | adaptive
    difficulty: int = 0      # 0 = derive from context (M3 difficulty engine)
    count: int = 1           # 1 for single checks; upper bound for adaptive
    illustration_request: str = "auto"
    q_type: str = ""         # "" = auto-select (MC for fast checks)
    assesses: list[str] = field(default_factory=list)   # sub-abilities to probe
    forbidden: list[str] = field(default_factory=list)  # methods disallowed
    # 布鲁姆认知层级焦点（""/"auto" = 由出题 LLM 结合认知档案综合判断——默认
    # 且推荐；显式层级只是"偏好聚焦"，不是硬约束）。带默认值：旧会话文件经
    # AssessmentGoal(**g) 重建保持兼容。
    bloom_focus: str = ""
    # 本次测评已出过的题干（CAT 去重：同一测评内禁止重复出题；也避免
    # critic 对连续同款题反复挑刺导致 generation_failed）。
    avoid_stems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "purpose": self.purpose,
            "difficulty": self.difficulty,
            "count": self.count,
            "q_type": self.q_type,
            "illustration_request": self.illustration_request,
            "assesses": list(self.assesses),
            "forbidden": list(self.forbidden),
            "bloom_focus": self.bloom_focus,
            "avoid_stems": list(self.avoid_stems),
        }


@dataclass
class AssessmentResult:
    """The outcome of grading one answer.

    Carries both the backward-compatible verdict (correct/wrong) and the
    richer three-level score + mistake_type, so old callers see no change
    while new callers (CAT, supervisor, analytics) get nuance.
    """
    question_id: str = ""
    concept: str = ""
    skill_id: str = ""
    verdict: str = VERDICT_UNKNOWN        # correct | partial | wrong | unknown
    score: float = 0.0                    # 0.0 / 0.5 / 1.0（本题局部得分）
    mistake_type: str = ""                # teaching_engine.MistakeType value
    diagnosis_note: str = ""              # <=60 char note for misconception engine
    feedback: str = ""                    # student-facing feedback
    difficulty_at: int = 0                # difficulty of the question answered
    # W2/A03: the submitted answer this verdict grades, so a re-submit can be
    # recognized as a replay instead of graded (and recorded) a second time.
    student_answer: str = ""
    # M10 evidence audit; additive API fields, never a second mastery store.
    evidence_level: str = ""
    evidence_gate: dict[str, Any] = field(default_factory=dict)
    # W3/D06: structured analysis (rubric criterion results, first error,
    # hypotheses, next_step) when it produced the verdict (active mode), and
    # the shadow copy computed alongside the legacy grade (shadow mode).
    structured: dict[str, Any] = field(default_factory=dict)
    structured_shadow: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "question_id": self.question_id,
            "concept": self.concept,
            "skill_id": self.skill_id,
            "verdict": self.verdict,
            "score": round(self.score, 4),
            "mistake_type": self.mistake_type,
            "diagnosis_note": self.diagnosis_note,
            "feedback": self.feedback,
            "difficulty_at": self.difficulty_at,
            "student_answer": self.student_answer[:200],
            "evidence_level": self.evidence_level,
            "evidence_gate": dict(self.evidence_gate),
        }
        if self.structured:
            d["structured"] = dict(self.structured)
        if self.structured_shadow:
            d["structured_shadow"] = dict(self.structured_shadow)
        return d

    @property
    def correct(self) -> bool:
        """本题局部判定：FULL 计 correct，PARTIAL/NONE 不计（旧客户端兼容
        布尔；能力结论以统一评价 journal 为准，G4）。"""
        return self.score >= 0.75

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AssessmentResult":
        d = d or {}
        return cls(
            question_id=str(d.get("question_id", "") or ""),
            concept=str(d.get("concept", "") or ""),
            skill_id=str(d.get("skill_id", "") or ""),
            verdict=str(d.get("verdict", VERDICT_UNKNOWN) or VERDICT_UNKNOWN),
            score=float(d.get("score", 0.0)),
            mistake_type=str(d.get("mistake_type", "") or ""),
            diagnosis_note=str(d.get("diagnosis_note", "") or ""),
            feedback=str(d.get("feedback", "") or ""),
            difficulty_at=int(d.get("difficulty_at", 0)),
            student_answer=str(d.get("student_answer", "") or "")[:200],
            evidence_level=str(d.get("evidence_level", "") or ""),
            evidence_gate=dict(d.get("evidence_gate", {}) or {}),
            structured=dict(d.get("structured", {}) or {}),
            structured_shadow=dict(d.get("structured_shadow", {}) or {}),
        )
