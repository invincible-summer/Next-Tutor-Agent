"""KnowledgeGraph: the multi-edge concept DAG + its traversal primitives.

This is M5's load-bearing structure. It holds KnowledgeNodes and typed
KnowledgeEdges, enforces the DAG invariant on every PREREQUISITE write (a
cycle would silently corrupt learning-order reasoning across M2/M3/M4), and
exposes the traversal primitives the rest of M5 + the bridge need:

  - get(id) / match_concept(text) : look a concept up
  - prerequisites_of(id)          : transitive PREREQUISITE ancestors (root-first)
  - descendants_of(id)            : what depends on this (for review paths)
  - neighbors(id, edge_type)      : 1-hop related/application/misconception
  - neighborhood(id, depth, types): bounded traversal for retriever expansion
  - add_edge(...)                 : cycle-guarded write (seed + reasoner)

Deterministic, pure-python, no deps. Cycle detection is a reachability check
("can target already reach source?") -- O(V+E) per write via the adjacency
index, far cheaper than a full SCC pass. The textbook packs merge ~18K edges
into one graph, so every primitive here must stay indexed: dedupe is O(1)
(edge-key set) and traversals walk adjacency maps instead of scanning the
edge list. Fuzzy match reuses the token-overlap scorer shape from
student_model/skill_graph so the two graphs agree on what a concept is.
"""
from __future__ import annotations

import re
from typing import Any

from .schema import EdgeType, KnowledgeEdge, KnowledgeNode

# Match score below which a free-text concept is treated as "not in graph".
# Matches student_model.skill_graph's 0.34 threshold so the bridge stays
# consistent with how SkillGraph already decides concept identity.
_MATCH_THRESHOLD = 0.34

# Strict bar for ATTRIBUTION decisions (BKT writeback / custom-graph anchors):
# exact name (1.0), alias (0.95) and substring (0.8) pass; loose token-Jaccard
# does not. Prevents a large graph from mis-attributing e.g. "相似对角化" to
# "相似三角形" (Jaccard ~0.43) just because they share characters.
_STRICT_THRESHOLD = 0.6

# When two candidates score within this epsilon, the one whose level matches
# the student's level wins (stage-aware matching, M5.8).
_LEVEL_PREF_EPS = 0.06


def _tokenize(text: str) -> set[str]:
    """CJK + latin token set for fuzzy matching (mirrors skill_graph._tokenize).

    Kept local rather than imported so this package has no runtime dependency
    on student_model (which would create an upward import edge toward a layer
    that depends on THIS one via the bridge).
    """
    out: set[str] = set()
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            out.add(ch)
    for m in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(m) > 1:
            out.add(m)
    return out


