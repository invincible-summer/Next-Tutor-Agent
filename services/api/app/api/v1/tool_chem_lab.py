"""Virtual chemistry bench routes; no chemistry in this module."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.chem_lab import service
from app.chem_lab.errors import ChemLabError
from app.identity.deps import require_user, resolve_student_id
from app.schemas.chem_lab import (
    ChemLabCatalog,
    ChemLabCheckpointCreate,
    ChemLabCommandAck,
    ChemLabCommandRequest,
    ChemLabCreateSession,
    ChemLabDeletedAck,
    ChemLabEnginePack,
    ChemLabEventPage,
    ChemLabExperimentDetail,
    ChemLabForkRequest,
    ChemLabForkResult,
    ChemLabResetRequest,
    ChemLabResultCard,
    ChemLabRevisionView,
    ChemLabSessionList,
    ChemLabSessionSnapshot,
)

router = APIRouter(
    prefix="/tools/lab/chemistry",
    tags=["tool-chem-lab"],
    dependencies=[Depends(require_user)],
)


def _public_code(code: str) -> str:
    return code if code.startswith("chem_lab_") else f"chem_lab_{code}"


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ChemLabError as exc:
        raise HTTPException(exc.status, detail={"code": _public_code(exc.code),
                                                "message": str(exc)}) from None


@router.get("/catalog", response_model=ChemLabCatalog)
def get_catalog():
    return _call(service.list_catalog)


@router.get("/experiments/{experiment_id}", response_model=ChemLabExperimentDetail)
def get_experiment(experiment_id: str, version: str | None = Query(default=None)):
    return _call(service.get_experiment, experiment_id, version)


@router.get("/experiments/{experiment_id}/engine-pack", response_model=ChemLabEnginePack)
def get_engine_pack(experiment_id: str, version: str | None = Query(default=None)):
    return _call(service.get_engine_pack, experiment_id, version)


@router.get("/sessions", response_model=ChemLabSessionList)
def list_sessions(cursor: str | None = Query(default=None),
                  owner: str = Depends(resolve_student_id)):
    return _call(service.list_sessions, owner, cursor)


@router.post("/sessions", response_model=ChemLabSessionSnapshot)
def create_session(body: ChemLabCreateSession, owner: str = Depends(resolve_student_id)):
    return _call(service.create_session, owner, body)


@router.get("/sessions/{session_id}", response_model=ChemLabSessionSnapshot)
def get_session(session_id: str, owner: str = Depends(resolve_student_id)):
    return _call(service.get_session, owner, session_id)


@router.get("/sessions/{session_id}/revisions/{revision}", response_model=ChemLabRevisionView)
def get_revision(session_id: str, revision: int,
                 owner: str = Depends(resolve_student_id)):
    """Read-only historical view: replays the stored script to ``revision``
    and writes nothing (branch creation stays on POST /fork)."""
    return _call(service.get_revision, owner, session_id, revision)


@router.delete("/sessions/{session_id}", response_model=ChemLabDeletedAck)
def delete_session(session_id: str, owner: str = Depends(resolve_student_id)):
    return _call(service.delete_session, owner, session_id)


@router.post("/sessions/{session_id}/commands", response_model=ChemLabCommandAck)
def post_command(session_id: str, body: ChemLabCommandRequest,
                 owner: str = Depends(resolve_student_id)):
    return _call(service.post_command, owner, session_id, body)


@router.get("/sessions/{session_id}/events", response_model=ChemLabEventPage)
def get_events(session_id: str,
               after_seq: int = Query(default=0, ge=0),
               limit: int = Query(default=200, ge=1, le=500),
               owner: str = Depends(resolve_student_id)):
    return _call(service.get_events, owner, session_id, after_seq, limit)


@router.post("/sessions/{session_id}/checkpoints", response_model=ChemLabCommandAck)
def create_checkpoint(session_id: str, body: ChemLabCheckpointCreate,
                      owner: str = Depends(resolve_student_id)):
    return _call(service.create_checkpoint, owner, session_id, body)


@router.post("/sessions/{session_id}/fork", response_model=ChemLabForkResult)
def fork_session(session_id: str, body: ChemLabForkRequest,
                 owner: str = Depends(resolve_student_id)):
    return _call(service.fork_session, owner, session_id, body)


@router.post("/sessions/{session_id}/reset", response_model=ChemLabForkResult)
def reset_session(session_id: str, body: ChemLabResetRequest,
                  owner: str = Depends(resolve_student_id)):
    return _call(service.reset_session, owner, session_id, body)


@router.post("/sessions/{session_id}/finish", response_model=ChemLabResultCard)
def finish_session(session_id: str, owner: str = Depends(resolve_student_id)):
    return _call(service.finish_session, owner, session_id)
