"""可重建投影：index / 概念视图（plan §6.3 / §12.1 缓存重建）。

缓存重建 = 纯函数重放 journal 中已接受的 observation/judgment/lifecycle/
synthesis——清空 index/views 后无需 LLM 也能重建相同当前结论（§12.1）。
语义重综合（synthesis job）不是这里的职责。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.atomic import atomic_write_text
from . import schema as S
from .store import EvidenceJournal, get_journal


def rebuild_index(student_id: str) -> dict[str, Any]:
    """从 journal 重建 `students/<sid>.learning_evidence_index.json`。

    index 是纯缓存：删除后重放产生相同内容；journal 后写失败只产生缓存
    滞后，不丢事实（§6.4）。
    """
    journal = get_journal(student_id)
    state = journal.state()
    sources: dict[str, Any] = {}
    for sid, src in state.sources.items():
        interp_id = src.current_interpretation_id
        interp = src.interpretations.get(interp_id, {})
        raw = interp.get("raw_interpretation") or {}
        claims = (raw.get("observation_claims")
                  if isinstance(raw, dict) else None) or []
        sources[sid] = {
            "kind": src.receipt.kind.value,
            "workspace_id": src.receipt.workspace_id_at_observation,
            "observed_at": src.receipt.observed_at,
            "availability": src.availability,
            "source_session_ref": src.receipt.source_session_ref,
            "task_ref": (src.receipt.task_ref.model_dump()
                         if src.receipt.task_ref else None),
            "attempt_id": src.receipt.attempt_id,
            "current_interpretation_id": interp_id,
            "interpretation_ids": sorted(src.interpretations),
            "concept_ids": sorted({str(c.get("concept_ref") or "")
                                   for c in claims
                                   if isinstance(c, dict)}),
            "scope_revision": src.receipt.scope_revision,
        }
    index = {
        "watermark": state.watermark,
        "schema_version": S.SCHEMA_VERSION,
        "sources": sources,
        "questions": {
            qid: {str(rev): {"rubric_hash": task.rubric_hash}
                  for rev, task in revs.items()}
            for qid, revs in state.tasks.items()},
        "jobs": {jid: {"state": rt.job.state.value,
                       "kind": rt.job.kind.value,
                       "workspace_id": rt.job.workspace_id,
                       "source_id": rt.job.source_id}
                 for jid, rt in state.jobs.items()},
        "concept_current": {f"{ws}|{key}": jid
                            for (ws, key), jid
                            in state.concept_current.items()},
        "dependencies": _dependency_index(state),
        "outbox_unacked": [
            {"event_id": eid, "consumer": consumer, "item": item}
            for (eid, consumer), item in state.outbox_unacked.items()],
    }
    atomic_write_text(journal.index_path(),
                      json.dumps(index, ensure_ascii=False))
    return index


def _dependency_index(state) -> dict[str, list[str]]:
    """source → interpretation → judgment → synthesis 反向依赖（§12.4）。"""
    deps: dict[str, list[str]] = {}
    for jid, judgment in state.judgments.items():
        refs = list(judgment.dependencies)
        if judgment.source_id:
            refs.append(judgment.source_id)
        deps[f"judgment:{jid}"] = sorted(set(refs))
    for syn in state.syntheses.values():
        refs = list(syn.claim_refs)
        deps[f"synthesis:{syn.synthesis_id}"] = sorted(set(refs))
    return deps


def load_index(student_id: str) -> dict[str, Any]:
    """读取 index；水位不匹配（或缺失/损坏）时从 journal 重建。"""
    journal = get_journal(student_id)
    path = journal.index_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and \
                data.get("watermark") == journal.state().watermark:
            return data
    except (OSError, ValueError):
        pass
    return rebuild_index(student_id)


# ---------------------------------------------------------------------------
# 概念视图（§12 + §11.3 coverage）
# ---------------------------------------------------------------------------

def _view_from_judgment(judgment: S.ConceptJudgment) -> S.ConceptEvaluationView:
    return S.ConceptEvaluationView(
        concept_ref=judgment.concept_ref,
        state=judgment.state,
        evaluation_status=S.EvaluationStatus.READY,
        judgment_id=judgment.judgment_id,
        statement=judgment.statement,
        claims=judgment.claims,
        change=judgment.change,
        next_probe=judgment.next_probe,
        evidence_count=len(judgment.claims),
        last_observed_at=judgment.created_at,
        updated_at=judgment.created_at)


def concept_views(student_id: str, scope: S.EvaluationScope
                  ) -> list[S.ConceptEvaluationView]:
    """scope 左连接生成统一概念投影（§11.2：未观察节点也出现）。"""
    journal = get_journal(student_id)
    state = journal.state()
    observed_ids: set[str] = set()
    views: list[S.ConceptEvaluationView] = []
    for concept in scope.allowed_concepts:
        jid = state.concept_current.get(
            (scope.workspace_id, concept.key), "")
        judgment = state.judgments.get(jid) if jid else None
        if judgment is not None:
            observed_ids.add(concept.concept_id)
            views.append(_view_from_judgment(judgment))
        else:
            views.append(S.ConceptEvaluationView(
                concept_ref=concept, state=S.ConceptState.NOT_OBSERVED,
                evaluation_status=S.EvaluationStatus.READY))
    views.sort(key=lambda v: (v.concept_ref.graph_owner_namespace,
                              v.concept_ref.concept_id))
    return views


def workspace_summary(student_id: str, scope: S.EvaluationScope, *,
                      workspace_name: str = "",
                      pending_source_count: int = 0
                      ) -> S.WorkspaceEvaluationSummary:
    views = concept_views(student_id, scope)
    by_state: dict[str, int] = {}
    reconciling = 0
    observed = 0
    for v in views:
        if v.evaluation_status == S.EvaluationStatus.RECONCILING:
            reconciling += 1
            observed += 1
            continue
        if v.state and v.state != S.ConceptState.NOT_OBSERVED:
            observed += 1
            by_state[v.state.value] = by_state.get(v.state.value, 0) + 1
    journal = get_journal(student_id)
    state = journal.state()
    synthesis = None
    for syn in state.syntheses.values():
        if syn.scope_type == S.ScopeType.WORKSPACE \
                and syn.workspace_id == scope.workspace_id:
            if synthesis is None or syn.generated_at > synthesis.generated_at:
                synthesis = syn
    return S.WorkspaceEvaluationSummary(
        workspace_id=scope.workspace_id,
        scope_revision=scope.scope_revision,
        evaluation_status=S.EvaluationStatus.READY,
        evaluated_through=state.watermark,
        pending_source_count=pending_source_count,
        allowed_concept_count=len(scope.allowed_concepts),
        coverage=S.CoverageCounts(
            observed_concepts=observed,
            not_observed_concepts=len(views) - observed,
            by_state=by_state,
            reconciling_concepts=reconciling),
        synthesis=synthesis,
        workspace_name=workspace_name)
