"""Mastery tracking via Bayesian Knowledge Tracing (BKT).

BKT is the classic education-modeling algorithm: each skill has a latent
"known/not known" state, and every observed answer (correct/incorrect) updates
the probability the student has mastered it. Four parameters per skill:

    P(L0) prior      : probability the student already knew it before any evidence
    P(T)  transit    : probability they learn it on a single (correct) attempt
    P(S)  slip       : probability they get it wrong despite knowing it
    P(G)  guess      : probability they get it right without knowing it

Update (one observation):
    after observing answer (correct=1/incorrect=0):
        P(L|obs) = P(obs|L) * P(L) / P(obs)          [Bayes, normalize]
    then learn:
        P(L_t+1) = P(L|obs) + (1 - P(L|obs)) * P(T)   [only on correct]

We hard-clamp into [0.01, 0.99] so probabilities never hit exactly 0/1 (which
would make them un-updatable) and keep a small `attempts`/`correct` tally so
downstream code can render "3/5" without replaying events.

Design: deterministic, pure-python, no deps. Parameters default from BKT
literature (Corbett & Anderson 1995 style) and can be overridden per-skill.
A weighted-blend fallback (`update_weighted`) covers the case where we have a
performance score (0..1) rather than a binary observation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .state import cap_list

# Probability clamp: keep BKT posteriors strictly inside (0,1) so a single
# answer can always move them. 0 or 1 would freeze the state.
_P_MIN = 0.01
_P_MAX = 0.99

# Default BKT parameters (Corbett-Anderson style midpoints). Tunable per skill.
DEFAULT_BKT_PARAMS = {"L0": 0.1, "T": 0.1, "S": 0.1, "G": 0.25}


@dataclass
class BKTParams:
    """Per-skill BKT parameters."""
    L0: float = 0.1
    T: float = 0.1
    S: float = 0.1
    G: float = 0.25

    def to_dict(self) -> dict[str, float]:
        return {"L0": self.L0, "T": self.T, "S": self.S, "G": self.G}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "BKTParams":
        d = d or {}
        return cls(L0=float(d.get("L0", 0.1)), T=float(d.get("T", 0.1)),
                   S=float(d.get("S", 0.1)), G=float(d.get("G", 0.25)))


def _clamp(p: float) -> float:
    return max(_P_MIN, min(_P_MAX, p))


@dataclass
class Mastery:
    """Per-skill mastery record (the BKT posterior + bookkeeping)."""
    skill_id: str
    p_known: float = 0.1          # current P(know), clamped to [0.01, 0.99]
    attempts: int = 0             # total observations seen
    correct: int = 0              # how many were correct
    last_review: float = 0.0
    mistakes: list[str] = field(default_factory=list)   # short error notes
    params: BKTParams = field(default_factory=BKTParams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "p_known": round(self.p_known, 4),
            "attempts": self.attempts,
            "correct": self.correct,
            "last_review": self.last_review,
            "mistakes": list(self.mistakes),
            "params": self.params.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Mastery":
        return cls(
            skill_id=str(d.get("skill_id", "")),
            p_known=_clamp(float(d.get("p_known", 0.1))),
            attempts=int(d.get("attempts", 0)),
            correct=int(d.get("correct", 0)),
            last_review=float(d.get("last_review", 0.0)),
            mistakes=list(d.get("mistakes", []) or []),
            params=BKTParams.from_dict(d.get("params")),
        )

    @property
    def mastery(self) -> float:
        """Alias for p_known (spec terminology)."""
        return self.p_known

    def update_binary(self, correct: bool, *, note: str = "",
                      params: BKTParams | None = None) -> float:
        """One BKT update given a binary observation. Returns the new p_known.

        Classic BKT forward step: Bayes-normalize on the observation, then
        apply the transit-on-correct learning step.
        """
        params = params or self.params
        L = self.p_known
        if correct:
            # P(correct) = P(known)*P(no slip) + P(not known)*P(guess)
            p_obs = L * (1 - params.S) + (1 - L) * params.G
            post = (L * (1 - params.S)) / p_obs if p_obs > 0 else L
            # learn: transit applies when transitioning not-known -> known
            post = post + (1 - post) * params.T
        else:
            # P(incorrect) = P(known)*P(slip) + P(not known)*P(no guess)
            p_obs = L * params.S + (1 - L) * (1 - params.G)
            post = (L * params.S) / p_obs if p_obs > 0 else L
            # no transit learning on an incorrect answer
        self.p_known = _clamp(post)
        self.attempts += 1
        if correct:
            self.correct += 1
        else:
            if note:
                self.mistakes = cap_list(self.mistakes + [note], 8)
        self.last_review = time.time()
        return self.p_known

    def update_weighted(self, performance: float, *, note: str = "",
                        weight: float = 0.3) -> float:
        """Weighted-blend fallback for graded scores in [0,1] (no binary obs).

        Used when we only have a correctness rate (e.g. "got 4/6 right"), not a
        single per-question observation. Blends the old posterior with the
        observed performance so mastery tracks the evidence smoothly.
        """
        performance = max(0.0, min(1.0, float(performance)))
        weight = max(0.05, min(0.7, float(weight)))
        self.p_known = _clamp((1 - weight) * self.p_known + weight * performance)
        self.attempts += 1
        # treat it as one "virtual" observation for the tally
        if performance >= 0.5:
            self.correct += 1
        if performance < 0.5 and note:
            self.mistakes = cap_list(self.mistakes + [note], 8)
        self.last_review = time.time()
        return self.p_known


class MasteryTracker:
    """In-memory map of {skill_id: Mastery} with BKT updates.

    Backed by the store blob's `mastery` dict (plain JSON), so state survives
    across turns and process restarts. `ensure` lazily creates a Mastery with
    seed-graph defaults when a skill is first seen.
    """
    def __init__(self, mastery_data: dict[str, dict[str, Any]] | None = None) -> None:
        self.records: dict[str, Mastery] = {}
        for sid, d in (mastery_data or {}).items():
            try:
                self.records[sid] = Mastery.from_dict(d)
            except Exception:
                continue

    def ensure(self, skill_id: str, *, params: BKTParams | None = None) -> Mastery:
        m = self.records.get(skill_id)
        if m is None:
            m = Mastery(skill_id=skill_id, params=params or BKTParams())
            self.records[skill_id] = m
        elif params is not None:
            m.params = params
        return m

    def record_observation(self, skill_id: str, correct: bool, *, note: str = "",
                           params: BKTParams | None = None) -> float:
        """Record one binary observation and return the new p_known."""
        m = self.ensure(skill_id, params=params)
        try:
            return m.update_binary(correct, note=note, params=params)
        except Exception:
            return m.p_known

    def record_performance(self, skill_id: str, performance: float, *,
                           note: str = "", weight: float = 0.3) -> float:
        """Record a graded score in [0,1] (weighted-blend fallback)."""
        m = self.ensure(skill_id)
        try:
            return m.update_weighted(performance, note=note, weight=weight)
        except Exception:
            return m.p_known

    def reset(self, skill_id: str) -> None:
        """Reset a skill to its prior (admin/debug)."""
        self.records[skill_id] = Mastery(skill_id=skill_id)

    def get(self, skill_id: str) -> Mastery | None:
        return self.records.get(skill_id)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {sid: m.to_dict() for sid, m in self.records.items()}
