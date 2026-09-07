"""Knowledge Intelligence (module 5: knowledge-ontology layer).

Where the Supervisor (M1) answers "what task to run", the Student Model (M2)
answers "what does this student know", the Teaching Engine (M3) answers "how to
teach now", and Assessment (M4) answers "did they learn it", this module
answers the layer beneath all of them:

    "what should the system KNOW about the subject matter?"

It owns the knowledge *ontology*: concepts, the typed relationships between
them (PREREQUISITE / RELATED / APPLICATION / MISCONCEPTION), and their teaching
content. The Student Model's SkillGraph is a per-student projection of the
PREREQUISITE edges drawn from here, so there is ONE source of truth for
learning order (M5 when enabled, skill_graph_seed as the resilient fallback).

Pipeline (filled in across phases):
  M5.1 schema + graph + facade + switch              <-- this commit
  M5.2 expanded seed ontology
  M5.3 ConceptRetriever (BM25 over concepts + KG traversal fusion)
  M5.4 Content Resolver + Context Builder + SkillGraph bridge + supervisor hook
  M5.5 Dependency Reasoner (candidate retrieval + rule filter + LLM validator
       + DAG-safe write) -- the only LLM-bearing component

Design contract (must hold to protect M1-M4):
  - PURE-READ over student_model: callers pass mastery/materials in as plain
    data; this package never imports student_model at runtime. The dependency
    graph stays one-directional (student_model/teaching_engine -> knowledge
    is downward).
  - SINGLE TRUTH SOURCE for PREREQUISITE edges: when this module is enabled,
    SkillGraph prerequisite edges come from here; when disabled, the existing
    skill_graph_seed is authoritative. Never both at once.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    KNOWLEDGE_INTELLIGENCE_MODE (default on); when off, every layer falls back
    to byte-identical M1-M4 behavior.
  - DETERMINISTIC-FIRST: M5.1-M5.4 are pure functions over the graph (no LLM,
    no network). Only M5.5 uses the LLM, and only to enrich newly-seen
    auto-nodes -- never on the critical teaching path.
"""
from __future__ import annotations

from .graph import KnowledgeGraph
from .manager import KnowledgeService, get_knowledge_service, is_enabled
from .retriever import ConceptRetriever
from .content import ContentResolver
from .context_builder import (build_knowledge_context,
                              render_knowledge_directive)
from .bridge import prerequisites_for, skill_node_extras, node_aliases
from .reasoning import DependencyReasoner, ReasonerResult, persist_result
from .schema import (EdgeType, KnowledgeContent, KnowledgeContext,
                     KnowledgeEdge, KnowledgeNode)

__all__ = [
    "EdgeType",
    "KnowledgeContent",
    "KnowledgeContext",
    "KnowledgeEdge",
    "KnowledgeGraph",
    "KnowledgeNode",
    "KnowledgeService",
    "ConceptRetriever",
    "ContentResolver",
    "build_knowledge_context",
    "render_knowledge_directive",
    "skill_node_extras",
    "prerequisites_for",
    "node_aliases",
    "DependencyReasoner",
    "ReasonerResult",
    "get_knowledge_service",
    "is_enabled",
]
