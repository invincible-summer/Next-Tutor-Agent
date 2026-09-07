"""Structured Question model: the single contract for generator, evaluator
and frontend.

The existing generate_quiz / fit_quiz tools emit ad-hoc JSON dicts. That works
for rendering, but assessment needs to REASON about a question: which
sub-abilities it probes, what methods it forbids, how hard it is on the 1..5
internal scale. This dataclass is that strong type, and from_quiz_dict lifts an
existing quiz-tool dict into it with zero changes to the tools themselves --
additive migration, exactly like M3's TeachingStrategy back-compat fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from ...core.bloom import normalize_level


class QuestionType:
    MULTIPLE_CHOICE = "multiple_choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"


# maps the quiz tool's easy/medium/hard triple onto the 1..5 internal scale
# (mirrors teaching_engine.difficulty.difficulty_to_level in reverse).
_LEVEL_TO_DIFFICULTY = {"easy": 2, "medium": 3, "hard": 5}

_KP_SPLIT = re.compile(r"[、,，;；·]")


@dataclass
class Question:
    """A single assessment item, strongly typed.

    `assesses` / `forbidden` / `distractor_targets` are M4 additions: they let
    the generator encode intent ("probe vertex identification, no calculus")
    and the evaluator read it back. They default empty so legacy quiz dicts
    upgrade losslessly. `bloom_level` tags the question's Bloom cognitive
    level (decided by the generating LLM, never validated as a gate) so it can
    flow into the shared learning ledger / cognitive profile.
    """
    id: str = ""
    concept: str = ""
    knowledge_points: list[str] = field(default_factory=list)
    difficulty: int = 3                       # 1..5 internal scale
    q_type: str = QuestionType.MULTIPLE_CHOICE
    stem: str = ""
    options: dict[str, str] = field(default_factory=dict)
    answer: str = ""
    explanation: str = ""
    assesses: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    distractor_targets: dict[str, str] = field(default_factory=dict)
    bloom_level: str = ""                      # remember..create ("" = untagged)
    # W2/A05: generation-time verification audit (quiz_verify meta shape) so
    # the evidence gate can distinguish content-checked from merely structural.
    verification: dict[str, Any] = field(default_factory=dict)
    # W3/D04: frozen scoring rubric (quiz_verify.freeze_rubric shape). Frozen
    # at generation time — before any student answer exists — and versioned;
    # empty for legacy questions and malformed generation output alike.
    rubric: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "concept": self.concept,
            "knowledge_points": list(self.knowledge_points),
            "difficulty": self.difficulty,
            "q_type": self.q_type,
            "stem": self.stem,
            "options": dict(self.options),
            "answer": self.answer,
            "explanation": self.explanation,
            "assesses": list(self.assesses),
            "forbidden": list(self.forbidden),
            "distractor_targets": dict(self.distractor_targets),
            "bloom_level": self.bloom_level,
            "verification": dict(self.verification),
            "rubric": dict(self.rubric),
        }

    @property
    def is_multiple_choice(self) -> bool:
        return self.q_type == QuestionType.MULTIPLE_CHOICE and bool(self.options)

    @classmethod
    def from_quiz_dict(cls, d: dict[str, Any], *,
                       concept: str = "", difficulty: int = 0) -> "Question":
        """Lift an existing generate_quiz/fit_quiz dict into a Question.

        Existing tools keep their field names (stem/options/answer/...). We
        normalize the few that differ: knowledge_point(s) -> knowledge_points,
        the easy/medium/hard `difficulty` string -> a 1..5 int. Never raises.
        """
        d = d or {}
        kp = d.get("knowledge_point") or d.get("knowledge_points")
        if isinstance(kp, str) and kp.strip():
            kp_list = [k for k in _KP_SPLIT.split(kp) if k.strip()]
        elif isinstance(kp, list):
            kp_list = [str(k) for k in kp if str(k).strip()]
        else:
            kp_list = []
        raw_diff = d.get("difficulty")
        if isinstance(raw_diff, str) and raw_diff in _LEVEL_TO_DIFFICULTY:
            diff = _LEVEL_TO_DIFFICULTY[raw_diff]
        elif isinstance(raw_diff, (int, float)):
            diff = int(raw_diff)
        else:
            diff = difficulty or 3
        return cls(
            id=str(d.get("id") or ""),
            concept=str(concept or d.get("topic") or ""),
            knowledge_points=kp_list,
            difficulty=max(1, min(5, diff)),
            q_type=str(d.get("type") or QuestionType.MULTIPLE_CHOICE),
            stem=str(d.get("stem", "") or ""),
            options=dict(d.get("options", {}) or {}),
            answer=str(d.get("answer", "") or ""),
            explanation=str(d.get("explanation", "") or ""),
            bloom_level=normalize_level(d.get("bloom_level")),
            verification=dict(d.get("verification", {}) or {}),
            rubric=dict(d.get("rubric", {}) or {}),
        )
