"""Legacy semantic compatibility store + conflict resolver.

Production turns no longer consolidate or write these detailed facts. The
module remains for pre-migration audit reads and explicit compatibility tests.
Semantic facts were stable long-term generalizations (consolidation output).
Unlike episodic events they are curated: the ConflictResolver decides whether
a new fact supports an existing one (increment confidence) or contradicts it
(supersede the old, newer wins). Superseded facts are never deleted -- they
keep an audit trail (superseded_by) and are excluded from retrieval.

The store is a JSON working set at students/<id>.semantic.json, mirroring M2
student_model/store.py and M3 teaching_log.py. Never raises into a turn.
"""
from __future__ import annotations

import time
from typing import Any

from . import store
from .schema import MemoryScope, SemanticFact, SEMANTIC_CATEGORIES


# confidence increment per supporting evidence, capped at 1.0
_CONF_INCREMENT = 0.08
# minimum confidence to be injected during retrieval
_MIN_CONFIDENCE_FOR_INJECTION = 0.4


def _fact_key(fact: SemanticFact) -> tuple[str, str, str, str]:
    """Identity key for matching: (category, fact_text, scope, subject)."""
    return (fact.category, fact.fact.strip().lower(),
            fact.scope.value, fact.subject.strip().lower())


def add_or_consolidate(student_id: str, fact: SemanticFact) -> SemanticFact | None:
    """Add a new semantic fact, or consolidate with an existing matching one.

    Resolution logic:
    - Same fact (same category+text+scope) exists and is active: increment
      evidence_count and confidence (supporting evidence).
    - Contradicting fact in same category+scope but different text: supersede
      the old one (newer evidence wins). The old fact is marked superseded_by.
    - No match: add as new.

    Returns the resulting fact (added/updated), or None on failure.
    """
    try:
        if fact.category and fact.category not in SEMANTIC_CATEGORIES:
            return None
        facts = store.load_all_semantic_facts(student_id)
        key = _fact_key(fact)

        # check for exact match (supporting evidence)
        for f in facts:
            if f.superseded_by:
                continue
            if _fact_key(f) == key:
                f.evidence_count += 1
                f.confidence = min(1.0, f.confidence + _CONF_INCREMENT)
                f.updated_ts = time.time()
                store.save_semantic_facts(student_id, facts)
                return f

        # check for contradiction (same category+scope, different fact text)
        for f in facts:
            if f.superseded_by:
                continue
            if (f.category == fact.category
                    and f.scope == fact.scope
                    and f.subject.strip().lower() == fact.subject.strip().lower()
                    and _fact_key(f) != key):
                import uuid as _uuid
                # contradiction: supersede old, newer wins
                if not fact.id:
                    fact.id = f"sf_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:6]}"
                store.supersede_semantic_fact(student_id, f.id, fact.id)
                fact.evidence_count = max(1, f.evidence_count // 2)
                fact.confidence = max(fact.confidence, _MIN_CONFIDENCE_FOR_INJECTION)
                store.add_or_update_semantic_fact(student_id, fact)
                return fact

        # no match: add as new
        if not fact.id:
            import uuid as _uuid
            fact.id = f"sf_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:6]}"
        fact.created_ts = time.time()
        fact.updated_ts = time.time()
        store.add_or_update_semantic_fact(student_id, fact)
        return fact
    except Exception:
        return None


def active_facts(student_id: str) -> list[SemanticFact]:
    """Return all active (non-superseded) semantic facts, highest confidence first."""
    try:
        facts = store.load_semantic_facts(student_id)
        facts.sort(key=lambda f: (-f.confidence, -f.evidence_count))
        return facts
    except Exception:
        return []


def facts_for_subject(student_id: str, subject: str) -> list[SemanticFact]:
    """Active facts relevant to a subject (subject-scoped + global)."""
    try:
        all_facts = active_facts(student_id)
        return [f for f in all_facts
                if f.scope == MemoryScope.GLOBAL
                or (subject and f.subject
                    and subject.lower() in f.subject.lower())]
    except Exception:
        return []


def injectable_facts(student_id: str, subject: str = "",
                     limit: int = 4) -> list[SemanticFact]:
    """Facts confident enough to inject during retrieval (>= min confidence).

    Prefers subject-scoped facts, then global. Excludes low-evidence facts
    (evidence_count < 1) and superseded facts.
    """
    try:
        pool = facts_for_subject(student_id, subject)
        injectable = [f for f in pool
                      if f.confidence >= _MIN_CONFIDENCE_FOR_INJECTION
                      and f.evidence_count >= 1]
        injectable.sort(key=lambda f: (f.scope != MemoryScope.SUBJECT,
                                       -f.confidence))
        return injectable[:limit]
    except Exception:
        return []
