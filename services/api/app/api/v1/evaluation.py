"""Evaluation & Improvement Intelligence API (M7 observability).

Exposes the tutor's self-evaluation metrics, improvement proposals, and
experiments for human inspection -- answering "is the tutor getting better?".
Mirrors the /trace endpoints' observability role.

All endpoints are read-only except proposal status transitions (approve/reject),
which are the human-in-the-loop gate for improvement proposals. Uses the
DEFAULT_STUDENT_ID (single-student system, same as M2-M6).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.agents.evaluation import get_evaluation_service
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/report")
def evaluation_report(student_id: str = Depends(resolve_student_id)) -> dict:
    """System-level effectiveness snapshot: learning gain, failure distribution,
    strategy effectiveness, pending proposals."""
    es = get_evaluation_service()
    return es.report(student_id).to_dict()


@router.get("/traces")
def evaluation_traces(
    student_id: str = Depends(resolve_student_id),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """Recent turn evaluation traces (the append-only black box)."""
    es = get_evaluation_service()
    return es.traces(student_id, limit=limit)


@router.get("/context-budget")
def evaluation_context_budget(
    student_id: str = Depends(resolve_student_id),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    """Non-sensitive context, compaction, reasoning and Provider telemetry."""
    from app.core.context_telemetry import context_runtime_report
    return context_runtime_report(student_id, limit=limit)


@router.get("/proposals")
def evaluation_proposals(student_id: str = Depends(resolve_student_id)) -> list[dict]:
    """All improvement proposals (proposed/approved/applied/rejected)."""
    es = get_evaluation_service()
    return es.proposals(student_id)


class ProposalStatusBody(BaseModel):
    status: str  # approved | rejected | applied


@router.patch("/proposals/{proposal_id}")
def update_proposal_status(
    proposal_id: str,
    body: ProposalStatusBody,
    student_id: str = Depends(resolve_student_id),
) -> dict:
    """Transition an improvement proposal's status (human-in-the-loop gate).

    Valid statuses: approved, rejected, applied. 'proposed' is set by the
    advisor and cannot be re-set here.

    status=applied is the DEPLOY step: the proposal's guidance text is written
    into the teaching engine's guidance store, where compose() picks it up for
    subsequent teaching turns. Legacy target-format proposals have no guidance
    text — they are marked applied without deploying anything (same no-op the
    status previously was).
    """
    if body.status not in ("approved", "rejected", "applied"):
        raise HTTPException(400, "status must be one of: approved, rejected, applied")
    es = get_evaluation_service()
    if body.status == "approved":
        ok = es.approve_proposal(student_id, proposal_id)
    elif body.status == "rejected":
        ok = es.reject_proposal(student_id, proposal_id)
    else:
        from app.agents.evaluation import store
        ok = store.update_proposal_status(student_id, proposal_id, "applied")
        if ok:
            _deploy_guidance(student_id, proposal_id)
    if not ok:
        raise HTTPException(404, "Proposal not found")
    return {"proposal_id": proposal_id, "status": body.status}


def _deploy_guidance(student_id: str, proposal_id: str) -> bool:
    """Write an applied proposal's guidance into teaching_engine/guidance_store.

    The one sanctioned cross-module write: M7's analyzer never touches M3; this
    human-initiated deploy action (via the API layer) is the only bridge.
    Never raises.
    """
    try:
        from app.agents.evaluation import store
        from app.agents.teaching_engine import guidance_store
        proposal = store.load_proposal(student_id, proposal_id)
        if proposal is None or not proposal.guidance:
            return False
        return guidance_store.apply_guidance(student_id, guidance_store.GuidanceEntry(
            source_proposal=proposal.id,
            title=proposal.title, applicability=proposal.applicability,
            guidance=proposal.guidance, cautions=list(proposal.cautions),
            confidence=proposal.confidence,
        ))
    except Exception:
        return False


@router.get("/guidance")
def evaluation_guidance(student_id: str = Depends(resolve_student_id)) -> list[dict]:
    """Applied teaching guidance (active + revoked), each with an impact echo.

    Active entries are consumed by the teaching engine every turn; impact_turns
    counts eval traces since the entry was applied.
    """
    from app.agents.evaluation import store
    from app.agents.teaching_engine import guidance_store
    entries = guidance_store.load_all(student_id)
    traces = store.read_traces(student_id) if entries else []
    out: list[dict] = []
    for e in entries:
        d = e.to_dict()
        d["impact_turns"] = (sum(1 for t in traces if t.ts >= e.applied_at)
                             if e.active and e.applied_at else None)
        out.append(d)
    out.sort(key=lambda d: (not d.get("active", False), -float(d.get("applied_at", 0.0))))
    return out


@router.delete("/guidance/{entry_id}")
def revoke_evaluation_guidance(
    entry_id: str,
    student_id: str = Depends(resolve_student_id),
) -> dict:
    """Revoke an applied teaching guidance entry (instant rollback).

    The entry is kept for audit (active=False) but the teaching engine stops
    consuming it immediately.
    """
    from app.agents.teaching_engine import guidance_store
    if not guidance_store.revoke_guidance(student_id, entry_id):
        raise HTTPException(404, "Guidance entry not found")
    return {"entry_id": entry_id, "active": False}
