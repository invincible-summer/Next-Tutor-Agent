"""SkillGraphBridge: project M5 knowledge onto the Student Model's SkillGraph.

This is the single touch on existing working code. The Student Model already
owns a SkillGraph (a prerequisite DAG fused with per-student BKT mastery). When
M5 is enabled, this bridge lets SkillGraph draw its richer match surface
(aliases / descriptions) and, for auto-derived floating nodes, richer
prerequisite edges from M5's graph -- without SkillGraph ever importing M5.

Design (mirrors how teaching_engine reuses, not rebuilds):
  - DOWNWARD dependency only: M5 knows nothing about SkillGraph; the bridge
    reads M5 and produces plain dicts the SkillGraph can merge. No cycle.
  - GUARDED: every helper is defensive; the caller (StudentModel) wraps the
    merge in try/except, so a bridge failure cannot break a turn.
  - SUPERSET-CONSISTENT: for seeded node ids the bridge returns the SAME
    prerequisites SkillGraph's own seed already has (they are provably equal --
    see test_seed_prereqs_equal_skill_graph_seed). So enabling M5 changes
    nothing for seeded skills; it only ADDS match accuracy (aliases) and fills
    in prereqs for previously-floating auto nodes.

The two projections SkillGraph actually consumes:
  - node_aliases(id)        : aliases to widen match_concept
  - prerequisites_for(id)   : PREREQUISITE ancestor ids (root->...->id)
"""
from __future__ import annotations

from typing import Any

from .schema import EdgeType


def node_aliases(graph, node_id: str) -> list[str]:
    """Return the aliases M5 holds for a concept (for wider fuzzy matching)."""
    try:
        n = graph.get(node_id)
        return list(n.aliases) if n else []
    except Exception:
        return []


def prerequisites_for(graph, node_id: str) -> list[str]:
    """PREREQUISITE ancestors of a concept, root-first.

    For a seeded id this equals SkillGraph's own prerequisite closure (the seed
    pairs are identical), so merging is a no-op there. For an auto-derived
    floating node that M5 later learned a prereq for (M5.5 Reasoner), this is
    the new value.
    """
    try:
        return graph.prerequisites_of(node_id)
    except Exception:
        return []


def skill_node_extras(graph, concept: str, fallback_id: str = "") -> dict[str, Any]:
    """Look up M5 enrichment for a concept, as plain data.

    Returns {} when M5 has nothing (concept not in the ontology). Keys:
      node_id, name, subject, difficulty, aliases, prerequisites.
    The caller (StudentModel) may use these to (a) widen match, (b) attach
    learned prereqs to a floating node. Never raises.
    """
    try:
        node = graph.match_concept(concept)
        if node is None:
            return {}
        return {
            "node_id": node.id,
            "name": node.name,
            "subject": node.subject,
            "difficulty": node.difficulty,
            "aliases": list(node.aliases),
            "prerequisites": graph.prerequisites_of(node.id),
        }
    except Exception:
        return {}
