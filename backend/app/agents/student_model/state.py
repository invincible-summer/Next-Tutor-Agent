"""Student Model core data structures (V2 module 2: student intelligence).

Plain dataclasses with to_dict/from_dict round-trips, mirroring
agents/state.py and core/session.py. No behaviour here beyond serialization;
the logic lives in the sibling modules (mastery / skill_graph / events /
adaptation / manager). Keeping data and behaviour separate matches how the
Supervisor split state.py from supervisor.py.

Scope note: this is the student-intelligence counterpart to the V2
Supervisor's task-intelligence layer. StudentProfile replaces V1's ad-hoc
"grade + files" snapshot with a real, auto-derived learner picture.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Per-list caps so a single field can never grow unbounded across a long
# student history. Older entries are recoverable from the events log.
_MAX_EVIDENCE = 6
_MAX_POINTS = 20


class EventType(str, Enum):
    """Kinds of learning events the system records (event-driven memory).

    These are the *only* inputs that mutate profile / mastery / memory. They
    are emitted as side-effects of normal turns (quiz graded, concept taught,
    ...), never as per-turn LLM work -- so student intelligence accrues cheaply.
    """
    QUIZ_GRADED = "quiz_graded"          # a question was answered (correct/incorrect)
    CONCEPT_TAUGHT = "concept_taught"    # a concept was explained this turn
    WEAKNESS_SIGNALED = "weakness_signaled"  # student self-reported / we inferred a weak spot
    GOAL_SET = "goal_set"                # student stated a learning goal
    MASTERY_RESET = "mastery_reset"      # admin/debug: reset a skill's mastery

    @classmethod
    def from_value(cls, v: Any) -> "EventType | None":
        if v is None:
            return None
        if isinstance(v, EventType):
            return v
        try:
            return cls(str(v))
        except ValueError:
            return None


class ConceptState(str, Enum):
    """Mastery state of a concept in semantic memory.

    UNKNOWN      : never touched
    INTRODUCED   : explained but not yet tested
    PARTIAL      : tested, mixed results (some right / some wrong)
    UNDERSTOOD   : tested, consistently correct
    MISCONCEPTION: tested, consistently wrong -- a confirmed wrong idea
    """
    UNKNOWN = "unknown"
    INTRODUCED = "introduced"
    PARTIAL = "partial"
    UNDERSTOOD = "understood"
    MISCONCEPTION = "misconception"

    @classmethod
    def from_value(cls, v: Any) -> "ConceptState":
        if isinstance(v, ConceptState):
            return v
        try:
            return cls(str(v)) if v else cls.UNKNOWN
        except ValueError:
            return cls.UNKNOWN


@dataclass
class LearningStyle:
    """How the student prefers to be taught, auto-inferred from behaviour."""
    preference: str = "balanced"          # step_by_step | examples_first | balanced
    explanation_depth: str = "adaptive"   # basic | deep | adaptive

    def to_dict(self) -> dict[str, Any]:
        return {"preference": self.preference, "explanation_depth": self.explanation_depth}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LearningStyle":
        d = d or {}
        return cls(
            preference=str(d.get("preference", "balanced")) or "balanced",
            explanation_depth=str(d.get("explanation_depth", "adaptive")) or "adaptive",
        )


@dataclass
class StudentProfile:
    """Long-term, cross-conversation picture of one student.

    Auto-derived from LearningEvents (NOT form-filling). grade is seeded from
    the first session; subjects/goals/style/weak/strong evolve as events land.
    """
    id: str = "student_default"
    grade: str = "本科"
    subjects: list[str] = field(default_factory=list)
    learning_style: LearningStyle = field(default_factory=LearningStyle)
    goals: list[str] = field(default_factory=list)
    weak_points: list[str] = field(default_factory=list)
    strong_points: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # number of events processed -> drives infrequent LLM consolidation
    events_processed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "grade": self.grade,
            "subjects": list(self.subjects),
            "learning_style": self.learning_style.to_dict(),
            "goals": list(self.goals),
            "weak_points": list(self.weak_points),
            "strong_points": list(self.strong_points),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_active": self.last_active,
            "events_processed": self.events_processed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "StudentProfile":
        d = d or {}
        return cls(
            id=str(d.get("id", "student_default")) or "student_default",
            grade=str(d.get("grade", "本科")) or "本科",
            subjects=list(d.get("subjects", []) or []),
            learning_style=LearningStyle.from_dict(d.get("learning_style")),
            goals=list(d.get("goals", []) or []),
            weak_points=list(d.get("weak_points", []) or []),
            strong_points=list(d.get("strong_points", []) or []),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
            last_active=float(d.get("last_active", time.time())),
            events_processed=int(d.get("events_processed", 0)),
        )


@dataclass
class ConceptRecord:
    """One concept in the student's semantic learning memory.

    A taught-but-untested concept is INTRODUCED; only quiz outcomes move it
    toward UNDERSTOOD / MISCONCEPTION. `attempts`/`correct` keep a tiny tally
    so we can classify state without keeping every event in memory.
    """
    skill_id: str = ""
    concept: str = ""
    state: ConceptState = ConceptState.UNKNOWN
    evidence: list[str] = field(default_factory=list)     # short observed facts
    misconceptions: list[str] = field(default_factory=list)
    mistake_types: list[str] = field(default_factory=list)  # MistakeType values
    attempts: int = 0
    correct: int = 0
    last_review: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "concept": self.concept,
            "state": self.state.value,
            "evidence": list(self.evidence),
            "misconceptions": list(self.misconceptions),
            "mistake_types": list(self.mistake_types),
            "attempts": self.attempts,
            "correct": self.correct,
            "last_review": self.last_review,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConceptRecord":
        return cls(
            skill_id=str(d.get("skill_id", "")),
            concept=str(d.get("concept", "")),
            state=ConceptState.from_value(d.get("state")),
            evidence=list(d.get("evidence", []) or [])[_MAX_EVIDENCE:],
            misconceptions=list(d.get("misconceptions", []) or [])[_MAX_EVIDENCE:],
            mistake_types=list(d.get("mistake_types", []) or [])[_MAX_EVIDENCE:],
            attempts=int(d.get("attempts", 0)),
            correct=int(d.get("correct", 0)),
            last_review=float(d.get("last_review", 0.0)),
        )


@dataclass
class LearningEvent:
    """An immutable record of one learning observation.

    Serialized to the append-only events log verbatim and consumed by the
    EventProcessor to update profile / mastery / memory. `payload` is
    event-type specific (see EventType docstring + EventProcessor).
    """
    type: EventType
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "ts": self.ts, "payload": dict(self.payload)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LearningEvent | None":
        t = EventType.from_value(d.get("type"))
        if t is None:
            return None
        return cls(type=t, ts=float(d.get("ts", time.time())),
                   payload=dict(d.get("payload", {}) or {}))


def cap_list(items: list[str], limit: int = _MAX_POINTS) -> list[str]:
    """Trim a growing list to its most recent entries (helper for processors)."""
    return list(items)[-limit:] if len(items) > limit else list(items)
