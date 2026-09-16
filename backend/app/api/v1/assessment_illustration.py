"""Text-first CAT illustration enrichment API.

The client submits only the frozen question identity.  Authoritative question
content, assessment binding, illustration policy and answer are resolved from
the student's journal; no client-supplied SVG or answer is trusted.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agents.assessment import adaptive_test as cat
from app.agents.student_model.evaluation.store import get_journal
from app.core.quiz_illustration_enrichment import generate_assessment_illustration
from app.core.quiz_illustration_policy import (
    IllustrationDisabled,
    resolve_illustration_policy,
)
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/assessment", tags=["assessment"])


class IllustrationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question_revision: int = Field(ge=1, le=1_000_000)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {
        "code": code,
        "message": message,
        "retryable": False,
        "request_id": "req_" + uuid.uuid4().hex[:12],
    }})


def _bound_instance(state, question_id: str, question_revision: int):
    for detail in state.assessments.values():
        if not isinstance(detail, dict):
            continue
        instance = cat.CatInstance.from_detail(detail)
        if any(ref.question_id == question_id and
               ref.question_revision == question_revision
               for ref in instance.question_refs):
            return instance
    return None


@router.post("/questions/{question_id}/illustration")
async def enrich_question_illustration(
    question_id: str,
    req: IllustrationRequest,
    student_id: str = Depends(resolve_student_id),
):
    state = get_journal(student_id).state()
    task = state.tasks.get(question_id, {}).get(req.question_revision)
    if task is None:
        raise _error(404, "question_not_found", "题目不存在或不属于当前账户")
    instance = _bound_instance(state, question_id, req.question_revision)
    if instance is None:
        raise _error(404, "assessment_question_not_found", "题目不属于测评实例")
    if task.illustration is not None:
        return {
            "status": "ready",
            "question_id": question_id,
            "question_revision": req.question_revision,
            "illustration": task.illustration.model_dump(mode="json"),
            "retryable": False,
            "metrics": {"generation_calls": 0, "cache_hit": 1},
        }
    try:
        policy = resolve_illustration_policy(
            student_id, instance.illustration_request or "auto")
    except IllustrationDisabled:
        raise _error(409, "illustration_disabled",
                     "题目插图已关闭，请在习题中心开启后重试")
    result = await generate_assessment_illustration(
        student_id=student_id, task=task, policy=policy)
    illustration = result.get("illustration")
    return {
        "status": result.get("status", "failed"),
        "question_id": question_id,
        "question_revision": req.question_revision,
        "illustration": illustration.model_dump(mode="json")
        if illustration is not None else None,
        "code": result.get("code", ""),
        "retryable": False,
        "metrics": result.get("metrics", {}),
    }
