"""KnowledgeService: the single facade the rest of the app uses for M5.

Like StudentModel (M2), TeachingManager (M3), and AssessmentManager (M4), this
is the one entry point for the knowledge-intelligence layer. It owns the live
KnowledgeGraph (seed + learned edges/content) and the ConceptRetriever, and
exposes the read surface the Supervisor + bridge consume:

    ks = get_knowledge_service()
    ks.graph                # KnowledgeGraph (seed + learned edges + contents)
    ks.retriever            # ConceptRetriever (BM25 + KG traversal)
    ks.retrieve(query)      # -> [{concept_id, name, score, source}]
    ks.build_context(...)   # -> KnowledgeContext (content + materials resolved)
    ks.build_directive(...) # -> "[知识智能·...]" soft-directive string
    await ks.reason_about(concept, llm=...)  # M5.5 Dependency Reasoner

Design contract (mirrors M2/M3/M4):
  - PURE-READ over student_model: callers pass mastery/materials in as plain
    data; this module never imports student_model at runtime. The dependency
    runs knowledge -> (consumed by) student_model/teaching_engine, one-way.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    KNOWLEDGE_INTELLIGENCE_MODE (default on). When off, callers fall back to
    the SkillGraph seed exactly (zero M5 surface).
  - LEARNED vs SEED: seed (code) is the source of truth for curated facts;
    only reasoner-derived edges are persisted to disk (store.py,
    knowledge/graph.json). The seed is never written to disk.
"""
from __future__ import annotations

import copy
import os
import threading
from typing import Any

from .context_builder import (build_knowledge_context as _build_ctx,
                              render_knowledge_directive as _render)
from .graph import KnowledgeGraph
from .retriever import ConceptRetriever
from .schema import KnowledgeContext


def is_enabled() -> bool:
    """Whether the knowledge-intelligence layer is active (default on)."""
    return os.getenv("KNOWLEDGE_INTELLIGENCE_MODE", "1") not in ("0", "false", "False", "off")


