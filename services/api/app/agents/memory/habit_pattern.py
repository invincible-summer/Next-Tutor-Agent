"""Bounded active study-habit aggregates.

M9 task execution remains its own source of truth. This module folds only
coarse orchestration milestones into a small per-student aggregate file. It
does not write detailed chat events or the legacy M6 semantic store. Existing
``study_habit`` semantic facts are read as a compatibility fallback until the
first active aggregate write migrates that bounded snapshot.
"""
from __future__ import annotations

import time
from typing import Any

from . import store
from .schema import MemoryScope, SemanticFact


# --- deterministic derivation (zero LLM) ------------------------------------

def derive_habit_facts(events: list[dict[str, Any]]) -> list[SemanticFact]:
    """Derive study_habit semantic facts from a batch of M9 orchestration events.

    Rules (each produces a stable behavioural generalisation, not a one-off):
      - habit_milestone (streak >= 7)  -> "具有较强持续学习能力"
      - habit_milestone (streak >= 30) -> "具备长期学习毅力"
      - task_batch_completed (>= 5 evidence) -> "能稳定完成每日学习任务"

    Returns SemanticFacts with category="study_habit". The caller (manager
    consume_turn) folds them through the ConflictResolver so repeated evidence
    accumulates confidence. Pure function over the events list.
    """
    facts: list[SemanticFact] = []
    streak_events = [e for e in events
                     if e.get("event_type") == "habit_milestone"]
    batch_events = [e for e in events
                    if e.get("event_type") == "task_batch_completed"]

    if streak_events:
        max_streak = max((int(e.get("payload", {}).get("streak", 0))
                          for e in streak_events), default=0)
        if max_streak >= 30:
            facts.append(_habit_fact("具备长期学习毅力，能保持月度以上连续学习"))
        elif max_streak >= 7:
            facts.append(_habit_fact("具有较强持续学习能力，能保持周度以上连续学习"))

    if len(batch_events) >= 5:
        facts.append(_habit_fact("能稳定完成每日学习任务，执行率较高"))

    return facts


def _habit_fact(text: str, subject: str = "") -> SemanticFact:
    """Build a study_habit SemanticFact with default confidence/scope."""
    return SemanticFact(
        fact=text, category="study_habit",
        confidence=0.6, evidence_count=1,
        scope=MemoryScope.GLOBAL, subject=subject,
        created_ts=time.time(), updated_ts=time.time())


# --- write side (fold through ConflictResolver) -----------------------------

def consolidate_habit_events(student_id: str,
                             events: list[dict[str, Any]]) -> int:
    """Fold M9 milestones into a bounded aggregate, never legacy semantic."""
    try:
        derived = derive_habit_facts(events)
        if not derived:
            return 0
        items = store.load_habit_patterns(student_id)
        count = 0
        now = time.time()
        for fact in derived:
            subject = str(fact.subject or "")
            target = next((x for x in items
                           if str(x.get("fact") or "").strip() == fact.fact.strip()
                           and str(x.get("subject") or "").strip().lower()
                           == subject.strip().lower()), None)
            if target is None:
                items.append({"fact": fact.fact, "confidence": fact.confidence,
                              "evidence_count": max(1, fact.evidence_count),
                              "subject": subject, "created_ts": now,
                              "updated_ts": now})
            else:
                target["evidence_count"] = int(target.get("evidence_count", 1)) + 1
                target["confidence"] = min(1.0, float(target.get("confidence", 0.6)) + 0.08)
                target["updated_ts"] = now
            count += 1
        store.save_habit_patterns(student_id, items)
        return count
    except Exception:
        return 0


# --- read side (M9 reads long-term patterns read-only) ----------------------

def read_habit_patterns(student_id: str, *,
                        subject: str = "") -> list[dict[str, Any]]:
    """Read bounded active patterns (or the read-only legacy fallback)."""
    try:
        out = []
        for item in store.load_habit_patterns(student_id):
            item_subject = str(item.get("subject") or "")
            if subject and item_subject and subject.lower() not in item_subject.lower():
                continue
            out.append({"fact": str(item.get("fact") or ""),
                        "confidence": float(item.get("confidence", 0.0)),
                        "evidence_count": int(item.get("evidence_count", 0)),
                        "subject": item_subject})
        out.sort(key=lambda x: (-x["confidence"], -x["evidence_count"]))
        return out
    except Exception:
        return []
