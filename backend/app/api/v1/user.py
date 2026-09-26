"""M0 User profile API: read + update the student's basic info.

This is the "Profile System" from the M0 spec -- NOT the StudentModel (which
holds academic state). Updated grade flows into the session default and the
StudentModel profile so M1-M9 see the correct grade band going forward.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from typing import Any

from app.core.account_data import purge_account
from app.identity.deps import require_user
from app.identity.models import User
from app.identity.security import verify_password
from app.identity.store import update_profile_fields

router = APIRouter(prefix="/user", tags=["user"])

# 个人课堂偏好白名单（plan.md §20.1）：只接受有类型的字段；tts_speed 沿用
# 既有顶层值。任何未知键/URL 形值一律 422——个人偏好绝不能借道携带供应商
# endpoint/base URL 之类的任意配置。
_CLASSROOM_PREF_BOOL_FIELDS = (
    "allow_local_fallback", "captions", "auto_advance", "low_stimulus",
    "pause_on_hidden",
)
_CLASSROOM_PREF_ID_FIELDS = ("theme_id", "pedagogy_id", "voice_id")
_CLASSROOM_PREF_KEYS = frozenset(
    _CLASSROOM_PREF_BOOL_FIELDS + _CLASSROOM_PREF_ID_FIELDS
    + ("voice_policy",))
_CLASSROOM_VOICE_POLICIES = frozenset(("auto", "cloud", "local", "silent"))
_URLISH = re.compile(r"[:/\s]")
_ID_TOKEN = re.compile(r"^[A-Za-z0-9_@.\-]{1,64}$")


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    grade: str | None = None
    school: str | None = Field(default=None, max_length=80)
    subjects: list[str] | None = None
    avatar: str | None = Field(default=None, max_length=200)
    prefs: dict[str, Any] | None = None

    @field_validator("prefs")
    @classmethod
    def validate_boolean_preferences(cls, prefs):
        for key in ("quiz_svg_enabled", "quiz_critic_enabled",
                    "quiz_illustration_review_enabled"):
            if prefs is not None and key in prefs and type(prefs[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
        if prefs is not None and "classroom" in prefs:
            prefs["classroom"] = _validate_classroom_prefs(prefs["classroom"])
        return prefs


def _validate_classroom_prefs(raw: Any) -> dict[str, Any]:
    """classroom 偏好严格校验：白名单键 + 显式类型 + 拒绝 URL 形值。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("classroom 偏好必须是对象")
    unknown = sorted(set(raw) - _CLASSROOM_PREF_KEYS)
    if unknown:
        raise ValueError(f"classroom 偏好包含未知字段: {', '.join(unknown)}")
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _CLASSROOM_PREF_BOOL_FIELDS:
            if type(value) is not bool:
                raise ValueError(f"classroom.{key} 必须是布尔值")
            cleaned[key] = value
        elif key == "voice_policy":
            if value not in _CLASSROOM_VOICE_POLICIES:
                raise ValueError("classroom.voice_policy 必须是 "
                                 "auto/cloud/local/silent")
            cleaned[key] = value
        else:   # _CLASSROOM_PREF_ID_FIELDS
            if not isinstance(value, str) or not value:
                raise ValueError(f"classroom.{key} 必须是非空字符串")
            if len(value) > 64 or "://" in value or _URLISH.search(value):
                raise ValueError(f"classroom.{key} 含非法字符（不接受 URL）")
            if not _ID_TOKEN.match(value):
                raise ValueError(f"classroom.{key} 只允许字母数字与 _@.-")
            cleaned[key] = value
    return cleaned


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
