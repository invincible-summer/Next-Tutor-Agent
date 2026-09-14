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
# 概念视图（§12 + §11.3 coverage；R07/R08/R10）
# ---------------------------------------------------------------------------

def _obs_to_source(state) -> dict[str, str]:
    """obs_id → source_id（R10 稳定映射的读侧聚合）。"""
    out: dict[str, str] = {}
    for src in state.sources.values():
        for meta in src.interpretations.values():
            if not isinstance(meta, dict):
                continue
            for e in (meta.get("observation_map") or []):
                if isinstance(e, dict) and e.get("obs_id"):
                    out[str(e["obs_id"])] = src.receipt.source_id
    return out


def _valid_sources_by_concept(state, workspace_id: str
                              ) -> dict[str, dict[str, str]]:
    """R08/R10：concept_key → {source_id: observed_at}（当前有效解释携带的
    真实表现）。用于：①撤销后 reconciling 判定（仍有合法证据待重综合）；
    ②证据计数按有效 source 去重。"""
    out: dict[str, dict[str, str]] = {}
    for src in state.sources.values():
        if src.availability != "available":
            continue
        if src.receipt.workspace_id_at_observation != workspace_id:
            continue
        if not src.current_interpretation_id:
            continue
        meta = src.interpretations.get(src.current_interpretation_id, {})
        if not isinstance(meta, dict) or meta.get("revoked") \
                or meta.get("abstained"):
            continue
        for e in (meta.get("observation_map") or []):
            if isinstance(e, dict) and e.get("concept_key"):
                out.setdefault(str(e["concept_key"]), {})[
                    src.receipt.source_id] = src.receipt.observed_at
    return out


def _view_from_judgment(judgment: S.ConceptJudgment, *,
                        obs_to_source: dict[str, str],
                        observed_at_by_source: dict[str, str],
                        valid_sources: dict[str, str]) -> S.ConceptEvaluationView:
    # R10：证据数量按有效 source identity 去重（一来源多主张不虚增）；
    # 来源时间取 observed_at，评价完成时间单列 evaluated_at。
    contributing: set[str] = set()
    for claim in judgment.claims:
        refs = list(claim.support_refs) + list(claim.challenge_refs)
        if claim.created_by_observation:
            refs.append(claim.created_by_observation)
        for ref in refs:
            src_id = obs_to_source.get(ref)
            if src_id is not None:
                contributing.add(src_id)
    # 没有可寻址引用的旧判断：退化为按有效解释来源计（不小于 0）
    if not contributing and valid_sources:
        contributing = set(valid_sources)
    observed_times = [observed_at_by_source[s] for s in contributing
                      if s in observed_at_by_source]
    return S.ConceptEvaluationView(
        concept_ref=judgment.concept_ref,
        state=judgment.state,
        evaluation_status=S.EvaluationStatus.READY,
        judgment_id=judgment.judgment_id,
        statement=judgment.statement,
        claims=judgment.claims,
        change=judgment.change,
        next_probe=judgment.next_probe,
        evidence_count=len(contributing),
        last_observed_at=max(observed_times) if observed_times else "",
        evaluated_at=judgment.created_at,
        updated_at=judgment.created_at)


def concept_views(student_id: str, scope: S.EvaluationScope
                  ) -> list[S.ConceptEvaluationView]:
    """scope 左连接生成统一概念投影（§11.2：未观察节点也出现）。

    R08：判断被撤销但仍有当前有效表现证据 → reconciling（state=null，
    正文隐藏），等待重综合恢复——不是"没学过"。
    R07：判断的来源已归档/删除 → 不再显示该判断。"""
    journal = get_journal(student_id)
    state = journal.state()
    obs_to_source = _obs_to_source(state)
    observed_at_by_source = {
        sid: src.receipt.observed_at for sid, src in state.sources.items()}
    valid_by_concept = _valid_sources_by_concept(
        state, scope.workspace_id)
    views: list[S.ConceptEvaluationView] = []
    for concept in scope.allowed_concepts:
        jid = state.concept_current.get(
            (scope.workspace_id, concept.key), "")
        judgment = state.judgments.get(jid) if jid else None
        if judgment is not None:
            src = state.sources.get(judgment.source_id)
            if src is not None and src.availability != "available":
                judgment = None      # R07：归档/删除来源的判断退出当前视图
        if judgment is not None:
            views.append(_view_from_judgment(
                judgment, obs_to_source=obs_to_source,
                observed_at_by_source=observed_at_by_source,
                valid_sources=valid_by_concept.get(concept.key, {})))
        elif valid_by_concept.get(concept.key):
            observed = valid_by_concept[concept.key]
            views.append(S.ConceptEvaluationView(
                concept_ref=concept, state=None,
                evaluation_status=S.EvaluationStatus.RECONCILING,
                last_observed_at=max(observed.values())))
        else:
            views.append(S.ConceptEvaluationView(
                concept_ref=concept, state=S.ConceptEvalState.NOT_OBSERVED,
                evaluation_status=S.EvaluationStatus.READY))
    views.sort(key=lambda v: (v.concept_ref.graph_owner_namespace,
                              v.concept_ref.concept_id))
    return views


def wrong_answer_items(student_id: str, limit: int = 200) -> list[dict]:
    """错题本投影（§11.2）：available 的 assessment 来源且当前 TaskResult
    判定 ∈ {wrong, partial}，新→旧。供 /student/error-notebook 与笔记素材
    共用；读侧聚合，不物化副本。"""
    state = get_journal(student_id).state()
    items: list[dict] = []
    for src in state.sources.values():
        if src.availability != "available":
            continue
        # 当前有效 TaskResult：优先当前解释槽；无解释（MC 只判分）时读
        # 受理时落盘的 "" 槽。复核改分后 task_result 随新解释更新，此处
        # 自动跟随（§11.2）。
        if state.review_active_by_source.get(src.receipt.source_id):
            continue    # 争议未结：不驱动错题/笔记链路（§9.7）
        meta = src.interpretations.get(src.current_interpretation_id, {})
        tr = meta.get("task_result") or {}
        if tr.get("verdict") not in ("wrong", "partial"):
            continue
        task = None
        ref = src.receipt.task_ref
        if ref is not None:
            task = state.tasks.get(ref.question_id, {}).get(
                ref.question_revision)
        items.append({
            # source_id 供前端打开证据详情（原题/作答/揭晓视图，§11.2）。
            "source_id": src.receipt.source_id,
            "topic": task.task_family if task else "",
            "knowledge_point": task.source_badge if task else "",
            "stem": task.stem[:300] if task else "",
            "verdict": tr.get("verdict"),
            "ts": src.receipt.observed_at,
        })
    items.sort(key=lambda i: i["ts"], reverse=True)
    return items[:max(1, int(limit))]


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
        if v.state and v.state != S.ConceptEvalState.NOT_OBSERVED:
            observed += 1
            by_state[v.state.value] = by_state.get(v.state.value, 0) + 1
    journal = get_journal(student_id)
    state = journal.state()
    synthesis = None
    for syn in state.syntheses.values():
        if syn.revoked:
            continue      # R08：引用失效的综合隐藏正文
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
