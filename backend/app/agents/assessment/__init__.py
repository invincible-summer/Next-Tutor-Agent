"""Assessment Intelligence (module 4: measurement intelligence).

Where the Supervisor (M1) answers "what task to run", the Student Model (M2)
answers "what does this student know", and the Teaching Engine (M3) answers
"how should I teach this student right now", this module answers the last
question of the learning loop:

    "did the student actually learn it?"

It produces structured Questions (constraint-driven), grades answers on a
three-level scale (none / partial / full) instead of binary right/wrong, and
folds each result into a single closed-loop point that feeds the Student
Model (quiz_graded events -> BKT mastery updates). In Phase 3 it adds a
Computerized Adaptive Test loop that steps question difficulty up/down from
recent performance.

Design contract (must hold to protect M1/M2/M3):
  - CONSOLIDATES, NOT DUPLICATES: this package reuses the Teaching Engine's
    difficulty model (teaching_engine.difficulty) and misconception diagnosis
    (teaching_engine.misconception) instead of rebuilding them. There is one
    difficulty engine and one misconception classifier in the whole system.
  - PURE-READ: this package never imports student_model at runtime. Everything
    it needs about the student arrives via AssessmentContext (plain
    str/float/list). The Student Model is written to through the EXISTING
    record_quiz_result facade, never via direct store mutation.
  - RULE-FIRST: MC grading is deterministic (letter compare, zero LLM); CAT
    stop/difficulty rules are pure functions (deterministic, testable, zero
    latency). LLM is used only where generation is the essence of the task
    (question generation, open-answer grading).
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    ASSESSMENT_ENGINE_MODE (default on); when off, callers keep the legacy
    /quiz/grade binary path and MC results are not reported.

Phase 1 (core): question + evaluator + manager + MC reporting endpoint.
Phase 2: constraint-driven QuestionGenerator + supervisor next_check fulfillment.
Phase 3: adaptive_test (CAT) + AssessmentSession persistence.
"""
from __future__ import annotations

from .manager import (AssessmentManager, evaluate_and_record,
                      get_assessment_manager, is_enabled)
from .state import (AssessmentContext, AssessmentGoal, AssessmentResult,
                    ScoreLevel)
from .question import Question, QuestionType
from .evaluator import (evaluate_mc, grade_open_prompt, parse_grade,
                        derive_concept_status, verdict_for_score)
from .generator import generate_question

__all__ = [
    "AssessmentContext",
    "AssessmentGoal",
    "AssessmentManager",
    "AssessmentResult",
    "Question",
    "QuestionType",
    "ScoreLevel",
    "derive_concept_status",
    "evaluate_and_record",
    "evaluate_mc",
    "get_assessment_manager",
    "grade_open_prompt",
    "is_enabled",
    "parse_grade",
    "verdict_for_score",
    "generate_question",
]
