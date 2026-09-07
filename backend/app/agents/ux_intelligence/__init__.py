"""UX Intelligence (module 8): the output-adaptation layer.

Where the Supervisor (M1) answers "what task to run", the Student Model (M2)
answers "what does this student know", the Teaching Engine (M3) answers "how
to teach now", Assessment (M4) answers "did they learn it", Knowledge
Intelligence (M5) answers "what does the system know about the subject",
Memory Intelligence (M6) answers "what should the system remember", and
Evaluation & Improvement (M7) answers "is the tutor getting better", this
module answers the last user-facing question:

    "how should this be EXPRESSED so the student understands more easily,
     stays motivated, and keeps using the tutor?"

It is a HORIZONTAL OUTPUT layer: every response is shaped by M8's advisory
directive before it reaches the student. It owns ONLY interaction / expression
state (tone / detail / visual / pacing / patience, and engagement-derived
preferences); it reads M2's academic LearningStyle, M6's episodes, and M7's
evaluation as READ-ONLY projections and never writes them back.

Design contract (must hold to protect M1-M7):
  - SINGLE TRUTH SOURCE: M2 owns LearningStyle; M8 owns only the UX-only dims.
  - HORIZONTAL ADVISORY: injects a "[交互智能·...]" soft directive; the LLM
    still composes the answer within the boundary.
  - GRACEFUL: UX_INTELLIGENCE_MODE (default on). When off, both supervisor
    hooks are no-ops and M1-M7 behavior is byte-identical. Eight layers
    orthogonal; turning off any layer lets upper layers degrade cleanly.
  - DETERMINISTIC-FIRST: profile inference, feedback classification,
    engagement analysis, streak are pure functions (zero LLM on the per-turn
    critical path).
"""
from __future__ import annotations

from .manager import UXService, get_ux_service, is_enabled
from .schema import (DetailLevel, FeedbackType, InteractionStyle,
                     MotivationState, Tone, UXEvent, UXProfile)
from .explanation_adapter import ResponseDirective
from .response_quality_evaluator import (ExpressionFailure,
    ResponseQualityScore, evaluate_response)

__all__ = [
    "UXService",
    "get_ux_service",
    "is_enabled",
    "DetailLevel",
    "FeedbackType",
    "InteractionStyle",
    "MotivationState",
    "ResponseDirective",
    "ExpressionFailure",
    "ResponseQualityScore",
    "evaluate_response",
    "Tone",
    "UXEvent",
    "UXProfile",
]
