"""Teaching state: the read-only input projection for the teaching engine.

This is the engine's ONLY input besides the teaching_log. It is a flat,
plain-data view of (current task + student state) assembled by the caller
(supervisor / StudentModel.adapt) from live student_model data. Keeping it as
plain str/float/list -- with NO reference to student_model types (SkillNode,
StudentProfile, Mastery, ...) -- is what makes this package import-clean: it
never imports student_model at runtime, so there is no circular-import surface.

Every field defaults to an empty value, so a caller that only knows the
concept name still gets a valid context (the engine degrades to INTRODUCTION
on missing mastery data). This mirrors student_model's "never break a turn"
contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# mastery bands shared with strategy/policy. Aligned with student_model's
# MASTERY_MET_THRESHOLD (0.6 = "mastered"): 0.6 is the EXPLANATION->PRACTICE
# boundary, so unmet_prerequisites (also gated at 0.6) compose cleanly.
BAND_NOVICE = 0.3       # below -> novice: basics first
BAND_PROGRESSING = 0.6  # below -> progressing; at/above -> solid/met
BAND_STRONG = 0.8       # at/above -> challenge / extension


class TeachingOutcome(str, Enum):
    """How the previous turn on this concept ended (for cross-turn advancement).

    UNKNOWN    : no record yet (first touch, or outcome not captured)
    ENGAGED    : taught, but not assessed (no quiz signal)
    CORRECT    : last assessment on this concept was answered correctly
    PARTIAL    : mixed / partially correct
    WRONG      : last assessment was wrong
    """
    UNKNOWN = "unknown"
    ENGAGED = "engaged"
    CORRECT = "correct"
    PARTIAL = "partial"
    WRONG = "wrong"

    @classmethod
    def from_value(cls, v: Any) -> "TeachingOutcome":
        if isinstance(v, TeachingOutcome):
            return v
        try:
            return cls(str(v)) if v else cls.UNKNOWN
        except ValueError:
            return cls.UNKNOWN


@dataclass
class TeachingContext:
    """One teaching target's worth of context, fully resolved for the engine.

    `unmet_prereqs` carries the *objects* (SkillNode) from the graph so the
    strategy can surface their .name/.id; typed as Any here precisely to keep
    this module decoupled from student_model (duck-typed: anything with .name).
    """
    concept: str = ""                # the concept being taught this turn
    subject: str = ""                # 物理/数学/...
    task_type: str = "explain"       # TaskType value (explain/practice/...)
    grade: str = "本科"
    # --- student-state projection (read-only) ---
    mastery: float = 0.0             # P(know) for the target concept
    unmet_prereqs: list[Any] = field(default_factory=list)  # SkillNode-like objs, weakest-first
    unmet_prereq_names: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)       # recent short error notes
    misconceptions: list[str] = field(default_factory=list)  # confirmed wrong ideas
    mistake_types: list[str] = field(default_factory=list)  # MistakeType values (concept/procedure/...)
    learning_style: dict[str, str] = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    # --- cross-turn memory (from teaching_log) ---
    concept_key: str = ""            # normalized teaching_log key (graph node id);
                                     # empty -> callers fall back to `concept`.
                                     # Set by the supervisor, which owns the graph
                                     # lookup; the engine itself stays import-clean.
    previous_mode: str = ""          # last TeachingMode used on this concept
    previous_outcome: TeachingOutcome = TeachingOutcome.UNKNOWN
    turns_on_concept: int = 0        # how many turns touched this concept

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "subject": self.subject,
            "task_type": self.task_type,
            "grade": self.grade,
            "mastery": round(self.mastery, 4),
            "unmet_prereq_names": list(self.unmet_prereq_names),
            "mistakes": list(self.mistakes),
            "misconceptions": list(self.misconceptions),
            "mistake_types": list(self.mistake_types),
            "learning_style": dict(self.learning_style),
            "goals": list(self.goals),
            "concept_key": self.concept_key,
            "previous_mode": self.previous_mode,
            "previous_outcome": self.previous_outcome.value,
            "turns_on_concept": self.turns_on_concept,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TeachingContext":
        d = d or {}
        return cls(
            concept=str(d.get("concept", "") or ""),
            subject=str(d.get("subject", "") or ""),
            task_type=str(d.get("task_type", "explain") or "explain"),
            grade=str(d.get("grade", "本科") or "本科"),
            mastery=float(d.get("mastery", 0.0)),
            unmet_prereq_names=list(d.get("unmet_prereq_names", []) or []),
            mistakes=list(d.get("mistakes", []) or []),
            misconceptions=list(d.get("misconceptions", []) or []),
            mistake_types=list(d.get("mistake_types", []) or []),
            learning_style=dict(d.get("learning_style", {}) or {}),
            goals=list(d.get("goals", []) or []),
            concept_key=str(d.get("concept_key", "") or ""),
            previous_mode=str(d.get("previous_mode", "") or ""),
            previous_outcome=TeachingOutcome.from_value(d.get("previous_outcome")),
            turns_on_concept=int(d.get("turns_on_concept", 0)),
        )

    @property
    def has_unmet_prereqs(self) -> bool:
        return bool(self.unmet_prereq_names)

    @property
    def has_misconception(self) -> bool:
        return bool(self.misconceptions)
