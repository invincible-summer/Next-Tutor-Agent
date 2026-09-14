"""学习评价读取与管理 API（plan §11.2）。

- 学生身份只经 `resolve_student_id`；所有 GET 零 LLM 调用（§10.4）。
- 个人评价响应 `Cache-Control: private, no-store`。
- 列表统一 `{items,total,offset,limit,revision}`；错误统一 §11.1 envelope。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.student_model.evaluation import (lifecycle, projections,
                                                 schema as S)
from app.agents.student_model.evaluation.jobs import JobScheduler
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime
from app.core.workspace import load_workspace
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/learner-evaluation", tags=["learner-evaluation"])

_NO_STORE = {"Cache-Control": "private, no-store"}


def _error(status: int, code: str, message: str, *,
           retryable: bool = False) -> HTTPException:
    return HTTPException(status_code=status, detail={
        "error": {"code": code, "message": message, "retryable": retryable,
                  "request_id": "req_" + uuid.uuid4().hex[:12]}})


def _resolve_scope_or_404(student_id: str, workspace_id: str):
    from app.agents.student_model.evaluation.scope import (
        ScopeNotFound, get_scope_resolver)
    try:
        return get_scope_resolver().resolve(student_id, workspace_id)
    except ScopeNotFound:
        raise _error(404, "workspace_not_found", "工作区不存在")


# ---------------------------------------------------------------------------
# 工作区读取
# ---------------------------------------------------------------------------

@router.get("/workspaces")
async def list_workspaces(offset: int = Query(0, ge=0),
                          limit: int = Query(20, ge=1, le=100),
                          student_id: str = Depends(resolve_student_id)):
    from app.core.workspace import list_workspaces as list_ws
    own = [w for w in list_ws()
           if w.get("student_id") == student_id]
    own.sort(key=lambda w: str(w.get("updated_at") or ""), reverse=True)
    total = len(own)
    page = own[offset:offset + limit]
    items = []
    for w in page:
        ws_id = str(w.get("workspace_id") or "")
        try:
            scope = _resolve_scope_or_404(student_id, ws_id)
            summary = projections.workspace_summary(
                student_id, scope, workspace_name=str(w.get("name") or ""))
            items.append({
                "workspace_id": ws_id,
                "workspace_name": str(w.get("name") or ""),
                "evaluation_status": summary.evaluation_status.value,
                "scope_revision": summary.scope_revision,
                "coverage": summary.coverage.model_dump(),
                "updated_at": str(w.get("updated_at") or ""),
            })
        except HTTPException:
            continue
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/workspaces/{wid}")
async def workspace_detail(wid: str,
                           student_id: str = Depends(resolve_student_id)):
    scope = _resolve_scope_or_404(student_id, wid)
    ws = load_workspace(wid)
    summary = projections.workspace_summary(student_id, scope,
                                            workspace_name=ws.name if ws
                                            else "")
    pending = sum(
        1 for src in get_journal(student_id).state().sources.values()
        if src.receipt.workspace_id_at_observation == wid
        and not src.current_interpretation_id
        and src.availability == "available")
    data = summary.model_dump()
    data["pending_source_count"] = pending
    return data


@router.get("/workspaces/{wid}/concepts")
async def workspace_concepts(wid: str,
                             state: str = Query(""),
                             textbook_id: str = Query(""),
                             q: str = Query("", max_length=120),
                             offset: int = Query(0, ge=0),
                             limit: int = Query(20, ge=1, le=100),
                             student_id: str = Depends(resolve_student_id)):
    scope = _resolve_scope_or_404(student_id, wid)
    views = projections.concept_views(student_id, scope)
    if state:
        views = [v for v in views
                 if (v.state.value if v.state else "") == state]
    if textbook_id:
        views = [v for v in views
                 if v.concept_ref.textbook_id == textbook_id]
    if q:
        views = [v for v in views
                 if q in v.concept_ref.display_name]
    total = len(views)
    page = views[offset:offset + limit]
    return {"items": [v.model_dump() for v in page], "total": total,
            "offset": offset, "limit": limit,
            "revision": scope.scope_revision}


@router.get("/workspaces/{wid}/concepts/{concept_key}")
async def concept_detail(wid: str, concept_key: str,
                         student_id: str = Depends(resolve_student_id)):
    scope = _resolve_scope_or_404(student_id, wid)
    views = projections.concept_views(student_id, scope)
    for v in views:
        if v.concept_ref.key == concept_key:
            return v.model_dump()
    raise _error(404, "concept_not_found", "概念不在当前范围内（可能已变化）")


@router.get("/workspaces/{wid}/sessions")
async def workspace_sessions(wid: str,
                             offset: int = Query(0, ge=0),
                             limit: int = Query(20, ge=1, le=100),
                             student_id: str = Depends(resolve_student_id)):
    _resolve_scope_or_404(student_id, wid)
    state = get_journal(student_id).state()
    sessions: dict[str, dict[str, Any]] = {}
    for src in state.sources.values():
        ref = src.receipt.source_session_ref
        if not ref or src.receipt.workspace_id_at_observation != wid:
            continue
        entry = sessions.setdefault(ref, {
            "source_session_ref": ref,
            "has_evidence": False,
            "availability": "available",
            "last_observed_at": "",
            "kinds": set(),
        })
        entry["kinds"].add(src.receipt.kind.value)
        if src.current_interpretation_id:
            entry["has_evidence"] = True
        entry["last_observed_at"] = max(
            entry["last_observed_at"], src.receipt.observed_at)
        if src.availability != "available":
            entry["availability"] = src.availability
    items = sorted(sessions.values(),
                   key=lambda e: e["last_observed_at"], reverse=True)
    for item in items:
        item["kinds"] = sorted(item["kinds"])
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total,
            "offset": offset, "limit": limit}


@router.get("/workspaces/{wid}/sessions/{source_session_ref}")
async def session_evidence(wid: str, source_session_ref: str,
                           student_id: str = Depends(resolve_student_id)):
    _resolve_scope_or_404(student_id, wid)
    state = get_journal(student_id).state()
    items = []
    for sid, src in state.sources.items():
        if src.receipt.source_session_ref != source_session_ref:
            continue
        if src.receipt.workspace_id_at_observation != wid:
            continue      # §5.3：历史归属按 workspace_id_at_observation
        meta = src.interpretations.get(src.current_interpretation_id, {}) \
            if src.current_interpretation_id else {}
        items.append({
            "source_id": sid,
            "kind": src.receipt.kind.value,
            "observed_at": src.receipt.observed_at,
            "availability": src.availability,
            "canonical_text": src.receipt.canonical_text[:400],
            "interpretation_id": src.current_interpretation_id,
            "review_status": (state.review_active_by_source.get(sid, "")
                              and "active" or ""),
        })
    items.sort(key=lambda i: i["observed_at"])
    return {"items": items, "total": len(items)}


@router.get("/workspaces/{wid}/evidence")
async def evidence_timeline(wid: str,
                            concept_key: str = Query(""),
                            source_kind: str = Query(""),
                            source_session_ref: str = Query(""),
                            offset: int = Query(0, ge=0),
                            limit: int = Query(20, ge=1, le=100),
                            student_id: str = Depends(resolve_student_id)):
    _resolve_scope_or_404(student_id, wid)
    state = get_journal(student_id).state()
    items = []
    for sid, src in state.sources.items():
        if src.receipt.workspace_id_at_observation != wid:
            continue
        if source_kind and src.receipt.kind.value != source_kind:
            continue
        if source_session_ref and \
                src.receipt.source_session_ref != source_session_ref:
            continue
        meta = src.interpretations.get(src.current_interpretation_id, {}) \
            if src.current_interpretation_id else {}
        # R10：概念筛选用稳定 ConceptRef.key（持久化 observation_map），
        # 不再用 pack 短引用 c1/c2（前端按 key 查询会全部落空）。
        concepts = sorted({str(e.get("concept_key") or "")
                           for e in (meta.get("observation_map") or [])
                           if isinstance(e, dict)}) \
            if isinstance(meta, dict) else []
        if concept_key and concept_key not in concepts:
            continue
        raw = meta.get("raw_interpretation") or {}
        items.append(S.SourceTimelineItem(
            source_id=sid, kind=src.receipt.kind,
            observed_at=src.receipt.observed_at,
            scope_status=S.ScopeStatus.CURRENT,
            availability=("deleted" if src.availability == "deleted"
                          else src.availability),
            concept_refs=concepts,
            summary=(raw.get("feedback") or "")[:600]
            if isinstance(raw, dict) else "",
            source_session_ref=src.receipt.source_session_ref,
            interpretation_id=src.current_interpretation_id,
            review_status=(state.review_active_by_source.get(sid, "")
                           and "active" or "")).model_dump())
    items.sort(key=lambda i: i["observed_at"], reverse=True)
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total,
            "offset": offset, "limit": limit}


@router.get("/evidence/{source_id}")
async def evidence_detail(source_id: str,
                          student_id: str = Depends(resolve_student_id)):
    state = get_journal(student_id).state()
    src = state.sources.get(source_id)
    if src is None:
        raise _error(404, "source_not_found", "证据不存在")
    task = None
    if src.receipt.task_ref is not None:
        task = state.tasks.get(src.receipt.task_ref.question_id, {}).get(
            src.receipt.task_ref.question_revision)
    interp_id = src.current_interpretation_id
    meta = src.interpretations.get(interp_id, {}) if interp_id else {}
    revealed = bool(meta)      # 已评价 → 揭晓视图（所有权双检由 journal 归属保证）
    return {
        "source_id": source_id,
        "kind": src.receipt.kind.value,
        "observed_at": src.receipt.observed_at,
        # C9 复核/删除需要当前版本号（§11.2 expected_revision / If-Match）。
        "source_revision": src.receipt.source_revision,
        "workspace_id": src.receipt.workspace_id_at_observation,
        "availability": src.availability,
        "canonical_text": src.receipt.canonical_text,
        "assistance": [a.model_dump() for a in src.receipt.assistance_events],
        "task": (task.public_view().model_dump() if task is not None else None),
        "interpretation_id": interp_id or "",
        "revealed": ({"answer": task.answer, "explanation": task.explanation}
                     if task is not None and revealed else None),
        "interpretation": meta.get("raw_interpretation"),
        "task_result": meta.get("task_result"),
        "reviews": [r.model_dump() for r in state.reviews.values()
                    if r.source_id == source_id],
    }


# ---------------------------------------------------------------------------
# 作业
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}")
async def job_detail(job_id: str,
                     student_id: str = Depends(resolve_student_id)):
    rt = get_journal(student_id).state().jobs.get(job_id)
    if rt is None:
        raise _error(404, "job_not_found", "作业不存在")
    job = rt.job
    return {
        "job_id": job_id,
        "state": job.state.value,
        "kind": job.kind.value,
        "workspace_id": job.workspace_id,
        "error_code": job.error_code or "",
        "attempt_count": job.attempt_count,
        "transport_attempts": job.transport_attempts,
        "retryable": job.state == S.JobState.RETRY_WAIT,
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, last_event_id: str = Header(default=""),
                     student_id: str = Depends(resolve_student_id)):
    """SSE：job 已是终态时立即发送完成事件并关闭；未完成发送当前状态
    （生产 worker 循环在 G4 接入前，作业以 inline 方式推进）。"""
    import asyncio
    rt = get_journal(student_id).state().jobs.get(job_id)
    if rt is None:
        raise _error(404, "job_not_found", "作业不存在")

    async def stream():
        state = rt.job.state.value
        yield f"event: {state}\ndata: {S.canonical_json({'job_id': job_id, 'state': state})}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers=_NO_STORE)


# ---------------------------------------------------------------------------
# 管理：复核 / 重试 / 综合 / 删除 / backfill
# ---------------------------------------------------------------------------

class ReviewCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    interpretation_id: str = Field(min_length=4, max_length=64)
    reason: str = Field(min_length=4, max_length=1200)
    issue_kind: str = Field("other", max_length=64)
    expected_revision: int = Field(1, ge=1)


@router.post("/evidence/{source_id}/reviews")
async def create_review(source_id: str, req: ReviewCreateRequest,
                        student_id: str = Depends(resolve_student_id)):
    """R06：复核受理（source/review/job 同一事务）。

    - interpretation_id 必须是该来源的已存有效解释（不再接受裸 ID）。
    - 同源只允许一个 active 复核；insufficient 决定后允许再次异议。
    - 执行按 review_id 绑定的 job（worker / inline 认领同一 job）。"""
    journal = get_journal(student_id)
    state = journal.state()
    src = state.sources.get(source_id)
    if src is None:
        raise _error(404, "source_not_found", "证据不存在")
    if req.expected_revision != src.receipt.source_revision:
        raise _error(409, "revision_conflict", "证据版本已变化，请刷新")
    if req.interpretation_id not in src.interpretations:
        raise _error(404, "interpretation_not_found",
                     "被争议的解释不存在（可能已被删除或撤销），请刷新")
    active = state.review_active_by_source.get(source_id, "")
    if active:
        active_review = state.reviews.get(active)
        # R06：insufficient 保持待确定，但用户可带着新证据再次异议
        if active_review is None or \
                active_review.decided_kind != "insufficient_evidence":
            return {"review_id": active, "job_id": "",
                    "duplicate": True}
    scheduler: JobScheduler = learner_runtime.get_scheduler()
    review = S.ReviewRequestRecord(
        review_id="rev_" + uuid.uuid4().hex[:14], source_id=source_id,
        interpretation_id=req.interpretation_id, reason=req.reason,
        issue_kind=req.issue_kind, requested_at=S.utc_now_iso(),
        requested_revision=src.receipt.source_revision)
    job = S.EvaluationJob(
        job_id="job_" + uuid.uuid4().hex[:16], kind=S.JobKind.REVIEW,
        source_id=source_id, source_revision=src.receipt.source_revision,
        workspace_id=src.receipt.workspace_id_at_observation,
        scope_revision=src.receipt.scope_revision,
        priority=S.JobPriority.REVIEW.value,
        created_at=S.utc_now_iso(), updated_at=S.utc_now_iso())
    # 受理 + job 同一事务（R06）
    journal.append([S.OpJobRequested(job=job),
                    S.OpReviewRequested(review=review, job_id=job.job_id)])
    # inline 执行本 job（worker 上线后由 dispatcher 认领；这里只认领
    # 指定 job，不排空其他类型作业）
    claimed = scheduler.claim_job(student_id, job.job_id)
    if claimed is not None:
        from app.agents.student_model.evaluation.evaluator import (
            run_review_job)
        await run_review_job(student_id, claimed,
                             runner=learner_runtime.get_evaluation_runner(),
                             scheduler=scheduler)
    return {"review_id": review.review_id, "job_id": job.job_id}


class JobRetryRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(0, ge=0)


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, req: JobRetryRequest,
                    student_id: str = Depends(resolve_student_id)):
    scheduler: JobScheduler = learner_runtime.get_scheduler()
    state = get_journal(student_id).state()
    rt = state.jobs.get(job_id)
    if rt is None:
        raise _error(404, "job_not_found", "作业不存在")
    if rt.job.state not in (S.JobState.FAILED,):
        raise _error(409, "job_not_retryable",
                     "只有失败作业可重试；完成结果请直接读取")
    # 手动重试另开 parent_job_id 的新 job（§10.3）
    job = scheduler.enqueue(
        student_id, kind=rt.job.kind, source_id=rt.job.source_id,
        source_revision=rt.job.source_revision,
        workspace_id=rt.job.workspace_id,
        scope_revision=rt.job.scope_revision,
        priority=rt.job.priority, parent_job_id=job_id)
    return {"job_id": job.job_id, "parent_job_id": job_id}


class SynthesisRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_scope_revision: str = Field("", max_length=128)


@router.post("/workspaces/{wid}/synthesis")
async def request_synthesis(wid: str, req: SynthesisRequest,
                            student_id: str = Depends(resolve_student_id)):
    scope = _resolve_scope_or_404(student_id, wid)
    if req.expected_scope_revision and \
            req.expected_scope_revision != scope.scope_revision:
        raise _error(409, "scope_changed", "教材范围已变化，请刷新后重试。")
    scheduler: JobScheduler = learner_runtime.get_scheduler()
    state = get_journal(student_id).state()
    for jid, rt in state.jobs.items():
        if rt.job.kind == S.JobKind.SYNTHESIS_WORKSPACE \
                and rt.job.workspace_id == wid \
                and rt.job.state in (S.JobState.QUEUED,
                                     S.JobState.RETRY_WAIT):
            return {"job_id": jid, "duplicate": True}
    job = scheduler.enqueue(
        student_id, kind=S.JobKind.SYNTHESIS_WORKSPACE, workspace_id=wid,
        scope_revision=scope.scope_revision,
        priority=S.JobPriority.SYNTHESIS_SESSION_WORKSPACE.value)
    return {"job_id": job.job_id}


class BackfillRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source_ids: list[str] = Field(min_length=1, max_length=50)
    expected_scope_revision: str = Field("", max_length=128)


@router.post("/workspaces/{wid}/backfill")
async def backfill(wid: str, req: BackfillRequest,
                   student_id: str = Depends(resolve_student_id)):
    """§16.4：显式来源重分析（逐条资格检查；缺 provenance 只归档不评价）。"""
    scope = _resolve_scope_or_404(student_id, wid)
    scheduler: JobScheduler = learner_runtime.get_scheduler()
    state = get_journal(student_id).state()
    accepted: list[str] = []
    skipped: list[dict] = []
    for sid in req.source_ids[:50]:
        src = state.sources.get(sid)
        if src is None or src.availability != "available":
            skipped.append({"source_id": sid, "reason": "not_available"})
            continue
        if not src.current_interpretation_id:
            job = scheduler.enqueue(
                student_id,
                kind=S.JobKind.BACKFILL if src.receipt.provenance ==
                S.SourceProvenance.MIGRATION else S.JobKind.DIALOGUE_EVALUATION,
                source_id=sid, source_revision=src.receipt.source_revision,
                workspace_id=wid, scope_revision=scope.scope_revision,
                priority=S.JobPriority.BACKFILL.value)
            accepted.append({"source_id": sid, "job_id": job.job_id})
        else:
            skipped.append({"source_id": sid, "reason": "already_evaluated"})
    return {"accepted": accepted, "skipped": skipped}


@router.delete("/evidence/{source_id}")
async def delete_evidence(source_id: str,
                          if_match: str = Header(default=""),
                          student_id: str = Depends(resolve_student_id)):
    """删除证据（§11.2/§5.3，R07）：先取消在途/dismiss 复核/递归失效，
    再 operation 级 rewrite 物理清除原文与派生副本——不是只删 source
    注册行。If-Match 携带当前 source_revision，不一致 409。"""
    state = get_journal(student_id).state()
    src = state.sources.get(source_id)
    if src is None:
        raise _error(404, "source_not_found", "证据不存在")
    if if_match and if_match.strip() != str(src.receipt.source_revision):
        raise _error(409, "revision_conflict", "证据版本已变化，请刷新")
    session_ref = src.receipt.source_session_ref
    result = lifecycle.delete_evidence_source(student_id, source_id)
    return {"status": "accepted", "deleted": source_id,
            "affected_concepts": result.get("affected_concepts", []),
            "source_session_ref": session_ref}
