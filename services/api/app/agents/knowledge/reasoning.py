"""DependencyReasoner: auto-discover prerequisite edges for new concepts.

This solves the M2 leftover: a concept observed in conversation that is NOT in
the seed becomes a SkillGraph "floating node" with no prerequisite links, so
the engine cannot reason about learning order for it. The Reasoner fills those
gaps, but deliberately does NOT let an LLM redraw the whole graph -- it only
adds INDIVIDUAL edges, each validated, threshold-gated, and DAG-checked.

Pipeline (candidate -> rule filter -> LLM validator -> gate -> DAG-safe write):

  New Concept (not in seed, or an under-linked seeded one)
      |
      v
  Candidate retrieval: BM25 over existing concepts for "what might be a
      prerequisite" (reuses ConceptRetriever, no new index)
      |
      v
  Rule filter: drop same-concept / already-linked / higher-difficulty-as-prereq
      / wrong-subject candidates -- pure deterministic pruning
      |
      v
  LLM validator: ONE structured call returning {relation, confidence, reason}
      per surviving candidate. relation is prerequisite|related|none.
      |
      v
  Threshold gate: only confidence >= _PREREQ_THRESHOLD + relation=prerequisite
      survive into a write
      |
      v
  DAG-safe write: graph.add_edge (cycle-guarded) + store.append_learned_edge

The LLM is used ONLY here in M5, and only to enrich new/under-linked nodes --
never on the critical teaching path (the retriever/context-builder stay
deterministic). Same reasoning as M2/M3/M4: stability + testability. Failures
degrade to "no edge added" (never raises); a bad LLM hint cannot corrupt the
DAG because add_edge rejects cycles.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ...core.llm_async import AsyncLLMClient
from .schema import EdgeType, KnowledgeEdge

# confidence above which a validated prerequisite edge is actually written
_PREREQ_THRESHOLD = 0.65
# how many candidate prereqs to ask the LLM about (keeps it cheap + bounded)
_MAX_CANDIDATES = 4


@dataclass
class ReasonerResult:
    """Outcome of reasoning about one concept's prerequisites."""
    concept_id: str = ""
    learned_edges: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id,
                "learned_edges": list(self.learned_edges),
                "rejected": list(self.rejected),
                "rationale": self.rationale}


class DependencyReasoner:
    """Auto-discover prerequisite edges via candidate retrieval + LLM validation."""

    def __init__(self, graph, retriever=None) -> None:
        self.graph = graph
        self.retriever = retriever

    async def reason_about(self, concept: str, *, subject: str = "",
                           grade: str = "",
                           llm: AsyncLLMClient | None = None,
                           max_candidates: int = _MAX_CANDIDATES) -> ReasonerResult:
        """Find + validate candidate prerequisite edges for a concept.

        Returns a ReasonerResult; writes nothing itself (the caller decides
        whether to persist via persist_result). Never raises.
        """
        res = ReasonerResult(concept_id=concept, rationale="无候选或未启用")
        try:
            node = self.graph.match_concept(concept) or self.graph.get(concept)
            if node is None:
                return res
            candidates = self._candidates(node, subject, max_candidates,
                                          level=grade)
            if not candidates:
                res.rationale = "无满足规则的候选前置概念"
                return res
            if llm is None:
                res.rationale = "无 LLM，跳过校验（候选未写入）"
                res.rejected = [{"source": c.id, "reason": "no_llm"} for c in candidates]
                return res
            validated = await self._validate_batch(node, candidates, llm, grade)
            for cand, verdict in validated:
                entry = {"source": cand.id, "target": node.id,
                         "type": EdgeType.PREREQUISITE.value,
                         "weight": verdict.get("confidence", 0.0),
                         "provenance": "reasoner",
                         "reason": verdict.get("reason", "")}
                rel = str(verdict.get("relation", "none")).lower()
                conf = float(verdict.get("confidence", 0.0))
                if rel == "prerequisite" and conf >= _PREREQ_THRESHOLD:
                    res.learned_edges.append(entry)
                else:
                    res.rejected.append({**entry, "rejection": f"{rel}@{conf:.2f}"})
            n = len(res.learned_edges)
            res.rationale = (f"校验{len(validated)}候选，新增{n}条前置边"
                             if validated else "LLM 校验无结果")
            return res
        except Exception as e:
            res.rationale = f"推理降级：{e}"
            return res

    # --- stage 1 + 2: candidate retrieval + rule filter ------------------
    def _candidates(self, node, subject: str, max_n: int, level: str = ""):
        """Existing concepts that could be a prerequisite of `node`.

        Rules (deterministic pruning before any LLM call):
          - not the node itself
          - not already a prerequisite/related neighbor of the node
          - same subject when the node has one (cross-subject prereqs are rare
            and noisy; allow only when subject unknown)
          - lower-or-equal difficulty (a prereq should not be harder than the
            target) -- a coarse but sound prior
          - retrieval ranking is stage-aware via `level`（空 = K-12 优先，
            见 retriever._stage_pref）
        """
        if self.retriever is None:
            return []
        existing = set(self.graph.neighbors(node.id))
        existing.update(self.graph.prerequisites_of(node.id))
        existing.add(node.id)
        hits = self.retriever.retrieve(node.name, top_k=max_n * 3,
                                       traverse_depth=0, level=level)
        out = []
        for h in hits:
            cid = h.get("concept_id", "")
            cand = self.graph.get(cid)
            if cand is None or cid in existing:
                continue
            if node.subject and cand.subject and cand.subject != node.subject:
                continue
            if cand.difficulty > node.difficulty:
                continue
            out.append(cand)
            if len(out) >= max_n:
                break
        return out

    # --- stage 3: LLM validation (batched, structured output) -----------
    async def _validate_batch(self, node, candidates, llm, grade):
        """Ask the LLM to judge each candidate in ONE call. Returns
        [(candidate_node, verdict_dict), ...]."""
        prompt = _build_prompt(node, candidates, grade)
        try:
            raw, _usage = await llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=600)
        except Exception:
            return []
        verdicts = _parse_verdicts(raw, [c.id for c in candidates])
        return [(c, verdicts.get(c.id, {"relation": "none", "confidence": 0.0}))
                for c in candidates]


