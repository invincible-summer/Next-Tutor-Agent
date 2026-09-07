"""Legacy/debug retrieval over pre-migration M6 records.

The active prompt path uses only ``prompt_memory.build_directive`` and never
calls this module. This compatibility surface retains deterministic BM25 over
all legacy memory items (episodic summaries + semantic facts +
procedural strategies), fused with rule-based filtering by scope / importance /
time decay / trial count. No vector index (project has no embedding endpoint;
BM25 is deterministic, model-free -- same rationale as M5 ConceptRetriever and
core/retriever.py).

Reuses core/retriever.py's BM25Index + tokenize, so there is ONE retrieval
implementation across the system.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ...core.retriever import BM25Index, Chunk, tokenize
from . import episodic, semantic, procedural
from .schema import (EpisodicMemory, Importance, MemoryScope, MIN_TRIALS_FOR_INJECTION,
                     ProceduralMemory, SemanticFact)


# time-decay half-life (seconds, ~90 days): older memories score lower
_DECAY_HALFLIFE = 90 * 24 * 3600
# below this BM25 score, a memory is not considered relevant
_MIN_BM25 = 0.01


@dataclass
class MemoryHit:
    """One retrieved memory item with relevance score."""
    kind: str               # "episodic" | "semantic" | "procedural"
    id: str
    text: str               # summary/fact/strategy text
    score: float            # fused relevance score (BM25 x decay x scope-priority)
    concept: str = ""
    subject: str = ""
    importance: float = Importance.NORMAL.value
    scope: MemoryScope = MemoryScope.GLOBAL
    source: dict[str, Any] = field(default_factory=dict)  # raw item dict


def _build_index(items: list[tuple[str, str, dict[str, Any]]]) -> BM25Index | None:
    """Build a BM25 index over (kind, text, raw) tuples. Returns None if empty."""
    if not items:
        return None
    chunks: list[Chunk] = []
    for kind, text, raw in items:
        cid = f"{kind}:{raw.get('id', '')}"
        chunks.append(Chunk(chunk_id=cid, source=cid, text=text,
                             index=len(chunks), tokens=tokenize(text)))
    return BM25Index(chunks)


def _time_decay(ts: float) -> float:
    """Exponential time decay: 1.0 now, 0.5 after one half-life."""
    if ts <= 0:
        return 0.5
    age = time.time() - ts
    return 0.5 ** (age / _DECAY_HALFLIFE)


def _scope_priority(scope: MemoryScope, query_concept: str,
                    query_subject: str) -> float:
    """Boost scores by scope match. Narrower scope that matches the query wins."""
    if scope == MemoryScope.CONCEPT and query_concept:
        return 1.3
    if scope == MemoryScope.SUBJECT and query_subject:
        return 1.15
    if scope == MemoryScope.GLOBAL:
        return 1.0
    return 0.85


def retrieve(student_id: str, query: str, *, concept: str = "",
             subject: str = "", top_k: int = 6) -> list[MemoryHit]:
    """JIT retrieval of relevant past memory for the current turn.

    Returns a fused, de-duplicated, score-ranked list of MemoryHit. Each hit's
    score is BM25 * time_decay * scope_priority * importance_weight. Never
    raises; returns [] on any failure.
    """
    try:
        concept = (concept or "").strip()
        subject = (subject or "").strip()

        # collect all memory items as (kind, text, raw_dict)
        items: list[tuple[str, str, dict[str, Any]]] = []

        for ep in episodic.recent_episodes(student_id, limit=100):
            items.append(("episodic", ep.search_text(), ep.to_dict()))

        for fact in semantic.active_facts(student_id):
            items.append(("semantic", fact.search_text(), fact.to_dict()))

        for proc in procedural.all_procedural(student_id):
            if proc.trials >= MIN_TRIALS_FOR_INJECTION:
                items.append(("procedural", proc.search_text(), proc.to_dict()))

        if not items:
            return []

        index = _build_index(items)
        if index is None:
            return []

        bm25_results = index.search(query, top_k=top_k * 2)

        hits: list[MemoryHit] = []
        seen: set[str] = set()
        for chunk, bm25_score in bm25_results:
            if bm25_score < _MIN_BM25:
                continue
            # resolve back to the raw item
            kind, raw = _resolve_chunk(chunk, items)
            if raw is None or raw.get("id") in seen:
                continue
            seen.add(raw.get("id"))

            ts = float(raw.get("ts") or raw.get("updated_ts") or
                       raw.get("created_ts") or raw.get("last_used_ts") or 0)
            scope = MemoryScope.from_value(raw.get("scope"))
            importance = Importance.from_value(raw.get("importance", Importance.NORMAL.value))

            decay = _time_decay(ts)
            scope_boost = _scope_priority(scope, concept, subject)
            importance_weight = 0.7 + 0.3 * importance  # [0.7, 1.0]

            fused = bm25_score * decay * scope_boost * importance_weight

            hits.append(MemoryHit(
                kind=kind, id=str(raw.get("id", "")),
                text=raw.get("summary") or raw.get("fact") or
                raw.get("strategy") or "",
                score=round(fused, 4),
                concept=str(raw.get("concept", "")),
                subject=str(raw.get("subject", "")),
                importance=importance, scope=scope, source=raw))

        hits.sort(key=lambda h: -h.score)
        return hits[:top_k]
    except Exception:
        return []


def _resolve_chunk(chunk: Chunk,
                   items: list[tuple[str, str, dict[str, Any]]]
                   ) -> tuple[str, dict[str, Any] | None]:
    """Map a BM25 result chunk back to its (kind, raw_dict)."""
    for kind, _text, raw in items:
        cid = f"{kind}:{raw.get('id', '')}"
        if cid == chunk.chunk_id:
            return kind, raw
    return "", None
