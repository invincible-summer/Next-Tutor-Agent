"""MemoryService: the single facade for the memory-intelligence layer (M6).

Like KnowledgeService (M5), TeachingManager (M3), and AssessmentManager (M4),
this is the one entry point the rest of the app uses. It exposes:

    ms = get_memory_service()
    ms.build_directive(...)  # READ: JIT retrieval -> "[记忆智能·...]" string
    ms.consume_turn(...)     # WRITE: bounded prompt contribution + procedural

Design contract (mirrors M2/M3/M4/M5):
  - READ SIDE is PURE-READ: callers pass concept/subject/mastery as plain data;
    this module never imports student_model at runtime. The dependency runs
    memory -> (consumed by) supervisor, one-way.
  - WRITE SIDE consumes the SAME LearningEvents but stores only a bounded,
    session-attributed prompt contribution plus procedural/habit aggregates.
    Legacy episodic/semantic files remain compatibility-read-only audit data.
  - SINGLE TRUTH SOURCE: owns prompt/procedural memory. Mastery,
    concept state, teaching mode history each have one owner already.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    MEMORY_INTELLIGENCE_MODE (default on). When off, both supervisor hooks are
    no-ops and M1-M5 behavior is byte-identical.
"""
from __future__ import annotations

import os
from typing import Any

from . import (habit_pattern, retrieval, semantic, store, procedural, prompt_memory)
from .context_builder import build_and_render
from .schema import (EpisodicMemory, Importance, MemoryScope, ProceduralMemory,
                     SemanticFact)  # noqa: F401 (re-exported via __init__)


def is_enabled() -> bool:
    """Whether the memory-intelligence layer is active (default on)."""
    return os.getenv("MEMORY_INTELLIGENCE_MODE", "1") not in ("0", "false", "False", "off")


class MemoryService:
    """Facade over episodic + semantic + procedural memory stores."""

    def __init__(self) -> None:
        pass  # stateless; all persistence is file-backed per-student

    # --- READ SIDE: JIT retrieval -> directive string -------------------

    def build_directive(self, *, student_id: str, concept: str,
                        subject: str, top_k: int = 6) -> str:
        """Render only the bounded prompt-memory profile.

        Legacy episodic/semantic files remain audit records. Active procedural
        and habit aggregates are stored separately and are not injected here.
        """
        try:
            return prompt_memory.build_directive(student_id)
        except Exception:
            return ""

    def retrieve(self, student_id: str, query: str, *, concept: str = "",
                 subject: str = "", top_k: int = 6) -> list[dict[str, Any]]:
        """Direct retrieval access (for tools / debug). Returns hit dicts."""
        try:
            hits = retrieval.retrieve(student_id, query, concept=concept,
                                      subject=subject, top_k=top_k)
            return [
                {"kind": h.kind, "id": h.id, "text": h.text, "score": h.score,
                 "concept": h.concept, "subject": h.subject}
                for h in hits
            ]
        except Exception:
            return []

    # --- WRITE SIDE: consume a turn's signals into memory ----------------

    def consume_turn(self, *, student_id: str,
                     session_id: str = "",
                     workspace_id: str = "",
                     events: list[dict[str, Any]] | None = None,
                     user_message: str = "",
                     answer: str = "",
                     strategy_mode: str = "",
                     strategy_outcome: str = "",
                     subject: str = "") -> dict[str, Any]:
        """Fold a turn into bounded prompt and aggregate strategy memory.

        Detailed episodic/semantic chat records are compatibility-read-only.
        Question/answer outcomes live in the independent learning ledger.
        """
        stats: dict[str, Any] = {"episodic_added": 0, "procedural_updated": 0}
        try:
            events = events or []
            if strategy_mode and strategy_outcome:
                result = procedural.record_outcome(
                    student_id, strategy_mode, subject, strategy_outcome)
                if result is not None:
                    stats["procedural_updated"] += 1

            orch_events = [e for e in events
                           if (e.get("event_type") or e.get("type"))
                           in ("habit_milestone", "task_batch_completed",
                               "milestone_completed", "goal_progress")]
            if orch_events:
                hp = habit_pattern.consolidate_habit_events(student_id, orch_events)
                if hp:
                    stats["habit_facts_updated"] = hp

            if session_id:
                stats["prompt_memory"] = prompt_memory.record_contribution(
                    student_id, session_id, workspace_id=workspace_id,
                    events=events, user_message=user_message,
                    strategy_outcome=strategy_outcome)
            return stats
        except Exception:
            return stats

    async def maybe_compact_prompt_memory(self, student_id: str,
                                          llm: Any | None = None) -> dict[str, Any]:
        return await prompt_memory.maybe_compact_core(student_id, llm)

    # --- inspection (for tools / debug / tests) --------------------------

    def episodic_count(self, student_id: str) -> int:
        try:
            return len(store.read_episodes(student_id))
        except Exception:
            return 0

    def semantic_fact_count(self, student_id: str) -> int:
        try:
            return len(semantic.active_facts(student_id))
        except Exception:
            return 0

    def procedural_count(self, student_id: str) -> int:
        try:
            return len(procedural.all_procedural(student_id))
        except Exception:
            return 0


# --- process-level cache (single-student system) ---------------------------

_INSTANCE: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = MemoryService()
    return _INSTANCE
