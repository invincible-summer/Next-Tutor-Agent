"""Evaluation & Improvement Intelligence (module 7: the improvement-advisor layer).

Where the Supervisor (M1) answers "what task to run", the Student Model (M2)
answers "what does this student know", the Teaching Engine (M3) answers "how
to teach now", Assessment (M4) answers "did they learn it", Knowledge
Intelligence (M5) answers "what does the system know about the subject", and
Memory Intelligence (M6) answers "what should the system remember", this module
answers the capstone question:

    "is the tutor itself getting better over time?"

It is a PURE OBSERVER: it owns ONLY evaluation artifacts (turn traces, learning
gains, strategy effectiveness, improvement proposals, experiments). It never
owns any business state -- mastery is M2's, teaching mode history is M3's,
narrative memories are M6's. It reads them as plain projections and layers
trace analysis, learning-gain measurement, strategy comparison, and structured
improvement proposals on top.

Design contract (must hold to protect M1-M6):
  - PURE-OBSERVER: reads M2/M3/M6 as plain projections; NEVER writes back to
    them. The dependency runs evaluation -> (observes) supervisor/M2/M3/M6,
    one-way. No reverse writes.
  - SINGLE TRUTH SOURCE: does not duplicate M6's per-student strategy data.
    The strategy_analyzer owns the AGGREGATION layer (avg gain across turns);
    M6 owns the per-student layer (success_rate for THIS student).
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    EVALUATION_INTELLIGENCE_MODE (default on); when off, both supervisor hooks
    are no-ops and M1-M6 behavior is byte-identical. Seven layers are
    orthogonal; turning off any layer lets the upper layers degrade cleanly.
  - DETERMINISTIC-FIRST: trace capture, failure diagnosis, learning gain, and
    strategy aggregation are pure functions (zero LLM). Only the advisor uses
    an LLM, and only periodically (frequency-gated by ADVISOR_FREQUENCY_GATE),
    never on the critical per-turn path.
  - HUMAN-IN-THE-LOOP: improvement proposals are NEVER auto-applied. They land
    with status="proposed" and require explicit approval. A bad LLM cannot
    silently rewrite prompts (validated target/status whitelists).
"""
from __future__ import annotations

from .manager import EvaluationService, get_evaluation_service, is_enabled
from .schema import (FailureType, LearningGain, MetricSnapshot,
                     ImprovementProposal, StrategyEffectiveness, TurnTrace)

__all__ = [
    "EvaluationService",
    "get_evaluation_service",
    "is_enabled",
    "FailureType",
    "LearningGain",
    "MetricSnapshot",
    "ImprovementProposal",
    "StrategyEffectiveness",
    "TurnTrace",
]
