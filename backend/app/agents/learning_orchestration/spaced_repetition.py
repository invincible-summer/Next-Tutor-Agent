"""Spaced Repetition (SM-2 algorithm) -- the M9 review scheduler (V1).

SM-2 (SuperMemo 2) is the classic, well-understood spaced-repetition
algorithm (Piotr Wozniak). It models each card with three numbers:

    easiness (EF)   : how easy the student finds this concept; starts at 2.5.
    interval        : days until the next review.
    repetitions     : the streak of consecutive successful reviews.

On each review the student gives a quality response q in [0,5]:

    5 = perfect, 4 = correct w/ hesitation, 3 = correct w/ difficulty,
    2 = incorrect but easy to recall, 1 = incorrect, some recall,
    0 = complete blackout.

Update rules (canonical SM-2):
    if q >= 3 (pass):
        repetitions == 0 -> interval = 1
        repetitions == 1 -> interval = 6
        repetitions >= 2 -> interval = round(interval * easiness)
        repetitions += 1
    else (fail):
        repetitions = 0
        interval = 1
    easiness = EF + (0.1 - (5-q)*(0.08 + (5-q)*0.02))
    easiness clamped to a minimum of 1.3.

DESIGN BOUNDARY: SM-2 owns only the SCHEDULING state (when to review next).
Whether the student "mastered" the concept is M2's BKT posterior -- a totally
separate signal. The quality score q is a *prompt-response* quality (did they
recall this review), not a mastery estimate. This orthogonality is why M9 SRS
composes cleanly with M2 mastery instead of duplicating it.

This module is deterministic and pure (zero LLM, zero I/O). The quality q can
be derived from an M4 quiz verdict (correct->5, partial->3, wrong->1) or from
a self-assessment; the mapping is the caller's choice, not SM-2's.
"""
from __future__ import annotations

import time
from typing import Any

from .schema import ReviewItem

_DAY_SECONDS = 24 * 3600
_MIN_EF = 1.3
_DEFAULT_EF = 2.5


def update_review(card: ReviewItem, quality: int, *, now: float | None = None) -> ReviewItem:
    """Apply one SM-2 quality observation to a card, returning the updated card.

    The original card is NOT mutated; a new ReviewItem is returned so callers
    can store it back atomically. quality is clamped to [0,5]. Never raises.
    """
    try:
        now = now if now is not None else time.time()
        q = max(0, min(5, int(quality)))
        ef = max(_MIN_EF, card.easiness)

        if q >= 3:
            if card.repetitions == 0:
                interval = 1
            elif card.repetitions == 1:
                interval = 6
            else:
                interval = max(1, round(card.interval * ef))
            repetitions = card.repetitions + 1
        else:
            interval = 1
            repetitions = 0

        # easiness update (canonical SM-2 formula)
        new_ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
        new_ef = max(_MIN_EF, round(new_ef, 3))

        return ReviewItem(
            concept_id=card.concept_id,
            concept_name=card.concept_name,
            easiness=new_ef,
            interval=interval,
            repetitions=repetitions,
            next_review=now + interval * _DAY_SECONDS,
            last_quality=q,
            created_at=card.created_at,
        )
    except Exception:
        return card


def create_card(concept_id: str, concept_name: str = "",
                *, now: float | None = None) -> ReviewItem:
    """Create a fresh SM-2 card for a concept (first review due tomorrow).

    last_quality is None until a real recall observation exists (W4/A07): a
    never-recalled card must not project as a pass-3. Old persisted files
    keep their historical value; only new cards default to no observation.
    """
    now = now if now is not None else time.time()
    return ReviewItem(concept_id=concept_id, concept_name=concept_name,
                      easiness=_DEFAULT_EF, interval=0, repetitions=0,
                      next_review=now + _DAY_SECONDS, last_quality=None,
                      created_at=now)


def is_due(card: ReviewItem, *, now: float | None = None) -> bool:
    """Whether a card is due for review (now >= next_review)."""
    now = now if now is not None else time.time()
    return card.next_review > 0 and now >= card.next_review


def due_cards(review_queue: dict[str, ReviewItem], *,
              now: float | None = None, limit: int = 10) -> list[ReviewItem]:
    """Return due cards sorted by how overdue they are (most overdue first)."""
    now = now if now is not None else time.time()
    due = [c for c in review_queue.values() if is_due(c, now=now)]
    due.sort(key=lambda c: c.next_review)
    return due[:limit]


def quality_from_verdict(verdict: str) -> int | None:
    """Map an M4 quiz verdict string to an SM-2 quality [0,5].

    M4 verdicts are correctness labels ([correct/partial/wrong] or the zh
    equivalents [对/部分对/错]). This is the composition point between M4 and
    M9: after a quiz is graded, the caller feeds the verdict here to get the
    SRS quality, then update_review schedules the next review. The mastery
    update (BKT) is M2's separate, independent responsibility.

    Returns None for ``unknown``/unrecognized verdicts: without valid recall
    evidence there is NO SM-2 observation (updatePlan.md A07) — the caller
    must register contact only (create/refresh the card, schedule the first
    check) instead of extending the review interval.
    """
    v = (verdict or "").lower().strip()
    if v in ("correct", "对", "right"):
        return 5
    if v in ("partial", "部分对", "部分"):
        return 3
    if v in ("wrong", "错", "incorrect"):
        return 1
    # unknown verdict -> no valid recall evidence, no SM-2 update
    return None
