"""TeachingManager: the single entry point the rest of the app uses.

Like student_model.manager.StudentModel, but for "how to teach" instead of
"what the student knows". Surface:

    tm = get_teaching_manager()
    strategy = tm.adapt(ctx)            # TeachingContext -> TeachingStrategy
    tm.record_turn(student_id, concept, mode, outcome)  # close the loop

It is PURE-READ over student state: callers (supervisor / StudentModel.adapt)
assemble the TeachingContext from live student_model data and hand it in. This
module never imports student_model at runtime, which keeps the dependency
graph one-directional (student_model -> teaching_engine for the strategy type,
supervisor -> teaching_engine for the engine) and free of import cycles.

Graceful: any failure degrades to a no-op strategy; never breaks a turn.
Toggled by TEACHING_ENGINE_MODE (default on); when off, callers should fall
back to the legacy adaptation path (they check is_enabled() themselves).
"""
from __future__ import annotations

import os
from typing import Any

from .policy import TeachingStrategy, compose
from .state import TeachingContext, TeachingOutcome
from .strategy import TeachingMode, select_strategy
from .curriculum import LearningPath, build_learning_path
from . import guidance_store
from .teaching_log import (last_turn, load_teaching_log, record_turn_outcome)


def is_enabled() -> bool:
    """Whether the teaching engine is active (default on)."""
    return os.getenv("TEACHING_ENGINE_MODE", "1") not in ("0", "false", "False", "off")


class TeachingManager:
    """Stateless facade: turns a TeachingContext into a TeachingStrategy.

    The only state it touches is the teaching_log (read for previous_mode,
    written by record_turn). A single shared instance is cached per process.
    """

    def adapt(self, ctx: TeachingContext, *,
              student_id: str = "") -> TeachingStrategy:
        """Produce a TeachingStrategy for one teaching target.

        When `student_id` is given, pulls recent assessed outcomes for the
        concept from the teaching_log so Phase 3 dynamic difficulty can step
        up/down, and loads any applied teaching guidance (M7 human-approved
        proposals) so compose can fold it into focus/avoid. Never raises; any
        failure returns a safe default strategy.
        """
        try:
            mode = select_strategy(ctx)
            recent: list = []
            guidance: list = []
            if student_id:
                # The difficulty dial reads the SAME normalized key the write
                # side (record_turn via strategy.target_skill_id) uses — the
                # graph node id when the supervisor resolved one, else the raw
                # concept. Reading by raw concept while writing by node id left
                # the dial permanently blind (always seed difficulty).
                concept_key = (ctx.concept_key or ctx.concept or "")
                if concept_key:
                    log = load_teaching_log(student_id)
                    recent = log.get(concept_key) or []
                guidance = guidance_store.load_active(student_id)
            return compose(ctx, mode, recent_outcomes=recent, guidance=guidance)
        except Exception:
            return TeachingStrategy(target_concept=ctx.concept,
                                     rationale="教学策略降级")

    def plan_curriculum(self, *, current_name: str = "",
                       current_skill_id: str = "",
                       next_learnable: list | None = None,
                       review_candidates: list | None = None) -> LearningPath:
        """Build a forward+review LearningPath (Phase 3). Advisory only.

        Callers (supervisor, when intent=plan) assemble next_learnable and
        review_candidates from the skill graph + memory and pass them as plain
        dicts; this method stays pure-read over student state.
        """
        return build_learning_path(current_name=current_name,
                                   current_skill_id=current_skill_id,
                                   next_learnable=next_learnable,
                                   review_candidates=review_candidates)

    def record_turn(self, student_id: str, concept_key: str, *,
                    mode: "TeachingMode | str", outcome: "TeachingOutcome | str",
                    note: str = "") -> None:
        """Close the loop: persist this turn's (mode, outcome) for next time.

        concept_key should match what the caller used to look up previous_mode
        (typically the skill_id). Never raises.
        """
        try:
            mode_v = mode.value if isinstance(mode, TeachingMode) else str(mode)
            out_v = outcome.value if isinstance(outcome, TeachingOutcome) else str(outcome)
            record_turn_outcome(student_id, concept_key, mode=mode_v,
                                outcome=out_v, note=note)
        except Exception:
            return


# --- process-level cache -----------------------------------------------------

_INSTANCE: TeachingManager | None = None


def get_teaching_manager() -> TeachingManager:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TeachingManager()
    return _INSTANCE


# --- convenience helpers (the ones callers actually use) --------------------

def adapt_from_context(ctx: TeachingContext, *,
                         student_id: str = "") -> TeachingStrategy:
    """Top-level convenience: build a strategy from a TeachingContext.

    This is what supervisor._adapt_for_turn and StudentModel.adapt call after
    they have assembled the context from live student state. Pass student_id to
    enable Phase 3 dynamic difficulty (recent-outcome stepping).
    """
    return get_teaching_manager().adapt(ctx, student_id=student_id)


def previous_mode_for(student_id: str, concept_key: str) -> "tuple[str, TeachingOutcome, int]":
    """Look up the cross-turn state for a concept: (mode, outcome, turns).

    Returns ("", UNKNOWN, 0) when there is no history. Used by callers when
    assembling a TeachingContext so the engine can advance across turns.
    """
    try:
        log = load_teaching_log(student_id)
        entries = log.get(concept_key) or []
        if not entries:
            return "", TeachingOutcome.UNKNOWN, 0
        last = entries[-1]
        return (last.mode, TeachingOutcome.from_value(last.outcome), len(entries))
    except Exception:
        return "", TeachingOutcome.UNKNOWN, 0
