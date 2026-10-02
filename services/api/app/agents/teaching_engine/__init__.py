"""Adaptive Teaching Engine (module 3: teaching-strategy intelligence).

Where the V2 Supervisor (module 1) answers "what task to run" and the V3
Student Model (module 2) answers "what does this student know", this module
answers "how should I teach this student right now".

Given a read-only TeachingContext (a projection of student state + the current
task) it produces a TeachingStrategy: a teaching mode, a depth, focus/avoid
directives, a next-step check, and soft plan hints -- all rendered into the
Supervisor's prompt as advisory guidance (the LLM still drives the answer).

Design contract (must hold to protect V2/V3):
  - PURE-READ: this package never imports student_model at runtime. Everything
    it needs arrives via the TeachingContext (plain str/float/list fields) so
    there is zero circular-import surface. supervisor / StudentModel.adapt are
    responsible for assembling the context from live student state.
  - RULE-BASED: deterministic, no LLM calls (same reasoning as student_model
    §14.5 -- stability, zero latency, testable).
  - GRACEFUL: any failure degrades to a no-op strategy; never breaks a turn.
    Toggled by TEACHING_ENGINE_MODE (default on); when off, callers fall back
    to the legacy adaptation behavior exactly.
  - CROSS-TURN MEMORY: teaching_log.py persists (concept, mode, outcome) per
    turn so strategy.py can advance INTRODUCTION -> EXPLANATION -> PRACTICE ->
    CHALLENGE across turns -- the load-bearing piece for "feels like a teacher".
"""
from __future__ import annotations

from .manager import (TeachingManager, adapt_from_context, get_teaching_manager,
                      is_enabled, previous_mode_for)
from .misconception import MistakeType, diagnose
from .curriculum import LearningPath, build_learning_path
from .difficulty import compute_difficulty, difficulty_to_level
from .policy import TeachingStrategy
from .state import TeachingContext, TeachingOutcome
from .strategy import TeachingMode, select_strategy
from .teaching_log import (TeachingLogEntry, load_teaching_log,
                           record_turn_outcome)

__all__ = [
    "TeachingContext",
    "TeachingLogEntry",
    "TeachingManager",
    "TeachingMode",
    "TeachingOutcome",
    "TeachingStrategy",
    "adapt_from_context",
    "MistakeType",
    "diagnose",
    "LearningPath",
    "build_learning_path",
    "compute_difficulty",
    "difficulty_to_level",
    "get_teaching_manager",
    "is_enabled",
    "load_teaching_log",
    "record_turn_outcome",
    "select_strategy",
    "previous_mode_for",
]
