"""Curated seed ontology for the KnowledgeGraph — pack aggregator (M5.6).

The seed is a STRICT SUPERSET of student_model/skill_graph_seed.py: every node
id and every PREREQUISITE edge present there also appears here with the same
name, subject, and difficulty. On top of that base, the seed adds the richer
M5 fields (level / description / aliases / common_errors) and the non-ordering
edge types (RELATED / PART_OF / APPLICATION / MISCONCEPTION) that the Context
Builder surfaces as teaching hints.

Since M5.6 the seed is organized as curriculum packs under seed_packs/ — one
pack per (学段, 学科), registered by hand in seed_packs/__init__.py. This file
keeps the original public API (seed_nodes/seed_edges/seed_contents/
seed_skill_prereqs) so manager.py and the tests are untouched by the split.

Superset invariant (enforced by tests): the PREREQUISITE pairs declared by
skill_graph_seed must all appear in seed_skill_prereqs(), with matching node
attributes. New packs ADD nodes/edges beyond that base, so the invariant is
one-directional (skill_graph_seed ⊆ M5 seed), not exact equality.
"""
from __future__ import annotations

from . import seed_packs as _packs
from .schema import EdgeType


def seed_nodes() -> list[dict]:
    """All seed nodes (concepts + chapter containers), deep-copied."""
    return _packs.all_nodes()


def seed_edges() -> list[dict]:
    """All seed edges (pack-internal + cross-subject links), deep-copied."""
    return _packs.all_edges()


def seed_contents() -> list[dict]:
    """All seed teaching content, deep-copied."""
    return _packs.all_contents()


def seed_skill_prereqs() -> set[tuple[str, str]]:
    """The (source, target) PREREQUISITE pairs this seed declares.

    MUST cover the pairs skill_graph_seed declares (superset direction), so
    the bridge projects onto SkillGraph without divergence. Tested in
    test_knowledge.
    """
    return {(e["source"], e["target"]) for e in _packs.all_edges()
            if e["type"] == EdgeType.PREREQUISITE.value}
