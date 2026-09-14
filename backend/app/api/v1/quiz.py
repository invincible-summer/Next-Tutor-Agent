"""Quiz API：统一作答服务的薄适配层（plan §11.4 / A02/A03/A05）。

保留 URL 供同版前端使用，但请求一律携带 question_id + question_revision
身份；内部只调用 `evaluate_submission`。旧 stem/correct_answer/raw_grade/
record=false 契约已删除——旧客户端得到明确 422 刷新提示，不长期维护旧
语义。开放题不向用户直播未经校验的模型判断（§10.4）。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.assessment import (AnswerTooLarge,
                                   AssessmentBindingError,
                                   QuestionAlreadyAnswered,
                                   QuestionNotFound,
                                   QuestionRevisionMismatch,
                                   ScopeRevisionConflict,
                                   SessionNotOwned,
                                   WorkspaceNotOwned,
                                   evaluate_submission, is_enabled)
from app.agents.assessment.manager import (assistance_events,
                                           load_task_snapshot,
                                           record_assistance)
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import (get_journal,
                                                       new_review_id)
from app.core.atomic import file_lock
from app.core.session import load_session, save_session, session_path
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/quiz", tags=["quiz"])


def _error(status: int, code: str, message: str, *,
           retryable: bool = False) -> HTTPException:
    return HTTPException(status_code=status, detail={
        "error": {"code": code, "message": message, "retryable": retryable,
                  "request_id": "req_" + uuid.uuid4().hex[:12]}})


def _session_workspace(student_id: str, session_id: str) -> tuple[str, Any]:
    """会话归属校验（404 先于一切）+ 工作区解析。"""
    session = load_session(session_id) if session_id else None
    if session_id and session is None:
        raise _error(404, "session_not_found", "会话不存在")
    if session is not None:
        owner = session.student_id or "student_default"
        if owner != student_id:
            raise _error(404, "session_not_found", "会话不存在")
        return (session.workspace_id or ""), session
    return "", None


def _write_back_result(session_id: str, question_id: str,
                       student_answer: str, verdict: str | None,
                       attempt_id: str) -> None:
    """把受理结果写回会话 quiz_history（按 question_id，不用题干前缀），
    让下一轮对话与重载的题卡能看到作答状态。fail-open。"""
    if not session_id or not verdict:
        return
    try:
        with file_lock(session_path(session_id)):
            session = load_session(session_id)
            if session is None:
                return
            for q in [q for qh in session.quiz_history or []
                      if isinstance(qh, dict)
                      for q in (qh.get("questions") or [])
                      if isinstance(q, dict)]:
                if str(q.get("id") or "") != question_id:
                    continue
                q["result"] = {"verdict": verdict,
                               "student_answer": student_answer[:200],
                               "attempt_id": attempt_id,
                               "question_id": question_id}
            for msg in session.messages or []:
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                for tc in (msg.get("toolCalls") or []):
                    data = tc.get("result", {}).get("data") \
                        if isinstance(tc, dict) and isinstance(
                            tc.get("result"), dict) else None
                    for mq in ((data or {}).get("questions") or []):
                        if isinstance(mq, dict) and \
                                str(mq.get("id") or "") == question_id:
                            mq["result"] = {
                                "verdict": verdict,
                                "student_answer": student_answer[:200],
                                "attempt_id": attempt_id,
                                "question_id": question_id}
            save_session(session)
    except Exception:
        pass


class QuizSubmitRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question_id: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(ge=1, le=1_000_000, default=1)
    student_answer: str = Field(min_length=1)
    session_id: str = Field("", max_length=96)
    reply_message_ref: str = Field("", max_length=96)


async def _submit(req: QuizSubmitRequest, student_id: str,
                  surface: str) -> dict[str, Any]:
    if not is_enabled():
        raise _error(503, "evaluation_disabled",
                     "学习评价当前已停用；作答与反馈暂不可用。", retryable=True)
    qref = S.QuestionRef(question_id=req.question_id,
                         question_revision=req.question_revision)
    try:
        load_task_snapshot(student_id, qref)
    except QuestionNotFound:
        raise _error(404, "question_not_found",
                     "题目不存在（旧标签页请刷新）")
    except QuestionRevisionMismatch:
        raise _error(409, "question_revision_mismatch", "题目已更新，请刷新")
    # 会话归属预检（404 先于一切）；workspace/scope 由 evaluate_submission
    # 从服务端事实解析（R05：会话 > 显式 workspace > 题目绑定）。
    _session_workspace(student_id, req.session_id)
    try:
        receipt = await evaluate_submission(
            student_id=student_id, question_ref=qref,
            student_answer=req.student_answer, source_surface=surface,
            source_session_ref=req.session_id,
            reply_message_ref=req.reply_message_ref or None,
            run_inline=True)
    except AnswerTooLarge:
        raise _error(413, "answer_too_large", "作答超过 32KiB 上限")
    except QuestionAlreadyAnswered:
        raise _error(409, "question_already_answered",
                     "这道题已有正式提交；再练一次请开启新练习实例")
    except (WorkspaceNotOwned, SessionNotOwned):
        raise _error(404, "workspace_not_found", "工作区不存在")
    except AssessmentBindingError as exc:
        raise _error(409, "assessment_binding_error", str(exc))
    except ScopeRevisionConflict:
        raise _error(409, "scope_revision_conflict",
                     "教材范围已变化，请刷新后重试")
    if req.session_id and receipt.task_result is not None:
        _write_back_result(req.session_id, req.question_id,
                           req.student_answer,
                           receipt.task_result.verdict.value
                           if receipt.task_result.verdict else None,
                           receipt.attempt_id)
    state = get_journal(student_id).state()
    src = state.sources.get(receipt.source_id)
    feedback = ""
    continuation = None
    if src is not None and src.current_interpretation_id:
        meta = src.interpretations.get(src.current_interpretation_id, {})
        raw = meta.get("raw_interpretation") or {}
        feedback = raw.get("feedback") or "" if isinstance(raw, dict) else ""
        cont = meta.get("continuation")
        if isinstance(cont, dict):
            continuation = cont
    return {
        "status": "ok",
        "attempt_id": receipt.attempt_id,
        "source_id": receipt.source_id,
        "task_result": (receipt.task_result.model_dump()
                        if receipt.task_result else None),
        "evaluation": {"status": receipt.evaluation_status,
                       "interpretation_id": receipt.interpretation_id},
        "feedback": feedback,
        "continuation": continuation,
        "duplicate": receipt.duplicate,
    }


@router.post("/grade")
async def quiz_grade(req: QuizSubmitRequest,
                     student_id: str = Depends(resolve_student_id)):
    """开放题/填空题提交（非流式：校验完成后一次正式反馈，§10.4）。"""
    return await _submit(req, student_id, "chat_quiz")


@router.post("/record")
async def quiz_record(req: QuizSubmitRequest,
                      student_id: str = Depends(resolve_student_id)):
    """MC 题卡提交（服务端确定性判分 + 语义评价）。"""
    return await _submit(req, student_id, "chat_quiz")


@router.get("/hint")
async def quiz_hint(question_id: str = Query(""),
                    question_revision: int = Query(1, ge=1),
                    student_id: str = Depends(resolve_student_id)):
    """从冻结量规派生关键步骤提示；服务端记录帮助事件（§7.3）。"""
    if not question_id:
        raise _error(422, "question_id_required",
                     "请升级前端后使用（旧接口已停用）")
    qref = S.QuestionRef(question_id=question_id,
                         question_revision=question_revision)
    try:
        task = load_task_snapshot(student_id, qref)
    except Exception:
        raise _error(404, "question_not_found", "题目不存在")
    state = get_journal(student_id).state()
    answered = any(
        src.receipt.task_ref is not None
        and src.receipt.task_ref.question_id == question_id
        for src in state.sources.values())
    if answered:
        return {"status": "already_answered", "hint": ""}
    steps = [f"{i}. {c.description}"
             for i, c in enumerate(task.rubric[:4], 1)]
    hint = "这道题可以按这些关键步骤来检查：\n" + "\n".join(steps) \
        if steps else "这道题暂无关键步骤提示。"
    record_assistance(student_id, qref,
                      kind=S.AssistanceEventKind.HINT_REQUESTED, detail=hint)
    return {"status": "ok", "hint": hint}


class DisputeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source_id: str = Field("", max_length=64)
    interpretation_id: str = Field("", max_length=64)
    reason: str = Field(..., min_length=4, max_length=1200)
    issue_kind: str = Field("other", max_length=64)


@router.post("/dispute")
async def quiz_dispute(req: DisputeRequest,
                       student_id: str = Depends(resolve_student_id)):
    """「评价不准确」入口：登记 review_requested（C9 复核 job 在评价
    worker 中执行，§7.5）。同一来源只允许一个 active review。"""
    state = get_journal(student_id).state()
    source_id = req.source_id
    interpretation_id = req.interpretation_id
    if not source_id and interpretation_id:
        for src in state.sources.values():
            if interpretation_id in src.interpretations:
                source_id = src.receipt.source_id
                break
    src = state.sources.get(source_id)
    if src is None:
        raise _error(404, "source_not_found", "未找到属于你的这条作答记录")
    if not interpretation_id:
        interpretation_id = src.current_interpretation_id
    if not interpretation_id:
        raise _error(409, "no_interpretation",
                     "该作答尚未完成评价，暂不能提出异议")
    if state.review_active_by_source.get(source_id):
        return {"status": "duplicate",
                "review_id": state.review_active_by_source[source_id]}
    review = S.ReviewRequestRecord(
        review_id=new_review_id(), source_id=source_id,
        interpretation_id=interpretation_id, reason=req.reason,
        issue_kind=req.issue_kind, requested_at=S.utc_now_iso(),
        requested_revision=src.receipt.source_revision)
    from app.core import learner_runtime
    scheduler = learner_runtime.get_scheduler()
    job = scheduler.enqueue(
        student_id, kind=S.JobKind.REVIEW, source_id=source_id,
        source_revision=src.receipt.source_revision,
        workspace_id=src.receipt.workspace_id_at_observation,
        priority=S.JobPriority.REVIEW.value)
    get_journal(student_id).append(
        [S.OpReviewRequested(review=review, job_id=job.job_id)])
    return {"status": "ok", "review_id": review.review_id,
            "job_id": job.job_id}


@router.get("/recent")
async def recent_questions(limit: int = Query(100, ge=1, le=100),
                           student_id: str = Depends(resolve_student_id)):
    """跨会话最近习题（journal 投影；替换 .quiz_recent.json 真相）。"""
    state = get_journal(student_id).state()
    rows: list[dict[str, Any]] = []
    for src in state.sources.values():
        if src.receipt.kind != S.SourceKind.ASSESSMENT or \
                src.receipt.task_ref is None:
            continue
        interp_id = src.current_interpretation_id
        meta = src.interpretations.get(interp_id, {}) if interp_id else {}
        task = state.tasks.get(
            src.receipt.task_ref.question_id, {}).get(
            src.receipt.task_ref.question_revision)
        rows.append({
            "id": src.receipt.attempt_id,
            "ts": src.receipt.observed_at,
            "session_id": src.receipt.source_session_ref,
            "question_id": src.receipt.task_ref.question_id,
            "question_revision": src.receipt.task_ref.question_revision,
            "topic": task.task_family if task else "",
            "knowledge_point": task.source_badge if task else "",
            "type": task.q_type.value if task else "",
            "stem": task.stem[:160] if task else "",
            "verdict": ((meta.get("task_result") or {})
                        .get("verdict") or ""),
            "student_answer": src.receipt.canonical_text[:200],
            "evaluation_status": ("ready" if interp_id else "pending"),
            "availability": src.availability,
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return {"status": "ok", "questions": rows[:limit]}
