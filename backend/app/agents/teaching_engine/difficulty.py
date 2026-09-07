"""Dynamic difficulty control (Phase 3): the "just hard enough" dial.

A tutor, like a game, must keep the learner in the zone of proximal
development: too hard -> frustration, too easy -> no growth. This module maps
recent performance onto a 1..5 difficulty scale and steps it up/down as the
student answers.

Signal source: the cross-turn teaching_log (teaching_log.py). Each turn's
outcome (CORRECT/WRONG/PARTIAL) is already persisted there per concept, so we
get a real sliding window without instrumenting the quiz path separately.
ENGAGED/UNKNOWN turns (taught but not assessed) are filtered out -- they carry
no difficulty signal.

Algorithm (deliberately simple, spec §3 Module 5):
  - seed from current mastery (1..5 band map) when there is no performance data
  - look at the most recent N assessed outcomes for this concept
  - >=80% correct -> step UP one
  - <=40% correct -> step DOWN one
  - clamp to [1, 5]
This is a pure function of (mastery, recent_outcomes). Deterministic, no LLM,
no persistence (the teaching_log already owns the history).

The 1..5 scale is the engine's internal model; policy maps it to the
easy/medium/hard triple the existing quiz tool expects, so no external
interface changes.
"""
from __future__ import annotations

from typing import Any

from .state import BAND_NOVICE, BAND_PROGRESSING, BAND_STRONG, TeachingOutcome

_MIN_D = 1
_MAX_D = 5
_WINDOW = 5
_ACCURACY_UP = 0.8
_ACCURACY_DOWN = 0.4


def seed_from_mastery(mastery: float) -> int:
    """Map a mastery score onto the 1..5 seed (used when no history yet)."""
    if mastery < BAND_NOVICE:
        return 1
    if mastery < BAND_PROGRESSING:
        return 2
    if mastery < BAND_STRONG:
        return 3
    return 4


def _assessed_outcomes(recent: list[Any]) -> list[str]:
    """Keep only outcomes that carry a difficulty signal (drop ENGAGED/UNKNOWN)."""
    keep = (TeachingOutcome.CORRECT.value, TeachingOutcome.WRONG.value,
            TeachingOutcome.PARTIAL.value)
    out = []
    for e in recent:
        v = e.outcome if hasattr(e, "outcome") else str(e)
        if v in keep:
            out.append(v)
    return out


def compute_difficulty(mastery: float, recent_outcomes: list[Any]) -> int:
    """Pick a 1..5 difficulty for the current concept.

    `recent_outcomes` is the list of recent TeachingLogEntry (or raw outcome
    strings) for this concept, oldest-first. Assessed outcomes only count.
    """
    base = seed_from_mastery(mastery)
    assessed = _assessed_outcomes(recent_outcomes)[-_WINDOW:]
    if not assessed:
        return base
    # PARTIAL counts as half-correct
    score = sum(1.0 if o == TeachingOutcome.CORRECT.value
                else 0.5 if o == TeachingOutcome.PARTIAL.value
                else 0.0 for o in assessed)
    acc = score / len(assessed)
    if acc >= _ACCURACY_UP:
        return min(_MAX_D, base + 1)
    if acc <= _ACCURACY_DOWN:
        return max(_MIN_D, base - 1)
    return base


def difficulty_to_level(d: int) -> str:
    """Map the internal 1..5 scale onto the quiz tool's easy/medium/hard triple.

    Keeps the existing external interface stable (no quiz-tool change needed).
    """
    if d <= 2:
        return "easy"
    if d <= 3:
        return "medium"
    return "hard"
