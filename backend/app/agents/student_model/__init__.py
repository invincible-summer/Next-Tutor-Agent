"""Student Model + Adaptive Learning Engine (V2 module 2: student intelligence).

Where the V2 Supervisor (module 1) answers "how do I accomplish this task?",
this module answers "how should I teach *this* student?". It maintains a
long-term, cross-conversation picture of one student:

  - profile        : stable long-term facts (grade, subjects, style, goals)
  - skill_graph    : the knowledge DAG (what depends on what)
  - mastery        : per-skill mastery via Bayesian Knowledge Tracing (BKT)
  - learning_memory: semantic concept records (understood / misconception)
  - adaptation     : turns student state into a TeachingStrategy

The whole thing is event-driven: the Supervisor and the quiz-grading endpoint
emit LearningEvents; an in-process processor (rule-based, *no* per-turn LLM)
updates profile / mastery / memory. A teaching strategy is recomputed per turn
from the current state and injected into the planner/executor as soft guidance.

Design contract (must hold to protect V2):
  - Everything degrades gracefully. Any failure is logged to trace and skipped;
    the chat stream never breaks. STUDENT_MODEL_MODE (default on) toggles it.
  - Persistence mirrors core/session.py: JSON at the project root under
    students/, plus an append-only events.jsonl black box. Path-traversal
    guarded like _resolve.
  - No new third-party deps. BKT is pure Python; tests run on stdlib unittest.
"""
from __future__ import annotations

from .adaptation import TeachingStrategy, adapt
from .events import EventCollector, EventProcessor
from .capability_projection import project_capabilities, rebuild_mastery
from .manager import (StudentModel, get_student_model, is_enabled,
                      record_quiz_result)
from .mastery import BKTParams, Mastery, MasteryTracker
from .skill_graph import SkillGraph, SkillNode
from .state import (ConceptRecord, ConceptState, EventType, LearningEvent,
                    LearningStyle, StudentProfile)
from .store import DEFAULT_STUDENT_ID, StudentStateBlob

__all__ = [
    "BKTParams",
    "ConceptRecord",
    "ConceptState",
    "DEFAULT_STUDENT_ID",
    "EventCollector",
    "EventProcessor",
    "EventType",
    "LearningEvent",
    "LearningStyle",
    "Mastery",
    "MasteryTracker",
    "SkillGraph",
    "SkillNode",
    "StudentModel",
    "StudentProfile",
    "StudentStateBlob",
    "TeachingStrategy",
    "adapt",
    "get_student_model",
    "is_enabled",
    "project_capabilities",
    "rebuild_mastery",
    "record_quiz_result",
]
