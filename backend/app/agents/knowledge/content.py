"""ContentResolver: turn an abstract concept into concrete teaching content.

A KnowledgeNode is the abstract concept (导数); teaching needs concrete content
(definition / formula / worked example / exercise hint). This resolver answers
that, with a deliberate source cascade so it never blocks a turn:

  1. SEED content   -- curated KnowledgeContent in seed.py for high-leverage
                       concepts (deterministic, always available, no I/O).
  2. MATERIAL       -- if the student uploaded course materials, BM25-search
                       them via the EXISTING core/knowledge_store.KnowledgeStore
                       for passages mentioning the concept (grounds teaching in
                       the student's actual textbook). Reused, not rebuilt.
  3. (future) LLM   -- generate a definition/example on the fly. Deliberately
                       NOT Phase 1 (same reasoning as student_model/teaching_engine:
                       keep M5 deterministic-first; LLM only where it is the
                       essence of the task, and that's M5.5's Reasoner, not here).

The resolver returns a KnowledgeContent plus a list of grounding material
snippets. Both feed the Context Builder. Import-clean: it takes the graph's
contents dict + an optional knowledge_store duck-typed object (anything with a
.search(query, top_k) method), never importing core.knowledge_store at module
scope, so this package stays decoupled.
"""
from __future__ import annotations

from typing import Any

from .schema import KnowledgeContent


class ContentResolver:
    """Resolve a concept id to teaching content + material grounding."""

    def __init__(self, graph_contents: dict[str, Any] | None = None,
                 knowledge_store: Any | None = None) -> None:
        # graph_contents: {concept_id: KnowledgeContent dict} (from the graph)
        self.contents = graph_contents or {}
        # duck-typed: anything with .search(query, top_k) -> list[{source,text,...}]
        self.store = knowledge_store

    def resolve(self, concept_id: str, *, query_hint: str = "",
                top_k: int = 3) -> tuple[KnowledgeContent, list[dict[str, Any]]]:
        """Return (content, material_snippets) for a concept.

        content is never None (an empty KnowledgeContent signals "nothing
        found"). material_snippets is [] when no store / no match. Never raises.
        """
        content = KnowledgeContent(concept_id=concept_id)
        snippets: list[dict[str, Any]] = []
        try:
            # 1. seed content
            seed = self.contents.get(concept_id)
            if seed:
                content = KnowledgeContent.from_dict(seed)
                content.source = "seed"
            # 2. material grounding (reuse the existing BM25 store)
            if self.store is not None and self._store_has_knowledge():
                q = query_hint or concept_id
                for hit in self._store_search(q, top_k=top_k):
                    snippets.append({
                        "source": hit.get("source", ""),
                        "text": (hit.get("text") or "")[:240],
                        "score": hit.get("score", 0.0),
                    })
                if snippets and not seed:
                    content.source = "material"
        except Exception:
            pass
        return content, snippets

    # --- duck-typed store access (guarded so a bare object is safe) -------
    def _store_has_knowledge(self) -> bool:
        try:
            return bool(self.store.has_knowledge())
        except Exception:
            return False

    def _store_search(self, query: str, *, top_k: int = 3) -> list[dict[str, Any]]:
        try:
            return list(self.store.search(query, top_k=top_k) or [])
        except Exception:
            return []
