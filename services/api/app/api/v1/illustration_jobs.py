"""Authenticated job lifecycle; identities and material always resolve server-side."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.agents.student_model.evaluation.store import get_journal
from app.core.quiz_illustration_policy import IllustrationDisabled, resolve_illustration_policy
from app.identity.deps import resolve_student_id
from app.illustration import persistence
from app.illustration.contracts import IllustrationError, material_contract
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


def policy_for(owner, state, task):
    instance = _bound_instance(state, task.question_id, task.question_revision)
    if instance is None:
        raise _error(404, "assessment_question_not_current", "只有进行中测评的当前题可启动新的补图任务")
    try:
        return resolve_illustration_policy(owner, instance.illustration_request or "auto")
    except IllustrationDisabled:
        raise _error(409, "illustration_disabled", "题目插图已关闭") from None


def task_contract(task):
    # Late diagrams cannot supply essential conditions or change a snapshot.
    return material_contract({"type": task.q_type.value, "stem": task.stem, "options": task.options,
        "answer": task.answer, "explanation": task.explanation,
        "rubric": [c.model_dump(mode="json") for c in task.rubric]},
        question_ref=task.question_id, revision=task.question_revision, frozen=True)


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
    existing = persistence.find_job(owner, req.question_id, req.question_revision)
    if existing:
        return public_job(owner, recovered_job(owner, existing))
    if task.illustration is not None:
        return {"status": "ready", "question_id": task.question_id,
            "question_revision": task.question_revision, "visual_role": task.visual_role,
            "artifact_id": task.illustration_artifact_id,
            "illustration": task.illustration.model_dump(mode="json")}
    try:
        job = start_job(owner, task_contract(task), policy_for(owner, state, task))
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
        new_job = start_job(owner, task_contract(task), policy_for(owner, state, task), retry=True)
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
