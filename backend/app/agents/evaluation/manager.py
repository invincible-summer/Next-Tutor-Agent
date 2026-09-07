"""EvaluationService: the single facade for the evaluation & improvement layer (M7).

Like MemoryService (M6), KnowledgeService (M5), and AssessmentManager (M4),
this is the one entry point the rest of the app uses. It exposes:

    es = get_evaluation_service()
    es.build_directive(...)          # READ: JIT analysis -> "[评估智能·...]"
    es.evaluate_turn(...)            # WRITE: capture TurnTrace + diagnose + gain
    es.report()                      # MetricSnapshot for the API
    await es.maybe_advise(...)        # periodic LLM advisory (gated)

Design contract (mirrors M2/M3/M4/M5/M6):
  - PURE-OBSERVER: owns ONLY evaluation artifacts (traces/metrics/proposals).
    Reads M2 mastery, M3 teaching_log, M6 procedural as plain projections.
    NEVER writes back to any of them.
  - SINGLE TRUTH SOURCE: does not duplicate M6's per-student strategy data;
    the strategy_analyzer owns the AGGREGATION layer on top.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    EVALUATION_INTELLIGENCE_MODE (default on). When off, both supervisor hooks
    are no-ops and M1-M6 behavior is byte-identical.
  - DETERMINISTIC-FIRST: trace capture, diagnosis, learning gain, strategy
    aggregation are all pure functions (zero LLM). Only the advisor uses an
    LLM, and only periodically (frequency-gated).
"""
from __future__ import annotations

import os
from typing import Any

from . import (store, strategy_analyzer, trace_analyzer, advisor,
               context_builder)
from .learning_gain import compute_gain
from .schema import (MetricSnapshot, ImprovementProposal, TurnTrace,
                     FailureType)


def is_enabled() -> bool:
    """Whether the evaluation & improvement layer is active (default on)."""
    return os.getenv("EVALUATION_INTELLIGENCE_MODE", "1") not in (
        "0", "false", "False", "off")


