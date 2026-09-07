"""Hybrid retrieval: BM25 + vector dual recall, RRF fusion (k=60).

Design notes:
  - BM25 is the always-on deterministic track; the vector track (embedding
    endpoint + Chroma) is optional. Any vector-side failure falls back to
    pure BM25 results — retrieval never breaks because an endpoint is down.
  - No LLM rerank: at this library size (a handful of uploaded files) RRF
    over two recall lanes is enough, and rerank would put an LLM on the
    critical path (against the project's LLM-off-critical-path principle).
  - The small-store pass-through is preserved: with only a few chunks the
    full content is more useful than any ranking (BM25 scores 0 on
    paraphrases and the LLM would wrongly conclude the material is irrelevant).
"""
from __future__ import annotations

import logging
from typing import Any

from . import vector_store
from .config import settings
from .knowledge_store import KnowledgeStore
from .retriever import BM25Index, Chunk

log = logging.getLogger(__name__)

_RRF_K = 60
_RECALL_FACTOR = 3   # each lane recalls top_k * 3 candidates before fusion


def rrf_merge(rankings: list[list[str]], k: int = _RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over per-lane ordered id lists.

    Pure function: score(id) = sum over lanes of 1/(k + rank). Ids absent
    from a lane simply contribute nothing — a lane returning [] degrades to
    the other lane's ordering.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _merged_chunks(scoped_stores: list[tuple[str, KnowledgeStore]]) -> list[Chunk]:
    """Flatten all scopes' chunks, de-duped by chunk_id."""
    seen: set[str] = set()
    out: list[Chunk] = []
    for _scope, store in scoped_stores:
        for c in getattr(store, "chunks", []) or []:
            if c.chunk_id in seen:
                continue
            seen.add(c.chunk_id)
            out.append(c)
    return out


def _as_result(c: Chunk, score: float, **signals: Any) -> dict[str, Any]:
    # Same dict shape as KnowledgeStore.search results plus auditable lane signals.
    out = {"source": c.source, "filename": c.source, "file_id": c.file_id,
           "chunk_id": c.chunk_id, "index": c.index, "text": c.text,
           "score": score, "page": c.page,
           "printed_page": c.metadata.get("printed_page"),
           "block_types": list(c.metadata.get("block_types", [])),
           "section_path": list(c.metadata.get("section_path", [])),
           "lesson": c.metadata.get("lesson"),
           "is_lesson": bool(c.metadata.get("is_lesson")),
           "noise_flags": list(c.metadata.get("noise_flags", []))}
    out.update(signals)
    return out


def _bm25_results(chunks: list[Chunk], query: str, top_k: int) -> list[dict[str, Any]]:
    ranked = BM25Index(chunks).search(query, top_k=top_k)
    return [_as_result(c, round(s, 3)) for c, s in ranked]


async def hybrid_search(scoped_stores: list[tuple[str, KnowledgeStore]],
                        query: str, top_k: int,
                        embed_client: Any | None = None) -> list[dict[str, Any]]:
    """Search across scoped stores with BM25 + vector RRF fusion.

    scoped_stores: [(scope, store)] with scope "session:{id}"/"workspace:{id}".
    Returns result dicts in the same shape as KnowledgeStore.search. Falls
    back to pure BM25 on any vector-track failure; returns [] only when
    there is genuinely nothing indexed.
    """
    chunks = _merged_chunks(scoped_stores)
    if not chunks:
        return []
    # Small library: return everything (same rule as KnowledgeStore.search).
    if len(chunks) <= max(top_k, KnowledgeStore.SMALL_STORE_MAX_CHUNKS):
        return [_as_result(c, 1.0) for c in chunks]

    if embed_client is None:
        from .embedding import get_embedding_client
        embed_client = get_embedding_client()
    if embed_client is None or not settings.rag_hybrid:
        return _bm25_results(chunks, query, top_k)

    try:
        # Small stores may backfill inline. Whole textbooks are queued on the
        # process-wide single-slot worker so an SSE/request path never embeds a
        # book synchronously; this query simply uses existing vectors or BM25.
        for scope, store in scoped_stores:
            scoped_chunks = getattr(store, "chunks", None) or []
            if not scoped_chunks:
                continue
            if len(scoped_chunks) <= 256:
                await vector_store.ensure_indexed(scope, scoped_chunks, embed_client)
            else:
                from .vector_jobs import schedule_index
                schedule_index(scope, scoped_chunks, embed_client,
                               key=f"query:{scope}")
        query_vecs = await embed_client.embed([query])
        schemas = list(dict.fromkeys(
            str((chunk.metadata or {}).get("chunk_schema") or "legacy-v1")
            for chunk in chunks))
        vector_hits = await vector_store.query(
            query_vecs[0], [s for s, _ in scoped_stores], top_k * _RECALL_FACTOR,
            embed_client=embed_client, chunk_schemas=schemas)
        if not vector_hits:
            return _bm25_results(chunks, query, top_k)

        vector_ranking = list(vector_hits.keys())  # nearest-first
        bm25_ranked = BM25Index(chunks).search(query, top_k * _RECALL_FACTOR)
        bm25_ranking = [c.chunk_id for c, _s in bm25_ranked]
        bm25_scores = {c.chunk_id: float(sc) for c, sc in bm25_ranked}
        by_id = {c.chunk_id: c for c in chunks}
        results: list[dict[str, Any]] = []
        for cid, score in rrf_merge([vector_ranking, bm25_ranking])[:top_k]:
            c = by_id.get(cid)
            if c is not None:
                results.append(_as_result(
                    c, round(score, 4), bm25_score=round(bm25_scores.get(cid, 0.0), 4),
                    vector_distance=vector_hits.get(cid), rrf_score=round(score, 4)))
        return results or _bm25_results(chunks, query, top_k)
    except Exception as e:
        log.warning("hybrid search fell back to BM25: %s", e)
        return _bm25_results(chunks, query, top_k)
