"""KnowledgeContextBuilder: assemble the knowledge view one teaching turn uses.

This composes the outputs of the graph + retriever + content resolver + the
student's mastery into a single KnowledgeContext, then renders it as a
[知识智能·...] soft-directive block the Supervisor injects into the LLM context
(alongside the existing [学生智能·...] / [教学策略] blocks).

It is the ONLY place that decides what knowledge hints a turn sees, which keeps
the coupling surface to exactly one guarded call from the Supervisor. It stays
import-clean: mastery arrives as plain {id: p_known} dicts and materials as
plain snippet dicts -- it never imports student_model.

Rendering rules (each line is advisory; the LLM stays in charge):
  - [知识智能·概念定位]      which concept + confidence
  - [知识智能·前置补缺]      missing prereqs (only when any are unmet)
  - [知识智能·易错点]        node + MISCONCEPTION-edge common errors
  - [知识智能·教学示例]      recommended example / application
  - [知识智能·教材引用]      uploaded-material grounding (when present)
A concept with no graph match renders nothing (returns ""), so M5 is invisible
for topics outside the ontology -- never noise.
"""
from __future__ import annotations

from typing import Any

from .schema import EdgeType, KnowledgeContext


# confidence above which we bother emitting a knowledge block at all
_EMIT_THRESHOLD = 0.25


def build_knowledge_context(*, concept: str, graph, retriever=None,
                            mastery_view: dict[str, Any] | None = None,
                            knowledge_store: Any | None = None,
                            grade: str = "") -> KnowledgeContext:
    """Assemble a KnowledgeContext for one concept (plain-data in/out).

    graph: a KnowledgeGraph. retriever: a ConceptRetriever (optional; used for
    confidence + APPLICATION examples). knowledge_store: duck-typed material
    store for content grounding. Never raises; returns an empty context on any
    failure so the caller's soft-directive block is just skipped.
    """
    try:
        ctx = KnowledgeContext(concept=concept)
        node = graph.match_concept(concept, level=grade)
        if node is None:
            return ctx
        ctx.node_id = node.id
        ctx.confidence = _confidence(retriever, concept) or 0.9
        # prerequisite chain (root-first) + unmet prereqs vs mastery
        chain_ids = graph.prerequisites_of(node.id)
        ctx.prerequisite_chain = [_name(graph, i) for i in chain_ids]
        ctx.missing_prereqs = _missing_prereqs(graph, chain_ids, mastery_view)
        # common errors: node-level + MISCONCEPTION-edge targets
        errs = list(node.common_errors)
        for e in graph.edges_of(node.id, edge_type=EdgeType.MISCONCEPTION):
            if e.target != node.id and e.target not in (node.id,):
                errs.append(f"易与「{_name(graph, e.target)}」混淆")
        ctx.common_errors = errs[:3]
        # recommended example: APPLICATION neighbors + seed content example
        ctx.recommended_examples = _recommended_examples(graph, node.id)
        # related concepts (RELATED edges)
        ctx.related_concepts = [_name(graph, i) for i in
                                graph.neighbors(node.id, edge_type=EdgeType.RELATED,
                                                limit=3)]
        # content resolution (seed + material grounding)
        from .content import ContentResolver
        resolver = ContentResolver(graph.contents, knowledge_store)
        content, snippets = resolver.resolve(node.id, query_hint=concept, top_k=2)
        ctx.definition = content.definition
        if content.example and content.example not in ctx.recommended_examples:
            ctx.recommended_examples.insert(0, content.example)
        ctx.available_materials = [s.get("source", "") for s in snippets if s.get("source")]
        return ctx
    except Exception:
        return KnowledgeContext(concept=concept)


def render_knowledge_directive(ctx: KnowledgeContext) -> str:
    """Render a KnowledgeContext into a [知识智能·...] soft-directive block.

    Returns "" when there is nothing actionable (no node match, or confidence
    below threshold, or no fields populated) so the caller just skips it.
    """
    if not ctx or not ctx.node_id:
        return ""
    if ctx.confidence and ctx.confidence < _EMIT_THRESHOLD:
        return ""
    lines: list[str] = []
    lines.append(f"[知识智能·概念定位] 当前涉及知识点「{ctx.concept}」"
                 + (f"（相关度 {ctx.confidence:.2f}）" if ctx.confidence else ""))
    if ctx.missing_prereqs:
        lines.append("[知识智能·前置补缺] 该知识点依赖"
                     + "、".join(ctx.prerequisite_chain[:3])
                     + "；其中「" + "、".join(ctx.missing_prereqs)
                     + "」尚未掌握，讲解前请简要回顾。")
    elif ctx.prerequisite_chain:
        lines.append("[知识智能·前置链] 依赖链："
                     + " -> ".join(ctx.prerequisite_chain[:4]) + "。")
    if ctx.common_errors:
        lines.append("[知识智能·易错点] 注意：" + "；".join(ctx.common_errors[:3]))
    if ctx.recommended_examples:
        lines.append("[知识智能·教学示例] 建议举例：" + "；".join(ctx.recommended_examples[:2]))
    if ctx.available_materials:
        lines.append("[知识智能·教材引用] 学生已上传资料提及此知识点："
                     + "、".join(ctx.available_materials[:2]) + "，可引用讲解。")
    if ctx.related_concepts:
        lines.append("[知识智能·相关概念] 相关：" + "、".join(ctx.related_concepts[:3]))
    if len(lines) <= 1 and not ctx.common_errors and not ctx.recommended_examples:
        return ""
    return "\n".join(lines)


# --- helpers (kept local + plain-data to stay import-clean) ---------------

def _confidence(retriever, concept: str) -> float:
    if retriever is None:
        return 0.0
    try:
        return retriever.confidence_for(concept)
    except Exception:
        return 0.0


def _name(graph, node_id: str) -> str:
    n = graph.get(node_id)
    return n.name if n else node_id


def _missing_prereqs(graph, chain_ids: list[str],
                     mastery_view: dict[str, Any] | None) -> list[str]:
    """Prereqs KNOWN to be weak: tracked (has a mastery record) and p<0.6.

    Nodes with NO mastery record are excluded — after M5.6 the graph holds
    ~1500 seed-pack concepts while BKT only tracks a subset, so treating
    "untracked" as "unmastered" would flag false missing-prereqs on every
    turn. Untracked nodes still appear in the [前置链] line (informative, no
    false claim); per-node tracking for pack concepts arrives with the M5.8
    bridge wiring.
    """
    if not chain_ids:
        return []
    mv = mastery_view or {}
    out: list[tuple[float, str]] = []
    for nid in chain_ids:
        rec = mv.get(nid)
        if not isinstance(rec, dict) or "p_known" not in rec:
            continue  # untracked -> unknown, not "missing"
        p = float(rec.get("p_known", 0))
        if p < 0.6:
            out.append((p, _name(graph, nid)))
    out.sort(key=lambda x: x[0])
    return [name for _, name in out[:3]]


def _recommended_examples(graph, node_id: str) -> list[str]:
    out: list[str] = []
    for nb in graph.neighbors(node_id, edge_type=EdgeType.APPLICATION, limit=3):
        out.append(f"联系「{_name(graph, nb)}」")
    return out
