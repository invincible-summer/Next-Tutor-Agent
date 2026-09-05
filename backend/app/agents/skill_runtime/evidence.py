"""Education-specific evidence gate for mastery writes.

M4 assessment uses this contract before calling M2's public write facade, so
an explanation, casual acknowledgement, empty answer, or unknown grade cannot
be treated as proof of mastery.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class EvidenceLevel(IntEnum):
    EXPOSURE = 0
    SELF_REPORT = 1
    RESTATEMENT = 2
    SAME_FORM_TASK = 3
    VARIANT_TASK = 4
    TRANSFER = 5


@dataclass(frozen=True)
class LearningEvidence:
    learning_skill_id: str
    level: EvidenceLevel
    source: str
    confidence: float
    student_action: bool
    response_id: str = ""
    question_id: str = ""
    # W2/A05: whether the question's CONTENT was independently re-solved by
    # the critic. None (unknown/basic/off/fail-open) must never read as trusted.
    question_verified: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "learning_skill_id": self.learning_skill_id,
            "level": self.level.name,
            "level_value": int(self.level),
            "source": self.source,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "student_action": self.student_action,
            "response_id": self.response_id,
            "question_id": self.question_id,
            "question_verified": self.question_verified,
        }


@dataclass(frozen=True)
class EvidenceGateResult:
    allow_mastery_update: bool
    max_confidence: float
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_mastery_update": self.allow_mastery_update,
            "max_confidence": self.max_confidence,
            "reason_code": self.reason_code,
        }


def evaluate_learning_evidence(evidence: LearningEvidence) -> EvidenceGateResult:
    confidence = max(0.0, min(1.0, evidence.confidence))
    if not evidence.student_action:
        return EvidenceGateResult(False, min(confidence, 0.15), "no_student_action")
    if evidence.level <= EvidenceLevel.SELF_REPORT:
        return EvidenceGateResult(False, min(confidence, 0.25), "self_report_only")
    if evidence.level == EvidenceLevel.RESTATEMENT:
        return EvidenceGateResult(False, min(confidence, 0.50),
                                  "insufficient_performance_evidence")
    if confidence < 0.60:
        return EvidenceGateResult(False, confidence, "evidence_confidence_too_low")
    cap = {
        EvidenceLevel.SAME_FORM_TASK: 0.75,
        EvidenceLevel.VARIANT_TASK: 0.90,
        EvidenceLevel.TRANSFER: 1.00,
    }[evidence.level]
    return EvidenceGateResult(True, min(confidence, cap),
                              "performance_evidence_valid")


def assessment_evidence(*, learning_skill_id: str, verdict: str,
                        student_answer: str, question_id: str = "",
                        source: str = "assessment",
                        grading_confidence: float = 0.8,
                        is_variant: bool = False,
                        question_verified: bool | None = None) -> LearningEvidence:
    """Build evidence from a graded learner response.

    Correctness is deliberately not used as confidence: a confidently graded
    wrong answer is still valuable negative BKT evidence. Unknown/ungraded
    responses receive zero confidence and are rejected by the gate.

    W2/A05: a question whose content was NOT independently re-solved (basic
    structural check only / critic off / metadata missing / fail-open) caps
    the grading confidence at 0.70 — missing verification never defaults to
    trusted, while a deterministic letter grade on an unverified MC (1.0)
    stays above the gate's 0.60 rejection line instead of being discarded.
    """
    normalized = (verdict or "").strip().lower()
    confidence = grading_confidence if normalized in {
        "correct", "partial", "wrong", "对", "部分对", "错",
    } else 0.0
    if question_verified is not True:
        confidence = min(confidence, 0.70)
    return LearningEvidence(
        learning_skill_id=learning_skill_id,
        level=(EvidenceLevel.VARIANT_TASK if is_variant
               else EvidenceLevel.SAME_FORM_TASK),
        source=source,
        confidence=confidence,
        student_action=bool((student_answer or "").strip()),
        question_id=question_id,
        question_verified=question_verified,
    )
