"""dispute/revoke/delete/revision 及失效传播（plan §12.4 / §5.3）。

依赖链：source → interpretation → claim/judgment → synthesis → prompt
reference。任何引用失效，依赖其结论的自由叙述标 stale，不再注入下轮
LLM；后来真实的学生原始表现不删除——受污染的是其解释/综合。
"""
from __future__ import annotations

from typing import Any

from . import schema as S
from .store import get_journal


def affected_judgments(state, interpretation_id: str) -> dict[str, str]:
    """受该解释影响的 (concept_key -> judgment_id)：以该来源物化或显式
    依赖它的当前判断（§12.4 依赖并集的工程近似）。"""
    affected: dict[str, str] = {}
    target_source = ""
    for src in state.sources.values():
        if interpretation_id in src.interpretations:
            target_source = src.receipt.source_id
            break
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


def invalidate_interpretation(
        student_id: str, interpretation_id: str, *, reason: str,
        workspace_id: str = "") -> dict[str, str]:
    """撤销一条解释并级联失效其判断（§12.4）。返回受影响概念映射。
    同 source 的解释版本替换不删除原始表现（A16/§12.4）。"""
    journal = get_journal(student_id)
    state = journal.state()
    affected = affected_judgments(state, interpretation_id)
    source_id = next(
        (src.receipt.source_id for src in state.sources.values()
         if interpretation_id in src.interpretations), "")
    ws = workspace_id or next(
        (src.receipt.workspace_id_at_observation
         for src in state.sources.values()
         if interpretation_id in src.interpretations), "")
    journal.append([S.OpInterpretationRevoked(
        interpretation_id=interpretation_id,
        source_id=source_id,
        reason=reason[:600],
        affected_judgment_ids=sorted(set(affected.values())))])
    if affected and ws:
        request_resynthesis(student_id, ws, reason="invalidate")
    return affected


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


def delete_session_sources(student_id: str, session_ref: str, *,
                           include_assessments: bool = False
                           ) -> dict[str, Any]:
    """永久删除对话（§5.3）：物理去除 dialogue 原文/引用副本并撤销依赖
    结论；独立 assessment 档案按“学习档案独立保留”语义保存（去掉对话
    定位），除非显式选择一并删除。"""
    journal = get_journal(student_id)
    scheduler = _scheduler()
    affected_all: dict[str, str] = {}
    workspaces: set[str] = set()

    def _keep(tx: S.JournalTransaction) -> bool:
        for op in tx.operations:
            if isinstance(op, (S.OpSourceRegistered, S.OpSourceRevised)):
                r = op.source if isinstance(op, S.OpSourceRegistered) \
                    else None
                if r is not None:
                    if r.source_session_ref != session_ref:
                        return True
                    if r.kind == S.SourceKind.DIALOGUE:
                        return False
                    if include_assessments:
                        return False
        return True

    state = journal.state()
    for src in list(state.sources.values()):
        if src.receipt.source_session_ref != session_ref:
            continue
        if src.receipt.kind == S.SourceKind.DIALOGUE or include_assessments:
            interp = src.current_interpretation_id
            if interp:
                affected_all.update(invalidate_interpretation(
                    student_id, interp,
                    reason="source_deleted:" + session_ref))
            workspaces.add(src.receipt.workspace_id_at_observation)
            # 取消在途 job，防止回包复活（§5.3）
            for jid, rt in state.jobs.items():
                if rt.job.source_id == src.receipt.source_id:
                    scheduler.cancel(student_id, jid, reason="source_deleted")
    journal.rewrite(_keep, reason=f"delete_session:{session_ref}")
    for ws in workspaces:
        if ws:
            request_resynthesis(student_id, ws, reason="source_deleted")
    return {"deleted_session": session_ref,
            "affected_concepts": sorted(affected_all),
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
