"""Skill Graph: a directed DAG of knowledge dependencies.

Edges run prerequisite -> skill ("to learn B you first need A"). The graph is
seeded from skill_graph_seed.py and extended at runtime with auto-derived
nodes (concepts observed in conversation but not in the seed get a free
floating node so they are still tracked, just without prerequisite edges).

The core operations the adaptation engine needs:
  - match_concept(text)     : fuzzy map a free-text concept to a node id
  - prerequisites_of(id)    : transitive closure of prerequisites (all ancestors)
  - unmet_prerequisites(id) : ancestors the student has NOT yet mastered
  - next_learnable(subject) : lowest-difficulty skills whose prereqs are all met
  - descendants_of(id)      : skills that depend on this one (for review paths)

Deterministic, pure-python, no deps. Fuzzy match is a simple token-overlap /
substring scorer -- deliberately not an embedding model (keeps the module
offline and testable; V4 can upgrade to vectors).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .skill_graph_seed import seed_nodes

# mastery threshold above which a prerequisite is considered "met"
MASTERY_MET_THRESHOLD = 0.6


@dataclass
class SkillNode:
    id: str
    name: str
    subject: str = ""
    prerequisites: list[str] = field(default_factory=list)
    difficulty: int = 3
    aliases: list[str] = field(default_factory=list)   # M5.8: widened match surface
    level: str = ""          # 小学/初中/高中/自定义 ("" = unknown/cross-level)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "subject": self.subject,
                "prerequisites": list(self.prerequisites),
                "difficulty": self.difficulty, "aliases": list(self.aliases),
                "level": self.level}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillNode":
        return cls(id=str(d.get("id", "")), name=str(d.get("name", "")),
                   subject=str(d.get("subject", "")),
                   prerequisites=list(d.get("prerequisites", []) or []),
                   difficulty=int(d.get("difficulty", 3)),
                   aliases=[str(a) for a in (d.get("aliases", []) or [])],
                   level=str(d.get("level", "") or ""))


def _tokenize(text: str) -> set[str]:
    """CJK + latin token set for fuzzy matching.

    Chinese chars count as single tokens (a 2-char concept yields 2 tokens);
    latin runs collapse to lowercase words. Punctuation is dropped.
    """
    import re
    out: set[str] = set()
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            out.add(ch)
    for m in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(m) > 1:
            out.add(m)
    return out


class SkillGraph:
    """Directed prerequisite DAG over SkillNodes.

    `strict_match` is set by the Student Model when the graph has been
    extended with the full M5 knowledge ontology (M5.8): with ~1400 nodes a
    loose token-overlap hit (e.g. 相似对角化 -> 相似三角形 at 0.43) is worse
    than no match for BKT attribution, so ensure_node_for then requires the
    strict bar (exact / alias / substring). Seeded-only graphs keep the
    legacy loose threshold.
    """
    def __init__(self, extra_nodes: list[dict[str, Any]] | None = None) -> None:
        self.nodes: dict[str, SkillNode] = {}
        self.strict_match = False
        # Derived traversal state (lazy; see invalidate_traversal_cache).
        self._ancestors_cache: dict[str, list[str]] = {}
        self._rev_adjacency: dict[str, list[str]] | None = None
        for n in seed_nodes():
            node = SkillNode.from_dict(n)
            self.nodes[node.id] = node
        # legacy seed ids are the keyspace of all persisted BKT mastery data;
        # match_concept prefers them on near-ties so attribution never splits
        # onto a duplicate node introduced by the M5 ontology (M5.8)
        self._seed_ids: set[str] = set(self.nodes)
        for n in (extra_nodes or []):
            node = SkillNode.from_dict(n)
            # only register nodes not already in the seed (seed wins)
            if node.id not in self.nodes:
                self.nodes[node.id] = node

    # --- lookup ----------------------------------------------------------
    def get(self, node_id: str) -> SkillNode | None:
        return self.nodes.get(node_id)

    def match_concept(self, text: str, *,
                      threshold: float = 0.34) -> SkillNode | None:
        """Fuzzy-match a free-text concept to a graph node.

        Scoring: exact name match wins; alias exact scores 0.95; substring
        0.8 (both sides >= 2 chars); token-overlap (Jaccard-ish); id-tail
        0.6. Returns None below `threshold` -- the caller then treats the
        concept as unseeded.
        """
        text = (text or "").strip()
        if not text:
            return None
        q_tokens = _tokenize(text)
        # Stable legacy ontology ids are the canonical BKT namespace. When a
        # seed name/alias is an exact concept, return it before comparing the
        # much larger textbook ontology; otherwise an exact custom textbook
        # node can steal attribution from (for example) physics.dynamics.friction.
        for n in self.nodes.values():
            if n.id in self._seed_ids and (
                    n.name == text or text in n.aliases
                    or (len(text) >= 2 and text in n.name)):
                return n
        best, best_score = None, 0.0
        best_seed, best_seed_score = None, 0.0
        for n in self.nodes.values():
            score = 0.0
            if n.name == text:
                score = 1.0
            for a in n.aliases:
                if a and a == text:
                    score = max(score, 0.95)
                    break
            if score < 0.9 and len(text) >= 2 and len(n.name) >= 2:
                if text in n.name or n.name in text:
                    score = max(score, 0.8)
            if q_tokens:
                n_tokens = _tokenize(n.name)
                for a in n.aliases:           # fold aliases into token overlap
                    n_tokens |= _tokenize(a)
                if n_tokens:
                    inter = len(q_tokens & n_tokens)
                    score = max(score, inter / max(1, len(q_tokens | n_tokens)))
                    # also match the id tail segment (e.g. "monotonicity")
            # id tail match bonus
            tail = n.id.rsplit(".", 1)[-1]
            if tail and tail in text.lower():
                score = max(score, 0.6)
            if score > best_score:
                best, best_score = n, score
            if n.id in self._seed_ids and score > best_seed_score:
                best_seed, best_seed_score = n, score
        # legacy-seed preference on near-ties (e.g. pack node name-exact 1.0
        # vs legacy alias-exact 0.95): keeps BKT attribution on the stable
        # legacy ids. Deterministic; falls back to the raw best otherwise.
        if best_seed is not None and best_seed_score >= threshold \
                and best_score - best_seed_score <= 0.06:
            return best_seed
        return best if best_score >= threshold else None

    def ensure_node_for(self, concept: str, subject: str = "") -> SkillNode:
        """Return the node for a concept, creating a floating one if absent.

        Auto-created nodes have a stable id derived from the concept so the
        same concept maps to the same node across turns. They have no
        prerequisites (the seed graph does not know about them) but are still
        tracked, so mastery/concept-memory work even for unseeded topics.
        When strict_match is on (M5-extended graph), matching uses the strict
        bar so an off-syllabus concept keeps its own floating node instead of
        being mis-attributed to a superficially similar one.
        """
        existing = self.match_concept(
            concept, threshold=0.6 if self.strict_match else 0.34)
        if existing:
            return existing
        nid = self._auto_id(concept, subject)
        if nid in self.nodes:
            return self.nodes[nid]
        node = SkillNode(id=nid, name=concept.strip()[:40], subject=subject,
                         prerequisites=[], difficulty=3)
        self.nodes[nid] = node
        return node

    @staticmethod
    def _auto_id(concept: str, subject: str) -> str:
        import re
        area = subject or "general"
        c = re.sub(r"[^\w]+", "_", concept.strip())[:30].strip("_").lower() or "concept"
        return f"{area}.auto.{c}"

    # --- graph traversal -------------------------------------------------
    def prerequisites_of(self, node_id: str) -> list[str]:
        """Transitive closure of prerequisites (all ancestors), DFS.

        Memoized: prerequisites are immutable after node construction and the
        M5-extended graph has ~15K nodes, so recomputing shared ancestor
        chains per next_learnable() scan was the learning-path hot spot.
        """
        cached = self._ancestors_cache.get(node_id)
        if cached is not None:
            return list(cached)
        seen: set[str] = set()
        order: list[str] = []
        stack = list(self._direct_prereqs(node_id))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            order.append(cur)
            stack.extend(self._direct_prereqs(cur))
        self._ancestors_cache[node_id] = order
        return list(order)

    def _direct_prereqs(self, node_id: str) -> list[str]:
        n = self.nodes.get(node_id)
        return list(n.prerequisites) if n else []

    def _reverse_adjacency(self) -> dict[str, list[str]]:
        """prereq -> [dependents], built once per graph version.

        Nodes' prerequisite lists never change after construction, so the
        only invalidation trigger is the M5 ontology merge adding nodes with
        new edges (see invalidate_traversal_cache).
        """
        if self._rev_adjacency is None:
            rev: dict[str, list[str]] = {}
            for nid, n in self.nodes.items():
                for pre in n.prerequisites:
                    rev.setdefault(pre, []).append(nid)
            self._rev_adjacency = rev
        return self._rev_adjacency

    def invalidate_traversal_cache(self) -> None:
        """Drop derived traversal state after bulk node insertion (M5 merge)."""
        self._ancestors_cache = {}
        self._rev_adjacency = None

    def descendants_of(self, node_id: str) -> list[str]:
        """Skills that directly or transitively depend on node_id."""
        rev = self._reverse_adjacency()
        seen: set[str] = set()
        order: list[str] = []
        stack = list(rev.get(node_id, []))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            order.append(cur)
            stack.extend(rev.get(cur, []))
        return order

    def unmet_prerequisites(self, node_id: str, mastery: dict[str, Any],
                            *, threshold: float = MASTERY_MET_THRESHOLD) -> list[SkillNode]:
        """Prerequisite ancestors the student has NOT yet mastered.

        `mastery` is the {skill_id: {p_known: float}} view from MasteryTracker.
        """
        out: list[SkillNode] = []
        for pre in self.prerequisites_of(node_id):
            rec = mastery.get(pre) or {}
            p = float(rec.get("p_known", 0)) if isinstance(rec, dict) else 0.0
            if p < threshold:
                node = self.nodes.get(pre)
                if node:
                    out.append(node)
        return out

    def next_learnable(self, subject: str | None, mastery: dict[str, Any], *,
                       threshold: float = MASTERY_MET_THRESHOLD,
                       limit: int = 5) -> list[SkillNode]:
        """Lowest-difficulty skills whose prerequisites are all met but which
        the student has not yet mastered themselves.

        This is the "what should I learn next" primitive. Filters by subject
        when given; excludes already-mastered skills.
        """
        ready: list[SkillNode] = []
        for nid, n in self.nodes.items():
            if subject and n.subject != subject:
                continue
            rec = mastery.get(nid) or {}
            p = float(rec.get("p_known", 0)) if isinstance(rec, dict) else 0.0
            if p >= threshold:
                continue  # already mastered
            unmet = self.unmet_prerequisites(nid, mastery, threshold=threshold)
            if not unmet:
                ready.append(n)
        ready.sort(key=lambda n: (n.difficulty, n.id))
        return ready[:limit]

    def to_node_dicts(self) -> list[dict[str, Any]]:
        return [n.to_dict() for n in self.nodes.values()]
