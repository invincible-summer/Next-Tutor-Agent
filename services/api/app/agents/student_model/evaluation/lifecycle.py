"""dispute/revoke/delete/revision 及失效传播（plan §12.4 / §5.3 + R07/R08）。

依赖链：source → interpretation → claim/judgment → synthesis → prompt
reference。任何引用失效，依赖其结论的自由叙述隐藏/撤销，不再注入下轮
LLM；后来真实的学生原始表现不删除——受污染的是其解释/综合（R08）。

R07：删除按 operation 级重写物理清除原文副本（canonical_text/引文），
先取消在途、失效依赖，再 rewrite——不是只删整行 source_registered；
独立保留的 assessment 明确 detach 会话定位。
"""
from __future__ import annotations

from typing import Any, Callable

from . import schema as S
from .store import EvidenceJournal, get_journal


def _source_of_interpretation(state, interpretation_id: str) -> str:
    for src in state.sources.values():
        if interpretation_id in src.interpretations:
            return src.receipt.source_id
    return ""


def affected_judgments(state, interpretation_id: str) -> dict[str, str]:
    """受该解释影响的 (concept_key -> judgment_id)：以该来源物化或显式
    依赖它的当前判断（§12.4 依赖并集的工程近似）。"""
    affected: dict[str, str] = {}
    target_source = _source_of_interpretation(state, interpretation_id)
    if not target_source:
        return affected
    for (ws, key), jid in state.concept_current.items():
        judgment = state.judgments.get(jid) if jid else None
        if judgment is None:
            continue
        deps = set(judgment.dependencies)
        if judgment.source_id == target_source or target_source in deps \
                or interpretation_id in deps:
            affected[key] = jid
    return affected


def _observation_ids_of(state, interpretation_id: str) -> set[str]:
    for src in state.sources.values():
        meta = src.interpretations.get(interpretation_id, {})
        if meta:
            return {str(e.get("obs_id") or "")
                    for e in (meta.get("observation_map") or [])
                    if isinstance(e, dict)}
    return set()


def build_invalidation_ops(
        student_id: str, state, interpretation_id: str, *,
        reason: str) -> tuple[list[Any], dict[str, str]]:
    """R08：递归失效。

    - 根解释撤销（原始表现保留在 journal 历史，current 位清空）。
    - 依赖闭包内的判断（同来源物化 + dependencies 引用已失效观察/判断）
      一并撤销——**只撤解释/判断，不删后续真实表现**（其他来源的解释
      保持有效，概念进入 reconciling，等待重综合恢复）。
    - 引用了受影响主张的同步/工作区综合隐藏正文（revoked，不删行）。
    返回 (operations, affected concept_key -> judgment_id)；调用方与后续
    决定（复核结案等）合成同一事务落盘。
    """
    target_source = _source_of_interpretation(state, interpretation_id)
    if not target_source:
        return [], {}
    invalidated_obs = _observation_ids_of(state, interpretation_id)
    affected_ids: set[str] = set()
    affected: dict[str, str] = {}
    # 闭包迭代：依赖集合随每轮扩大，直到不动点
    changed = True
    while changed:
        changed = False
        for (ws, key), jid in state.concept_current.items():
            if not jid or jid in affected_ids:
                continue
            judgment = state.judgments.get(jid)
            if judgment is None:
                continue
            deps = set(judgment.dependencies)
            hit = (judgment.source_id == target_source
                   or target_source in deps
                   or interpretation_id in deps
                   or (deps & invalidated_obs)
                   or (deps & affected_ids))
            if hit:
                affected_ids.add(jid)
                affected[key] = jid
                invalidated_obs |= _claim_obs_ids(judgment)
                changed = True
    # 引用受影响主张的综合 → 隐藏正文
    affected_claim_ids: set[str] = set()
    for jid in affected_ids:
        judgment = state.judgments.get(jid)
        if judgment is not None:
            affected_claim_ids |= {c.claim_id for c in judgment.claims}
    affected_syntheses: list[str] = []
    for syn in state.syntheses.values():
        refs = set(syn.claim_refs)
        if refs & affected_claim_ids:
            affected_syntheses.append(syn.synthesis_id)
        elif syn.concept_ref is not None and \
                syn.concept_ref.key in affected:
            affected_syntheses.append(syn.synthesis_id)
    ops: list[Any] = [S.OpInterpretationRevoked(
        interpretation_id=interpretation_id,
        source_id=target_source,
        reason=reason[:600],
        affected_judgment_ids=sorted(affected_ids),
        affected_synthesis_ids=sorted(set(affected_syntheses)),
        outbox=[{"event_id": f"m9_revoke_{interpretation_id}",
                 "consumer": "m9", "kind": "interpretation_revoked",
                 "concept_keys": sorted(affected.keys())}])]
    return ops, affected


