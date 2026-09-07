"""Core data structures for the Evaluation & Improvement Intelligence layer (M7).

Plain dataclasses with to_dict/from_dict round-trips, mirroring
student_model/state.py, knowledge/schema.py, and memory/schema.py. No behaviour
here beyond serialization; the logic lives in sibling modules
(trace_analyzer, learning_gain, strategy_analyzer, advisor, experiment).

This layer owns ONLY evaluation artifacts -- never any business state. Mastery
is M2's, teaching mode history is M3's, narrative memories are M6's. M7 reads
them as plain projections and layers trace analysis / learning-gain / strategy
effectiveness / improvement proposals on top.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Failure classification (rule-based, zero LLM)
# ---------------------------------------------------------------------------

class FailureType(str, Enum):
    """Where a teaching turn failed (diagnosed by trace_analyzer).

    Diagnosing the *locus* of failure -- not just "wrong answer" -- is what
    turns M7 from a scoreboard into a feedback instrument: it tells the system
    WHICH layer to adjust (teaching depth? prerequisites? retrieval?).
    """
    NONE = "none"                     # no failure: outcome was correct/engaged
    TEACHING_DEPTH_MISMATCH = "teaching_depth_mismatch"  # explanation too high/low
    PREREQUISITE_MISSING = "prerequisite_missing"        # missing prior knowledge
    RETRIEVAL_MISS = "retrieval_miss"                    # knowledge_search found nothing useful
    ASSESSMENT_TOO_HARD = "assessment_too_hard"          # quiz difficulty above level
    STRATEGY_MISMATCH = "strategy_mismatch"             # mode didn't fit the student
    NO_ASSESSMENT = "no_assessment"                      # taught but never measured

    @classmethod
    def from_value(cls, v: Any) -> "FailureType":
        if isinstance(v, FailureType):
            return v
        try:
            return cls(str(v)) if v else cls.NONE
        except ValueError:
            return cls.NONE


# ---------------------------------------------------------------------------
# TurnTrace: one teaching turn's evaluation snapshot (append-only black box)
# ---------------------------------------------------------------------------

@dataclass
class TurnTrace:
    """A structured evaluation record of one completed teaching turn.

    Append-only, uncapped (the jsonl is a black box like the chat transcript
    and the M6 episodes log). Captures WHAT happened (mode/outcome/tools),
    HOW MUCH it cost (tokens/steps/duration), and WHETHER it worked
    (before/after mastery + learning gain + failure diagnosis).

    This is the atomic unit M7 analyzes: strategy_analyzer aggregates over
    traces, the advisor reads accumulated traces to propose improvements, and
    the evaluation directive reads recent traces to advise the LLM.
    """
    id: str = ""
    ts: float = field(default_factory=time.time)
    session_id: str = ""
    student_id: str = ""
    concept: str = ""
    subject: str = ""
    intent: str = ""                 # TaskType value (explain/practice/...)
    grade: str = ""
    mode: str = ""                   # TeachingMode used that turn
    outcome: str = "unknown"         # TeachingOutcome value (correct/wrong/...)
    tool_calls: list[str] = field(default_factory=list)
    tool_count: int = 0
    steps: int = 0                   # ReAct loop iterations
    tokens_used: int = 0             # prompt + completion total
    duration_sec: float = 0.0
    before_mastery: float | None = None
    after_mastery: float | None = None
    learning_gain: float | None = None
    failure_type: str = FailureType.NONE.value
    failure_cause: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "session_id": self.session_id,
            "student_id": self.student_id,
            "concept": self.concept,
            "subject": self.subject,
            "intent": self.intent,
            "grade": self.grade,
            "mode": self.mode,
            "outcome": self.outcome,
            "tool_calls": list(self.tool_calls),
            "tool_count": self.tool_count,
            "steps": self.steps,
            "tokens_used": self.tokens_used,
            "duration_sec": round(self.duration_sec, 3),
            "before_mastery": self.before_mastery,
            "after_mastery": self.after_mastery,
            "learning_gain": self.learning_gain,
            "failure_type": self.failure_type,
            "failure_cause": self.failure_cause,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TurnTrace":
        d = d or {}
        return cls(
            id=str(d.get("id", "")),
            ts=float(d.get("ts", 0.0)),
            session_id=str(d.get("session_id", "")),
            student_id=str(d.get("student_id", "")),
            concept=str(d.get("concept", "")),
            subject=str(d.get("subject", "")),
            intent=str(d.get("intent", "")),
            grade=str(d.get("grade", "")),
            mode=str(d.get("mode", "")),
            outcome=str(d.get("outcome", "unknown")),
            tool_calls=list(d.get("tool_calls", []) or []),
            tool_count=int(d.get("tool_count", 0)),
            steps=int(d.get("steps", 0)),
            tokens_used=int(d.get("tokens_used", 0)),
            duration_sec=float(d.get("duration_sec", 0.0)),
            before_mastery=d.get("before_mastery"),
            after_mastery=d.get("after_mastery"),
            learning_gain=d.get("learning_gain"),
            failure_type=FailureType.from_value(d.get("failure_type")).value,
            failure_cause=str(d.get("failure_cause", "")),
            recommendation=str(d.get("recommendation", "")),
        )

    def search_text(self) -> str:
        """Unified text for BM25 indexing (concept + subject + failure_cause)."""
        return " ".join(p for p in (self.concept, self.subject, self.failure_cause,
                                    self.recommendation, self.mode) if p)


# ---------------------------------------------------------------------------
# LearningGain: per-concept teaching effectiveness
# ---------------------------------------------------------------------------

@dataclass
class LearningGain:
    """The mastery delta produced by teaching a concept.

    before/after are P(know) from M2 BKT. gain = after - before (clamped to
    [-1, 1]). n_questions is how many were assessed (gain is only meaningful
    when assessment actually happened).
    """
    concept: str = ""
    subject: str = ""
    before: float = 0.0
    after: float = 0.0
    gain: float = 0.0
    n_questions: int = 0
    ts: float = field(default_factory=time.time)
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept, "subject": self.subject,
            "before": round(self.before, 4), "after": round(self.after, 4),
            "gain": round(self.gain, 4), "n_questions": self.n_questions,
            "ts": self.ts, "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LearningGain":
        d = d or {}
        return cls(
            concept=str(d.get("concept", "")), subject=str(d.get("subject", "")),
            before=float(d.get("before", 0.0)), after=float(d.get("after", 0.0)),
            gain=float(d.get("gain", 0.0)), n_questions=int(d.get("n_questions", 0)),
            ts=float(d.get("ts", 0.0)), trace_id=str(d.get("trace_id", "")),
        )


# ---------------------------------------------------------------------------
# StrategyEffectiveness: cross-turn aggregation ("which strategy works best")
# ---------------------------------------------------------------------------

@dataclass
class StrategyEffectiveness:
    """Aggregated effectiveness of a teaching mode, computed by strategy_analyzer.

    This is the M7 contribution ON TOP of M6 procedural: M6 tracks per-student
    success_rate; M7 aggregates avg learning_gain across turns to compare
    strategies against each other. Reads M3 teaching_log + M6 procedural +
    M7's own traces -- does NOT duplicate their raw data.
    """
    strategy: str = ""              # TeachingMode value
    subject: str = ""
    avg_gain: float = 0.0          # mean learning_gain across turns
    avg_success_rate: float = 0.0  # mean of per-turn correct/engaged ratio
    sample_size: int = 0          # how many turns contributed
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "subject": self.subject,
            "avg_gain": round(self.avg_gain, 4),
            "avg_success_rate": round(self.avg_success_rate, 4),
            "sample_size": self.sample_size, "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategyEffectiveness":
        d = d or {}
        return cls(
            strategy=str(d.get("strategy", "")),
            subject=str(d.get("subject", "")),
            avg_gain=float(d.get("avg_gain", 0.0)),
            avg_success_rate=float(d.get("avg_success_rate", 0.0)),
            sample_size=int(d.get("sample_size", 0)),
            last_updated=float(d.get("last_updated", 0.0)),
        )


# ---------------------------------------------------------------------------
# ImprovementProposal: LLM-generated, human-approved improvement suggestion
# ---------------------------------------------------------------------------

# Legacy target labels (pre-guidance proposals in existing data). The advisor
# no longer emits a target: proposals are open-ended teaching guidance
# (title/applicability/guidance/cautions) with no value domain. The set is
# kept so old files round-trip and legacy writes stay validated.
PROPOSAL_TARGETS = frozenset({
    "prompt",     # change a prompt template (e.g. add analogy-first policy)
    "policy",     # change a decision rule (e.g. mastery band threshold)
    "strategy",   # change a strategy preference (e.g. prefer example_first)
})

PROPOSAL_STATUSES = frozenset({
    "proposed",   # generated by advisor, awaiting human review
    "approved",   # human accepted (but not yet applied)
    "applied",    # applied to the system
    "rejected",   # human rejected
})


@dataclass
class ImprovementProposal:
    """A structured suggestion to improve the system, generated periodically.

    NOT auto-applied. The advisor (LLM, frequency-gated) generates these from
    accumulated metrics + failure patterns; a human reviews, approves, and
    applies. Applying a proposal deploys its guidance text into the teaching
    engine (teaching_engine/guidance_store) — the only path by which a proposal
    ever influences teaching.

    Current format is open-ended teaching guidance (title/applicability/
    guidance/cautions — no value domain, no parameter assignment). The legacy
    fields (target/change/rationale) stay with defaults so pre-guidance
    proposals in existing files round-trip unchanged.
    """
    id: str = ""
    ts: float = field(default_factory=time.time)
    # --- open-ended teaching guidance (current advisor output) ---
    title: str = ""                # 一句话标题
    applicability: str = ""        # 适用范围（情境/学科/概念）；空 = 通用
    guidance: str = ""             # 指导原则文本（教学应该怎么做）
    cautions: list[str] = field(default_factory=list)
    # --- legacy target-style fields (pre-guidance data only) ---
    target: str = ""               # one of PROPOSAL_TARGETS (legacy)
    change: str = ""               # legacy one-line change; new = title mirror
    rationale: str = ""            # why this should help
    # --- common ---
    confidence: float = 0.5        # [0,1] how strong the evidence is
    evidence: list[str] = field(default_factory=list)  # supporting stats/trace refs
    status: str = "proposed"       # one of PROPOSAL_STATUSES
    applied_ts: float = 0.0        # set when status transitions to applied

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "ts": self.ts,
            "title": self.title, "applicability": self.applicability,
            "guidance": self.guidance, "cautions": list(self.cautions),
            "target": self.target,
            "change": self.change, "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
            "evidence": list(self.evidence), "status": self.status,
            "applied_ts": self.applied_ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImprovementProposal":
        d = d or {}
        return cls(
            id=str(d.get("id", "")), ts=float(d.get("ts", 0.0)),
            title=str(d.get("title", "")),
            applicability=str(d.get("applicability", "")),
            guidance=str(d.get("guidance", "")),
            cautions=[str(c) for c in (d.get("cautions") or []) if str(c)],
            target=str(d.get("target", "")), change=str(d.get("change", "")),
            rationale=str(d.get("rationale", "")),
            confidence=max(0.0, min(1.0, float(d.get("confidence", 0.5)))),
            evidence=list(d.get("evidence", []) or []),
            status=str(d.get("status", "proposed")),
            applied_ts=float(d.get("applied_ts", 0.0)),
        )


# ---------------------------------------------------------------------------
# MetricSnapshot: system-level health summary (for the API / observability)
# ---------------------------------------------------------------------------

@dataclass
class MetricSnapshot:
    """A point-in-time summary of system teaching effectiveness.

    Built by EvaluationService.report() from accumulated traces + strategy
    effectiveness + proposals. Exposed via the evaluation API
    for human inspection of "is the tutor getting better?".
    """
    ts: float = field(default_factory=time.time)
    total_turns: int = 0
    total_evaluated: int = 0        # turns with a measurable outcome
    avg_learning_gain: float = 0.0
    failure_distribution: dict[str, int] = field(default_factory=dict)
    top_strategies: list[dict[str, Any]] = field(default_factory=list)
    pending_proposals: int = 0
    tokens_per_turn: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts, "total_turns": self.total_turns,
            "total_evaluated": self.total_evaluated,
            "avg_learning_gain": round(self.avg_learning_gain, 4),
            "failure_distribution": dict(self.failure_distribution),
            "top_strategies": list(self.top_strategies),
            "pending_proposals": self.pending_proposals,
            "tokens_per_turn": round(self.tokens_per_turn, 1),
        }


# minimum traces a strategy needs before its effectiveness is trustworthy
MIN_TRACES_FOR_EFFECTIVENESS = 3

# how many recent traces the evaluation directive inspects (rolling window)
EVAL_DIRECTIVE_WINDOW = 20

# frequency gate for the LLM advisor (traces between advisory runs)
ADVISOR_FREQUENCY_GATE = 15

# caps for the working-set JSON (older traces recoverable from the jsonl)
MAX_PROPOSALS = 40
MAX_STRATEGY_RECORDS = 30
MAX_TRACES_REPLAY = 500
