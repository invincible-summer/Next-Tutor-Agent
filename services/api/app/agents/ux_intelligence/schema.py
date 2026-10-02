"""UX Intelligence (module 8): the output-adaptation layer.

Where M1-M7 answer "what to do / what the student knows / how to teach / did
they learn / what the system knows / what to remember / is it improving", M8
answers the last user-facing question:

    "how should this be EXPRESSED so the student understands more easily,
     stays motivated, and keeps using the tutor?"

It is a HORIZONTAL OUTPUT layer (the counterpart of M5's horizontal INPUT
infrastructure layer): every response the LLM produces is shaped by M8's
advisory directive before it reaches the student. It owns ONLY interaction /
expression state; it never owns academic state.

Design contract (must hold to protect M1-M7):
  - SINGLE TRUTH SOURCE: M2 already owns LearningStyle (academic explanation
    preference / depth). M8 OWNS ONLY the UX-only dimensions M2 lacks --
    tone (encouraging/formal), visual_preference, pacing/patience, and
    preferred_length inferred from engagement. The academic preference is READ
    from M2 as a read-only projection (like M5 reads M2's skill_graph). M8
    never writes mastery / skill_graph / knowledge_graph / teaching_strategy.
  - HORIZONTAL ADVISORY: like M3/M5/M6/M7, M8 is advisory -- it injects a
    "[交互智能·...]" soft directive; the LLM still expresses itself within the
    advisory boundary. No response is regenerated or rewritten by code.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    UX_INTELLIGENCE_MODE (default on). When off, both supervisor hooks are
    no-ops and M1-M7 behavior is byte-identical. Eight layers are orthogonal.
  - DETERMINISTIC-FIRST: profile inference, feedback classification, engagement
    analysis, streak computation are pure functions (zero LLM). No LLM on the
    per-turn critical path -- M8 expresses advice as rules over signals.
  - READS (never writes) M6 episodes for the learning streak, mirroring how M7
    reads M6 as a read-only projection. The long-term memory stays in M6; M8
    only owns the EXPRESSION of motivation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Caps so a single student's UX state can never grow unbounded across a long
# history. Older events remain in the append-only ux_events.jsonl black box.
_MAX_UX_EVENTS_REPLAY = 120
_MAX_FEEDBACK_SIGNALS = 12
_MAX_RECENT_LENGTHS = 8


class Tone(str, Enum):
    """The emotional register the student responds to best."""
    ENCOURAGING = "encouraging"   # warm, growth-mindset, praise effort
    NEUTRAL = "neutral"           # calm, factual, no cheerleading
    FORMAL = "formal"             # precise, academic register

    @classmethod
    def from_value(cls, v: Any) -> "Tone":
        if isinstance(v, Tone):
            return v
        try:
            return cls(str(v)) if v else cls.ENCOURAGING
        except ValueError:
            return cls.ENCOURAGING


class DetailLevel(str, Enum):
    """How much explanation the student tolerates (UX, not academic depth)."""
    CONCISE = "concise"     # short, dense, scan-first
    MEDIUM = "medium"       # balanced
    DETAILED = "detailed"   # verbose, walk-through heavy

    @classmethod
    def from_value(cls, v: Any) -> "DetailLevel":
        if isinstance(v, DetailLevel):
            return v
        try:
            return cls(str(v)) if v else cls.MEDIUM
        except ValueError:
            return cls.MEDIUM


class FeedbackType(str, Enum):
    """Student-side UX feedback inferred from their phrasing (rule-based).

    NOT academic correctness (that is M4 verdict). This is "the experience was
    bad in this way", which becomes an experience metric feeding M7 evaluation
    and adjusts the UX profile (e.g. shorten future explanations).
    """
    EXPLANATION_TOO_HARD = "explanation_too_hard"   # "看不懂/太复杂了/太难了"
    EXPLANATION_TOO_LONG = "explanation_too_long"   # "太长了/太啰嗦/讲太多了"
    EXPLANATION_TOO_SHORT = "explanation_too_short"  # "太简略/没讲清/展开点"
    TOO_FAST = "too_fast"                            # "太快了/跟不上"
    TOO_SLOW = "too_slow"                            # "太慢了/讲快点"
    PRAISE = "praise"                                # "讲得好/谢谢/懂了"
    NONE = "none"

    @classmethod
    def from_value(cls, v: Any) -> "FeedbackType":
        if isinstance(v, FeedbackType):
            return v
        try:
            return cls(str(v)) if v else cls.NONE
        except ValueError:
            return cls.NONE


@dataclass
class InteractionStyle:
    """The expression dimensions M8 OWNS (M2 lacks them).

    `explanation_style` is intentionally NOT here -- it is M2's LearningStyle.
    M8 reads M2 for that academic axis and only adds the UX presentation layer.
    """
    tone: Tone = Tone.ENCOURAGING
    detail_level: DetailLevel = DetailLevel.MEDIUM
    visual_preference: bool = True      # prefers diagrams/figures/tables
    pacing: str = "steady"              # steady | fast | slow (UX rhythm)
    patience: str = "medium"            # low | medium | high (tolerance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tone": self.tone.value,
            "detail_level": self.detail_level.value,
            "visual_preference": self.visual_preference,
            "pacing": self.pacing,
            "patience": self.patience,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "InteractionStyle":
        d = d or {}
        tone = Tone.from_value(d.get("tone"))
        detail = DetailLevel.from_value(d.get("detail_level"))
        return cls(
            tone=tone,
            detail_level=detail,
            visual_preference=bool(d.get("visual_preference", True)),
            pacing=str(d.get("pacing", "steady")) or "steady",
            patience=str(d.get("patience", "medium")) or "medium",
        )


@dataclass
class MotivationState:
    """Lightweight motivation bookkeeping. The streak VALUE is read from M6
    episodes (read-only); here we only keep the last time we surfaced a nudge
    so we don't repeat the same encouragement every turn."""
    last_nudge_ts: float = 0.0
    last_milestone_surfaced: int = 0   # the streak day we last congratulated

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_nudge_ts": self.last_nudge_ts,
            "last_milestone_surfaced": self.last_milestone_surfaced,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "MotivationState":
        d = d or {}
        return cls(
            last_nudge_ts=float(d.get("last_nudge_ts", 0.0) or 0.0),
            last_milestone_surfaced=int(d.get("last_milestone_surfaced", 0) or 0),
        )


