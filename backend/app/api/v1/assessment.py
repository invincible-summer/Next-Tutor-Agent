"""Assessment API：统一受理协议 + 自适应诊断（plan §11.4/§11.5）。

- 学生身份只经 `resolve_student_id`；请求体不信任任何判分结果/帮助自报。
- 所有作答（聊天题卡/习题中心/CAT）经 `evaluate_submission` 单入口受理。
- 错误统一 §11.1 envelope：`{"error": {code, message, retryable}}`。
- 语义评价 inline 执行（claim → pack → P3 → validator → commit 全链），
  GET 永不触发模型调用。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.assessment import (
    AnswerTooLarge, QuestionAlreadyAnswered, QuestionNotFound,
    QuestionRevisionMismatch, evaluate_submission, is_enabled, new_assessment_id,
    record_assistance, register_task_snapshot, task_snapshot_from_legacy)
from app.agents.assessment import adaptive_test as cat
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core.llm_async import get_llm
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/assessment", tags=["assessment"])


def api_error(status_code: int, code: str, message: str, *,
              retryable: bool = False) -> HTTPException:
    return HTTPException(status_code=status_code, detail={
        "error": {"code": code, "message": message, "retryable": retryable,
                  "request_id": "req_" + uuid.uuid4().hex[:12]}})


def _require_enabled() -> None:
    if not is_enabled():
        raise api_error(503, "evaluation_disabled",
                        "学习评价当前已停用；作答与反馈暂不可用。",
                        retryable=True)


# ---------------------------------------------------------------------------
# §11.4 统一受理
# ---------------------------------------------------------------------------

class SubmissionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question_id: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(ge=1, le=1_000_000)
    student_answer: str = Field(min_length=1)
    expected_scope_revision: str = Field("", max_length=128)
    assessment_id: str = Field("", max_length=96)
    reply_message_ref: str = Field("", max_length=96)
    workspace_id: str = Field("", max_length=96)


def _resolve_submission_workspace(student_id: str, req: SubmissionRequest,
                                  task: S.TaskSnapshot) -> tuple[str, str]:
    """服务端自行解析 workspace/scope（§11.4）：CAT 实例 > 会话回指 >
    显式 workspace_id > 题目注册时的 workspace。404 优先于一切。"""
    if req.assessment_id:
        inst = cat.load_instance(get_journal(student_id).state(),
                                 req.assessment_id)
        if inst is not None and inst.workspace_id:
            return inst.workspace_id, ""
    if req.reply_message_ref:
        from app.core.session import load_session
        session = load_session(req.reply_message_ref)
        if session is not None and session.workspace_id:
            owner = session.student_id or "student_default"
            if owner != student_id:
                raise api_error(404, "session_not_found", "会话不存在")
            return session.workspace_id, ""
    if req.workspace_id:
        return req.workspace_id, ""
    return task.workspace_id, ""


@router.post("/submissions")
async def submit_answer(
        req: SubmissionRequest,
        _sid: str = Depends(resolve_student_id),
        idempotency_key: str = Header(default="", alias="Idempotency-Key")):
    _require_enabled()
    qref = S.QuestionRef(question_id=req.question_id,
                         question_revision=req.question_revision)
    try:
        from app.agents.assessment.manager import load_task_snapshot
        task = load_task_snapshot(_sid, qref)
    except QuestionNotFound:
        raise api_error(404, "question_not_found", "题目不存在或未注册")
    except QuestionRevisionMismatch:
        raise api_error(409, "question_revision_mismatch",
                        "题目已更新，请刷新后重试")
    workspace_id, _ = _resolve_submission_workspace(_sid, req, task)
    scope_revision = req.expected_scope_revision
    if workspace_id and not scope_revision:
        try:
            from app.agents.student_model.evaluation.scope import (
                get_scope_resolver)
            scope_revision = get_scope_resolver().resolve(
                _sid, workspace_id).scope_revision
        except Exception:
            scope_revision = ""
    try:
        receipt = await evaluate_submission(
            student_id=_sid, question_ref=qref,
            student_answer=req.student_answer,
            source_surface="assessment_center",
            idempotency_key=idempotency_key,
            expected_scope_revision=scope_revision or None,
            assessment_id=req.assessment_id or None,
            reply_message_ref=req.reply_message_ref or None,
            workspace_id=workspace_id, scope_revision=scope_revision,
            run_inline=True)
    except AnswerTooLarge:
        raise api_error(413, "answer_too_large",
                        "作答超过 32KiB 上限，请缩小范围后提交")
    except QuestionAlreadyAnswered:
        raise api_error(409, "question_already_answered",
                        "这道题已有正式提交；再练一次请开启新练习实例")
    return _receipt_payload(_sid, receipt)


def _receipt_payload(_sid: str, receipt) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attempt_id": receipt.attempt_id,
        "source_id": receipt.source_id,
        "job_id": receipt.job_id,
        "question_id": receipt.question_id,
        "question_revision": receipt.question_revision,
        "task_result": (receipt.task_result.model_dump()
                        if receipt.task_result else None),
        "evaluation": {"status": receipt.evaluation_status,
                       "interpretation_id": receipt.interpretation_id,
                       "reason_code": receipt.evaluation_reason},
        "links": {
            "poll": f"/api/v1/assessment/submissions/{receipt.attempt_id}",
            "events": (f"/api/v1/learner-evaluation/jobs/{receipt.job_id}"
                       "/events"),
        },
    }
    if receipt.duplicate:
        payload["duplicate"] = True
    state = get_journal(_sid).state()
    src = state.sources.get(receipt.source_id)
    if src is not None and src.current_interpretation_id:
        meta = src.interpretations.get(src.current_interpretation_id, {})
        raw = meta.get("raw_interpretation")
        if isinstance(raw, dict):
            payload["learner_feedback"] = raw.get("feedback") or ""
    return payload


@router.get("/submissions/{attempt_id}")
async def get_submission(attempt_id: str,
                         _sid: str = Depends(resolve_student_id)):
    state = get_journal(_sid).state()
    for src in state.sources.values():
        if src.receipt.attempt_id != attempt_id:
            continue
        interp_id = src.current_interpretation_id
        meta = src.interpretations.get(interp_id, {}) if interp_id else {}
        task = None
        if src.receipt.task_ref is not None:
            revs = state.tasks.get(src.receipt.task_ref.question_id, {})
            task = revs.get(src.receipt.task_ref.question_revision)
        answered = bool(meta)
        return {
            "attempt_id": attempt_id,
            "source_id": src.receipt.source_id,
            "task_result": meta.get("task_result"),
            "evaluation": {
                "status": ("ready" if interp_id else "pending"),
                "interpretation_id": interp_id,
            },
            "question": (task.public_view().model_dump()
                         if task is not None and answered else
                         task.public_view().model_dump()
                         if task is not None else None),
            "feedback": ((meta.get("raw_interpretation") or {})
                         .get("feedback", "")),
        }
    raise api_error(404, "submission_not_found", "作答记录不存在")


class HintRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question_revision: int = Field(ge=1, le=1_000_000)
    hint_kind: str = Field("key_step", max_length=32)


@router.post("/questions/{qid}/hint")
async def question_hint(qid: str, req: HintRequest,
                        _sid: str = Depends(resolve_student_id)):
    """服务端记录帮助事件后返回可展示提示（§11.4）；提示从冻结量规派生，
    不含答案。"""
    qref = S.QuestionRef(question_id=qid,
                         question_revision=req.question_revision)
    from app.agents.assessment.manager import load_task_snapshot
    try:
        task = load_task_snapshot(_sid, qref)
    except Exception:
        raise api_error(404, "question_not_found", "题目不存在")
    steps = [f"{i}. {c.description}"
             for i, c in enumerate(task.rubric[:4], 1)]
    hint = "这道题可以按这些关键步骤来检查：\n" + "\n".join(steps) \
        if steps else "这道题暂无关键步骤提示。"
    record_assistance(_sid, qref, kind=S.AssistanceEventKind.HINT_REQUESTED,
                      detail=hint[:600], client_entry=req.hint_kind)
    return {"status": "ok", "hint": hint}


class RevealRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question_revision: int = Field(ge=1, le=1_000_000)


@router.post("/questions/{qid}/reveal")
async def question_reveal(qid: str, req: RevealRequest,
                          _sid: str = Depends(resolve_student_id)):
    """记录 reveal 后返回答案/解析；未作答时不生成 learner evidence
    （§11.4）。"""
    qref = S.QuestionRef(question_id=qid,
                         question_revision=req.question_revision)
    from app.agents.assessment.manager import load_task_snapshot
    try:
        task = load_task_snapshot(_sid, qref)
    except Exception:
        raise api_error(404, "question_not_found", "题目不存在")
    state = get_journal(_sid).state()
    answered = any(
        src.receipt.task_ref is not None
        and src.receipt.task_ref.question_id == qid
        and src.receipt.task_ref.question_revision == req.question_revision
        for src in state.sources.values())
    record_assistance(_sid, qref, kind=S.AssistanceEventKind.ANSWER_REVEALED,
                      detail="revealed" + ("_after_answer" if answered
                                           else "_before_answer"))
    return {"status": "ok", "answer": task.answer,
            "explanation": task.explanation, "already_answered": answered}


@router.get("/questions/{qid}")
async def question_public(qid: str,
                          revision: int = Query(default=0, ge=0),
                          _sid: str = Depends(resolve_student_id)):
    """QuestionPublic 白名单投影（A07）；答案可见性由服务器判定。"""
    state = get_journal(_sid).state()
    revs = state.tasks.get(qid, {})
    rev = revision or (max(revs) if revs else 0)
    task = revs.get(rev)
    if task is None:
        raise api_error(404, "question_not_found", "题目不存在")
    answered = any(
        src.receipt.task_ref is not None
        and src.receipt.task_ref.question_id == qid
        for src in state.sources.values())
    hints = bool(state.assistance_by_question.get((qid, rev)))
    if answered:
        return {"question": task.public_view(hints_available=hints),
                "revealed": {"answer": task.answer,
                             "explanation": task.explanation}}
    return {"question": task.public_view(hints_available=hints)}


class PracticeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question_revision: int = Field(ge=1, le=1_000_000)
    mode: str = Field("same", pattern="^(same|variant)$")
    expected_scope_revision: str = Field("", max_length=128)


@router.post("/questions/{qid}/practice")
async def question_practice(qid: str, req: PracticeRequest,
                            _sid: str = Depends(resolve_student_id)):
    """再练一次：新 question instance（同原题内容或变式），带
    origin_question_ref/task_family 与既有揭晓暴露（§11.5）。"""
    state = get_journal(_sid).state()
    task = state.tasks.get(qid, {}).get(req.question_revision)
    if task is None:
        raise api_error(404, "question_not_found", "题目不存在")
    if req.mode == "variant":
        new_task = await _generate_variant(_sid, task)
    else:
        new_task = task.model_copy(update={
            "question_id": "q_" + uuid.uuid4().hex[:8],
            "question_revision": 1,
            "origin_question_ref": S.QuestionRef(
                question_id=qid, question_revision=req.question_revision),
            "task_family": task.task_family or qid,
        })
    register_task_snapshot(_sid, new_task)
    return {"status": "ok",
            "question": new_task.public_view(hints_available=False),
            "origin_question_ref": {"question_id": qid,
                                    "question_revision":
                                        req.question_revision}}


async def _generate_variant(student_id: str, task: S.TaskSnapshot
                            ) -> S.TaskSnapshot:
    """变式经 P1/P2 质量链（generator）；失败退回同题新实例。"""
    try:
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import (AssessmentContext,
                                                 AssessmentGoal)
        concept = (task.source_badge.split(", ")[0]
                   if task.source_badge else "")
        goal = AssessmentGoal(concept=concept, purpose="practice", count=1)
        ctx = AssessmentContext(concept=concept, grade="本科",
                                base_difficulty=2)
        q = await generate_question(goal, ctx, llm=get_llm(),
                                    student_id=student_id)
        if q is not None:
            new_task = task_snapshot_from_legacy(
                q, workspace_id=task.workspace_id,
                concept_refs=list(task.concept_refs))
            new_task.origin_question_ref = S.QuestionRef(
                question_id=task.question_id,
                question_revision=task.question_revision)
            new_task.task_family = task.task_family or task.question_id
            return new_task
    except Exception:
        pass
    return task.model_copy(update={
        "question_id": "q_" + uuid.uuid4().hex[:8], "question_revision": 1,
        "origin_question_ref": S.QuestionRef(
            question_id=task.question_id,
            question_revision=task.question_revision)})


@router.get("/records")
async def assessment_records(offset: int = Query(0, ge=0),
                             limit: int = Query(20, ge=1, le=100),
                             verdict: str = Query(""),
                             workspace_id: str = Query(""),
                             _sid: str = Depends(resolve_student_id)):
    """本人原始作答档案（替换旧作答档案路由，§11.4）。"""
    state = get_journal(_sid).state()
    items: list[dict[str, Any]] = []
    for src in state.sources.values():
        if src.receipt.kind != S.SourceKind.ASSESSMENT:
            continue
        if workspace_id and \
                src.receipt.workspace_id_at_observation != workspace_id:
            continue
        interp_id = src.current_interpretation_id
        meta = src.interpretations.get(interp_id, {}) if interp_id else {}
        task_result = meta.get("task_result") or {}
        if verdict and str(task_result.get("verdict") or "") != verdict:
            continue
        task = None
        if src.receipt.task_ref is not None:
            revs = state.tasks.get(src.receipt.task_ref.question_id, {})
            task = revs.get(src.receipt.task_ref.question_revision)
        items.append({
            "attempt_id": src.receipt.attempt_id,
            "source_id": src.receipt.source_id,
            "observed_at": src.receipt.observed_at,
            "workspace_id": src.receipt.workspace_id_at_observation,
            "assessment_id": src.receipt.assessment_id,
            "availability": src.availability,
            "question": (task.public_view().model_dump()
                         if task is not None else None),
            "task_result": task_result or None,
            "evaluation_status": ("ready" if interp_id else "pending"),
        })
    items.sort(key=lambda i: i["observed_at"], reverse=True)
    total = len(items)
    page = items[offset:offset + limit]
    return {"items": page, "total": total, "offset": offset,
            "limit": limit}


# ---------------------------------------------------------------------------
# §11.5 自适应诊断实例
# ---------------------------------------------------------------------------

class CatGoal(BaseModel):
    model_config = {"extra": "forbid"}
    purpose: str = Field("adaptive", pattern="^(adaptive|diagnose|practice)$")
    target_claims: list[str] = Field(default_factory=list, max_length=8)


class CatStartRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str = Field("", max_length=96)
    concept_keys: list[str] = Field(min_length=1, max_length=20)
    goal: CatGoal = Field(default_factory=CatGoal)
    q_type: str = Field("", max_length=32)
    count: int = Field(6, ge=1, le=20)
    probe_ref: dict[str, Any] = Field(default_factory=dict)
    expected_scope_revision: str = Field("", max_length=128)
    grade: str = Field("本科", max_length=16)
    subject: str = Field("", max_length=32)


async def _generate_cat_question(student_id: str, instance: cat.CatInstance,
                                 q_type: str) -> S.TaskSnapshot | None:
    from app.agents.assessment.generator import generate_question
    from app.agents.assessment.state import (AssessmentContext,
                                             AssessmentGoal)
    goal = AssessmentGoal(
        concept=instance.concept, purpose=instance.purpose,
        count=1, q_type=q_type, difficulty=instance.difficulty,
        assesses=list(instance.target_claims[:4]))
    ctx_kwargs: dict[str, Any] = {}
    # M5 教材 grounding：工作区已选教材即命题证据 scope（非 strict；
    # strict 语义由 P2 unsupported 审核承担）
    if instance.workspace_id:
        try:
            from app.api.v1.assessment_grounding import (
                build_assessment_grounding, bundle_to_context_fields)
            textbook_ids: list[str] = []
            try:
                from app.agents.student_model.evaluation.scope import (
                    get_scope_resolver)
                scope = get_scope_resolver().resolve(
                    student_id, instance.workspace_id)
                textbook_ids = sorted({v.textbook_id
                                       for v in scope.selected_volumes})
            except Exception:
                pass
            if textbook_ids:
                bundle = await build_assessment_grounding(
                    student_id=student_id, concept=instance.concept,
                    session_id="", textbook_ids=textbook_ids,
                    strict_textbook=False)
                ctx_kwargs = bundle_to_context_fields(bundle)
        except Exception:
            ctx_kwargs = {}
    ctx = AssessmentContext(concept=instance.concept,
                            subject=instance.subject, grade=instance.grade,
                            base_difficulty=instance.difficulty, **ctx_kwargs)
    q = await generate_question(goal, ctx, llm=get_llm(),
                                student_id=student_id)
    if q is None:
        return None
    concept_refs = _match_concept_refs(student_id, instance, q)
    task = task_snapshot_from_legacy(q, workspace_id=instance.workspace_id,
                                     concept_refs=concept_refs)
    task.task_family = task.task_family or instance.concept
    task.grounding_refs = [str(r.get("id") or r.get("file_id") or "")
                           for r in (q.source_refs or [])
                           if isinstance(r, dict)][:16] or task.grounding_refs
    if instance.probe_ref:
        task.novelty = "probe:" + str(
            instance.probe_ref.get("probe_id") or "")[:200]
    return task


def _match_concept_refs(student_id: str, instance: cat.CatInstance,
                        question) -> list[S.ConceptRef]:
    """题目知识点 → scope 允许概念（严格同名/别名；不造节点，§7.2）。"""
    if not instance.workspace_id:
        return []
    try:
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        scope = get_scope_resolver().resolve(student_id,
                                             instance.workspace_id)
    except Exception:
        return []
    out: list[S.ConceptRef] = []
    names = set(question.knowledge_points or [])
    if question.concept:
        names.add(question.concept)
    for concept in scope.allowed_concepts:
        if len(out) >= 3:
            break
        if concept.display_name in names or concept.concept_id in \
                instance.concept_keys:
            out.append(concept)
    return out


@router.post("/start")
async def start_cat(req: CatStartRequest,
                    _sid: str = Depends(resolve_student_id)):
    _require_enabled()
    # §5.1.1：workspace 归属 404 先于任何检索/LLM 调用
    if req.workspace_id:
        from app.agents.student_model.evaluation.scope import (
            ScopeNotFound, get_scope_resolver)
        try:
            get_scope_resolver().resolve(_sid, req.workspace_id)
        except ScopeNotFound:
            raise api_error(404, "workspace_not_found", "工作区不存在")
    instance = cat.CatInstance(
        assessment_id=new_assessment_id(),
        workspace_id=req.workspace_id,
        purpose=req.goal.purpose,
        target_claims=list(req.goal.target_claims),
        concept_keys=list(req.concept_keys),
        concept=req.concept_keys[0] if req.concept_keys else "",
        grade=req.grade, subject=req.subject,
        count_limit=req.count, difficulty=2,
        probe_ref=dict(req.probe_ref),
        created_at=S.utc_now_iso())
    # 同 owner+workspace 至多一个 active CAT；不同区可各有一个（§11.5）
    state = get_journal(_sid).state()
    for aid, detail in state.assessments.items():
        if detail.get("status") == cat.STATUS_ACTIVE and \
                detail.get("workspace_id") == req.workspace_id and aid:
            old = cat.CatInstance.from_detail(detail)
            old.status = cat.STATUS_STOPPED
            old.stop_code = "user_stopped"
            cat.save_instance(_sid, old, change="superseded")
    task = await _generate_cat_question(_sid, instance, req.q_type)
    if task is None:
        instance.status = cat.STATUS_STOPPED
        instance.stop_code = "generation_failed"
        cat.save_instance(_sid, instance, change="start_failed")
        raise api_error(503, "generation_failed", "暂时无法出题，请稍后再试",
                        retryable=True)
    register_task_snapshot(_sid, task)
    instance.question_refs.append(S.QuestionRef(
        question_id=task.question_id,
        question_revision=task.question_revision))
    cat.save_instance(_sid, instance, change="start")
    return {"status": "ok", "assessment_id": instance.assessment_id,
            "workspace_id": instance.workspace_id,
            "difficulty": instance.difficulty,
            "question": task.public_view()}


class CatAnswerRequest(BaseModel):
    model_config = {"extra": "forbid"}
    assessment_id: str = Field(min_length=4, max_length=96)
    question_id: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(ge=1, le=1_000_000)
    student_answer: str = Field(min_length=1)


@router.post("/answer")
async def cat_answer(req: CatAnswerRequest,
                     _sid: str = Depends(resolve_student_id)):
    _require_enabled()
    state = get_journal(_sid).state()
    instance = cat.load_instance(state, req.assessment_id)
    if instance is None:
        raise api_error(404, "assessment_not_found", "测评实例不存在")
    if instance.status != cat.STATUS_ACTIVE:
        raise api_error(409, "assessment_not_active", "该测评已结束")
    current = instance.question_refs[-1] if instance.question_refs else None
    if current is None or current.question_id != req.question_id:
        raise api_error(409, "question_not_current",
                        "题目与当前测评不一致，请刷新")
    receipt = await evaluate_submission(
        student_id=_sid,
        question_ref=S.QuestionRef(question_id=req.question_id,
                                   question_revision=req.question_revision),
        student_answer=req.student_answer, source_surface="cat",
        assessment_id=req.assessment_id,
        workspace_id=instance.workspace_id, run_inline=True)
    state = get_journal(_sid).state()
    instance = cat.load_instance(state, req.assessment_id)
    instance.answered_question_ids.append(req.question_id)
    verdicts = cat.verdicts_of(cat.instance_task_results(state, instance))
    stop_status, stop_code = cat.should_stop(instance, verdicts)
    if stop_status:
        instance.status = stop_status
        instance.stop_code = stop_code
    else:
        cat.apply_continuation(instance,
                               cat.continuation_for(state, instance))
    cat.save_instance(_sid, instance, change="answer")
    out = {
        "status": "ok",
        "assessment_id": instance.assessment_id,
        "task_result": (receipt.task_result.model_dump()
                        if receipt.task_result else None),
        "evaluation": {"status": receipt.evaluation_status,
                       "interpretation_id": receipt.interpretation_id},
        "stop_reason": instance.stop_code or "",
    }
    if instance.status != cat.STATUS_ACTIVE:
        out["summary"] = cat.report(state, instance.assessment_id)
    return out


class CatNextRequest(BaseModel):
    model_config = {"extra": "forbid"}
    assessment_id: str = Field(min_length=4, max_length=96)
    expected_revision: int = Field(0, ge=0)


@router.post("/next")
async def cat_next(req: CatNextRequest,
                   _sid: str = Depends(resolve_student_id)):
    _require_enabled()
    state = get_journal(_sid).state()
    instance = cat.load_instance(state, req.assessment_id)
    if instance is None:
        raise api_error(404, "assessment_not_found", "测评实例不存在")
    if instance.status != cat.STATUS_ACTIVE:
        return {"status": "ok", "assessment_id": instance.assessment_id,
                "stop_reason": instance.stop_code,
                "question": None,
                "summary": cat.report(state, req.assessment_id)}
    if len(instance.answered_question_ids) < len(instance.question_refs):
        # 当前题未作答：重发相同 ID，不叠题（§11.5）
        pending = instance.question_refs[-1]
        task = state.tasks.get(pending.question_id, {}).get(
            pending.question_revision)
        return {"status": "ok", "assessment_id": instance.assessment_id,
                "stop_reason": "", "difficulty": instance.difficulty,
                "question": task.public_view() if task else None}
    # 最后一题已提交但语义未判定 → 409 evaluation_pending
    last = instance.question_refs[-1]
    pending_eval = any(
        src.receipt.task_ref is not None
        and src.receipt.task_ref.question_id == last.question_id
        and not src.current_interpretation_id
        for src in state.sources.values())
    if pending_eval:
        raise api_error(409, "evaluation_pending",
                        "上一题评价仍在进行，请稍候再取下一题",
                        retryable=True)
    verdicts = cat.verdicts_of(cat.instance_task_results(state, instance))
    instance.difficulty = cat.next_difficulty(verdicts, instance.difficulty)
    task = await _generate_cat_question(_sid, instance, "")
    if task is None:
        instance.status = cat.STATUS_STOPPED
        instance.stop_code = "generation_failed"
        cat.save_instance(_sid, instance, change="gen_failed")
        return {"status": "ok", "assessment_id": instance.assessment_id,
                "stop_reason": "generation_failed", "question": None,
                "summary": cat.report(get_journal(_sid).state(),
                                      req.assessment_id)}
    register_task_snapshot(_sid, task)
    instance.question_refs.append(S.QuestionRef(
        question_id=task.question_id,
        question_revision=task.question_revision))
    cat.save_instance(_sid, instance, change="next")
    return {"status": "ok", "assessment_id": instance.assessment_id,
            "stop_reason": "", "difficulty": instance.difficulty,
            "question": task.public_view()}


@router.get("/active")
async def cat_active(workspace_id: str = Query(""),
                     _sid: str = Depends(resolve_student_id)):
    state = get_journal(_sid).state()
    actives = [cat.CatInstance.from_detail(d)
               for d in state.assessments.values()
               if d.get("status") == cat.STATUS_ACTIVE
               and (not workspace_id
                    or d.get("workspace_id") == workspace_id)]
    if not actives:
        return {"status": "none"}
    instance = max(actives, key=lambda i: i.created_at)
    out: dict[str, Any] = {
        "status": "ok", "assessment_id": instance.assessment_id,
        "workspace_id": instance.workspace_id,
        "session_status": instance.status,
        "answered": len(instance.answered_question_ids),
        "stop_reason": instance.stop_code,
    }
    if instance.question_refs and \
            len(instance.answered_question_ids) < len(instance.question_refs):
        pending = instance.question_refs[-1]
        task = state.tasks.get(pending.question_id, {}).get(
            pending.question_revision)
        out["question"] = task.public_view() if task else None
    else:
        out["question"] = None
        out["summary"] = cat.report(state, instance.assessment_id)
    return out


@router.get("/report")
async def cat_report(assessment_id: str = Query(""),
                     _sid: str = Depends(resolve_student_id)):
    if not assessment_id:
        raise api_error(400, "assessment_id_required", "请指定测评实例")
    summary = cat.report(get_journal(_sid).state(), assessment_id)
    if summary is None:
        raise api_error(404, "assessment_not_found", "测评实例不存在")
    return {"status": "ok", "summary": summary}


class CatAbandonRequest(BaseModel):
    model_config = {"extra": "forbid"}
    assessment_id: str = Field(min_length=4, max_length=96)
    expected_revision: int = Field(0, ge=0)


@router.post("/abandon")
async def cat_abandon(req: CatAbandonRequest,
                      _sid: str = Depends(resolve_student_id)):
    state = get_journal(_sid).state()
    instance = cat.load_instance(state, req.assessment_id)
    if instance is None:
        raise api_error(404, "assessment_not_found", "测评实例不存在")
    if instance.status == cat.STATUS_ACTIVE:      # 终态幂等
        instance.status = cat.STATUS_ABANDONED
        instance.stop_code = "user_stopped"
        cat.save_instance(_sid, instance, change="abandon")
    return {"status": "ok", "assessment_id": instance.assessment_id,
            "stop_reason": instance.stop_code}
