"""UX Intelligence API (M8 observability + frontend data).

Exposes the student's interaction profile, engagement events, and motivation
state for human inspection and for the frontend "学习画像" panel and
personalized greeting. Mirrors the /evaluation endpoints' observability role.

All endpoints are READ-ONLY. Uses the DEFAULT_STUDENT_ID (single-student
system, same as M2-M7). Never raises into a request (defensive).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.agents.ux_intelligence import get_ux_service
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/ux", tags=["ux"])


@router.get("/profile")
def ux_profile(student_id: str = Depends(resolve_student_id)) -> dict:
    """The student's UX interaction profile: tone / detail / visual / pacing /
    patience, recent feedback counts, average answer length, abandon signals.
    This is the data behind the "学习画像" panel."""
    return get_ux_service().profile(student_id)


@router.get("/engagement")
def ux_engagement(
    student_id: str = Depends(resolve_student_id),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """Recent UX events (the append-only black box): feedback signals, response
    lengths, abandon heuristics."""
    return get_ux_service().engagement(student_id, limit=limit)


@router.get("/motivation")
def ux_motivation(student_id: str = Depends(resolve_student_id)) -> dict:
    """Streak / milestone summary (reads the unified activity day-union)."""
    return get_ux_service().motivation(student_id)


@router.get("/activity")
def ux_activity(
    student_id: str = Depends(resolve_student_id),
    days: int = Query(default=14, ge=1, le=90),
) -> dict:
    """Per-day learning-activity counts (answers / teachings / reviews) for
    the dashboard chart, plus the streak summary and its data source
    ("aggregated" live ledgers vs "legacy_episodes" compatibility fallback)."""
    return get_ux_service().activity(student_id, days=days)


@router.get("/greeting")
def ux_greeting(
    student_id: str = Depends(resolve_student_id),
    grade: str = Query(default=""),
    lang: str = Query(default="zh"),
    grade_zh: str = Query(default="", description="kept for compat"),
) -> dict:
    """A personalized opener for a new/empty session: resume hint + streak.
    The frontend empty state calls this to greet the student by what they last
    studied and their current streak."""
    g = grade or grade_zh
    text = get_ux_service().greeting(student_id, grade=g, lang=lang)
    return {"greeting": text, "lang": lang}
