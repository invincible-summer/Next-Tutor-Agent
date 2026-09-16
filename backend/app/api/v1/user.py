"""M0 User profile API: read + update the student's basic info.

This is the "Profile System" from the M0 spec -- NOT the StudentModel (which
holds academic state). Updated grade flows into the session default and the
StudentModel profile so M1-M9 see the correct grade band going forward.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from typing import Any

from app.core.account_data import purge_account
from app.identity.deps import require_user
from app.identity.models import User
from app.identity.security import verify_password
from app.identity.store import update_profile_fields

router = APIRouter(prefix="/user", tags=["user"])


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    grade: str | None = None
    school: str | None = Field(default=None, max_length=80)
    subjects: list[str] | None = None
    avatar: str | None = Field(default=None, max_length=200)
    prefs: dict[str, Any] | None = None

    @field_validator("prefs")
    @classmethod
    def validate_svg_preference(cls, prefs):
        if prefs is not None and "quiz_svg_enabled" in prefs:
            if type(prefs["quiz_svg_enabled"]) is not bool:
                raise ValueError("quiz_svg_enabled must be a boolean")
        return prefs


@router.get("/profile")
def get_profile(user: User = Depends(require_user)):
    from app.core.quiz_illustration_policy import svg_available
    return {"status": "ok", "profile": user.profile.to_dict(),
            "quiz_svg_available": svg_available()}


@router.put("/profile")
def update_profile(req: UpdateProfileRequest, user: User = Depends(require_user)):
    """Update mutable profile fields. Only non-None fields are changed."""
    fields = {name: getattr(req, name) for name in
              ("name", "grade", "school", "subjects", "avatar")
              if getattr(req, name) is not None}
    if fields or req.prefs is not None:
        try:
            user = update_profile_fields(user.id, fields, req.prefs)
        except ValueError:
            raise HTTPException(status_code=404, detail="account_not_found")
        # Sync grade into the StudentModel profile so M1-M9 use the new band.
        if "grade" in fields:
            _sync_grade_to_student_model(user)
    return get_profile(user)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.delete("/account")
def delete_account(req: DeleteAccountRequest, user: User = Depends(require_user)):
    """Self-service account deletion. Requires password re-confirmation so a
    borrowed session alone cannot destroy the account.

    名下全部数据（会话/转写/trace/上传/工作区/资料库/回收站/笔记/学习档案/
    知识图谱）随账号不可恢复地清除（account_data.purge_account），账号记录
    最后删，中途失败可重试且不残留孤儿数据；JWT 随账号记录消失（get_by_id
    misses -> 401 everywhere）。"""
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="invalid_password")
    report = purge_account(user.id)
    return {"status": "deleted", "report": report}


def _sync_grade_to_student_model(user: User) -> None:
    """Push the new grade into the StudentModel profile (M2), best-effort."""
    try:
        from app.agents.student_model import get_student_model
        sm = get_student_model(user.student_id)
        sm.profile.grade = user.profile.grade
        sm._persist()
    except Exception:
        pass
