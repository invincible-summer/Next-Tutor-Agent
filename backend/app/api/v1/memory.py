"""Memory Intelligence API (M6 observability + bounded prompt-memory policy).

Read-only compatibility views over the legacy episodic/semantic stores plus
the active bounded procedural aggregates for the frontend "记忆" panel.
Production turns no longer append detailed episodic events or run semantic
consolidation; those two stores remain visible only for migration/audit.

Prompt-profile endpoints expose the bounded active cross-chat profile and allow
the owner to choose a 5–30 session window; they never expose transcripts, raw
attachment text, or detailed learning content.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.agents import memory as _mem
from app.agents.memory import procedural as _procedural
from app.agents.memory import store as _mem_store
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/episodes")
def memory_episodes(
    student_id: str = Depends(resolve_student_id),
    limit: int = Query(default=50, ge=1, le=500),
    before: float | None = Query(default=None),
) -> dict:
    """Legacy episodic audit records, newest-first (compatibility read-only).

    ``before`` (Unix time) pages backwards; production turns do not append new
    detailed episodic records.
    """
    if not _mem.is_enabled():
        return {"status": "disabled"}
    try:
        eps = _mem_store.read_episodes(student_id)
        if before is not None:
            eps = [e for e in eps if e.ts < before]
        eps.sort(key=lambda e: e.ts, reverse=True)
        page = eps[:limit]
        return {"status": "ok", "episodes": [e.to_dict() for e in page],
                "has_more": len(eps) > limit}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/semantic")
def memory_semantic(student_id: str = Depends(resolve_student_id)) -> dict:
    """Legacy semantic facts, including superseded audit entries.

    This endpoint is compatibility-read-only; semantic facts are no longer
    consolidated or injected into production prompts.
    """
    if not _mem.is_enabled():
        return {"status": "disabled"}
    try:
        facts = _mem_store.load_all_semantic_facts(student_id)
        return {"status": "ok", "facts": [f.to_dict() for f in facts]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/procedural")
def memory_procedural(student_id: str = Depends(resolve_student_id)) -> dict:
    """Procedural memories (M6): which teaching strategies worked for this
    student (sliding-window success rate + trial count)."""
    if not _mem.is_enabled():
        return {"status": "disabled"}
    try:
        items = _procedural.all_procedural(student_id)
        return {"status": "ok", "strategies": [p.to_dict() for p in items]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class PromptMemoryWindowRequest(BaseModel):
    window_size: int = Field(..., ge=5, le=30)


@router.get("/prompt-profile")
def prompt_memory_profile(student_id: str = Depends(resolve_student_id)) -> dict:
    from app.agents.memory.prompt_memory import public_view
    return {"status": "ok", **public_view(student_id)}


@router.put("/prompt-profile/window")
def update_prompt_memory_window(
    req: PromptMemoryWindowRequest,
    student_id: str = Depends(resolve_student_id),
) -> dict:
    from app.agents.memory.prompt_memory import set_user_window, public_view
    set_user_window(student_id, req.window_size)
    return {"status": "ok", **public_view(student_id)}


@router.get("/prompt-profile/sessions/{session_id}")
def prompt_memory_session_status(
    session_id: str,
    student_id: str = Depends(resolve_student_id),
) -> dict:
    from app.agents.memory.prompt_memory import session_forget_status
    return {"status": session_forget_status(student_id, session_id)}