class KnowledgeGraph:
    """Nodes + typed edges, with a guarded PREREQUISITE DAG."""

    def __init__(self, nodes: list[dict[str, Any]] | None = None,
                 edges: list[dict[str, Any]] | None = None,
                 contents: list[dict[str, Any]] | None = None) -> None:
        self.nodes: dict[str, KnowledgeNode] = {}
        self.edges: list[KnowledgeEdge] = []
        self.contents: dict[str, Any] = {}   # concept_id -> KnowledgeContent dict
        # Derived indexes maintained incrementally by add_edge. `edges` stays
        # the serialized source of truth; these exist so that merging the
        # public textbook packs (tens of thousands of edges) stays linear --
        # a per-add linear dedupe scan over `edges` made cold merges O(E^2)
        # and froze the whole service for minutes.
        self._edge_keys: set[tuple[str, str, EdgeType]] = set()
        # edge_type -> node -> [neighbor ids], insertion-ordered
        self._adj_out: dict[EdgeType, dict[str, list[str]]] = {}
        self._adj_in: dict[EdgeType, dict[str, list[str]]] = {}
        for n in (nodes or []):
            node = KnowledgeNode.from_dict(n)
            if node.id:
                self.nodes[node.id] = node
        for e in (edges or []):
            self.add_edge(KnowledgeEdge.from_dict(e), _trusted=True)
        for c in (contents or []):
            cid = (c or {}).get("concept_id", "")
            if cid:
                self.contents[cid] = c

    # --- lookup ----------------------------------------------------------
    def get(self, node_id: str) -> KnowledgeNode | None:
        return self.nodes.get(node_id)

    def match_concept(self, text: str, *, level: str = "") -> KnowledgeNode | None:
        """Fuzzy-match free text to a node (name / alias / id-tail / tokens).

        `level` (小学/初中/高中/本科) makes the match stage-aware: when two
        candidates score within _LEVEL_PREF_EPS, the one at the student's own
        stage wins (e.g. 浮力 exists in both 初中 and 高中 packs).
        """
        node, _score = self.match_concept_scored(text, level=level)
        return node

    def match_concept_scored(self, text: str, *, level: str = "",
                             min_score: float = _MATCH_THRESHOLD,
                             ) -> tuple[KnowledgeNode | None, float]:
        """Scored variant of match_concept. Returns (node, score); (None, 0.0)
        below `min_score`. Pass min_score=_STRICT_THRESHOLD for attribution
        decisions (BKT writeback, custom-graph anchors) where a loose
        token-overlap hit is worse than no match.

        Scoring layers (best wins), matching student_model.skill_graph so the
        bridge and the SkillGraph agree on identity:
          exact name      -> the node (score 1.0)
          alias exact     -> 0.95
          substring       -> 0.8
          id-tail in text -> 0.6
          token overlap   -> Jaccard
        """
        text = (text or "").strip()
        if not text:
            return None, 0.0
        # exact name (possibly several nodes share a name across stages)
        exact = [n for n in self.nodes.values() if n.name == text]
        if exact:
            if level:
                for n in exact:
                    if n.level == level:
                        return n, 1.0
            return exact[0], 1.0
        # alias exact + substring + token overlap (single pass)
        q_tokens = _tokenize(text)
        best, best_score = None, 0.0
        best_lv, best_lv_score = None, 0.0
        for n in self.nodes.values():
            score = 0.0
            for a in n.aliases:
                if a and a == text:
                    score = max(score, 0.95)
                    break
            if score < 0.9:
                # substring: require both sides >= 2 chars, else single-char
                # node names (角/数/式) would match any query containing them
                if len(text) >= 2 and len(n.name) >= 2 \
                        and (text in n.name or n.name in text):
                    score = max(score, 0.8)
            if q_tokens:
                n_tokens = _tokenize(n.name)
                for a in n.aliases:           # fold aliases into token overlap
                    n_tokens |= _tokenize(a)
                if n_tokens:
                    inter = len(q_tokens & n_tokens)
                    score = max(score, inter / max(1, len(q_tokens | n_tokens)))
            tail = n.id.rsplit(".", 1)[-1]
            if tail and tail in text.lower():
                score = max(score, 0.6)
            if score > best_score:
                best, best_score = n, score
            if level and n.level == level and score > best_lv_score:
                best_lv, best_lv_score = n, score
        # stage-aware preference: a same-stage candidate within epsilon of the
        # top score beats a cross-stage one (deterministic: ties keep the
        # higher raw score, then first-seen).
        if best_lv is not None and best_lv_score >= min_score \
                and best_score - best_lv_score <= _LEVEL_PREF_EPS:
            return best_lv, best_lv_score
        if best_score >= min_score:
            return best, best_score
        return None, 0.0

    def ensure_node(self, node: KnowledgeNode) -> KnowledgeNode:
        """Insert a node (id wins); no edge changes."""
        if node.id:
            self.nodes[node.id] = node
        return node

    # --- edge management (DAG-guarded) -----------------------------------
    def add_edge(self, edge: KnowledgeEdge, *, _trusted: bool = False) -> bool:
        """Add an edge. Returns True on accept, False on reject.

        Rejects (silently) when:
          - it would create a PREREQUISITE cycle, or
          - the same directed (source,target,type) edge already exists, or
          - either endpoint is unknown.

        `_trusted=True` is reserved for seeding (edges there are curated to be
        acyclic; the check still runs, but a future caller that pre-validates
        bulk loads can short-circuit). Learner/reasoner edges must go through
        this gate so a bad LLM hint can never corrupt the DAG.
        """
        if edge.source == edge.target or edge.source not in self.nodes \
                or edge.target not in self.nodes:
            return False
        # dedupe identical directed edge (O(1) via the key set)
        if (edge.source, edge.target, edge.type) in self._edge_keys:
            return False
        # DAG invariant: only PREREQUISITE edges constrain learning order, so
        # only they must stay acyclic. Reject if `target` can already reach
        # `source` via PREREQUISITE (adding source->target would close a loop).
        if edge.type == EdgeType.PREREQUISITE and not _trusted:
            if self._reaches(edge.target, edge.source, EdgeType.PREREQUISITE):
                return False
        self.edges.append(edge)
        self._edge_keys.add((edge.source, edge.target, edge.type))
        self._adj_out.setdefault(edge.type, {}).setdefault(
            edge.source, []).append(edge.target)
        self._adj_in.setdefault(edge.type, {}).setdefault(
            edge.target, []).append(edge.source)
        return True

    def _remove_edge(self, edge: KnowledgeEdge) -> bool:
        """Remove an existing edge object, keeping indices consistent.

        Inverse of add_edge for the direction-repair pass only: removes the
        edge from the list plus the (source,target,type) key set and both
        adjacency maps. Returns True when the edge was found and removed.
        """
        try:
            self.edges.remove(edge)
        except ValueError:
            return False
        self._edge_keys.discard((edge.source, edge.target, edge.type))
        out = self._adj_out.get(edge.type, {}).get(edge.source)
        if out and edge.target in out:
            out.remove(edge.target)
        inc = self._adj_in.get(edge.type, {}).get(edge.target)
        if inc and edge.source in inc:
            inc.remove(edge.source)
        return True

    def _reaches(self, src: str, dst: str, edge_type: EdgeType) -> bool:
        """Can `src` reach `dst` following edges of `edge_type` (src->...->dst)?"""
        adj = self._adj_out.get(edge_type) or {}
        seen: set[str] = set()
        stack = [src]
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(t for t in adj.get(cur, ()) if t not in seen)
        return False

    def edges_of(self, node_id: str, *, edge_type: EdgeType | None = None,
                 direction: str = "out") -> list[KnowledgeEdge]:
        """Edges touching node_id. direction: out/in/both."""
        out: list[KnowledgeEdge] = []
        for e in self.edges:
            touches = (e.source == node_id) if direction == "out" \
                else (e.target == node_id) if direction == "in" \
                else (e.source == node_id or e.target == node_id)
            if touches and (edge_type is None or e.type == edge_type):
                out.append(e)
        return out

    # --- traversal -------------------------------------------------------
    def prerequisites_of(self, node_id: str) -> list[str]:
        """Transitive closure of PREREQUISITE ancestors, returned root-first.

        Root-first (topologically oldest first) so the Context Builder can say
        "first review functions, then limits" in the right order. A node is
        NOT its own prerequisite.
        """
        ancestors = self._ancestors_set(node_id)
        # topological-ish ordering by difficulty (lower = earlier/foundational)
        ranked = sorted(ancestors, key=lambda nid: (
            self.nodes[nid].difficulty if nid in self.nodes else 3, nid))
        return ranked

    def _ancestors_set(self, node_id: str) -> set[str]:
        adj = self._adj_in.get(EdgeType.PREREQUISITE) or {}
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for src in adj.get(cur, ()):
                if src not in seen:
                    seen.add(src)
                    stack.append(src)
        return seen

    def descendants_of(self, node_id: str) -> list[str]:
        """Concepts that transitively depend on node_id (for review/next paths)."""
        adj = self._adj_out.get(EdgeType.PREREQUISITE) or {}
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for tgt in adj.get(cur, ()):
                if tgt not in seen:
                    seen.add(tgt)
                    stack.append(tgt)
        return sorted(seen, key=lambda nid: (
            self.nodes[nid].difficulty if nid in self.nodes else 3, nid))

    def neighbors(self, node_id: str, *, edge_type: EdgeType | None = None,
                  limit: int = 6) -> list[str]:
        """1-hop neighbors (RELATED/APPLICATION/MISCONCEPTION), de-duped.

        Bidirectional for non-PREREQUISITE edges (RELATED is symmetric in
        spirit), capped so a hub concept cannot flood the context.
        """
        out: list[str] = []
        seen: set[str] = {node_id}
        for e in self.edges:
            if edge_type is not None and e.type != edge_type:
                continue
            if e.type == EdgeType.PREREQUISITE:
                continue
            other = e.target if e.source == node_id \
                else e.source if e.target == node_id else None
            if other and other not in seen:
                seen.add(other)
                out.append(other)
        return out[:limit]

    def neighborhood(self, node_id: str, *, depth: int = 1,
                     edge_types: tuple[EdgeType, ...] | None = None,
                     limit: int = 12) -> list[str]:
        """Bounded BFS over the (non-PREREQUISITE) context graph.

        Used by the ConceptRetriever for KG-traversal expansion: starting from
        a matched concept, pull in related/applied concepts within `depth`
        hops. Only non-ordering edges are traversed here; PREREQUISITE chains
        are handled separately by prerequisites_of.
        """
        if node_id not in self.nodes:
            return []
        types = edge_types or (EdgeType.RELATED, EdgeType.APPLICATION)
        out_adjs = [self._adj_out.get(t) or {} for t in types]
        in_adjs = [self._adj_in.get(t) or {} for t in types]
        seen: set[str] = {node_id}
        frontier = [node_id]
        out: list[str] = []
        for _ in range(max(0, depth)):
            nxt: list[str] = []
            for cur in frontier:
                for adj in out_adjs:
                    for other in adj.get(cur, ()):
                        if other not in seen:
                            seen.add(other)
                            nxt.append(other)
                            out.append(other)
                for adj in in_adjs:
                    for other in adj.get(cur, ()):
                        if other not in seen:
                            seen.add(other)
                            nxt.append(other)
                            out.append(other)
            frontier = nxt
            if len(out) >= limit:
                break
        return out[:limit]

    # --- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "contents": list(self.contents.values()),
        }

    @property
    def size(self) -> tuple[int, int]:
        return len(self.nodes), len(self.edges)