@dataclass
class UXProfile:
    """The interaction picture of one student -- "how this student likes to be
    addressed", as opposed to M2's StudentProfile "what this student knows".

    Auto-derived from UXEvents (engagement + feedback), NOT form-filling. Tone
    shifts toward FORMAL when a student asks terse, precise questions; shifts
    toward ENCOURAGING after failures. Detail shortens when they abandon long
    answers or complain about length. All pure-function inference.
    """
    student_id: str = "student_default"
    style: InteractionStyle = field(default_factory=InteractionStyle)
    motivation: MotivationState = field(default_factory=MotivationState)
    # rolling signals that drive inference
    recent_feedback: list[FeedbackType] = field(default_factory=list)
    recent_response_lengths: list[int] = field(default_factory=list)
    abandon_signals: int = 0          # heuristic count of "long answer then left"
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "style": self.style.to_dict(),
            "motivation": self.motivation.to_dict(),
            "recent_feedback": [f.value for f in self.recent_feedback],
            "recent_response_lengths": list(self.recent_response_lengths),
            "abandon_signals": self.abandon_signals,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "UXProfile":
        d = d or {}
        return cls(
            student_id=str(d.get("student_id", "student_default")) or "student_default",
            style=InteractionStyle.from_dict(d.get("style")),
            motivation=MotivationState.from_dict(d.get("motivation")),
            recent_feedback=[FeedbackType.from_value(v) for v in (d.get("recent_feedback") or [])],
            recent_response_lengths=[int(x) for x in (d.get("recent_response_lengths") or [])][:_MAX_RECENT_LENGTHS],
            abandon_signals=int(d.get("abandon_signals", 0) or 0),
            updated_at=float(d.get("updated_at", time.time())),
        )


@dataclass
class UXEvent:
    """One user-experience event appended to the black-box log (ux_events.jsonl).

    Distinct from M2's LearningEvent (academic) and M6's EpisodicMemory
    (narrative). A UXEvent is purely about the experience of THIS interaction.
    """
    ts: float = field(default_factory=time.time)
    student_id: str = "student_default"
    session_id: str = ""
    concept: str = ""
    type: str = "feedback"             # feedback | response | abandon
    feedback: FeedbackType = FeedbackType.NONE
    response_length: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "student_id": self.student_id,
            "session_id": self.session_id,
            "concept": self.concept,
            "type": self.type,
            "feedback": self.feedback.value,
            "response_length": self.response_length,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "UXEvent":
        d = d or {}
        return cls(
            ts=float(d.get("ts", time.time()) or time.time()),
            student_id=str(d.get("student_id", "student_default")) or "student_default",
            session_id=str(d.get("session_id", "") or ""),
            concept=str(d.get("concept", "") or ""),
            type=str(d.get("type", "feedback")) or "feedback",
            feedback=FeedbackType.from_value(d.get("feedback")),
            response_length=int(d.get("response_length", 0) or 0),
            note=str(d.get("note", "") or ""),
        )