_VALIDATE_PROMPT = """你是学科知识结构专家。判断下列候选概念是否为「{concept}」（学科：{subject}，学段：{grade}）的前置知识。

目标概念：{concept}（难度 {diff}/5）
候选前置概念：
{cand_list}

对每个候选，判断它与「{concept}」的关系，只输出一个 JSON 对象（不要任何其它文字、不要 markdown）：
{{
  "verdicts": [
    {{"id": "<候选id>", "relation": "prerequisite|related|none", "confidence": 0.0-1.0, "reason": "一句话理由"}}
  ]
}}
判定原则：
 - prerequisite：学「{concept}」之前必须先掌握该候选（强依赖），confidence 给 0.7-0.95。
 - related：两者有关联但无学习先后依赖，confidence 给 0.3-0.6。
 - none：无明显关系，confidence 给 0.0-0.2。
 - 每个候选都要给一个 verdict，id 必须与候选列表完全对应。"""


def _build_prompt(node, candidates, grade: str) -> str:
    cand_lines = []
    for i, c in enumerate(candidates, 1):
        cand_lines.append(f"  {i}. id={c.id} 名称={c.name} 难度={c.difficulty}/5"
                          + (f" 说明={c.description[:40]}" if c.description else ""))
    return _VALIDATE_PROMPT.format(
        concept=node.name, subject=node.subject or "未知", grade=grade or "本科",
        diff=node.difficulty,
        cand_list="\n".join(cand_lines) or "  （无）")


def _parse_verdicts(raw: str, expected_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Extract the verdicts JSON. Tolerant: extracts the first {...} block,
    then pulls the verdicts list. Returns {} on any parse failure."""
    out: dict[str, dict[str, Any]] = {}
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = m.group(0) if m else raw
        data = json.loads(candidate)
        verdicts = data.get("verdicts", []) if isinstance(data, dict) else []
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("id", ""))
            if not vid:
                continue
            out[vid] = {
                "relation": str(v.get("relation", "none")),
                "confidence": _clamp_conf(v.get("confidence", 0.0)),
                "reason": str(v.get("reason", ""))[:120],
            }
        # backfill any expected id the LLM skipped as a safe "none"
        for eid in expected_ids:
            if eid not in out:
                out[eid] = {"relation": "none", "confidence": 0.0, "reason": "缺失"}
    except Exception:
        pass
    return out


def _clamp_conf(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def persist_result(result: ReasonerResult, graph, *, store=None) -> int:
    """Write a ReasonerResult's learned edges into the graph + store.

    Each edge goes through graph.add_edge (cycle-guarded) so a bad hint cannot
    corrupt the DAG. Returns the number actually written. The store write is
    fire-and-forget (defensive). Never raises.
    """
    written = 0
    try:
        from . import store as _store_mod
        st = store or _store_mod
        for entry in result.learned_edges:
            edge = KnowledgeEdge(
                source=entry.get("source", ""),
                target=entry.get("target", ""),
                type=EdgeType.from_value(entry.get("type")),
                weight=float(entry.get("weight", 1.0)),
                provenance=str(entry.get("provenance", "reasoner")),
            )
            if graph.add_edge(edge):
                written += 1
                try:
                    st.append_learned_edge(entry)
                except Exception:
                    pass
    except Exception:
        pass
    return written
