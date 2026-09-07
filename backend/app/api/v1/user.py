"""M0 User profile API: read + update the student's basic info.

This is the "Profile System" from the M0 spec -- NOT the StudentModel (which
holds academic state). Updated grade flows into the session default and the
StudentModel profile so M1-M9 see the correct grade band going forward.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any

from app.core.account_data import purge_account
from app.identity.deps import require_user
from app.identity.models import User
from app.identity.security import verify_password
from app.identity.store import update_user

router = APIRouter(prefix="/user", tags=["user"])


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    grade: str | None = None
    school: str | None = Field(default=None, max_length=80)
    subjects: list[str] | None = None
    avatar: str | None = Field(default=None, max_length=200)
    prefs: dict[str, Any] | None = None


@router.get("/profile")
def get_profile(user: User = Depends(require_user)):
    return {"status": "ok", "profile": user.profile.to_dict()}


@router.put("/profile")
def update_profile(req: UpdateProfileRequest, user: User = Depends(require_user)):
    """Update mutable profile fields. Only non-None fields are changed."""
    changed = False
    for field_name in ("name", "grade", "school", "subjects", "avatar"):
        val = getattr(req, field_name)
        if val is not None:
            setattr(user.profile, field_name, val)
            changed = True
    if req.prefs is not None:
        # 通用偏好浅合并（如 ocr_parallel）；dict 形态免 schema 演进。
        user.profile.prefs.update(req.prefs)
        changed = True
    if changed:
        # Sync grade into the StudentModel profile so M1-M9 use the new band.
        _sync_grade_to_student_model(user)
        update_user(user)
    return {"status": "ok", "profile": user.profile.to_dict()}


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
