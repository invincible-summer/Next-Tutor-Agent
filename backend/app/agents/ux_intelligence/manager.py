"""UXService: the single facade for the UX Intelligence layer (M8).

Like EvaluationService (M7), MemoryService (M6), KnowledgeService (M5), and
AssessmentManager (M4), this is the one entry point the rest of the app uses.
It exposes:

    ux = get_ux_service()
    ux.build_directive(...)      # READ: JIT analysis -> "[交互智能·...]"
    ux.record_turn(...)          # WRITE: capture UXEvent + update profile
    ux.profile(...)              # READ: UX profile for the API
    ux.greeting(...)             # READ: personalized opener for the frontend

Design contract (mirrors M2/M3/M4/M5/M6/M7):
  - SINGLE TRUTH SOURCE: owns ONLY interaction/expression state. Reads M2
    LearningStyle, M6 episodes, M7 evaluation read-only; NEVER writes them.
  - HORIZONTAL ADVISORY: the directive is a soft instruction; the LLM still
    composes the answer within the advisory boundary.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    UX_INTELLIGENCE_MODE (default on). When off, both supervisor hooks are
    no-ops and M1-M7 behavior is byte-identical.
  - DETERMINISTIC-FIRST: profile inference, feedback classification, engagement
    analysis, streak computation are all pure functions (zero LLM). No LLM on
    the per-turn critical path.
"""
from __future__ import annotations

import os
from typing import Any

from . import (context_builder, engagement_tracker, feedback_analyzer,
               learner_profile, motivation_engine, store)
from .response_quality_evaluator import (ResponseQualityScore,
    evaluate_response, apply_score_to_profile, ExpressionFailure)
from .schema import FeedbackType, UXEvent, UXProfile


def is_enabled() -> bool:
    """Whether the UX Intelligence layer is active (default on)."""
    return os.getenv("UX_INTELLIGENCE_MODE", "1") not in ("0", "false", "False", "off")


class UXService:
    """Facade over UX profile inference, feedback analysis, and engagement.

    Stateless; all persistence is file-backed per-student. A single shared
    instance is cached per process.
    """
    _instance: "UXService | None" = None

    @classmethod
    def get(cls) -> "UXService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # --- READ SIDE: JIT analysis -> directive string --------------------

    def build_directive(self, *, student_id: str, concept: str = "",
                        subject: str = "", intent: str = "explain",
                        grade: str = "") -> str:
        """Build the [交互智能·...] advisory block for this turn.

        Returns "" when there is nothing actionable. This is the single call
        the Supervisor makes per turn (step 3g). Never raises.
        """
        try:
            return context_builder.build_ux_directive(
                student_id=student_id, concept=concept, subject=subject,
                intent=intent, grade=grade)
        except Exception:
            return ""

    def greeting(self, student_id: str, *, grade: str = "",
                 lang: str = "zh") -> str:
        """A personalized opener for a new/empty session. Never raises."""
        try:
            return context_builder.greeting(student_id, grade=grade, lang=lang)
        except Exception:
            return ""

    # --- WRITE SIDE: capture a turn's UX signals -------------------------

    def record_turn(self, *, student_id: str, session_id: str = "",
                    concept: str = "", subject: str = "",
                    user_message: str = "", answer: str = "",
                    grade: str = "", intent: str = "explain",
                    follow_up_count: int = 0, verdict: str = "") -> None:
        """Capture one completed turn's UX signals.

        Classifies any feedback in the user message (rule-based, zero LLM),
        folds the response-length + feedback into the UX profile (re-deriving
        the interaction style), evaluates the expression effectiveness
        (communication_score + failure reason), folds the score back into the
        profile's presentation hints, and appends UXEvents to the black-box
        log. Never raises; failures are swallowed so a turn never breaks.
        """
        try:
            profile = learner_profile.get_profile(student_id)
            learner_profile.seed_style_from_grade(profile, grade)
            feedback = feedback_analyzer.classify(user_message or "")
            events = learner_profile.record_turn_signals(
                profile=profile, student_id=student_id, session_id=session_id,
                concept=concept, message=user_message, answer=answer,
                feedback=feedback)
            # evaluate expression effectiveness (pure function, zero LLM)
            score = evaluate_response(
                answer=answer, feedback=feedback, profile=profile,
                follow_up_count=follow_up_count, verdict=verdict)
            apply_score_to_profile(score, profile)
            events.append(UXEvent(
                student_id=student_id, session_id=session_id, concept=concept,
                type="quality", feedback=feedback,
                response_length=len(answer or ""),
                note=f"score={score.communication_score:.2f} "
                     f"failure={score.failure.value}"))
            for ev in events:
                store.append_event(student_id, ev)
            store.save_profile(student_id, profile)
        except Exception:
            pass

    # --- READ SIDE: API projections -------------------------------------

    def profile(self, student_id: str) -> dict[str, Any]:
        """The UX profile + derived signals for the API. Never raises."""
        return store.profile_summary(student_id)

    def engagement(self, student_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Recent UX events (the append-only black box) for the API."""
        try:
            evs = store.read_events(student_id, limit=limit)
            return [e.to_dict() for e in evs]
        except Exception:
            return []

    def motivation(self, student_id: str) -> dict[str, Any]:
        """Streak / milestone summary (reads the unified activity union).
        Never raises."""
        return motivation_engine.motivation_snapshot(student_id)

    def activity(self, student_id: str, *, days: int = 14) -> dict[str, Any]:
        """Dashboard activity chart: per-day classified counts (answers /
        teachings / reviews) + the streak summary and its data source.
        Never raises."""
        try:
            from .. import activity_aggregator
            return {
                "days": activity_aggregator.daily_counts(
                    student_id, days=days),
                **activity_aggregator.activity_snapshot(student_id),
            }
        except Exception:
            return {"days": [], "source": "none", "streak_days": 0,
                    "longest_streak": 0, "last_active_day": "",
                    "active_days": 0}


_SERVICE = None


def get_ux_service() -> UXService:
    """Return the process-wide UXService singleton."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = UXService.get()
    return _SERVICE
