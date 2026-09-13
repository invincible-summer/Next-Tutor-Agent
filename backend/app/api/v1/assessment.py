"""Assessment API: Computerized Adaptive Test endpoints (M4 Phase 3).

These expose the CAT loop to the frontend / supervisor. They are deliberately
thin: all logic lives in AssessmentManager. Each endpoint degrades gracefully
(returns a clear status) and never raises into the response.

Lifecycle (W2/A03: every state below is persisted; a refresh resumes it):
  POST /assessment/start   -> {assessment_id, question}   (first question)
  POST /assessment/answer  -> {result, stop_reason, summary?}  (grade current;
                               a stop triggered here is persisted in the same
                               write, so restart/report agree with the response)
  POST /assessment/next    -> {question?, stop_reason, summary?} (advance or
                               stop; re-issues the current question instead of
                               stacking a new one while it is unanswered)
  GET  /assessment/active  -> recovery view of the current session (question /
                               progress / terminal state)
  GET  /assessment/report  -> summary of active/finished session
  POST /assessment/abandon -> end without verdict (persisted, file kept)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.llm_async import get_llm
from app.agents.assessment import (AssessmentContext, AssessmentGoal,
                                    get_assessment_manager,
                                    is_enabled as assessment_enabled)
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/assessment", tags=["assessment"])


def _public_question(qd: dict | None) -> dict | None:
    """Strip the authoritative answer/rationale from a question payload.

    Grading is server-side (W1/A02); the client only renders stem/options, so
    nothing after the reveal time should ship with an unanswered question."""
    if not isinstance(qd, dict):
        return None
    return {k: v for k, v in qd.items() if k not in ("answer", "explanation")}


class StartRequest(BaseModel):
    concept: str = Field(..., description="要测评的知识点")
    purpose: str = Field("adaptive", description="测评目的: adaptive | diagnose")
    count: int = Field(6, description="自适应测试题数上限")
    assesses: list[str] = Field(default_factory=list, description="重点检测的子能力")
    forbidden: list[str] = Field(default_factory=list, description="禁用方法")
    q_type: str = Field("", description="题型，空则自动选")
    bloom_focus: str = Field("", description="布鲁姆层级焦点（空/auto=由出题 LLM 结合认知档案综合判断）")
    grade: str = Field("本科", description="学段")
    subject: str = Field("", description="学科")
    difficulty: int = Field(0, description="起始难度 1-5，0=按掌握度推断")
    mastery: float = Field(0.0, description="（仅兼容保留，一律忽略）当前掌握度由服务端按身份读取")
    student_id: str = Field("", description="学生 id（默认 default）")
    # 统一 Quiz Grounding（plan.md §5.2）：教材 scope 由服务端按授权解析，
    # 不开放任意 owner/file namespace。
    session_id: str = Field("", description="可选：从该已授权会话/工作区获取教材范围")
    textbook_ids: list[str] = Field(default_factory=list, max_length=8,
                                    description="可选：直接指定当前学生可访问的教材组")
    strict_textbook: bool = Field(
        False, description="true 时，没有可靠教材证据就不生成教材测评题")


def _server_mastery(sid: str, concept: str) -> float:
    """Best-effort current mastery from the identity-bound student profile.

    W1（updatePlan.md A02）：client-supplied mastery 不是可信输入——它此前
    直达 derive_concept_status，可把一次答对标成「已掌握」。读不到（能力
    关闭/无记录）时保持 0.0 起点推断。"""
    try:
        from app.agents.student_model import (get_student_model,
                                              is_enabled as sm_enabled)
        if not sm_enabled():
            return 0.0
        sm = get_student_model(sid).load()
        node = sm.graph.match_concept(concept or "", threshold=0.6)
        if node is not None:
            rec = sm.mastery.records.get(node.id)
            if rec is not None:
                return float(rec.p_known or 0.0)
    except Exception:
        pass
    return 0.0


@router.post("/start")
async def start_test(req: StartRequest, _token_sid: str = Depends(resolve_student_id)):
    if not assessment_enabled():
        return {"status": "disabled"}
    # M0 隔离：student_id 只认 JWT 解析结果（游客回退 DEFAULT_STUDENT_ID），
    # 请求体里的 student_id 字段仅为旧客户端兼容保留，一律忽略——否则任何
    # 登录用户都能读写他人/游客命名空间的 CAT 会话。
    sid = _token_sid
    llm = get_llm()
    goal = AssessmentGoal(
        concept=req.concept, purpose=req.purpose or "adaptive",
        count=max(1, min(20, int(req.count))), q_type=req.q_type,
        assesses=list(req.assesses), forbidden=list(req.forbidden),
        difficulty=max(0, min(5, int(req.difficulty))),
        bloom_focus=req.bloom_focus or "")
    ctx_kwargs = {}
    # 统一 Quiz Grounding（plan.md §5.2）：按授权解析教材证据 scope；非本人
    # session/无权教材 404；strict 无 scope 400（helper 内抛出）。scope 有效
    # 但 strict+NOT_FOUND -> 不开始假教材 CAT，明确返回 grounding_not_found。
    if req.session_id or req.textbook_ids or req.strict_textbook:
        from app.api.v1.assessment_grounding import (build_assessment_grounding,
                                                     bundle_to_context_fields)
        bundle = await build_assessment_grounding(
            student_id=sid, concept=req.concept, session_id=req.session_id,
            textbook_ids=list(req.textbook_ids or []),
            strict_textbook=req.strict_textbook)
        ctx_kwargs = bundle_to_context_fields(bundle)
        if req.strict_textbook and (bundle is None or not bundle.usable):
            meta = bundle.grounding_meta() if bundle is not None else {
                "mode": "textbook", "tier": "not_found", "required": True,
                "reason": "textbook_scope_required", "query": req.concept,
                "source_count": 0}
            return {"status": "grounding_not_found", "grounding": meta,
                    "question": None}
    ctx = AssessmentContext(concept=req.concept, subject=req.subject,
                            grade=req.grade,
                            current_mastery=_server_mastery(sid, req.concept),
                            **ctx_kwargs)
    am = get_assessment_manager()
    try:
        session, q = await am.start_adaptive_test(goal, ctx, llm=llm, student_id=sid)
        return {"status": "ok", "session_id": sid,
                "assessment_id": session.assessment_id,
                "difficulty": session.current_difficulty,
                "question": _public_question(q.to_dict()) if q else None}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class AnswerRequest(BaseModel):
    student_answer: str = Field(..., description="学生作答（MC 为字母）")
    raw_grade: str = Field("", description="（仅兼容保留，一律忽略）批改一律由服务端完成")
    student_id: str = Field("")


@router.post("/answer")
async def record_answer(req: AnswerRequest, _token_sid: str = Depends(resolve_student_id)):
    """Grade the current question of an active CAT. MC is deterministic; for
    open questions the LLM grades here (non-streaming).

    W1（updatePlan.md A02）：``raw_grade`` 不再是可信输入——任何调用方都
    无法再用自己的批改全文跳过服务端评分。字段仅为旧客户端 schema 兼容
    保留，值被一律忽略。"""
    if not assessment_enabled():
        return {"status": "disabled"}
    # M0 隔离：student_id 只认 JWT 解析结果（游客回退 DEFAULT_STUDENT_ID），
    # 请求体里的 student_id 字段仅为旧客户端兼容保留，一律忽略——否则任何
    # 登录用户都能读写他人/游客命名空间的 CAT 会话。
    sid = _token_sid
    am = get_assessment_manager()
    llm = get_llm()
    try:
        result = await am.record_cat_answer(
            sid, answer=req.student_answer,
            raw_grade=None, llm=llm)
        if result is None:
            return {"status": "no_active_question"}
        # W2/A03: read the PERSISTED stop state — record_cat_answer finalized
        # the session in the same write when this answer triggered a stop, so
        # refresh/report/restart can no longer disagree with this response.
        session = am.get_session(sid)
        stop_reason = session.stop_reason if session else "no_session"
        out = {"status": "ok",
               "assessment_id": session.assessment_id if session else "",
               "result": result.to_dict(),
               "stop_reason": stop_reason}
        if session is not None and session.status != "active":
            out["summary"] = am.cat_report(sid)
        return out
    except Exception as e:
        return {"status": "error", "message": str(e)}


class NextRequest(BaseModel):
    student_id: str = Field("")


@router.post("/next")
async def next_question(req: NextRequest, _token_sid: str = Depends(resolve_student_id)):
    if not assessment_enabled():
        return {"status": "disabled"}
    # M0 隔离：student_id 只认 JWT 解析结果（游客回退 DEFAULT_STUDENT_ID），
    # 请求体里的 student_id 字段仅为旧客户端兼容保留，一律忽略——否则任何
    # 登录用户都能读写他人/游客命名空间的 CAT 会话。
    sid = _token_sid
    am = get_assessment_manager()
    llm = get_llm()
    try:
        session, q, stop_reason = await am.next_question(sid, llm=llm)
        if session is None:
            return {"status": "no_active_session"}
        out = {"status": "ok",
               "assessment_id": session.assessment_id,
               "stop_reason": stop_reason,
               "difficulty": session.current_difficulty,
               "question": _public_question(q.to_dict()) if q else None}
        if session.status != "active" or stop_reason:
            out["summary"] = am.cat_report(sid)
        return out
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/report")
async def report(student_id: str = "", _token_sid: str = Depends(resolve_student_id)):
    if not assessment_enabled():
        return {"status": "disabled"}
    # M0 隔离：同上，query 里的 student_id 一律忽略。
    sid = _token_sid
    summary = get_assessment_manager().cat_report(sid)
    return {"status": "ok" if summary else "no_active_session", "summary": summary}


@router.get("/active")
async def active_state(_token_sid: str = Depends(resolve_student_id)):
    """Recovery view of the caller's current CAT (W2/A03).

    Refresh/multi-tab/restart all resume from here: the pending question (its
    public content only) plus answered progress while asking, or the persisted
    terminal state once the test finished or was abandoned."""
    if not assessment_enabled():
        return {"status": "disabled"}
    sid = _token_sid
    session = get_assessment_manager().get_session(sid)
    if session is None:
        return {"status": "none"}
    out: dict = {
        "status": "ok",
        "assessment_id": session.assessment_id,
        "session_status": session.status,
        "answered": session.answered_count,
        "stop_reason": session.stop_reason,
    }
    pending = (session.status == "active" and session.questions
               and len(session.results) < len(session.questions))
    if pending:
        out["question"] = _public_question(session.questions[-1].to_dict())
    else:
        out["question"] = None
        out["summary"] = get_assessment_manager().cat_report(sid)
    return out


class AbandonRequest(BaseModel):
    student_id: str = Field("")


@router.post("/abandon")
async def abandon(req: AbandonRequest, _token_sid: str = Depends(resolve_student_id)):
    if not assessment_enabled():
        return {"status": "disabled"}
    # M0 隔离：student_id 只认 JWT 解析结果（游客回退 DEFAULT_STUDENT_ID），
    # 请求体里的 student_id 字段仅为旧客户端兼容保留，一律忽略——否则任何
    # 登录用户都能读写他人/游客命名空间的 CAT 会话。
    sid = _token_sid
    get_assessment_manager().abandon_session(sid)
    return {"status": "ok"}
