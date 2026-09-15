"""M4 统一作答服务：evaluate_submission 唯一业务入口（plan §11.4/§11.5）。

聊天题卡、习题中心、自适应诊断共用本入口；服务端自行解析 TaskSnapshot、
session/workspace、assistance 与 task_binding。旧 raw_grade 流式旁路、
stem/correct_answer 旧契约在本版退出（A02/A03/A05）。

受理协议（§10.4）：先可靠保存 SourceReceipt（+job 同一事务，fsync 成功
才确认），再返回 202/attempt id；MC 正误确定性判定并在受理事务先行落盘，
语义解释由 job 提交。CAT 实例身份（assessment_id）与题目/作答/报告全部
在 journal 持久化（A10），不覆盖旧报告。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation import context as pack_builder
from app.agents.student_model.evaluation.grading import (compute_task_result,
                                                         grade_mc_task)
from app.agents.student_model.evaluation.jobs import (ClaimedJob,
                                                        job_deadline)
from app.agents.student_model.evaluation.llm import (EvaluationLLMRunner,
                                                     build_system_message)
from app.agents.student_model.evaluation.service import (
    CommitRejected, LearnerEvaluationService)
from app.agents.student_model.evaluation.store import (
    JournalState, answer_fingerprint, get_journal, new_source_id)
from app.core.config import settings


class SubmissionError(RuntimeError):
    code = "submission_error"


class QuestionNotFound(SubmissionError):
    code = "question_not_found"


class QuestionRevisionMismatch(SubmissionError):
    code = "question_revision_mismatch"


class AnswerTooLarge(SubmissionError):
    code = "answer_too_large"


class QuestionAlreadyAnswered(SubmissionError):
    """同一 question instance 只接受一个正式提交；不同答案需新 instance
    （POST /assessment/questions/{qid}/practice，§11.5）。"""
    code = "question_already_answered"


class WorkspaceNotOwned(SubmissionError):
    """R05：请求携带的 workspace_id 不存在/非本人——服务端事实解析，
    请求字段只做期望断言。"""
    code = "workspace_not_owned"


class SessionNotOwned(SubmissionError):
    code = "session_not_owned"


class AssessmentBindingError(SubmissionError):
    """R05：CAT 实例不存在/非本人，或题目不属于该实例。"""
    code = "assessment_binding_error"


class ScopeRevisionConflict(SubmissionError):
    """R05：expected_scope_revision 与服务端当前解析不一致（期望断言）。"""
    code = "scope_revision_conflict"


@dataclass
class SubmissionReceipt:
    attempt_id: str
    source_id: str
    job_id: str
    question_id: str
    question_revision: int
    task_result: S.TaskResult | None = None
    evaluation_status: str = "pending"
    evaluation_reason: str = ""
    interpretation_id: str = ""
    duplicate: bool = False
    links: dict[str, str] = field(default_factory=dict)


def is_enabled() -> bool:
    """评价/测评链开关：off 时聊天与 MC 正误仍可工作（§10.3）。"""
    return settings.learner_evaluation_mode == "active"


def new_attempt_id() -> str:
    return "att_" + uuid.uuid4().hex[:16]


def new_assessment_id() -> str:
    return "asmt_" + uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# TaskSnapshot 注册与解析
# ---------------------------------------------------------------------------

def load_task_snapshot(student_id: str, qref: S.QuestionRef
                       ) -> S.TaskSnapshot:
    state = get_journal(student_id).state()
    revs = state.tasks.get(qref.question_id)
    if not revs:
        raise QuestionNotFound(f"题目 {qref.question_id} 未注册")
    task = revs.get(qref.question_revision)
    if task is None:
        raise QuestionRevisionMismatch(
            f"题目 {qref.question_id} 无 revision "
            f"{qref.question_revision}（旧标签页请刷新）")
    return task


def register_task_snapshot(student_id: str, task: S.TaskSnapshot) -> None:
    """question_registered：出题/迁移时的任务注册（幂等）。"""
    journal = get_journal(student_id)
    existing = journal.state().tasks.get(task.question_id, {}).get(
        task.question_revision)
    if existing is None:
        journal.register_question(task)


def task_snapshot_from_legacy(question: Any, *,
                              workspace_id: str = "",
                              concept_refs: list[S.ConceptRef] | None = None,
                              ) -> S.TaskSnapshot:
    """旧出题器 Question → TaskSnapshot（量规冻结语义保持：作答前生成）。"""
    from .question import Question
    assert isinstance(question, Question)
    q_type = (S.QuestionType.MULTIPLE_CHOICE if question.is_multiple_choice
              else S.QuestionType(question.q_type
                                  if question.q_type in ("fill_blank",
                                                         "short_answer")
                                  else "short_answer"))
    rubric: list[S.FrozenCriterion] = []
    legacy_rubric = question.rubric if isinstance(question.rubric, dict) \
        else {}
    for c in (legacy_rubric.get("criteria") or []):
        rubric.append(S.FrozenCriterion(
            id=str(c.get("id") or f"c{len(rubric) + 1}"),
            description=str(c.get("description") or "")[:600] or "判分点",
            weight=float(c.get("weight") or 1.0),
            critical=bool(c.get("critical"))))
    if not rubric:
        # 无生成期量规：单 criterion「最终答案正确」；开放题解释由 P3
        # 语义层给出，不伪造冻结量规语义（frozen_at 空 = 未冻结）。
        rubric = [S.FrozenCriterion(id="c1", description="最终答案正确",
                                    weight=1.0, critical=True)]
    verification = question.verification if isinstance(
        question.verification, dict) else {}
    status = "passed" if verification.get("answer_verified") else "unreviewed"
    question_id = str(question.id or "") or ("q_" + uuid.uuid4().hex[:10])
    return S.TaskSnapshot(
        question_id=question_id, question_revision=1, q_type=q_type,
        stem=question.stem, options=dict(question.options or {}),
        answer=question.answer or "", explanation=question.explanation or "",
        equivalent_solutions=[str(e) for e in
                              (legacy_rubric.get("equivalent_solutions")
                               or [])][:8],
        rubric=rubric,
        verification=S.TaskVerification(status=status),
        concept_refs=concept_refs or [],
        task_family="",
        grounding_refs=[str(r.get("id") or r) for r in
                        (question.source_refs or [])
                        if isinstance(r, (str, dict))][:16],
        source_badge=", ".join(question.knowledge_points[:3]),
        frozen_at="", workspace_id=workspace_id)


def task_snapshot_from_quiz_dict(qd: dict, *,
                                 workspace_id: str = "",
                                 concept_refs: list[S.ConceptRef] | None = None,
                                 variant_reference: str = "",
                                 ) -> S.TaskSnapshot:
    """quiz_history 题目 dict → TaskSnapshot（聊天题卡注册路径，§11.4）。"""
    q_type_raw = str(qd.get("type") or "multiple_choice")
    q_type = S.QuestionType(
        q_type_raw if q_type_raw in ("multiple_choice", "fill_blank",
                                     "short_answer") else "multiple_choice")
    rubric: list[S.FrozenCriterion] = []
    legacy = qd.get("rubric") if isinstance(qd.get("rubric"), dict) else {}
    for c in (legacy.get("criteria") or []):
        try:
            rubric.append(S.FrozenCriterion(
                id=str(c.get("id") or f"c{len(rubric) + 1}"),
                description=str(c.get("description") or "")[:600] or "判分点",
                weight=float(c.get("weight") or 1.0),
                critical=bool(c.get("critical"))))
        except Exception:
            continue
        if len(rubric) >= 12:
            break
    if not rubric:
        rubric = [S.FrozenCriterion(id="c1", description="最终答案正确",
                                    weight=1.0, critical=True)]
    verification = qd.get("verification") if isinstance(
        qd.get("verification"), dict) else {}
    verified = bool(verification.get("answer_verified"))
    refs = qd.get("source_refs")
    grounding = [str(r.get("id") or r.get("file_id") or "") for r in refs
                 if isinstance(r, dict)][:16] if isinstance(refs, list) else []
    question_id = str(qd.get("id") or ("q_" + uuid.uuid4().hex[:10]))
    return S.TaskSnapshot(
        question_id=question_id, question_revision=1, q_type=q_type,
        stem=str(qd.get("stem") or "")[:4000],
        options={str(k): str(v) for k, v in
                 (qd.get("options") or {}).items()},
        answer=str(qd.get("answer") or ""),
        explanation=str(qd.get("explanation") or ""),
        equivalent_solutions=[str(e) for e in
                              (legacy.get("equivalent_solutions") or [])][:8],
        rubric=rubric,
        verification=S.TaskVerification(
            status="passed" if verified else "unreviewed"),
        concept_refs=concept_refs or [],
        task_family=variant_reference or str(qd.get("topic") or ""),
        grounding_refs=grounding,
        source_badge=str(qd.get("knowledge_point") or "")[:192],
        frozen_at="", workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# 唯一受理入口
# ---------------------------------------------------------------------------

def resolve_submission_binding(
        student_id: str, *, question_ref: S.QuestionRef,
        task: S.TaskSnapshot, assessment_id: str = "",
        source_session_ref: str = "", workspace_id: str = "",
        expected_scope_revision: str = "") -> tuple[str, str]:
    """R05：归属从服务端事实解析，请求字段只做期望断言。

    解析顺序（§11.4）：CAT 实例 > 会话 > 显式 workspace_id > 题目注册时的
    workspace。返回 (workspace_id, scope_revision)；workspace 为空表示
    task-only 反馈（评价 unavailable(workspace_required)）。任何归属冲突
    抛 SubmissionError 子类（API 层 404/409，零模型调用）。
    """
    journal = get_journal(student_id)
    state = journal.state()
    if assessment_id:
        from . import adaptive_test as cat
        inst = cat.load_instance(state, assessment_id)
        if inst is None:
            raise AssessmentBindingError(f"测评实例 {assessment_id} 不存在")
        if question_ref.question_id not in {
                q.question_id for q in inst.question_refs}:
            raise AssessmentBindingError(
                f"题目 {question_ref.question_id} 不属于测评 {assessment_id}")
        if inst.workspace_id:
            workspace_id = inst.workspace_id      # 服务端事实优先
    if not workspace_id and source_session_ref:
        from app.core.session import load_session
        session = load_session(source_session_ref)
        if session is not None:
            owner = session.student_id or "student_default"
            if owner != student_id:
                raise SessionNotOwned("会话不存在")
            workspace_id = session.workspace_id or ""
    if not workspace_id:
        if not task.workspace_id:
            return "", ""
        workspace_id = task.workspace_id
        explicit = False
    else:
        explicit = True
    # 显式/继承的 workspace 必须是本人的（404 语义）；题目注册绑定失效时
    # 降级为无工作区，作答仍可受理为 task-only（§11.4）。
    scope_revision = ""
    from app.agents.student_model.evaluation.scope import ScopeNotFound
    try:
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        scope = get_scope_resolver().resolve(student_id, workspace_id)
        scope_revision = scope.scope_revision
    except ScopeNotFound:
        if explicit:
            raise WorkspaceNotOwned(f"工作区 {workspace_id} 不存在")
        return "", ""       # 旧题目绑定的工作区已不存在 → task-only
    except Exception:
        return "", ""
    if expected_scope_revision and expected_scope_revision != scope_revision:
        raise ScopeRevisionConflict(
            f"期望 scope revision {expected_scope_revision} 与当前 "
            f"{scope_revision} 不一致，请刷新后重试")
    return workspace_id, scope_revision


async def evaluate_submission(
        *,
        student_id: str,
        question_ref: S.QuestionRef,
        student_answer: str,
        source_surface: str = "assessment_center",
        idempotency_key: str = "",
        expected_scope_revision: str | None = None,
        assessment_id: str | None = None,
        reply_message_ref: str | None = None,
        source_session_ref: str = "",
        workspace_id: str = "",
        scope_revision: str = "",
        run_inline: bool = False,
        runner: EvaluationLLMRunner | None = None,
) -> SubmissionReceipt:
    """受理一份作答（§11.4 契约）。

    - 完整答案指纹判重（A05）：同题同答案重放同 attempt；同题不同答案
      409（需新 instance）。
    - MC：TaskResult 确定性判定并随受理事务落盘（§6.4）。
    - run_inline：测试/同步路径直接跑完语义 job（生产走队列）。
    """
    task = load_task_snapshot(student_id, question_ref)
    raw = S.canonicalize_text(student_answer)
    if len(raw.encode("utf-8")) > S.MAX_ANSWER_BYTES:
        raise AnswerTooLarge("作答超过 32KiB 上限，请缩小范围后提交")
    # R05：归属/范围从服务端事实解析（CAT/会话/显式 workspace 逐级；
    # expected_scope_revision 仅作期望断言，不一致即刻 409）
    workspace_id, scope_revision = resolve_submission_binding(
        student_id, question_ref=question_ref, task=task,
        assessment_id=assessment_id or "",
        source_session_ref=source_session_ref,
        workspace_id=workspace_id,
        expected_scope_revision=expected_scope_revision or "")
    fingerprint = answer_fingerprint(raw)
    journal = get_journal(student_id)
    state = journal.state()

    prior_source = _find_prior_attempt(state, question_ref, fingerprint)
    if prior_source is not None:
        src = state.sources[prior_source]
        interp_id = src.current_interpretation_id
        task_result = _committed_task_result(state, prior_source)
        return SubmissionReceipt(
            attempt_id=src.receipt.attempt_id,
            source_id=prior_source,
            job_id=_job_for_source(state, prior_source),
            question_id=question_ref.question_id,
            question_revision=question_ref.question_revision,
            task_result=task_result,
            evaluation_status=("ready" if interp_id else "pending"),
            interpretation_id=interp_id, duplicate=True)

    if _find_prior_attempt(state, question_ref, None) is not None:
        raise QuestionAlreadyAnswered(
            f"题目 {question_ref.question_id}@"
            f"{question_ref.question_revision} 已有正式提交；"
            "再练一次请新建练习实例（practice）")

    # 帮助事件（hint/reveal 答前记录，§7.3）
    assistance = list(state.assistance_by_question.get(
        (question_ref.question_id, question_ref.question_revision), []))
    assistance_floor = _assistance_floor(assistance)

    # R21（update_plan §4）：共享游客（student_default）只给当场反馈——
    # MC 本地判分直接返回，不写共享长期 learner journal（登录后从新表现
    # 建档）；开放题在游客态不可判，返回未判定回执。
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    if student_id == DEFAULT_STUDENT_ID:
        mc = task.q_type == S.QuestionType.MULTIPLE_CHOICE
        task_result = grade_mc_task(task, raw) if mc else None
        return SubmissionReceipt(
            attempt_id=new_attempt_id(), source_id="", job_id="",
            question_id=question_ref.question_id,
            question_revision=question_ref.question_revision,
            task_result=task_result,
            evaluation_status="unavailable",
            evaluation_reason="guest_no_persistent_evaluation",
            interpretation_id="", duplicate=False)

    attempt_id = new_attempt_id()
    source_id = new_source_id()
    receipt = S.SourceReceipt(
        source_id=source_id, source_revision=1,
        kind=S.SourceKind.ASSESSMENT,
        observed_at=S.utc_now_iso(),
        workspace_id_at_observation=workspace_id,
        canonical_text=raw,
        assistance_events=assistance,
        task_ref=question_ref, attempt_id=attempt_id,
        assessment_id=assessment_id or "",
        source_session_ref=source_session_ref,
        reply_message_ref=reply_message_ref or "",
        scope_revision=scope_revision or expected_scope_revision or "",
        assistance_floor=assistance_floor)
    mc = task.q_type == S.QuestionType.MULTIPLE_CHOICE
    task_result = grade_mc_task(task, raw) if mc else None

    from app.core import learner_runtime
    # R21：评价停用（LEARNER_EVALUATION_MODE=off）＝暂停长期评价——
    # 原作答与可确定 MC 判分照常受理落盘；语义解释 job 不建（不积压
    # 付费重试），恢复 active 后新表现正常评价。
    evaluation_off = not learner_runtime.evaluation_enabled()
    # 阶段C（§5.2/§6.2）：策略归属受理时冻结。daily_midnight 时：
    # - MC 本题判定已随受理落盘（即时反馈保留）；
    # - 开放题追加一个 task_only job（立即评价本题量规反馈，不发布
    #   learner 判断）+ learner job（零点后语义解释）。两者同一 journal，
    #   不新增第三种观察来源；成本变化已在配置说明记录。
    from app.core.learner_evaluation_policy import (SCHEDULE_DAILY_MIDNIGHT,
                                                    load_policy,
                                                    scheduling_facts)
    facts = scheduling_facts(receipt.observed_at, load_policy())
    daily_mode = (not evaluation_off
                  and facts["schedule_mode"] == SCHEDULE_DAILY_MIDNIGHT)
    learner_facts = facts if daily_mode else {
        "eligible_after_utc": "", "local_activity_date": "",
        "schedule_mode": facts["schedule_mode"],
        "policy_revision": facts["policy_revision"]}
    scheduler = learner_runtime.get_scheduler()
    ops: list[Any] = [S.OpSourceRegistered(source=receipt)]
    job = None
    task_only_job = None
    if not evaluation_off:
        job = S.EvaluationJob(
            job_id="job_" + uuid.uuid4().hex[:16],
            kind=S.JobKind.ASSESSMENT_EVALUATION,
            source_id=source_id, source_revision=1,
            workspace_id=workspace_id, scope_revision=receipt.scope_revision,
            priority=S.JobPriority.AWAITING_FEEDBACK.value,
            created_at=S.utc_now_iso(), updated_at=S.utc_now_iso(),
            eligible_after_utc=learner_facts["eligible_after_utc"],
            local_activity_date=learner_facts["local_activity_date"],
            schedule_mode=learner_facts["schedule_mode"],
            policy_revision=int(learner_facts["policy_revision"]))
        ops.append(S.OpJobRequested(job=job))
        if daily_mode and task is not None and not mc:
            task_only_job = S.EvaluationJob(
                job_id="job_" + uuid.uuid4().hex[:16],
                kind=S.JobKind.ASSESSMENT_EVALUATION,
                source_id=source_id, source_revision=1,
                workspace_id=workspace_id, scope_revision=receipt.scope_revision,
                priority=S.JobPriority.AWAITING_FEEDBACK.value,
                created_at=S.utc_now_iso(), updated_at=S.utc_now_iso(),
                schedule_mode="task_only_immediate",
                prompt_binding="task_only@1")
            ops.append(S.OpJobRequested(job=task_only_job))
    if task_result is not None:
        # off 模式无语义 job：MC 判分事务用本地占位 job_id（journal 中
        # 无此 job → apply 不终结任何作业，仅落 TaskResult）
        ops.append(S.OpResultCommitted(
            job_id=(job.job_id if job is not None
                    else "job_mc_" + source_id[4:]),
            source_id=source_id, source_revision=1,
            scope_revision=receipt.scope_revision or "no_scope",
            task_result=task_result))
    journal.append(ops)     # 受理 + job（+MC 判分）同一事务，fsync 后确认
    try:
        from app.agents.student_model.evaluation.worker import (
            notify_evaluation_worker)
        notify_evaluation_worker()
    except Exception:
        pass

    receipt_out = SubmissionReceipt(
        attempt_id=attempt_id, source_id=source_id,
        job_id=(job.job_id if job is not None else ""),
        question_id=question_ref.question_id,
        question_revision=question_ref.question_revision,
        task_result=task_result,
        evaluation_status=(
            "disabled" if evaluation_off else
            "unavailable" if not workspace_id else "pending"),
        evaluation_reason=(
            "evaluation_disabled" if evaluation_off else
            "" if workspace_id else "workspace_required"))
    if run_inline and job is not None:
        # R02：inline 只认领**本次提交的作业**（claim_job 按 ID），绝不
        # 借 HTTP 请求排空其他类型/其他来源的 job——那是 worker 的职责；
        # 认领失败（worker 已先认领）时自然等待后台完成。
        claimed = scheduler.claim_job(student_id, job.job_id)
        if claimed is not None:
            await run_assessment_job(student_id, claimed,
                                     runner=runner or _default_runner())
        src = journal.state().sources.get(source_id)
        if src is not None:
            receipt_out.interpretation_id = src.current_interpretation_id
            # 开放题判分随语义提交产生；回填已落盘的 TaskResult
            committed = _committed_task_result(journal.state(), source_id)
            if committed is not None:
                receipt_out.task_result = committed
            # 无 workspace：task-only 反馈可提交，但评价状态保持
            # unavailable(workspace_required)（§11.4）
            if src.current_interpretation_id and workspace_id:
                receipt_out.evaluation_status = "ready"
            elif workspace_id:
                # 语义作业已终态却无 interpretation：按 §10.3 硬故障
                # →unavailable，不能停在 pending 误导"仍在评价"。
                rt = journal.state().jobs.get(job.job_id)
                if rt is not None and rt.job.state in (
                        S.JobState.FAILED, S.JobState.CANCELLED):
                    receipt_out.evaluation_status = "unavailable"
                    receipt_out.evaluation_reason = "evaluation_failed"
    return receipt_out


def _default_runner() -> EvaluationLLMRunner:
    from app.core import learner_runtime
    return learner_runtime.get_evaluation_runner()


def _find_prior_attempt(state: JournalState, qref: S.QuestionRef,
                        fingerprint: str | None) -> str | None:
    for sid, src in state.sources.items():
        ref = src.receipt.task_ref
        if ref is None:
            continue
        if ref.question_id != qref.question_id:
            continue
        if ref.question_revision != qref.question_revision:
            continue
        if fingerprint is None:
            return sid                       # 任一已有提交（冲突探测）
        if answer_fingerprint(src.receipt.canonical_text) == fingerprint:
            return sid                       # 完整答案重放
    return None


def _committed_task_result(state: JournalState, source_id: str
                           ) -> S.TaskResult | None:
    src = state.sources.get(source_id)
    if src is None:
        return None
    for iid in sorted(src.interpretations, reverse=True):
        tr = src.interpretations[iid].get("task_result")
        if isinstance(tr, dict) and tr:
            return S.TaskResult.model_validate(tr)
    return None


def _job_for_source(state: JournalState, source_id: str) -> str:
    for jid, rt in state.jobs.items():
        if rt.job.source_id == source_id:
            return jid
    return ""


def _assistance_floor(events: list[S.AssistanceEvent]
                      ) -> S.AssistanceLevel:
    kinds = {e.kind for e in events}
    if S.AssistanceEventKind.ANSWER_REVEALED in kinds or \
            S.AssistanceEventKind.WORKED_EXAMPLE in kinds:
        return S.AssistanceLevel.FULL_DEMO
    if S.AssistanceEventKind.HINT_REQUESTED in kinds:
        return S.AssistanceLevel.KEY_HINTS
    return S.AssistanceLevel.INDEPENDENT


# ---------------------------------------------------------------------------
# C4 语义 job：pack → P3 → validator → commit
# ---------------------------------------------------------------------------

def _scenarios_for(task: S.TaskSnapshot, source: S.SourceReceipt,
                   state: JournalState) -> list[str]:
    """§9.5 情景组合：题型→帮助→变化→特殊机会（顺序固定）。"""
    from app.prompts.learner_evaluation import P3_SCENARIOS
    out: list[str] = []
    mc = task.q_type == S.QuestionType.MULTIPLE_CHOICE
    out.append(P3_SCENARIOS["choice_only" if mc else "open_process"])
    if source.assistance_floor in (S.AssistanceLevel.FULL_DEMO,
                                   S.AssistanceLevel.KEY_HINTS):
        out.append(P3_SCENARIOS["assisted_or_revealed"])
    if task.origin_question_ref is not None:
        out.append(P3_SCENARIOS["repeated_practice"])
    if task.novelty and "same_form" not in task.novelty:
        out.append(P3_SCENARIOS["transfer_candidate"])
    if source.provenance == S.SourceProvenance.MIGRATION:
        out.append(P3_SCENARIOS["historical_backfill"])
    return out


def normalize_evidence_spans(interpretation: S.LearnerInterpretation,
                             canonical_text: str) -> S.LearnerInterpretation:
    """真实 provider 兼容层：修正证据 span 的字符坐标。

    评测解释的硬校验要求 span 是 canonical_text 上的精确 [start,end) 且引文
    一致（§7.2）。真实 LLM（DeepSeek 等）常输出 (0,0) 或越界坐标、但 quote
    文本本身正确——fake-llm 夹具永远给合法坐标，因此此前只有 live 调用才会
    撞 validation_rejected。规则：
    - quote 在原文中唯一出现 → 改写坐标（修复坐标，不新增证据，§9.12）；
    - 其余（quote 定位不到＝疑似伪造，或多处出现＝坐标不可定）→ 保留
      原样，交给 check_evidence_spans 硬校验拒绝整份——本函数不得成为
      绕过 §7.2 反幻觉门的通道。
    返回新对象，不修改输入。
    """
    if not interpretation.observation_claims:
        return interpretation
    text = canonical_text
    new_claims: list[S.ObservationClaim] = []
    changed = False
    for claim in interpretation.observation_claims:
        spans = []
        for span in claim.current_evidence:
            if (0 <= span.start < span.end <= len(text)
                    and text[span.start:span.end] == span.quote):
                spans.append(span)
                continue
            changed = True
            quote = span.quote or ""
            idx = text.find(quote) if quote else -1
            if quote and idx >= 0 and text.find(quote, idx + 1) < 0:
                spans.append(span.model_copy(
                    update={"start": idx, "end": idx + len(quote)}))
            else:
                spans.append(span)      # 无法核验 → 原样保留，硬校验拒绝
        if len(spans) != len(claim.current_evidence) or any(
                a is not b for a, b in zip(spans, claim.current_evidence)):
            changed = True
            claim = claim.model_copy(update={"current_evidence": spans})
        new_claims.append(claim)
    if not changed:
        return interpretation
    return interpretation.model_copy(update={"observation_claims": new_claims})


async def run_assessment_job(student_id: str, claimed: ClaimedJob, *,
                             runner: EvaluationLLMRunner,
                             scheduler=None) -> str:
    """执行一个 assessment_evaluation job；返回终态。"""
    from app.core import learner_runtime
    scheduler = scheduler or learner_runtime.get_scheduler()
    service = LearnerEvaluationService(scheduler)
    journal = get_journal(student_id)
    state = journal.state()
    # R05：generation 在认领后立即冻结——提交时 CAS 用快照值，防止"回包时
    # 重新读取当前 generation"使并发防护失效。
    generation_at_claim = state.generation
    job = claimed.job
    src = state.sources.get(job.source_id)
    if src is None or src.receipt.source_revision != job.source_revision:
        scheduler.cancel(student_id, job.job_id, reason="source_gone")
        return "cancelled"
    if src.availability != "available":
        scheduler.cancel(student_id, job.job_id, reason="source_unavailable")
        return "cancelled"
    receipt = src.receipt
    task: S.TaskSnapshot | None = None
    if receipt.task_ref is not None:
        revs = state.tasks.get(receipt.task_ref.question_id, {})
        task = revs.get(receipt.task_ref.question_revision)

    scope = None
    if receipt.workspace_id_at_observation:
        try:
            from app.agents.student_model.evaluation.scope import (
                get_scope_resolver)
            scope = get_scope_resolver().resolve(
                student_id, receipt.workspace_id_at_observation)
        except Exception:
            scope = None

    async with learner_runtime.workspace_lock(student_id, receipt.workspace_id_at_observation):
        mc_result = _committed_task_result(state, job.source_id)
        scenarios = _scenarios_for(task, receipt, state) if task else []
        binding = ("learning_evidence_contract@1.0.0+"
                   "assessment_learner_evaluation@1.0.0")
        pack = pack_builder.assemble_assessment_pack(
            source=receipt, task=task, scope=scope, state=state,
            task_result=mc_result, scenarios=scenarios,
            prompt_binding=binding,
            hint_concepts=[p.strip() for p in task.source_badge.split(",")]
            if task else [])
        pack.job_id = job.job_id
        service.record_job_input(
            student_id, job.job_id, input_hash=pack.manifest.input_hash,
            prompt_binding=binding, generation=state.generation,
            included_refs=list(pack.manifest.included_refs),
            truncations=list(pack.manifest.truncations))

        system = build_system_message(
            "assessment_learner_evaluation", scenarios=scenarios,
            output_model=S.AssessmentInterpretationOutput)
        try:
            out = await runner.run_structured(
                deadline_at=job_deadline(claimed.job),
                system=system, user=pack_builder.pack_user_message(pack),
                output_model=S.AssessmentInterpretationOutput,
                max_output_tokens=4000)
            if out.parsed is None:
                scheduler.fail(student_id, job.job_id,
                               error_code=out.error_code or "llm_failed",
                               retryable=out.retryable_error,
                               transport_attempts=out.transport_attempts)
                return "failed"
            parsed: S.AssessmentInterpretationOutput = out.parsed
            parsed.learner = normalize_evidence_spans(
                parsed.learner, receipt.canonical_text)
            # R14：正式概念评价硬准入门——题目未通过 C3 审核时学习者
            # 判断不发布（本题判分/反馈保留），降级为 task-only 弃权。
            if task is not None and                     task.verification.status != "passed":
                parsed.learner = parsed.learner.model_copy(update={
                    "applicable": False,
                    "abstain_reason": "task_not_verified",
                    "observation_claims": [], "concept_updates": []})
            # 阶段C §5.2：daily 模式的 task_only job——只提交本题量规
            # 结果与反馈（TaskResult + criterion/first_error/feedback 全
            # 保留），learner 判断显式弃权，零点由 learner job 再解释。
            if job.schedule_mode == "task_only_immediate":
                parsed.learner = parsed.learner.model_copy(update={
                    "applicable": False,
                    "abstain_reason": "task_only_deferred",
                    "observation_claims": [], "concept_updates": [],
                    "next_probe": None})

            task_result = mc_result
            if task is not None and task.q_type != S.QuestionType.MULTIPLE_CHOICE:
                task_result = compute_task_result(
                    task, parsed.criterion_results,
                    receipt.canonical_text,
                    first_error=parsed.first_error,
                    hypotheses=parsed.hypotheses,
                    feedback=parsed.task_feedback)

            base_claims = _active_claims_for(state, scope, pack)
            # R05：pack 构建时冻结基线判断 ID（concept_key → judgment_id），
            # 提交时 CAS——并发提交更新过基线即丢弃本次结果。
            base_judgment_ids: dict[str, str] = {}
            if scope is not None:
                for entry in pack.allowlist:
                    base_judgment_ids[entry.concept.key] = \
                        state.concept_current.get(
                            (scope.workspace_id, entry.concept.key), "")
            # G4 §6.6：可观察召回（verdict 非 null）→ journal outbox 给 M9 消费
            m9_outbox: list[dict] = []
            if task_result is not None and task_result.verdict is not None:
                v = task_result.verdict.value if hasattr(task_result.verdict, "value") \
                    else task_result.verdict
                # R19：消费者需要的完整归属（此前缺 attempt/session/
                # workspace/帮助/审核条件，消费端只能回退 event_id）
                m9_outbox.append({
                    "event_id": f"m9_{receipt.source_id}",
                    "consumer": "m9", "kind": "task_result",
                    "source_id": receipt.source_id,
                    "question_id": task.question_id if task is not None else "",
                    "question_revision": (
                        task.question_revision if task is not None else 0),
                    "concept": (task.source_badge if task is not None else "") or "",
                    "verdict": str(v), "observed_at": receipt.observed_at,
                    "attempt_id": receipt.attempt_id,
                    "session_id": receipt.source_session_ref or
                        receipt.reply_message_ref,
                    "workspace_id": receipt.workspace_id_at_observation,
                    "assistance_floor": receipt.assistance_floor.value,
                    "verified": (task.verification.status == "passed"
                                 if task is not None else False),
                    "student_answer_fingerprint":
                        task_result.answer_fingerprint})
        except Exception as exc:  # noqa: BLE001
            # run_structured 自身不抛（transport 层全分类为 error_code）；这里
            # 兜的是它之外的未预期异常。放任逃逸会让 job 永远停在 leased-
            # running（同题守卫又挡住重试），只能等 lease 过期后被重新认领。
            # 兜成可重试失败，保证终态落盘。（客户端中断走 CancelledError，
            # 不进本分支，依赖 lease 过期 + claim 排空自愈。）
            import logging
            logging.getLogger(__name__).exception(
                "assessment job %s runner crashed", job.job_id)
            scheduler.fail(student_id, job.job_id,
                           error_code="runner_crashed:" + type(exc).__name__,
                           retryable=True)
            return "failed"
        try:
            service.commit_result(
                student_id, job_id=job.job_id, lease_token=claimed.lease_token,
                expected_generation=generation_at_claim,
                source=receipt, pack=pack, task=task,
                interpretation=parsed.learner,
                task_result=task_result, continuation=parsed.continuation,
                expected_scope_revision=job.scope_revision or None,
                expected_base_judgments=base_claims,
                expected_base_judgment_ids=base_judgment_ids,
                outbox=m9_outbox)
        except CommitRejected as exc:
            # 硬校验失败：job failed，不产生新能力结论（§7.1 C6）
            import logging
            logging.getLogger(__name__).warning(
                "assessment job %s commit rejected: %s", job.job_id, exc)
            scheduler.fail(student_id, job.job_id,
                           error_code="validation_rejected",
                           retryable=False,
                           transport_attempts=out.transport_attempts)
            return "failed"
        except Exception as exc:  # noqa: BLE001
            # commit 内部的意外异常（如构造判断时的 pydantic 校验）同样不能以
            # 裸 500 逃逸——受理事务已落盘，同题守卫会挡住重试。兜成可重试
            # 失败，保留 job 终态与错误码供诊断。
            import logging
            logging.getLogger(__name__).exception(
                "assessment job %s commit crashed", job.job_id)
            scheduler.fail(student_id, job.job_id,
                           error_code="commit_crashed:" + type(exc).__name__,
                           retryable=True)
            return "failed"
        return "succeeded"


def _active_claims_for(state: JournalState, scope, pack
                       ) -> dict[str, list[S.ClaimView]]:
    """当前有效主张（§8.2 prior_same_concept 的物化来源）。"""
    if scope is None:
        return {}
    out: dict[str, list[S.ClaimView]] = {}
    for entry in pack.allowlist:
        jid = state.concept_current.get(
            (scope.workspace_id, entry.concept.key), "")
        judgment = state.judgments.get(jid) if jid else None
        if judgment is not None:
            out[entry.concept.key] = list(judgment.claims)
    return out


# ---------------------------------------------------------------------------
# 帮助事件（§7.3：服务端记录，不采信客户端自报时间）
# ---------------------------------------------------------------------------

def record_assistance(student_id: str, qref: S.QuestionRef, *,
                      kind: S.AssistanceEventKind, detail: str = "",
                      client_entry: str = "") -> None:
    journal = get_journal(student_id)
    journal.append([S.OpAssistanceRecorded(
        question_ref=qref,
        assistance=S.AssistanceEvent(kind=kind, at=S.utc_now_iso(),
                                     detail=detail[:600],
                                     client_entry=client_entry[:64]))])


def assistance_events(student_id: str, qref: S.QuestionRef
                      ) -> list[S.AssistanceEvent]:
    return list(get_journal(student_id).state().assistance_by_question.get(
        (qref.question_id, qref.question_revision), []))
