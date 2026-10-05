"""Independent tool-assistant scene sessions; no assessment task registration."""
from fastapi import APIRouter, Depends, HTTPException

from app.identity.deps import require_user, resolve_student_id
from app.illustration import scenario
from app.illustration.references import ReferenceError
from app.illustration.scenario_contracts import CreateSession, SceneTurn
from app.schemas.illustration import (
    DeletedSessionAck,
    IllustrationSession,
    IllustrationSessionList,
    ToolIllustrationJob,
)

router = APIRouter(prefix="/tools/illustration", tags=["tool-assistant"], dependencies=[Depends(require_user)])


def _call(fn, *args):
    try:
        return fn(*args)
    except (scenario.SceneError, ReferenceError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None


@router.get("/sessions", response_model=IllustrationSessionList)
def list_sessions(owner: str = Depends(resolve_student_id)):
    return _call(scenario.list_sessions, owner)


@router.post("/sessions", response_model=IllustrationSession)
def create_session(body: CreateSession, owner: str = Depends(resolve_student_id)):
    return _call(scenario.create_session, owner, body)


@router.get("/sessions/{session_id}", response_model=IllustrationSession)
def get_session(session_id: str, owner: str = Depends(resolve_student_id)):
    return _call(scenario.get_session, owner, session_id)


@router.delete("/sessions/{session_id}", response_model=DeletedSessionAck)
def delete_session(session_id: str, owner: str = Depends(resolve_student_id)):
    return _call(scenario.delete_session, owner, session_id)


@router.post("/sessions/{session_id}/turns", response_model=ToolIllustrationJob)
async def create_turn(session_id: str, body: SceneTurn, owner: str = Depends(resolve_student_id)):
    return _call(scenario.start_turn, owner, session_id, body)


@router.get("/jobs/{job_id}", response_model=ToolIllustrationJob)
def get_job(job_id: str, owner: str = Depends(resolve_student_id)):
    return _call(scenario.get_job, owner, job_id)


@router.post("/jobs/{job_id}/retry", response_model=ToolIllustrationJob)
async def retry_job(job_id: str, owner: str = Depends(resolve_student_id)):
    return _call(scenario.retry_job, owner, job_id)