def _claim_obs_ids(judgment: S.ConceptJudgment) -> set[str]:
    out: set[str] = set()
    for claim in judgment.claims:
        if claim.created_by_observation:
            out.add(claim.created_by_observation)
        out.update(claim.support_refs)
        out.update(claim.challenge_refs)
    return out


def invalidate_interpretation(
        student_id: str, interpretation_id: str, *, reason: str,
        workspace_id: str = "") -> dict[str, str]:
    """撤销一条解释并级联失效其判断（§12.4）。返回受影响概念映射。
    同 source 的解释版本替换不删除原始表现（A16/§12.4）。"""
    journal = get_journal(student_id)
    state = journal.state()
    ops, affected = build_invalidation_ops(
        student_id, state, interpretation_id, reason=reason)
    if not ops:
        return affected
    journal.append(ops)
    ws = workspace_id or next(
        (src.receipt.workspace_id_at_observation
         for src in state.sources.values()
         if interpretation_id in src.interpretations), "")
    if affected and ws:
        request_resynthesis(student_id, ws, reason="invalidate")
    return affected


def request_resynthesis(student_id: str, workspace_id: str, *,
                        reason: str) -> str:
    """失效后入队工作区重综合（§12.5：复核/撤销/scope 变化才额外重综合；
    同区已排队的 dirty job 复用）。返回 job_id。"""
    from app.core import learner_runtime
    scheduler = learner_runtime.get_scheduler()
    state = get_journal(student_id).state()
    for jid, rt in state.jobs.items():
        if rt.job.kind == S.JobKind.SYNTHESIS_WORKSPACE \
                and rt.job.workspace_id == workspace_id \
                and rt.job.state in (S.JobState.QUEUED,
                                     S.JobState.RETRY_WAIT):
            return jid
    job = scheduler.enqueue(
        student_id, kind=S.JobKind.SYNTHESIS_WORKSPACE,
        workspace_id=workspace_id, scope_revision="",
        priority=S.JobPriority.SYNTHESIS_CONCEPT.value)
    return job.job_id


# ---------------------------------------------------------------------------
# 归档/恢复（回收站，§5.3）
# ---------------------------------------------------------------------------

def archive_session_sources(student_id: str, session_ref: str) -> list[str]:
    """对话进回收站：暂停该对话来源（§5.3——当前评价排除归档 dialogue）。"""
    journal = get_journal(student_id)
    state = journal.state()
    archived: list[str] = []
    for sid, src in state.sources.items():
        if src.receipt.source_session_ref == session_ref and \
                src.availability == "available":
            journal.append([S.OpSourceArchived(source_id=sid,
                                               reason="session_trashed")])
            archived.append(sid)
    return archived


def restore_session_sources(student_id: str, session_ref: str) -> list[str]:
    journal = get_journal(student_id)
    state = journal.state()
    restored: list[str] = []
    for sid, src in state.sources.items():
        if src.receipt.source_session_ref == session_ref and \
                src.availability == "archived":
            journal.append([S.OpSourceRestored(source_id=sid)])
            restored.append(sid)
    return restored


# ---------------------------------------------------------------------------
# 物理删除（R07）
# ---------------------------------------------------------------------------

def _cancel_source_jobs(student_id: str, source_id: str) -> list[str]:
    """取消该来源全部在途 job（评价/复核），防止回包复活（§5.3）。"""
    from app.core import learner_runtime
    scheduler = learner_runtime.get_scheduler()
    state = get_journal(student_id).state()
    cancelled: list[str] = []
    for jid, rt in list(state.jobs.items()):
        if rt.job.source_id != source_id:
            continue
        if scheduler.cancel(student_id, jid, reason="source_deleted"):
            cancelled.append(jid)
    return cancelled


def _dismiss_source_reviews(student_id: str, source_id: str) -> None:
    """该来源的 active 复核全部 dismiss——允许删除后新证据的新异议。"""
    journal = get_journal(student_id)
    state = journal.state()
    ops = []
    for rid, review in state.reviews.items():
        if review.source_id == source_id and review.status == "active":
            job_id = next((jid for jid, r in state.review_by_job.items()
                           if r == rid), "")
            ops.append(S.OpReviewDismissed(
                review_id=rid, reason="source_deleted", job_id=job_id))
    if ops:
        journal.append(ops)


