"""Assessment API: Computerized Adaptive Test endpoints (M4 Phase 3).

These expose the CAT loop to the frontend / supervisor. They are deliberately
thin: all logic lives in AssessmentManager. Each endpoint degrades gracefully
(returns a clear status) and never raises into the response.

Lifecycle:
  POST /assessment/start   -> {session_id, question}      (first question)
  POST /assessment/answer  -> {result, stop_reason, summary?}  (grade current)
  POST /assessment/next    -> {question?, stop_reason, summary?} (advance or stop)
  GET  /assessment/report  -> summary of active/finished session
  POST /assessment/abandon -> end without verdict
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
    ctx = AssessmentContext(concept=req.concept, subject=req.subject,
                            grade=req.grade,
                            current_mastery=_server_mastery(sid, req.concept))
    am = get_assessment_manager()
    try:
        session, q = await am.start_adaptive_test(goal, ctx, llm=llm, student_id=sid)
        return {"status": "ok", "session_id": sid,
                "difficulty": session.current_difficulty,
                "question": q.to_dict() if q else None}
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
        # check if the test should stop after this answer
        from app.agents.assessment.adaptive_test import should_stop
        session = am.get_active_session(sid)
        stop_reason = should_stop(session) if session else "no_session"
        out = {"status": "ok", "result": result.to_dict(),
               "stop_reason": stop_reason}
        if stop_reason:
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
        out = {"status": "ok", "stop_reason": stop_reason,
               "difficulty": session.current_difficulty,
               "question": q.to_dict() if q else None}
        if stop_reason:
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
