"""Legacy episodic compatibility helpers (read-only views).

Production turns no longer write this store (the append wrapper was removed
with the rest of the old write path — zero production callers). The functions
remain for reading pre-migration audit rows; they are never injected into the
active cross-chat prompt profile.
"""
from __future__ import annotations

from typing import Any

from .schema import EpisodicMemory, Importance, MemoryScope
from . import store


def recent_episodes(student_id: str, limit: int = 20) -> list[EpisodicMemory]:
    """Return the most recent `limit` episodes (newest-first)."""
    try:
        eps = store.read_episodes(student_id, limit=limit * 2)
        eps.reverse()
        return eps[:limit]
    except Exception:
        return []


def episodes_for_concept(student_id: str, concept: str,
                         limit: int = 5) -> list[EpisodicMemory]:
    """Return episodes mentioning a concept (newest-first)."""
    try:
        eps = store.read_episodes(student_id)
        matched = [e for e in eps if concept and (
            concept.lower() in e.concept.lower()
            or concept.lower() in e.summary.lower())]
        matched.reverse()
        return matched[:limit]
    except Exception:
        return []


def episodes_for_subject(student_id: str, subject: str,
                         limit: int = 5) -> list[EpisodicMemory]:
    """Return episodes for a subject (newest-first)."""
    try:
        eps = store.read_episodes(student_id)
        matched = [e for e in eps if subject and e.subject
                   and subject.lower() in e.subject.lower()]
        matched.reverse()
        return matched[:limit]
    except Exception:
        return []
