"""Authenticated job lifecycle; identities and material always resolve server-side."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.agents.student_model.evaluation.store import get_journal
from app.core.quiz_illustration_policy import IllustrationDisabled, resolve_illustration_policy
from app.identity.deps import resolve_student_id
from app.illustration import persistence
from app.illustration.contracts import (IllustrationError,
                                        QuestionMaterialContract,
                                        material_contract)
from app.illustration.events import public_job
from app.illustration.orchestrator import recover_job, start_job

from .assessment_illustration import _bound_instance, _error

router = APIRouter(tags=["illustrations"])


class DraftReference(BaseModel):
    model_config = {"extra": "forbid"}
    question_id: str = Field(min_length=1, max_length=96)
    question_revision: int = Field(ge=1, le=1_000_000)


def owned_task(owner, question_id, revision):
    state = get_journal(owner).state()
    task = state.tasks.get(question_id, {}).get(revision)
    if task is None:
        raise _error(404, "question_not_found", "题目不存在或不属于当前账户")
    return state, task


def policy_for(owner, state, task, *, pipeline_mode=None):
    instance = _bound_instance(state, task.question_id, task.question_revision)
    if instance is None:
        raise _error(404, "assessment_question_not_current", "只有进行中测评的当前题可启动新的补图任务")
    if instance.illustration_mode not in {"v2", "v3"}:
        raise _error(409, "illustration_mode_mismatch", "当前测评选择的是 V1 配图方式")
    if pipeline_mode is not None and instance.illustration_mode != pipeline_mode:
        raise _error(409, "illustration_mode_mismatch", "配图任务与测评选择的版本不一致")
    try:
        return resolve_illustration_policy(owner, instance.illustration_request or "auto")
    except IllustrationDisabled:
        raise _error(409, "illustration_disabled", "题目插图已关闭") from None


def task_contract(task, *, illustration_guidance: str = "", pipeline_mode="v2"):
    if pipeline_mode == "v3":
        from app.illustration.v3_contracts import material_contract as v3_contract
        # Frozen CAT supplements use public text, never gold-derived drawing
        # inputs or an old V2 hidden-value projection.
        return v3_contract({"type": task.q_type.value, "stem": task.stem,
            "options": task.options, "answer": task.answer, "explanation": task.explanation,
            "rubric": [c.model_dump(mode="json") for c in task.rubric]},
            question_ref=task.question_id, revision=task.question_revision,
            frozen=True, illustration_guidance=illustration_guidance)
    # Late diagrams cannot supply essential conditions or change a snapshot.
    if task.material_contract is not None:
        # CAT generation may already have produced and validated the complete
        # material contract.  Reusing it keeps V2 grounded in the same facts
        # instead of reconstructing a weaker stem/options-only contract.
        payload = task.material_contract.model_dump(mode="json")
        payload["illustration_guidance"] = str(illustration_guidance or "").strip()[:1200]
        payload["contract_hash"] = ""
        return QuestionMaterialContract.model_validate(payload)
    return material_contract({"type": task.q_type.value, "stem": task.stem, "options": task.options,
        "answer": task.answer, "explanation": task.explanation,
        "rubric": [c.model_dump(mode="json") for c in task.rubric]},
        question_ref=task.question_id, revision=task.question_revision, frozen=True,
        illustration_guidance=illustration_guidance)


def recovered_job(owner, job):
    try:
        return recover_job(owner, job)
    except IllustrationError:
        # A deletion may invalidate a job after its initial read. Recovery
        # must not expose stale material or recreate a deleted owner root.
        raise _error(404, "illustration_job_not_found", "配图任务不存在") from None


@router.post("/quiz/illustration-jobs")
async def create_illustration_job(req: DraftReference, owner: str = Depends(resolve_student_id)):
    # Browser clients may reference only already owned server material. New
    # essential drafts use the internal generation path before registration.
    state, task = owned_task(owner, req.question_id, req.question_revision)
    instance = _bound_instance(state, task.question_id, task.question_revision)
    mode = instance.illustration_mode if instance else None
    existing = persistence.find_job(owner, req.question_id, req.question_revision, pipeline_mode=mode)
    if existing:
        existing = recovered_job(owner, existing)
        if existing["status"] != "failed":
            return public_job(owner, existing)
        # A failed job cannot bypass policy/current-question checks. The
        # orchestrator may replace it when its implementation version changed.
    if task.illustration is not None:
        return {"status": "ready", "question_id": task.question_id,
            "question_revision": task.question_revision, "visual_role": task.visual_role,
            "artifact_id": task.illustration_artifact_id,
            "illustration": task.illustration.model_dump(mode="json")}
    try:
        instance = _bound_instance(state, task.question_id, task.question_revision)
        job = start_job(owner, task_contract(
            task, illustration_guidance=instance.generation_hint if instance else "", pipeline_mode=mode or "v2"),
            policy_for(owner, state, task), pipeline_mode=mode or "v2")
    except IllustrationError as exc:
        raise _error(409, exc.code, "题目材料无法补图，请修订题目或检查生成设置") from None
    return public_job(owner, job)


@router.get("/illustration-jobs/{job_id}")
def get_illustration_job(job_id: str, owner: str = Depends(resolve_student_id)):
    try:
        job = persistence.read(owner, "jobs", job_id)
    except ValueError:
        job = None
    if job is None or job.get("shadow"):
        raise _error(404, "illustration_job_not_found", "配图任务不存在")
    owned_task(owner, job["question_id"], job["question_revision"])
    return public_job(owner, recovered_job(owner, job))


@router.post("/illustration-jobs/{job_id}/retry")
async def retry_illustration_job(job_id: str, owner: str = Depends(resolve_student_id)):
    try:
        job = persistence.read(owner, "jobs", job_id)
    except ValueError:
        job = None
    if job is None or job.get("shadow"):
        raise _error(404, "illustration_job_not_found", "配图任务不存在")
    job = recovered_job(owner, job)
    state, task = owned_task(owner, job["question_id"], job["question_revision"])
    latest = persistence.find_job(owner, task.question_id, task.question_revision)
    if latest is not None:
        latest = recovered_job(owner, latest)
    if task.illustration or job["status"] == "ready" or latest and latest["status"] == "ready":
        raise _error(409, "illustration_frozen", "已冻结题图不能覆盖，请创建新的题目版本")
    if job["status"] in {"queued", "running"}:
        return public_job(owner, job)
    try:
        instance = _bound_instance(state, task.question_id, task.question_revision)
        mode = job.get("pipeline_mode", "v2")
        new_job = start_job(owner, task_contract(
            task, illustration_guidance=instance.generation_hint if instance else "", pipeline_mode=mode),
            policy_for(owner, state, task, pipeline_mode=mode), retry=True, pipeline_mode=mode)
    except IllustrationError as exc:
        raise _error(409, exc.code, "题目材料无法补图") from None
    return public_job(owner, new_job)


@router.get("/questions/{question_id}/illustration")
def frozen_illustration(question_id: str, question_revision: int = Query(ge=1),
                        owner: str = Depends(resolve_student_id)):
    _, task = owned_task(owner, question_id, question_revision)
    if task.illustration:
        return {"status": "ready", "question_id": question_id, "question_revision": question_revision,
            "visual_role": task.visual_role, "artifact_id": task.illustration_artifact_id,
            "illustration": task.illustration.model_dump(mode="json")}
    job = persistence.find_job(owner, question_id, question_revision)
    if job:
        return public_job(owner, recovered_job(owner, job))
    from app.core.quiz_illustration_enrichment import get_cached_assessment_illustration
    cached = get_cached_assessment_illustration(owner, question_id, question_revision)
    return {"status": cached["status"] if cached else "not_required", "question_id": question_id,
        "question_revision": question_revision, "visual_role": "supplemental",
        "illustration": cached["illustration"].model_dump(mode="json") if cached and cached.get("illustration") else None}
