"""Core data structures for the Memory Intelligence layer (M6).

Plain dataclasses with to_dict/from_dict round-trips, mirroring
student_model/state.py and knowledge/schema.py. No behaviour here beyond
serialization; the logic lives in sibling modules (store, classifier,
episodic, semantic, procedural, retrieval, context_builder).

Compatibility dataclasses plus the active procedural aggregate schema:
  - EpisodicMemory: legacy narrative audit rows (production write retired)
  - SemanticFact: legacy consolidated facts (production write retired)
  - ProceduralMemory: active bounded teaching-strategy effectiveness
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryScope(str, Enum):
    """Scope of a memory item: drives rule-based filtering at retrieval time.

    Narrower scopes (concept > subject > global) get priority when the query
    targets a specific concept. SESSION scope is the lightest and may be
    in-memory only (not persisted).
    """
    GLOBAL = "global"        # cross-subject long-term facts
    SUBJECT = "subject"      # subject-level (e.g. subject_math)
    CONCEPT = "concept"      # concept-level (e.g. a specific misconception)
    SESSION = "session"      # within a single session (ephemeral)

    @classmethod
    def from_value(cls, v: Any) -> "MemoryScope":
        if isinstance(v, MemoryScope):
            return v
        try:
            return cls(str(v)) if v else cls.GLOBAL
        except ValueError:
            return cls.GLOBAL


class Importance(float, Enum):
    """Importance [0,1]: retention priority + time-decay coefficient.

    Lower importance items decay faster and are filtered out sooner during
    JIT retrieval. Consolidation promotes repeated LOW/NORMAL signals to HIGH.
    """
    LOW = 0.3       # one-off events
    NORMAL = 0.5    # typical learning behaviour
    HIGH = 0.8      # repeated patterns, explicit goals

    @classmethod
    def from_value(cls, v: Any) -> float:
        """Return a float importance from any input (enum or raw number)."""
        if isinstance(v, Importance):
            return v.value
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return cls.NORMAL.value


# --- episodic memory: what happened (time-anchored narrative) ---------------

@dataclass
class EpisodicMemory:
    """A narrative event the student experienced.

    Legacy rows are time-anchored audit records. Production turns no longer
    append or inject them; compatibility readers may inspect historical files
    and explicit tools/tests may construct rows.

    event_type reuses M2 EventType values (quiz_graded/concept_taught/goal_set)
    so there is ONE taxonomy of learning events across the system.
    """
    id: str = ""
    ts: float = field(default_factory=time.time)
    session_id: str = ""        # source chat; empty for independent/business events
    summary: str = ""            # "completed the quadratic-function test, 85, confident"
    event_type: str = ""         # M2 EventType value
    concept: str = ""
    subject: str = ""
    score: float | None = None   # assessment-type events only
    emotion: str = ""            # confident/confused/frustrated (rule-inferred)
    importance: float = Importance.NORMAL.value
    scope: MemoryScope = MemoryScope.GLOBAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "session_id": self.session_id,
            "summary": self.summary,
            "event_type": self.event_type,
            "concept": self.concept,
            "subject": self.subject,
            "score": self.score,
            "emotion": self.emotion,
            "importance": self.importance,
            "scope": self.scope.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EpisodicMemory":
        d = d or {}
        return cls(
            id=str(d.get("id", "")),
            ts=float(d.get("ts", 0.0)),
            session_id=str(d.get("session_id", "")),
            summary=str(d.get("summary", "")),
            event_type=str(d.get("event_type", "")),
            concept=str(d.get("concept", "")),
            subject=str(d.get("subject", "")),
            score=d.get("score"),
            emotion=str(d.get("emotion", "")),
            importance=Importance.from_value(d.get("importance", Importance.NORMAL.value)),
            scope=MemoryScope.from_value(d.get("scope")),
        )

    def search_text(self) -> str:
        """Unified text for BM25 indexing (summary + concept + subject)."""
        return " ".join(part for part in (self.summary, self.concept, self.subject) if part)


# --- semantic memory: stable long-term facts -------------------------------

# categories for SemanticFact. A closed set so writes can be validated
# against a whitelist (bad input cannot corrupt the store with arbitrary
# categories). NOTE: the consolidation pipeline that once produced these is
# retired; the store persists as audit data (compatibility-read-only).
#
# Boundary: "preference" and "goal" are deliberately EXCLUDED. Those are M2
# StudentProfile's domain (learning_style + goals). M6 Semantic only owns
# behavioral/cognitive PATTERNS derived from consolidated episodic evidence
# -- things M2 does not track at this granularity.
SEMANTIC_CATEGORIES = frozenset({
    "context",               # "math foundation weak"
    "misconception_pattern",  # "confuses derivative with function value"
    "study_habit",           # "morning study sessions complete fastest" (M9 HabitPatternMemory)
})


@dataclass
class SemanticFact:
    """A long-term stable fact about the student (legacy consolidation output).

    Unlike episodic events, semantic facts are stable generalizations derived
    from multiple episodes. The ConflictResolver handles contradictions: when
    a new fact contradicts an existing one in the same category+scope, the
    older fact is marked superseded (not deleted) and the newer wins.

    confidence is adjusted by evidence_count (more supporting episodes -> higher
    confidence). Low-evidence facts (evidence_count=1) are treated cautiously
    during retrieval.
    """
    id: str = ""
    fact: str = ""               # "prefers_example_first" / "preparing for gaokao"
    category: str = ""           # one of SEMANTIC_CATEGORIES
    confidence: float = 0.5
    evidence_count: int = 1      # how many episodes support this
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    superseded_by: str | None = None  # id of the fact that replaced this one
    scope: MemoryScope = MemoryScope.GLOBAL
    subject: str = ""            # for SUBJECT/CONCEPT scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fact": self.fact,
            "category": self.category,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "superseded_by": self.superseded_by,
            "scope": self.scope.value,
            "subject": self.subject,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SemanticFact":
        d = d or {}
        return cls(
            id=str(d.get("id", "")),
            fact=str(d.get("fact", "")),
            category=str(d.get("category", "")),
            confidence=max(0.0, min(1.0, float(d.get("confidence", 0.5)))),
            evidence_count=int(d.get("evidence_count", 1)),
            created_ts=float(d.get("created_ts", time.time())),
            updated_ts=float(d.get("updated_ts", time.time())),
            superseded_by=d.get("superseded_by"),
            scope=MemoryScope.from_value(d.get("scope")),
            subject=str(d.get("subject", "")),
        )

    def search_text(self) -> str:
        """Unified text for BM25 indexing."""
        return " ".join(part for part in (self.fact, self.category, self.subject) if part)


# --- procedural memory: what teaching strategies worked --------------------

@dataclass
class ProceduralMemory:
    """Effectiveness of a teaching strategy for this student (education-specific).

    Records which teaching approaches produced good outcomes for this student
    so the Teaching Engine can prefer them. success_rate is a sliding window
    over recent trials; low-trial strategies (trials < _MIN_TRIALS) are NOT
    injected during retrieval to avoid small-sample noise.
    """
    strategy: str = ""            # "use_visual_analogy" / "step_by_step_derivation"
    subject: str = ""
    success_rate: float = 0.0    # sliding window success/(success+fail)
    trials: int = 0              # sample size
    last_used_ts: float = field(default_factory=time.time)
    scope: MemoryScope = MemoryScope.SUBJECT

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "subject": self.subject,
            "success_rate": self.success_rate,
            "trials": self.trials,
            "last_used_ts": self.last_used_ts,
            "scope": self.scope.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProceduralMemory":
        d = d or {}
        return cls(
            strategy=str(d.get("strategy", "")),
            subject=str(d.get("subject", "")),
            success_rate=max(0.0, min(1.0, float(d.get("success_rate", 0.0)))),
            trials=int(d.get("trials", 0)),
            last_used_ts=float(d.get("last_used_ts", time.time())),
            scope=MemoryScope.from_value(d.get("scope")),
        )

    def search_text(self) -> str:
        """Unified text for BM25 indexing."""
        return " ".join(part for part in (self.strategy, self.subject) if part)


# minimum trials before a procedural memory is trusted enough to inject
MIN_TRIALS_FOR_INJECTION = 3

# sliding window for success_rate (how many recent trials to average over)
PROCEDURAL_WINDOW = 10
