"""M6 memory intelligence.

Active cross-chat prompt memory is bounded and session-attributed: overall
learning situation, current level, tone preference and explanation preference.
Procedural/habit aggregates remain structured and bounded. Legacy episodic and
semantic stores are compatibility-read-only audit data; production turns no
longer append detailed chat events or run semantic consolidation. Mastery and
full question/answer learning records remain owned by the learning-evidence journal (G4).
"""
from __future__ import annotations

from .manager import MemoryService, get_memory_service, is_enabled
from .schema import (EpisodicMemory, Importance, MemoryScope,
                     ProceduralMemory, SemanticFact)

__all__ = [
    "EpisodicMemory",
    "Importance",
    "MemoryScope",
    "ProceduralMemory",
    "SemanticFact",
    "MemoryService",
    "get_memory_service",
    "is_enabled",
]
