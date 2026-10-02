"""ConceptRetriever: map a free-text query to knowledge-graph concepts.

This is M5.3's core. Unlike the file-RAG retriever (core/retriever + the
knowledge_search tool), which retrieves uploaded MATERIAL chunks, this retrieves
CONCEPT NODES from the ontology. It is hybrid retrieval, same shape as the
agent-develop Goldilocks pattern:

  Query
    |
    +-- BM25 over concept search_text (name + aliases + description)
    |        (reuses core/retriever.BM25Index + tokenize -- no new index code)
    |
    +-- KG-traversal expansion: from each BM25 hit, walk RELATED/APPLICATION
    |   edges (depth-limited) so a velocity query surfaces derivative
    |
    +-- score fusion + dedupe + confidence normalization

Phase 1 is BM25 + traversal; a vector pass is a documented future slot (it
would slot in as a third branch before fusion, exactly where V2's roadmap puts
vector retrieval). Deterministic, no deps beyond the reused BM25, no LLM.
"""
from __future__ import annotations

from typing import Any

from ...core.retriever import BM25Index, Chunk, tokenize as _bm25_tokenize
from .graph import KnowledgeGraph
from .schema import EdgeType

# how much a traversal-expanded concept is discounted vs a direct BM25 hit
_EXPANSION_DISCOUNT = 0.6
_DEFAULT_TOP_K = 4
# 学段偏好桶宽（与 graph.match_concept 的 _LEVEL_PREF_EPS 同值）：归一化得分
# 落在同一桶内的候选视为打平，由学段偏好决定先后。
_LEVEL_BUCKET = 0.06


def _stage_pref(node_level: str, query_level: str) -> int:
    """排序用学段偏好（0=优先）。给定学段时同学段优先；未给定时非本科优先——
    本科种子包是 K-12 之上的补充覆盖层，没有学段上下文的查询（学校部署的
    默认场景）应命中 K-12 旗舰义项，本科调用方显式传 level="本科"。"""
    if query_level:
        return 0 if node_level == query_level else 1
    return 1 if node_level == "本科" else 0


class ConceptRetriever:
    """Hybrid concept retriever over one KnowledgeGraph."""

    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph
        self._index: BM25Index | None = None
        self._id_by_chunk: dict[str, str] = {}  # chunk_id -> concept node id

    def _ensure_index(self) -> BM25Index:
        """Build a BM25 index over each node's search_text, reusing core BM25.

        One Chunk per node, keyed so a hit maps back to the node id. Rebuilt
        lazily (the seed graph is immutable in Phase 1, so this is once per
        process). BM25Index reads c.tokens for term frequencies, but Chunk
        defaults tokens to []; chunk_text normally tokenizes, so we mirror it.
        """
        if self._index is not None:
            return self._index
        chunks: list[Chunk] = []
        self._id_by_chunk = {}
        for i, node in enumerate(self.graph.nodes.values()):
            # chapter containers are navigation scaffolding for PART_OF
            # grouping, not retrievable concepts — keep them out of the index.
            if node.kind == "chapter":
                continue
            cid = f"concept#{i}"
            self._id_by_chunk[cid] = node.id
            text = node.search_text()
            chunks.append(Chunk(chunk_id=cid, source=node.id, text=text,
                                index=i, tokens=_bm25_tokenize(text)))
        self._index = BM25Index(chunks)
        return self._index

    def retrieve(self, query: str, *, top_k: int = _DEFAULT_TOP_K,
                 traverse_depth: int = 1, level: str = "") -> list[dict[str, Any]]:
        """Return the top_k concepts for a query, BM25 + traversal, fused.

        Each result dict: {concept_id, name, score, source(bm25|traversal)}.
        `score` is normalized BM25 (0..1) + discount; never raises; bad query
        or no match -> []. `level`（小学/初中/高中/本科）让排序学段感知：
        得分同桶（±0.06）打平时同学段胜出；未给定学段时本科节点让位 K-12。
        """
        try:
            query = (query or "").strip()
            if not query:
                return []
            hits = self._bm25(query, budget=top_k)
            if not hits:
                return []
            max_raw = max(s for _, s in hits) or 1.0
            scores: dict[str, float] = {}
            sources: dict[str, str] = {}
            names: dict[str, str] = {}
            for node_id, raw in hits:
                s = raw / max_raw
                if node_id not in scores or s > scores[node_id]:
                    scores[node_id] = s
                    sources[node_id] = "bm25"
                node = self.graph.get(node_id)
                if node:
                    names[node_id] = node.name
            # traversal expansion from the strongest hit
            if traverse_depth > 0 and hits:
                seed_id = hits[0][0]
                seed_score = scores.get(seed_id, 0.5)
                for nb in self.graph.neighborhood(
                        seed_id, depth=traverse_depth,
                        edge_types=(EdgeType.RELATED, EdgeType.APPLICATION),
                        limit=top_k * 2):
                    node = self.graph.get(nb)
                    if not node or nb in scores:
                        continue
                    scores[nb] = round(seed_score * _EXPANSION_DISCOUNT, 3)
                    sources[nb] = "traversal"
                    names[nb] = node.name
            ranked = sorted(
                scores.items(),
                key=lambda kv: (
                    # 同桶打平 → 学段偏好 → 更高原始分（确定性）
                    -(int(kv[1] / _LEVEL_BUCKET)),
                    _stage_pref((self.graph.get(kv[0]).level
                                 if self.graph.get(kv[0]) else ""), level),
                    -kv[1],
                ))
            out: list[dict[str, Any]] = []
            for nid, s in ranked[:top_k]:
                out.append({"concept_id": nid, "name": names.get(nid, nid),
                            "score": round(s, 3), "source": sources.get(nid, "")})
            return out
        except Exception:
            return []

    def confidence_for(self, query: str) -> float:
        """Top-1 confidence in [0,1]; 0 when nothing matches. Used by the
        Context Builder to decide whether to emit a [知识智能] block at all."""
        r = self.retrieve(query, top_k=1, traverse_depth=0)
        return min(1.0, r[0]["score"]) if r else 0.0

    def _bm25(self, query: str, budget: int = 0) -> list[tuple[str, float]]:
        idx = self._ensure_index()
        # 槽位预算跟随调用方的 top_k：固定 6 槽在图谱变大（新增本科种子包）
        # 后会把第 7 名以后的强候选静默裁掉（reasoner 曾因此丢候选）。
        results = idx.search(query, top_k=max(6, budget, _DEFAULT_TOP_K))
        out: list[tuple[str, float]] = []
        for chunk, score in results:
            nid = self._id_by_chunk.get(chunk.chunk_id)
            if nid:
                out.append((nid, score))
        return out