class EvaluationService:
    """Facade over trace capture, diagnosis, strategy analysis, and advising.

    Stateless; all persistence is file-backed per-student. A single shared
    instance is cached per process.
    """

    # --- READ SIDE: JIT analysis -> directive string --------------------

    def build_directive(self, *, student_id: str, concept: str = "",
                        subject: str = "") -> str:
        """Build the [评估智能·...] advisory block for this turn.

        Returns "" when there is nothing actionable. This is the single call
        the Supervisor makes per turn (step 3f). Never raises.
        """
        try:
            return context_builder.build_evaluation_directive(
                student_id=student_id, concept=concept, subject=subject)
        except Exception:
            return ""

    # --- WRITE SIDE: capture a turn's evaluation trace -------------------

    def evaluate_turn(self, *, student_id: str, session_id: str = "",
                      concept: str = "", subject: str = "", intent: str = "",
                      grade: str = "", mode: str = "", outcome: str = "unknown",
                      tool_calls: list[str] | None = None,
                      steps: int = 0, tokens_used: int = 0,
                      duration_sec: float = 0.0,
                      before_mastery: float | None = None,
                      after_mastery: float | None = None,
                      n_questions: int = 0,
                      unmet_prereqs: list[str] | None = None,
                      misconceptions: list[str] | None = None,
                      quiz_difficulty: str = "",
                      had_assessment: bool = False) -> TurnTrace | None:
        """Capture one completed turn as a TurnTrace.

        Computes the learning gain (pure function), runs the rule-based failure
        diagnosis (pure function), appends the trace to the black-box log, and
        bumps the advisor frequency-gate counter. Returns the trace or
        None on failure. Never raises.
        """
        try:
            tool_list = [str(t) for t in (tool_calls or [])]
            # learning gain
            gain = compute_gain(before_mastery, after_mastery,
                                concept=concept, subject=subject,
                                n_questions=n_questions)
            trace = TurnTrace(
                session_id=session_id, student_id=student_id,
                concept=concept, subject=subject, intent=intent, grade=grade,
                mode=mode, outcome=outcome, tool_calls=tool_list,
                tool_count=len(tool_list), steps=steps,
                tokens_used=tokens_used, duration_sec=duration_sec,
                before_mastery=before_mastery, after_mastery=after_mastery,
                learning_gain=gain.gain if gain else None,
            )
            # rule-based failure diagnosis (mutates trace in place)
            trace_analyzer.apply_diagnosis(
                trace, unmet_prereqs=unmet_prereqs,
                misconceptions=misconceptions, quiz_difficulty=quiz_difficulty,
                had_assessment=had_assessment)
            store.append_trace(student_id, trace)
            self._bump_advisor_counter(student_id)
            return trace
        except Exception:
            return None

    def _bump_advisor_counter(self, student_id: str) -> None:
        try:
            state = store.load_advisor_state(student_id)
            state["traces_since_last"] = int(state.get("traces_since_last", 0)) + 1
            store.save_advisor_state(student_id, state)
        except Exception:
            pass

    # --- PERIODIC: LLM advisory (gated) ------------------------------

    async def maybe_advise(self, student_id: str,
                             llm: Any | None = None) -> ImprovementProposal | None:
        return await advisor.maybe_advise(student_id, llm=llm)

    # --- inspection (for the API / debug / tests) ------------------------

    def report(self, student_id: str) -> MetricSnapshot:
        """Build a MetricSnapshot summarizing system effectiveness."""
        try:
            traces = store.read_traces(student_id)
            metrics = strategy_analyzer.summarize(traces)
            gains = [t.learning_gain for t in traces
                     if t.learning_gain is not None and t.after_mastery is not None]
            measured = [t for t in traces if t.after_mastery is not None]
            avg_gain = (sum(gains) / len(gains)) if gains else 0.0
            # refresh strategy effectiveness from traces
            strats = strategy_analyzer.refresh_effectiveness(student_id)
            proposals = store.load_proposals(student_id)
            pending = sum(1 for p in proposals if p.status == "proposed")
            return MetricSnapshot(
                total_turns=len(traces),
                total_evaluated=len(measured),
                avg_learning_gain=round(avg_gain, 4),
                failure_distribution=metrics.get("failure_distribution", {}),
                top_strategies=[s.to_dict() for s in strats[:5]],
                pending_proposals=pending,
                tokens_per_turn=metrics.get("avg_tokens", 0.0),
            )
        except Exception:
            return MetricSnapshot()

    def traces(self, student_id: str, limit: int = 50) -> list[dict[str, Any]]:
        try:
            return [t.to_dict() for t in store.read_traces(student_id, limit=limit)]
        except Exception:
            return []

    def proposals(self, student_id: str) -> list[dict[str, Any]]:
        """Proposals with an impact echo for applied ones.

        impact_turns = how many eval traces landed after applied_ts — the
        "已应用 → 影响最近 N 轮" readback. Legacy applied proposals without
        applied_ts carry impact_turns = None (impact unknown, shown as "—").
        """
        try:
            proposals = store.load_proposals(student_id)
            traces = None  # read lazily: only when an applied proposal needs it
            out: list[dict[str, Any]] = []
            for p in proposals:
                d = p.to_dict()
                if p.status == "applied":
                    if p.applied_ts:
                        if traces is None:
                            traces = store.read_traces(student_id)
                        d["impact_turns"] = sum(
                            1 for t in traces if t.ts >= p.applied_ts)
                    else:
                        d["impact_turns"] = None
                out.append(d)
            return out
        except Exception:
            return []

    def approve_proposal(self, student_id: str, proposal_id: str) -> bool:
        return store.update_proposal_status(student_id, proposal_id, "approved")

    def reject_proposal(self, student_id: str, proposal_id: str) -> bool:
        return store.update_proposal_status(student_id, proposal_id, "rejected")

    def trace_count(self, student_id: str) -> int:
        try:
            return len(store.read_traces(student_id))
        except Exception:
            return 0


# --- process-level cache (single-student system) ---------------------------

_INSTANCE: EvaluationService | None = None


def get_evaluation_service() -> EvaluationService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = EvaluationService()
    return _INSTANCE
