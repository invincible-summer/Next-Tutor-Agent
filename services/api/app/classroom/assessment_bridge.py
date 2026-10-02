"""课堂随堂题桥接（plan.md §13.2/§13.3，阶段 I01/I02）。

run→question 稳定实例化：question_id 由 owner+run+checkpoint+template_hash
确定性派生（§13.2.3）；初始化按 §13.2.6 的可恢复顺序——先在 run 文件锁内
持久化 planned question IDs，再幂等 register（同材料重放被
register_task_snapshots 跳过），崩溃后重复调用收敛到同一身份。

同题重听（§13.2.4）：同课另一 run 复用同一模板时，新题以
origin_question_ref 关联原题族，并把原题已发生的 answer_revealed /
worked_example 帮助事件按相同 kind 重放到新题——不能仅有 origin_ref 就
假定 evaluator 自动知晓。

受理/帮助全部走既有唯一链路（evaluate_submission / record_assistance），
课堂只做 run/checkpoint 归属校验与投影；提交只接 student_answer 等
§13.3 白名单字段。
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..agents.assessment import manager as assessment
from ..agents.student_model.evaluation import schema as S
from ..core import classroom_store as store
from ..schemas import classroom as sc
from .errors import ClassroomError

_QUESTION_REVISION = 1


def template_hash_of(template: sc.CheckpointTemplate) -> str:
    payload = template.verified_question_template or {}
    digest = hashlib.sha256(
        store.canonical_json(payload).encode("utf-8")).hexdigest()
    return digest[:32]


def checkpoint_question_id(owner_id: str, run_id: str, checkpoint_id: str,
                           template_hash: str) -> str:
    digest = hashlib.sha256(
        f"{owner_id}|{run_id}|{checkpoint_id}|{template_hash}"
        .encode("utf-8")).hexdigest()
    return f"q_cls_{digest[:22]}"


def question_ref_of(ref: str) -> S.QuestionRef:
    qid, _, rev = ref.partition(":")
    return S.QuestionRef(question_id=qid,
                         question_revision=int(rev or _QUESTION_REVISION))


def _templates_by_checkpoint(spec: sc.LessonRevision
                             ) -> dict[str, sc.CheckpointTemplate]:
    return {t.checkpoint_id: t for t in spec.checkpoint_templates}


def _find_prior_exposure(student_id: str, workspace_id: str, lesson_id: str,
                         run: sc.ClassroomRun, checkpoint_id: str,
                         template_hash: str) -> S.QuestionRef | None:
    """同课其他 run 是否已用过同一模板（§13.2.4 重新上一遍）。"""
    for other in store.list_runs(student_id, workspace_id, lesson_id):
        if other.run_id == run.run_id:
            continue
        for ref in other.checkpoint_refs:
            if ref.checkpoint_id != checkpoint_id or not ref.question_ref:
                continue
            prior = question_ref_of(ref.question_ref)
            task = assessment.load_task_snapshot(student_id, prior)
            family = getattr(task, "task_family", "") if task else ""
            if family.endswith(template_hash[:24]):
                return prior
    return None


def _replay_prior_assistance(student_id: str, new_ref: S.QuestionRef,
                             prior: S.QuestionRef) -> None:
    """旧题已发生的 answer_revealed / worked_example 对新题同样记录。"""
    try:
        prior_events = assessment.assistance_events(student_id, prior)
    except Exception:
        return
    for event in prior_events:
        if event.kind in (S.AssistanceEventKind.ANSWER_REVEALED,
                          S.AssistanceEventKind.WORKED_EXAMPLE):
            assessment.record_assistance(
                student_id, new_ref, kind=event.kind,
                detail=("prior run: " + event.detail)[:600],
                client_entry="classroom_prior_run")


def ensure_run_questions(student_id: str, workspace_id: str, lesson_id: str,
                         run_id: str) -> sc.ClassroomRun:
    """run 初始化题（幂等、可崩溃恢复）；返回最新 run。"""
    run = store.load_run(student_id, workspace_id, lesson_id, run_id)
    if run is None:
        raise ClassroomError("source_not_found", "课堂 run 不存在")
    spec = store.load_revision(student_id, workspace_id, lesson_id,
                               run.lesson_revision)
    if spec is None:
        raise ClassroomError("source_not_found", "课程固定版本不存在")
    templates = _templates_by_checkpoint(spec)
    planned: list[tuple[str, S.QuestionRef, sc.CheckpointTemplate]] = []
    for ref in run.checkpoint_refs:
        if ref.kind != sc.CheckpointKind.question:
            continue
        template = templates.get(ref.checkpoint_id)
        if template is None or not template.verified_question_template:
            continue
        thash = template_hash_of(template)
        qid = checkpoint_question_id(student_id, run.run_id,
                                     ref.checkpoint_id, thash)
        qref = S.QuestionRef(question_id=qid,
                             question_revision=_QUESTION_REVISION)
        planned.append((ref.checkpoint_id, qref, template))

    # 步骤 1：planned IDs 先落盘（run 锁内；记账写入不推 state_revision）
    def _plan(r: sc.ClassroomRun) -> None:
        by_cp = {p[0]: p for p in planned}
        for ref in r.checkpoint_refs:
            hit = by_cp.get(ref.checkpoint_id)
            if hit is not None and not ref.question_ref:
                ref.question_ref = (f"{hit[1].question_id}:"
                                    f"{hit[1].question_revision}")

    if any(not ref.question_ref for ref in run.checkpoint_refs):
        run = store.update_run(student_id, workspace_id, lesson_id, run_id,
                               _plan, bump_revision=False)

    # 步骤 2：幂等注册（同材料重放被 journal 跳过）
    registered: list[tuple[S.QuestionRef, sc.CheckpointTemplate]] = []
    for checkpoint_id, qref, template in planned:
        if run and not any(
                ref.checkpoint_id == checkpoint_id and ref.question_ref
                for ref in run.checkpoint_refs):
            continue
        qdict = dict(template.verified_question_template or {})
        qdict["question_id"] = qref.question_id
        qdict["question_revision"] = qref.question_revision
        task = assessment.task_snapshot_from_quiz_dict(
            qdict, workspace_id=workspace_id,
            variant_reference=f"classroom:{lesson_id}:"
                              f"{template_hash_of(template)[:24]}",
            source_session_ref="", registered_at=S.utc_now_iso())
        prior = _find_prior_exposure(student_id, workspace_id, lesson_id,
                                     run, checkpoint_id,
                                     template_hash_of(template))
        if prior is not None:
            task.origin_question_ref = prior
        assessment.register_task_snapshots(student_id, [task])
        if prior is not None:
            _replay_prior_assistance(student_id, qref, prior)
        registered.append((qref, template))
    return run


def checkpoint_public(student_id: str, workspace_id: str, lesson_id: str,
                      run: sc.ClassroomRun, checkpoint_id: str
                      ) -> sc.CheckpointPublic:
    """GET R/checkpoints/{cid}：QuestionPublic（未揭晓无答案）+ run 状态。"""
    spec = store.load_revision(student_id, workspace_id, lesson_id,
                               run.lesson_revision)
    if spec is None:
        raise ClassroomError("source_not_found", "课程固定版本不存在")
    template = next((t for t in spec.checkpoint_templates
                     if t.checkpoint_id == checkpoint_id), None)
    ref = next((r for r in run.checkpoint_refs
                if r.checkpoint_id == checkpoint_id), None)
    if template is None or ref is None:
        raise ClassroomError("source_not_found", "检查点不存在")
    question = None
    if ref.kind == sc.CheckpointKind.question and ref.question_ref:
        task = assessment.load_task_snapshot(student_id,
                                             question_ref_of(ref.question_ref))
        if task is not None:
            question = task.public_view(
                hints_available="hint_requested" in ref.assistance_events
            ).model_dump(mode="json", by_alias=True)
    return sc.CheckpointPublic(
        checkpoint_id=checkpoint_id, slide_id=template.slide_id,
        kind=ref.kind, prompt=template.prompt,
        reflection_seconds=template.reflection_seconds,
        optional=template.optional, question=question,
        run_state=ref.state)


async def submit_checkpoint(student_id: str, workspace_id: str,
                            lesson_id: str, run: sc.ClassroomRun,
                            checkpoint_id: str,
                            request: sc.CheckpointSubmitRequest
                            ) -> dict[str, Any]:
    """POST submit：唯一受理链 evaluate_submission（§13.3）。

    课堂 transport 额外校验 run/checkpoint 归属；服务端传可信
    workspace_id，保持 SourceKind.ASSESSMENT。答题不改变播放进度——
    不写 state_revision。
    """
    ref = next((r for r in run.checkpoint_refs
                if r.checkpoint_id == checkpoint_id), None)
    if ref is None or ref.kind != sc.CheckpointKind.question \
            or not ref.question_ref:
        raise ClassroomError("content_invalid", "检查点不是可作答题目")
    expected = question_ref_of(ref.question_ref)
    if request.question_ref and request.question_ref != \
            f"{expected.question_id}:{expected.question_revision}":
        raise ClassroomError("revision_conflict", "题目引用与课堂记录不一致")
    if ref.state == sc.CheckpointRunState.skipped:
        raise ClassroomError("scope_changed", "该检查点已跳过")
    receipt = await assessment.evaluate_submission(
        student_id=student_id, question_ref=expected,
        student_answer=request.student_answer,
        source_surface="classroom",
        idempotency_key=request.idempotency_key,
        expected_scope_revision=request.expected_scope_revision,
        workspace_id=workspace_id, run_inline=True)
    _mark_state(student_id, workspace_id, lesson_id, run.run_id,
                checkpoint_id, sc.CheckpointRunState.answered)
    return {
        "attempt_id": receipt.attempt_id,
        "source_id": receipt.source_id,
        "job_id": receipt.job_id,
        "question_id": receipt.question_id,
        "question_revision": receipt.question_revision,
        "task_result": (receipt.task_result.model_dump(mode="json")
                        if receipt.task_result is not None else None),
        "evaluation_status": receipt.evaluation_status,
        "evaluation_reason": receipt.evaluation_reason,
        "duplicate": receipt.duplicate,
    }


def hint_checkpoint(student_id: str, workspace_id: str, lesson_id: str,
                    run: sc.ClassroomRun, checkpoint_id: str) -> dict[str, Any]:
    """POST hint：先持久化帮助事件，再返回提示（§13.3）。"""
    ref = _require_question_ref(run, checkpoint_id)
    qref = question_ref_of(ref.question_ref)
    assessment.record_assistance(
        student_id, qref, kind=S.AssistanceEventKind.HINT_REQUESTED,
        client_entry="classroom_hint")
    _remember_assistance(student_id, workspace_id, lesson_id, run.run_id,
                         checkpoint_id, "hint_requested")
    task = assessment.load_task_snapshot(student_id, qref)
    hint = _derive_hint(task)
    return {"hint": hint}


def reveal_checkpoint(student_id: str, workspace_id: str, lesson_id: str,
                      run: sc.ClassroomRun, checkpoint_id: str
                      ) -> dict[str, Any]:
    """POST reveal：先记 answer_revealed，再返回答案/解析（§13.3）。"""
    ref = _require_question_ref(run, checkpoint_id)
    qref = question_ref_of(ref.question_ref)
    assessment.record_assistance(
        student_id, qref, kind=S.AssistanceEventKind.ANSWER_REVEALED,
        client_entry="classroom_reveal")
    _remember_assistance(student_id, workspace_id, lesson_id, run.run_id,
                         checkpoint_id, "answer_revealed")
    task = assessment.load_task_snapshot(student_id, qref)
    if task is None:
        raise ClassroomError("source_not_found", "题目不存在")
    return {"answer": task.answer, "explanation": task.explanation}


def skip_checkpoint(student_id: str, workspace_id: str, lesson_id: str,
                    run: sc.ClassroomRun, checkpoint_id: str,
                    expected_state_revision: int | None) -> None:
    """POST skip：不伪称作答、不触发评价（§13.3）。"""
    ref = next((r for r in run.checkpoint_refs
                if r.checkpoint_id == checkpoint_id), None)
    if ref is None:
        raise ClassroomError("source_not_found", "检查点不存在")
    _mark_state(student_id, workspace_id, lesson_id, run.run_id,
                checkpoint_id, sc.CheckpointRunState.skipped,
                expected_state_revision=expected_state_revision)


def submission_of(student_id: str, run: sc.ClassroomRun,
                  checkpoint_id: str) -> dict[str, Any] | None:
    """GET submission：只读已受理结果（未提交 null；GET 永不判分）。"""
    ref = _require_question_ref(run, checkpoint_id)
    qref = question_ref_of(ref.question_ref)
    from ..core.quiz_submission import quiz_submission

    return quiz_submission(student_id, qref.question_id,
                           qref.question_revision)


# ---------------------------------------------------------------------------
# 内部：run 检查点状态/帮助事件记账（不推 state_revision）
# ---------------------------------------------------------------------------

def _require_question_ref(run: sc.ClassroomRun,
                          checkpoint_id: str) -> sc.RunCheckpointRef:
    ref = next((r for r in run.checkpoint_refs
                if r.checkpoint_id == checkpoint_id), None)
    if ref is None or ref.kind != sc.CheckpointKind.question \
            or not ref.question_ref:
        raise ClassroomError("content_invalid", "检查点不是可作答题目")
    return ref


def _mark_state(student_id: str, workspace_id: str, lesson_id: str,
                run_id: str, checkpoint_id: str,
                state: sc.CheckpointRunState, *,
                expected_state_revision: int | None = None) -> None:
    def mutate(r: sc.ClassroomRun) -> None:
        for ref in r.checkpoint_refs:
            if ref.checkpoint_id == checkpoint_id:
                # 已作答不回退为跳过（§13.3 跳过不算答错，也不顶替作答）
                if ref.state == sc.CheckpointRunState.answered \
                        and state == sc.CheckpointRunState.skipped:
                    return
                ref.state = state

    store.update_run(student_id, workspace_id, lesson_id, run_id, mutate,
                     expected_state_revision=expected_state_revision,
                     bump_revision=False)


def _remember_assistance(student_id: str, workspace_id: str, lesson_id: str,
                         run_id: str, checkpoint_id: str, kind: str) -> None:
    def mutate(r: sc.ClassroomRun) -> None:
        for ref in r.checkpoint_refs:
            if ref.checkpoint_id == checkpoint_id \
                    and kind not in ref.assistance_events:
                ref.assistance_events.append(kind)

    store.update_run(student_id, workspace_id, lesson_id, run_id, mutate,
                     bump_revision=False)


def _derive_hint(task: S.TaskSnapshot | None) -> str:
    """确定性提示：来自冻结量规/知识点，不额外调用模型（§13.3 提示由
    record_assistance 后返回；本版先给量规首条判分点的方向性提示）。"""
    if task is None:
        return "暂无提示，可以再读一遍题目要求。"
    if task.source_badge:
        return (f"围绕「{task.source_badge}」思考：先写出你认为关键的"
                f"一步，再对照题目要求检查条件。")
    if task.rubric:
        return f"提示：关注「{task.rubric[0].description}」。"
    return "提示：先明确题目要求的结果形式，再组织你的答案。"
