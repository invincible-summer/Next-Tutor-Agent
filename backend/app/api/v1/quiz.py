"""Quiz grading API: stream LLM grading feedback for a student's answer.

For fill-in-the-blank / short-answer questions (where a simple string compare
is unreliable), the frontend submits the student's answer here; the LLM
judges correctness and streams a concise explanation. Multiple-choice keeps
its client-side letter compare (deterministic, no LLM round-trip needed).

W1 信任边界（docs/updatePlan.md A01/A02）：判分依据收归服务端。请求里的
correct_answer 仅为旧客户端兼容字段，服务端从调用者本人会话的 quiz_history
快照解析权威题目（答案/选项/解析/知识点）。无法解析的题目降级为「未验证
练习」——只给反馈、零掌握度/账本写入；他人 session_id 在任何 LLM、评分或
持久化之前返回 404（与 chat 路由同款「不可见」语义）。
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.atomic import file_lock
from app.core.llm_async import get_llm
from app.core.quiz_attempts import record_quiz_attempt
from app.core.quiz_recent import list_recent_questions, record_recent_verdict
from app.core.quiz_verify import question_verified
from app.identity.deps import resolve_student_id
from app.agents.student_model import is_enabled as student_model_enabled
from app.agents.student_model.store import DEFAULT_STUDENT_ID
from app.core.session import TutorSession, load_session, save_session, session_path
from app.agents.assessment import (AssessmentContext, Question,
                                   get_assessment_manager, grade_open_prompt,
                                   is_enabled as assessment_enabled,
                                   evaluate_mc)

router = APIRouter(prefix="/quiz", tags=["quiz"])

logger = logging.getLogger(__name__)

_SCORE_MAP = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}


def _session_owned_by(session: TutorSession, student_id: str) -> bool:
    """Legacy sessions (no student_id stamp) belong to the shared guest."""
    return (session.student_id or DEFAULT_STUDENT_ID) == student_id


def _load_owned_session(session_id: str, student_id: str) -> TutorSession:
    """Load a session and enforce ownership (404 = invisible, not 403).

    Mirrors chat.py: a foreign session must be indistinguishable from a
    missing one, and the check runs before any LLM/scoring/persistence."""
    session = load_session(session_id)
    if session is None or not _session_owned_by(session, student_id):
        raise HTTPException(404, "会话不存在")
    return session


def _resolve_question_snapshot(
        session: TutorSession | None, stem: str) -> tuple[dict, dict] | None:
    """Locate the authoritative question dict for ``stem`` in the session's
    quiz_history (newest set first; exact stem match, then the same 60-char
    prefix key ``_write_back_answer`` uses).

    Returns ``(question_dict, quiz_set_dict)`` — the set carries the
    generation-time ``answer_verified`` / ``verification`` info — or None
    when nothing matches. A prefix match is only trusted when it is
    unambiguous (one distinct stem); otherwise fail closed to a recoverable
    error instead of guessing which question was answered. Never raises.
    """
    try:
        if session is None:
            return None
        key_full = (stem or "").strip()
        key_prefix = key_full[:60]
        if not key_prefix:
            return None
        prefix_stems: set[str] = set()
        fallback: tuple[dict, dict] | None = None
        for qh in reversed(session.quiz_history or []):
            if not isinstance(qh, dict):
                continue
            for q in (qh.get("questions") or []):
                if not isinstance(q, dict):
                    continue
                q_stem = str(q.get("stem", "") or "").strip()
                if not q_stem:
                    continue
                if q_stem == key_full:
                    return q, qh
                if q_stem[:60] == key_prefix:
                    prefix_stems.add(q_stem)
                    if fallback is None:
                        fallback = (q, qh)
        if fallback is not None and len(prefix_stems) == 1:
            return fallback
        return None
    except Exception:
        return None


def _prior_result(q: dict, student_answer: str) -> dict | None:
    """A recorded result for the SAME student answer means a duplicate
    submission (double-click / retry): the identical answer must be scored
    exactly once, so callers return this instead of re-grading."""
    res = q.get("result")
    if not isinstance(res, dict) or not res.get("verdict"):
        return None
    if str(res.get("student_answer", "")) != (student_answer or "")[:200]:
        return None
    return res


def _audit_answer_mismatch(session_id: str, client_answer: str,
                           server_answer: str) -> None:
    """Client-supplied answer keys are compatibility-only. Log the mismatch
    flag (never the content) so tampering attempts stay auditable."""
    client = (client_answer or "").strip()
    if client and client != (server_answer or "").strip():
        logger.warning("quiz grading: client correct_answer mismatch sid=%s",
                       session_id or "-")


def _write_back_answer(session_id: str, *, stem: str, verdict: str,
                       student_answer: str,
                       owner_student_id: str = "",
                       attempt_id: str = "") -> None:
    """Attach the graded result to the matching question in the session's
    quiz_history, so the NEXT chat turn sees what the student answered.

    Card interactions (MC reveal / open-answer grading) happen outside the
    chat stream; without this write-back the model has zero visibility into
    them ("我这边没有收到你的作答"). Matches by stem prefix within the newest
    quiz set first. Never raises. W1: the load-modify-save critical section
    holds the session file lock, and a caller identity that does not own the
    session is skipped (defense in depth — routes already 404). W2/A14: the
    attempt id rides on the result so a replay can return the SAME recorded
    attempt instead of re-grading.
    """
    try:
        if not session_id or not verdict or verdict == "unknown":
            return
        with file_lock(session_path(session_id)):
            session = load_session(session_id)
            if session is None:
                return
            if owner_student_id and \
                    not _session_owned_by(session, owner_student_id):
                logger.warning("quiz write-back skipped: caller does not own "
                               "session sid=%s", session_id)
                return
            stem_key = (stem or "").strip()[:60]
            if not stem_key:
                return
            for qh in reversed(session.quiz_history or []):
                if not isinstance(qh, dict):
                    continue
                for q in (qh.get("questions") or []):
                    if not isinstance(q, dict):
                        continue
                    if str(q.get("stem", "")).strip()[:60] != stem_key:
                        continue
                    result = {"verdict": verdict,
                              "student_answer": (student_answer or "")[:200]}
                    if attempt_id:
                        result["attempt_id"] = attempt_id
                    q["result"] = result
                    # Also sync the result into the persisted assistant message's
                    # tool payload, so a reloaded chat restores the card's
                    # answered state (locked) instead of allowing a second answer.
                    for msg in reversed(session.messages or []):
                        if not isinstance(msg, dict) or msg.get("role") != "assistant":
                            continue
                        for tc in (msg.get("toolCalls") or []):
                            data = tc.get("result", {}).get("data") \
                                if isinstance(tc, dict) and isinstance(tc.get("result"), dict) else None
                            for mq in ((data or {}).get("questions") or []):
                                if isinstance(mq, dict) and \
                                        str(mq.get("stem", "")).strip()[:60] == stem_key:
                                    mq["result"] = dict(result)
                    from app.core.session_learning_card import (SessionLearningCard,
                                                                 reconcile_quiz_history)
                    card = SessionLearningCard.from_dict(session.context_card)
                    reconcile_quiz_history(card, session.quiz_history or [])
                    session.context_card = card.to_dict()
                    save_session(session)
                    # 同步回填跨会话「最近习题」库的判分状态
                    record_recent_verdict(
                        session_id, getattr(session, "student_id", "") or "",
                        stem=stem, verdict=verdict, student_answer=student_answer,
                        attempt_id=attempt_id)
                    return
    except Exception:
        pass


class GradeRequest(BaseModel):
    stem: str = Field(..., description="题干")
    q_type: str = Field("short_answer", description="题型: fill_blank | short_answer")
    student_answer: str = Field(..., min_length=1, description="学生作答")
    correct_answer: str = Field(..., description="参考答案（仅兼容保留；服务端以会话内题目快照为准）")
    explanation: str = Field("", description="参考解析（可选，辅助判断）")
    knowledge_point: str = Field("", description="对应知识点")
    grade: str = Field("本科", description="学段")
    session_id: str = Field("", description="会话 id（据此记录做题结果更新掌握度）")
    subject: str = Field("", description="学科，可选")
    difficulty: int = Field(0, description="题目难度 1-5（M4 评分上下文，可选）")
    record: bool = Field(True, description="是否写入掌握度/作答记录；MC 点评等只读调用传 false")


@router.post("/grade")
async def grade_answer(req: GradeRequest,
                       student_id: str = Depends(resolve_student_id)):
    # W1: ownership first — a foreign session 404s before any LLM work, and
    # the grading basis is the server's own question snapshot when one exists.
    session = _load_owned_session(req.session_id, student_id) if req.session_id else None
    snap = _resolve_question_snapshot(session, req.stem)
    llm = get_llm()
    question: Question | None = None
    qh: dict = {}
    if snap is not None:
        qd, qh = snap
        if str(qd.get("answer") or "").strip():
            question = Question.from_quiz_dict(qd, difficulty=req.difficulty)
            _audit_answer_mismatch(req.session_id, req.correct_answer,
                                   str(qd.get("answer") or ""))
    if question is not None:
        prompt = grade_open_prompt(
            stem=question.stem, q_type=question.q_type,
            correct_answer=question.answer, explanation=question.explanation,
            student_answer=req.student_answer, grade=req.grade,
        )
    else:
        # No server snapshot (missing/foreign-free session or unmatched stem):
        # practice-only feedback against the caller's own reference; never
        # recorded as evidence (A02「外部自带题目只进入未验证练习」).
        prompt = grade_open_prompt(
            stem=req.stem, q_type=req.q_type, correct_answer=req.correct_answer,
            explanation=req.explanation, student_answer=req.student_answer,
            grade=req.grade,
        )

    prior = (_prior_result(snap[0], req.student_answer)
             if req.record and snap is not None else None)
    # W3/§8.5：同题不同答的重评会在账本 supersede 旧 attempt，BKT 需重放。
    had_prior_verdict = bool(req.record and snap is not None
                             and snap[0].get("result"))

    async def _duplicate_stream(verdict: str):
        payload = {"verdict": verdict, "feedback": "", "full": "",
                   "score": _SCORE_MAP.get(verdict, 0.0),
                   "duplicate": True}
        if prior and prior.get("attempt_id"):
            payload["attempt_id"] = prior["attempt_id"]
        yield ("event: done\ndata: "
               + json.dumps(payload, ensure_ascii=False) + "\n\n")

    if prior is not None:
        # Same submission scored once: resubmitting re-plays the recorded
        # verdict without another LLM call or a second mastery write.
        return StreamingResponse(
            _duplicate_stream(str(prior.get("verdict"))),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"},
        )

    async def event_stream():
        full = ""
        try:
            async for ev in llm.stream(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=400,
            ):
                if ev["kind"] == "answer":
                    full += ev["delta"]
                    yield f"event: delta\ndata: {json.dumps({'content': ev['delta']}, ensure_ascii=False)}\n\n"
                elif ev["kind"] == "retry":
                    yield f"event: retry\ndata: {json.dumps({'attempt': ev.get('attempt'), 'reason': ev.get('reason')}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"
            return
        # M4: route the streamed grade through the assessment engine for a
        # three-level parse (score / concept_status / mistake_type) and a
        # single closed-loop writeback to the Student Model. Backward
        # compatible: old frontends read verdict/feedback/full only.
        authoritative = question is not None
        result = None
        # W2/A14: one server-generated attempt id per accepted grading; it keys
        # the M2 event, the ledger attempt and the session write-back as ONE
        # submission (idempotent replay, supersede on re-answer).
        attempt_id = "att_" + uuid.uuid4().hex[:16]
        if req.record and authoritative and assessment_enabled():
            try:
                result = await get_assessment_manager().evaluate_and_record(
                    question, req.student_answer,
                    AssessmentContext(concept=question.concept or req.knowledge_point,
                                      subject=req.subject, grade=req.grade,
                                      skill_id=""),
                    raw_grade=full, student_id=student_id,
                    is_variant=_is_variant_set(qh),
                    question_verified=question_verified(qh.get("verification")),
                    attempt_id=attempt_id,
                    assistance="hint" if qd.get("hint_requested") else "")
            except Exception:
                result = None
        # fall back to the legacy inline parse if the engine is off / failed,
        # so grading never regresses to "no verdict".
        if result is not None and result.verdict != "unknown":
            verdict = result.verdict
            body = result.feedback
            done = {"verdict": verdict, "feedback": body, "full": full,
                    "score": result.score, "concept_status": result.concept_status}
            # W3/F01: the structured block (first error / hypotheses /
            # next_step) rides the done payload for the card's detail panel.
            if result.structured:
                done["structured"] = result.structured
        else:
            verdict = None
            stripped = full.lstrip()
            if stripped.startswith("[对]"):
                verdict = "correct"
            elif stripped.startswith("[错]"):
                verdict = "wrong"
            elif stripped.startswith("[部分对]"):
                verdict = "partial"
            body = (stripped[3:].lstrip() if verdict in ("correct", "wrong")
                    else stripped[5:].lstrip() if verdict == "partial" else full)
            if (req.record and authoritative and verdict and req.session_id
                    and student_model_enabled() and req.knowledge_point):
                try:
                    from app.agents.student_model import record_quiz_result
                    # W2/A04: partial must not collapse to a binary wrong here
                    # either (engine-off degraded path).
                    record_quiz_result(
                        concept=req.knowledge_point,
                        correct=(verdict == "correct"),
                        session_id=req.session_id,
                        knowledge_point=req.knowledge_point,
                        subject=req.subject,
                        note=(body[:60] if verdict != "correct" else ""),
                        student_id=student_id,
                        verdict=verdict,
                    )
                except Exception:
                    pass
            done = {"verdict": verdict, "feedback": body, "full": full}
        if req.record and authoritative:
            _write_back_answer(req.session_id, stem=req.stem,
                               verdict=verdict or "",
                               student_answer=req.student_answer,
                               owner_student_id=student_id,
                               attempt_id=attempt_id)
            record_quiz_attempt(
                req.session_id, stem=req.stem, verdict=verdict or "",
                student_answer=req.student_answer,
                concept=req.knowledge_point,
                subject=req.subject, student_id=student_id,
                correct=(verdict == "correct"), note=(body or "")[:60],
                attempt_id=attempt_id)
            _maybe_rebuild_mastery(student_id,
                                   had_prior_verdict=had_prior_verdict,
                                   attempt_id=attempt_id)
            done["attempt_id"] = attempt_id
        elif req.record:
            # Unresolved question: practice-only, explicitly marked so the
            # client can show why nothing was recorded.
            done["unverified"] = True
        yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


class RecordRequest(BaseModel):
    """Report a (deterministically graded) multiple-choice result back so it
    feeds the Student Model mastery loop -- closes the section-14.14 gap where
    MC was graded client-side and never recorded."""
    stem: str = Field("", description="题干")
    q_type: str = Field("multiple_choice", description="题型")
    student_answer: str = Field(..., description="学生选择的选项字母")
    correct_answer: str = Field(..., description="正确选项字母（仅兼容保留；服务端以会话内题目快照为准）")
    options: dict[str, str] = Field(default_factory=dict, description="选项（可选，辅助上下文）")
    explanation: str = Field("", description="参考解析")
    knowledge_point: str = Field("", description="对应知识点")
    grade: str = Field("本科", description="学段")
    session_id: str = Field("", description="会话 id")
    subject: str = Field("", description="学科")
    difficulty: int = Field(3, description="题目难度 1-5")


def _is_variant_set(qh: dict) -> bool:
    """fit_quiz variant sets carry ``reference`` instead of ``topic``; their
    questions are same-family variants, not independent evidence."""
    return isinstance(qh, dict) and "reference" in qh and "topic" not in qh


def _maybe_rebuild_mastery(student_id: str, *, had_prior_verdict: bool,
                           attempt_id: str) -> None:
    """W3/§8.5: a re-answer superseded a prior attempt — the legacy BKT
    posterior is replayed from the valid event sequence (a Bayesian update
    cannot be subtracted back out). Idempotent, low-frequency (re-answers
    only), best-effort."""
    if not had_prior_verdict:
        return
    try:
        from app.agents.student_model import rebuild_mastery
        rebuild_mastery(student_id, reason=f"reanswer:{attempt_id}")
    except Exception:
        pass


@router.post("/record")
async def record_answer(req: RecordRequest,
                        student_id: str = Depends(resolve_student_id)):
    """Record a graded answer (MC) into the Student Model via the assessment
    engine's single closed-loop point. Returns the structured result so the
    frontend can show score / concept_status if it wants to.

    W1: the verdict basis is the SERVER's own question snapshot (answer,
    options, knowledge point) resolved from the caller's session; the
    client-supplied correct_answer is ignored. An unresolvable question
    becomes unverified practice (zero writes) instead of client-graded
    evidence; the identical resubmission replays the recorded verdict."""
    session = _load_owned_session(req.session_id, student_id) if req.session_id else None
    snap = _resolve_question_snapshot(session, req.stem)
    if snap is None or not str(snap[0].get("answer") or "").strip():
        return {"status": "unverified_practice", "code": "question_unresolved",
                "message": "未能定位该题目的服务端快照（会话缺少该题或已过期）；"
                           "本次作答仅作练习，不计入掌握度。",
                "result": None}
    qd, qh = snap
    _audit_answer_mismatch(req.session_id, req.correct_answer,
                           str(qd.get("answer") or ""))
    prior = _prior_result(qd, req.student_answer)
    if prior is not None:
        # Same submission scored once: replay the recorded verdict, no
        # second mastery/ledger write.
        verdict = str(prior.get("verdict"))
        out = {"status": "ok", "duplicate": True,
               "result": {"verdict": verdict,
                          "score": _SCORE_MAP.get(verdict, 0.0),
                          "concept_status": ""}}
        if prior.get("attempt_id"):
            out["attempt_id"] = prior["attempt_id"]
        return out
    question = Question.from_quiz_dict(qd, difficulty=req.difficulty)
    concept = ", ".join(question.knowledge_points) or req.knowledge_point
    ctx = AssessmentContext(concept=concept, subject=req.subject, grade=req.grade)
    # W2/A14: server-generated attempt id shared by the M2 event, the ledger
    # attempt and the session write-back (one submission, one record).
    attempt_id = "att_" + uuid.uuid4().hex[:16]
    # W3/§8.5: a DIFFERENT answer to an already-graded question supersedes the
    # prior attempt in the ledger — the BKT posterior needs a replay after.
    had_prior_verdict = bool(qd.get("result"))

    def _finalize(verdict: str) -> None:
        _write_back_answer(req.session_id, stem=req.stem, verdict=verdict,
                           student_answer=req.student_answer,
                           owner_student_id=student_id,
                           attempt_id=attempt_id)
        record_quiz_attempt(
            req.session_id, stem=req.stem, verdict=verdict,
            student_answer=req.student_answer, concept=concept,
            subject=req.subject, student_id=student_id,
            correct=(verdict == "correct"), attempt_id=attempt_id)
        _maybe_rebuild_mastery(student_id, had_prior_verdict=had_prior_verdict,
                               attempt_id=attempt_id)

    if not assessment_enabled():
        result = evaluate_mc(question, req.student_answer)
        _finalize(result.verdict)
        return {"status": "disabled",
                "result": result.to_dict()}
    try:
        result = await get_assessment_manager().evaluate_and_record(
            question, req.student_answer, ctx, student_id=student_id,
            is_variant=_is_variant_set(qh),
            question_verified=question_verified(qh.get("verification")),
            attempt_id=attempt_id,
            assistance="hint" if qd.get("hint_requested") else "")
        _finalize(result.verdict)
        return {"status": "ok", "attempt_id": attempt_id,
                "result": result.to_dict()}
    except Exception as e:
        result = evaluate_mc(question, req.student_answer)
        _finalize(result.verdict)
        return {"status": "error", "message": str(e),
                "result": result.to_dict()}


@router.get("/hint")
async def quiz_hint(session_id: str = Query(...), stem: str = Query(...),
                    student_id: str = Depends(resolve_student_id)) -> dict:
    """F02 关键步骤提示：从该题的冻结量规派生（W3/D04），只含步骤描述、
    不含答案与判定——先答后揭晓原则不破。提示请求由服务端记录
   （hint_requested 标记随题落盘），判分时自动携带 assistance=hint；
    客户端自报的帮助状态一律不采信（A02 纪律）。"""
    session = _load_owned_session(session_id, student_id)
    snap = _resolve_question_snapshot(session, stem)
    if snap is None:
        return {"status": "question_unresolved", "hint": ""}
    qd, _qh = snap
    if qd.get("result"):
        return {"status": "already_answered", "hint": ""}
    rubric = qd.get("rubric") if isinstance(qd.get("rubric"), dict) else {}
    criteria = rubric.get("criteria") or []
    if not criteria:
        return {"status": "no_rubric", "hint": "",
                "message": "这道题还没有关键步骤提示。"}
    steps = [f"{i}. {str(c.get('description') or '').strip()}"
             for i, c in enumerate(criteria[:4], 1) if c.get("description")]
    hint = "这道题可以按这些关键步骤来检查：\n" + "\n".join(steps)
    # load-modify-save（锁内重载，避免覆盖并发的判分写回）
    try:
        with file_lock(session_path(session_id)):
            fresh = load_session(session_id)
            if fresh is not None and _session_owned_by(fresh, student_id):
                snap2 = _resolve_question_snapshot(fresh, stem)
                if snap2 is not None:
                    q2, _ = snap2
                    if not q2.get("result"):   # 期间已作答则不记
                        q2["hint_requested"] = True
                        q2["hint_count"] = int(q2.get("hint_count") or 0) + 1
                        save_session(fresh)
    except Exception:
        pass
    return {"status": "ok", "hint": hint}


class DisputeRequest(BaseModel):
    attempt_id: str = Field(..., min_length=4, description="被异议的作答 attempt id")
    reason: str = Field("", max_length=200, description="异议理由")


@router.post("/dispute")
async def quiz_dispute(req: DisputeRequest,
                       student_id: str = Depends(resolve_student_id)) -> dict:
    """F05 异议最小形式：标记该 attempt 为 disputed（账本审计）。

    保守语义：异议不抹除证据、不改当前投影；修正走 W2 的重答 supersede
    路径（同一道题重新作答会 supersede 旧判定并触发 BKT 重放）。完整的
    独立复核 job（§9.2 POST /learning/assessments/{id}/review）属后续迭代。"""
    from app.core.learning_records import flag_attempt_disputed
    ok = flag_attempt_disputed(student_id, req.attempt_id.strip(),
                               reason=req.reason)
    return {"status": "ok" if ok else "not_found",
            "message": "" if ok else "未找到属于你的这条作答记录。"}


@router.get("/recent")
async def recent_questions(student_id: str = Depends(resolve_student_id)) -> dict:
    """跨会话「最近习题」列表（新→旧，每学生上限 100 道）。

    测评中心分页展示用：出题时快照入库，答题卡判分后回填 verdict。
    """
    return {"status": "ok", "questions": list_recent_questions(student_id)}