class KnowledgeService:
    """Stateless-ish facade over the knowledge graph + retriever + reasoner."""

    def __init__(self) -> None:
        self._graph: KnowledgeGraph | None = None
        self._retriever: ConceptRetriever | None = None
        # per-student merged views (base graph + that student's M5.7 custom
        # graphs), invalidated by the store's cheap mtime stamp
        self._student_graphs: dict[str, tuple[tuple, KnowledgeGraph]] = {}
        self._student_retrievers: dict[str, tuple[int, ConceptRetriever]] = {}
        self._custom_lock = threading.Lock()
        # per-student merge locks: a sync handler (threadpool) and an async
        # handler (event loop) can hit the same cold namespace at once --
        # without the lock both built duplicate 15K-node graphs and the GIL
        # contention froze every other request.
        self._graph_build_locks: dict[str, threading.Lock] = {}
        self._graph_init_lock = threading.Lock()

    @property
    def graph(self) -> KnowledgeGraph:
        with self._graph_init_lock:
            if self._graph is None:
                self._graph = self._build_graph()
            return self._graph

    @property
    def retriever(self) -> ConceptRetriever:
        """Lazily-built ConceptRetriever over this service's graph."""
        if self._retriever is None:
            self._retriever = ConceptRetriever(self.graph)
        return self._retriever

    def _build_graph(self) -> KnowledgeGraph:
        from .reasoning import persist_result  # noqa: F401 (kept for callers)
        from .seed import seed_contents, seed_edges, seed_nodes
        from . import store as _store
        graph = KnowledgeGraph(nodes=seed_nodes(), edges=seed_edges(),
                               contents=seed_contents())
        # merge learned edges from disk (reasoner-derived); each still passes
        # add_edge's DAG guard, so a corrupt file cannot introduce a cycle.
        for e in _store.load_learned_edges():
            from .schema import KnowledgeEdge
            graph.add_edge(KnowledgeEdge.from_dict(e))
        return graph

    # --- M5.7: per-student merged view (seed + learned + custom graphs) ----
    def graph_for(self, student_id: str = "") -> KnowledgeGraph:
        """The base graph merged with public + this student's custom graphs.

        P6-B3：公用教材图谱（``public`` 命名空间）合并进**所有**用户的视图；
        自有教材图谱只合并进本人。缓存 key = 自有 ∪ 公用两个命名空间的 mtime
        stamp，任一侧写入都自动失效。无任何图谱时返回共享主图（零拷贝）。

        READ-ONLY and LLM-free: it only reads files written by the explicit
        textbook build flow.
        """
        try:
            sid = (student_id or "").strip()
            if not sid:
                return self.graph
            from . import store as _store
            from ...core.textbook import PUBLIC_STUDENT_ID
            own_stamp = _store.list_custom_stamp(sid)
            pub_stamp = () if sid == PUBLIC_STUDENT_ID else \
                _store.list_custom_stamp(PUBLIC_STUDENT_ID)
            if not own_stamp and not pub_stamp:
                return self.graph
            stamp = (own_stamp, pub_stamp)
            cached = self._student_graphs.get(sid)
            if cached and cached[0] == stamp:
                return cached[1]
            lock = self._graph_build_locks.setdefault(sid, threading.Lock())
            with lock:
                # re-check under the lock: another request may have finished
                # the (expensive) merge while this one waited
                cached = self._student_graphs.get(sid)
                if cached and cached[0] == stamp:
                    return cached[1]
                merged = copy.deepcopy(self.graph)
                from .schema import KnowledgeEdge, KnowledgeNode
                payloads = list(_store.list_custom_graphs(sid))
                if sid != PUBLIC_STUDENT_ID:
                    payloads += _store.list_custom_graphs(PUBLIC_STUDENT_ID)
                for payload in payloads:
                    for nd in payload.get("nodes", []) or []:
                        node = KnowledgeNode.from_dict(nd)
                        if node.id and node.id not in merged.nodes:
                            merged.ensure_node(node)
                    for ed in payload.get("edges", []) or []:
                        merged.add_edge(KnowledgeEdge.from_dict(ed), _trusted=True)
                    for c in payload.get("contents", []) or []:
                        cid = (c or {}).get("concept_id", "")
                        if cid and cid in merged.nodes:
                            merged.contents[cid] = c
                self._student_graphs[sid] = (stamp, merged)
                return merged
        except Exception:
            return self.graph

    def invalidate_custom_cache(self, owner_id: str = "") -> None:
        """Invalidate merged graph/retriever views after a custom-graph write.

        A private namespace only affects its owner. The public namespace is
        merged into every authenticated student's view, so changing it must
        invalidate every per-student cache rather than only ``public``.
        File stamps remain a secondary guard for writes performed outside the
        service process.
        """
        sid = (owner_id or "").strip()
        try:
            from ...core.textbook import PUBLIC_STUDENT_ID
            with self._custom_lock:
                if sid == PUBLIC_STUDENT_ID:
                    self._student_graphs.clear()
                    self._student_retrievers.clear()
                elif sid:
                    self._student_graphs.pop(sid, None)
                    self._student_retrievers.pop(sid, None)
        except Exception:
            # Cache invalidation must never turn a successful persistent write
            # into an application error. The mtime stamp still self-heals on
            # the next graph_for() call.
            pass

    def retriever_for(self, student_id: str = "") -> ConceptRetriever:
        """ConceptRetriever over graph_for(student_id); cached per graph."""
        try:
            g = self.graph_for(student_id)
            if g is self.graph:
                return self.retriever
            sid = (student_id or "").strip()
            cached = self._student_retrievers.get(sid)
            if cached and cached[0] == id(g):
                return cached[1]
            r = ConceptRetriever(g)
            self._student_retrievers[sid] = (id(g), r)
            return r
        except Exception:
            return self.retriever

    # --- read surface ----------------------------------------------------
    def match_concept(self, text: str, *, level: str = "",
                      student_id: str = "", strict: bool = False):
        """Map free text to a KnowledgeNode (or None).

        level: stage-aware preference (M5.8). student_id: include that
        student's custom graphs (M5.7). strict: raise the bar from fuzzy
        0.34 to exact/alias/substring — use for attribution decisions (BKT
        writeback, quiz anchoring) where a wrong match is worse than none.
        """
        try:
            from .graph import _STRICT_THRESHOLD
            node, _score = self.graph_for(student_id).match_concept_scored(
                text, level=level,
                min_score=_STRICT_THRESHOLD if strict else 0.34)
            return node
        except Exception:
            return None

    def retrieve(self, query: str, *, top_k: int = 4,
                 traverse_depth: int = 1,
                 student_id: str = "", level: str = "") -> list[dict[str, Any]]:
        """Hybrid concept retrieval: BM25 over concepts + KG-traversal fusion.
        `level` 让排序学段感知（空 = K-12 优先，见 retriever._stage_pref）。"""
        try:
            return self.retriever_for(student_id).retrieve(
                query, top_k=top_k, traverse_depth=traverse_depth, level=level)
        except Exception:
            return []

    def confidence_for(self, query: str, *, student_id: str = "") -> float:
        """Top-1 retrieval confidence in [0,1]; 0 when nothing matches."""
        try:
            return self.retriever_for(student_id).confidence_for(query)
        except Exception:
            return 0.0

    def build_context(self, *, concept: str, mastery_view: dict[str, Any] | None = None,
                      knowledge_store: Any | None = None,
                      grade: str = "", student_id: str = "") -> KnowledgeContext:
        """Assemble a KnowledgeContext (content + materials resolved)."""
        try:
            return _build_ctx(concept=concept, graph=self.graph_for(student_id),
                              retriever=self.retriever_for(student_id),
                              mastery_view=mastery_view,
                              knowledge_store=knowledge_store, grade=grade)
        except Exception:
            return KnowledgeContext(concept=concept)

    def build_directive(self, *, concept: str,
                        mastery_view: dict[str, Any] | None = None,
                        knowledge_store: Any | None = None,
                        grade: str = "", student_id: str = "") -> str:
        """Render the [知识智能·...] soft-directive block for one concept.

        Returns "" when there is nothing actionable (concept outside the
        ontology, or below confidence threshold). This is the single call the
        Supervisor makes per turn. Never raises.
        """
        try:
            ctx = self.build_context(concept=concept, mastery_view=mastery_view,
                                     knowledge_store=knowledge_store,
                                     grade=grade, student_id=student_id)
            return _render(ctx)
        except Exception:
            return ""

    # --- M5.7 write surface: custom graphs (唯一性铁律见 custom_graph.py) ---
    def list_custom(self, *, student_id: str) -> list[dict[str, Any]]:
        """Meta summaries of the student's active custom graphs."""
        try:
            from . import store as _store
            from .custom_graph import graph_meta
            out = []
            for p in _store.list_custom_graphs(student_id):
                meta = graph_meta(p)
                meta["archive_count"] = _store.archive_count(
                    student_id, meta["topic_key"])
                out.append(meta)
            return out
        except Exception:
            return []

    def delete_custom(self, *, student_id: str, topic_key: str) -> dict[str, Any]:
        """Remove the active graph (a final archive copy is kept on disk)."""
        try:
            from . import store as _store
            with self._custom_lock:
                ok = _store.delete_custom_graph(student_id, topic_key)
            return {"status": "ok"} if ok else \
                {"status": "not_found", "message": "该主题还没有图谱"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # --- M5.5 write surface: Dependency Reasoner -------------------------
    async def reason_about(self, concept: str, *, subject: str = "",
                           grade: str = "",
                           llm: Any | None = None) -> dict[str, Any]:
        """Auto-discover prerequisite edges for a concept (M5.5).

        Returns the ReasonerResult as a dict. Writes validated edges into the
        live graph + persists them. Never raises; on any failure returns a
        degraded rationale. The LLM is the only async input; callers pass the
        live client (or None to dry-run candidates without writing).
        """
        try:
            from .reasoning import DependencyReasoner, persist_result
            reasoner = DependencyReasoner(self.graph, self.retriever)
            result = await reasoner.reason_about(
                concept, subject=subject, grade=grade, llm=llm)
            if result.learned_edges:
                persist_result(result, self.graph)
            return result.to_dict()
        except Exception as e:
            return {"concept_id": concept, "learned_edges": [],
                    "rejected": [], "rationale": f"推理降级：{e}"}


# --- process-level cache (single graph for the single-student system) ------

_INSTANCE: KnowledgeService | None = None


def get_knowledge_service() -> KnowledgeService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = KnowledgeService()
    return _INSTANCE