def delete_evidence_source(student_id: str, source_id: str) -> dict[str, Any]:
    """R07：物理删除单条证据。

    顺序：取消在途 → dismiss 复核 → 递归失效依赖 → operation 级 rewrite
    物理清除该来源的原文/引文/判分/解释副本（其余来源与无关事务不动）。
    """
    journal = get_journal(student_id)
    state = journal.state()
    src = state.sources.get(source_id)
    if src is None:
        return {"deleted": False}
    affected_all: dict[str, str] = {}
    workspaces: set[str] = set()
    _cancel_source_jobs(student_id, source_id)
    _dismiss_source_reviews(student_id, source_id)
    state = journal.state()
    for interp_id in [src.current_interpretation_id] + [
            iid for iid, meta in src.interpretations.items()
            if iid and not meta.get("revoked")]:
        if interp_id:
            ops, affected = build_invalidation_ops(
                student_id, journal.state(), interp_id,
                reason="source_deleted:" + source_id)
            journal.append(ops)
            affected_all.update(affected)
    workspaces.add(src.receipt.workspace_id_at_observation)

    def _keep(tx: S.JournalTransaction) -> bool:
        for op in tx.operations:
            if isinstance(op, S.OpSourceRegistered) and \
                    op.source.source_id == source_id:
                return False
            if isinstance(op, (S.OpSourceRevised, S.OpResultCommitted)) and \
                    op.source_id == source_id:
                return False
        return True

    journal.rewrite(_keep, reason=f"delete_evidence:{source_id}")
    for ws in workspaces:
        if ws:
            request_resynthesis(student_id, ws, reason="source_deleted")
    return {"deleted": True, "source_id": source_id,
            "affected_concepts": sorted(affected_all)}


def delete_session_sources(student_id: str, session_ref: str, *,
                           include_assessments: bool = False
                           ) -> dict[str, Any]:
    """永久删除对话（§5.3）：物理去除 dialogue 原文/引用副本并撤销依赖
    结论；独立 assessment 档案按"学习档案独立保留"语义保存并 detach 会话
    定位，除非显式选择一并删除（R07）。"""
    journal = get_journal(student_id)
    state = journal.state()
    target_ids = [
        src.receipt.source_id for src in state.sources.values()
        if src.receipt.source_session_ref == session_ref]
    dialogue_ids = [src.receipt.source_id for src in state.sources.values()
                    if src.receipt.source_session_ref == session_ref
                    and src.receipt.kind == S.SourceKind.DIALOGUE]
    remove_ids = set(target_ids if include_assessments else dialogue_ids)
    detach_ids = set(target_ids) - remove_ids
    affected_all: dict[str, str] = {}
    workspaces: set[str] = set()
    for source_id in sorted(remove_ids):
        src = journal.state().sources.get(source_id)
        if src is None:
            continue
        result = delete_evidence_source(student_id, source_id)
        affected_all.update({k: "" for k in
                             result.get("affected_concepts", [])})
        workspaces.add(src.receipt.workspace_id_at_observation)

    def _transform(tx: S.JournalTransaction) -> S.JournalTransaction:
        """独立保留的 assessment：去掉会话定位（detach），原文保留。"""
        if not detach_ids:
            return tx
        ops = []
        changed = False
        for op in tx.operations:
            if isinstance(op, S.OpSourceRegistered) and \
                    op.source.source_id in detach_ids and \
                    op.source.source_session_ref:
                receipt = op.source.model_copy(update={
                    "source_session_ref": ""})
                op = op.model_copy(update={"source": receipt})
                changed = True
            ops.append(op)
        return tx.model_copy(update={"operations": ops}) if changed else tx

    def _keep(tx: S.JournalTransaction) -> bool:
        for op in tx.operations:
            if isinstance(op, S.OpSourceRegistered) and \
                    op.source.source_id in remove_ids:
                return False
            if isinstance(op, (S.OpSourceRevised, S.OpResultCommitted)) and \
                    getattr(op, "source_id", "") in remove_ids:
                return False
        return True

    if remove_ids or detach_ids:
        journal.rewrite(_keep, reason=f"delete_session:{session_ref}",
                        transform=_transform)
    for ws in workspaces:
        if ws:
            request_resynthesis(student_id, ws, reason="source_deleted")
    return {"deleted_session": session_ref,
            "deleted_sources": sorted(remove_ids),
            "detached_sources": sorted(detach_ids),
            "assessments_deleted": include_assessments}


def on_scope_change(student_id: str, workspace_id: str, *,
                    change: str) -> tuple[list[str], list[str]]:
    """选卷/图谱变化（§5.3）：scope_changed 落盘（重放清空越界概念当前
    判断）+ 取消该 scope 未开始任务 + 排队重综合。"""
    journal = get_journal(student_id)
    scheduler = _scheduler()
    state = journal.state()
    try:
        from .scope import get_scope_resolver
        scope = get_scope_resolver().resolve(student_id, workspace_id)
        new_revision = scope.scope_revision
        allowed = {c.key for c in scope.allowed_concepts}
    except Exception:
        new_revision, allowed = "", set()
    dropped = [key for (ws, key) in state.concept_current
               if ws == workspace_id and key not in allowed]
    journal.append([S.OpScopeChanged(
        workspace_id=workspace_id,
        scope_revision=new_revision or "unknown",
        change=change[:600], affected_concept_keys=dropped)])
    cancelled = scheduler.cancel_scope_jobs(
        student_id, workspace_id, reason="scope_changed")
    if dropped:
        request_resynthesis(student_id, workspace_id, reason="scope_changed")
    return dropped, cancelled


def _scheduler():
    from app.core import learner_runtime
    return learner_runtime.get_scheduler()
