"""User account and profile data models.

Two distinct concepts, kept separate from the StudentModel (which holds
academic state like mastery/weakness):

  - User: identity/auth info (email, username, password_hash, role).
  - UserProfile: student basic info (name, grade, school, subjects).

The user_id doubles as the student_id (the namespace key for all M2-M9 data).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

VALID_ROLES = ("student", "parent", "teacher", "admin")
VALID_GRADES = ("小学", "初中", "高中", "本科")


@dataclass
class UserProfile:
    """Student basic info -- NOT academic state. Set during registration.

    ``prefs`` 是通用每用户偏好（如 ocr_parallel），dict 形态免 schema 演进。"""
    name: str = ""
    grade: str = "本科"
    school: str = ""
    subjects: list[str] = field(default_factory=list)
    avatar: str = ""
    prefs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "grade": self.grade, "school": self.school,
                "subjects": list(self.subjects), "avatar": self.avatar,
                "prefs": dict(self.prefs)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "UserProfile":
        d = d or {}
        return cls(
            name=str(d.get("name", "")),
            grade=str(d.get("grade", "本科")),
            school=str(d.get("school", "")),
            subjects=list(d.get("subjects") or []),
            avatar=str(d.get("avatar", "")),
            prefs=dict(d.get("prefs") or {}),
        )


@dataclass
class User:
    """A registered user account. user_id is the identity + student namespace."""
    id: str = ""
    email: str = ""
    username: str = ""
    password_hash: str = ""
    role: str = "student"
    created_at: float = field(default_factory=time.time)
    last_login_at: float = 0.0
    profile: UserProfile = field(default_factory=UserProfile)

    @property
    def student_id(self) -> str:
        """The student namespace key -- identical to user_id by design."""
        return self.id

    def to_public_dict(self) -> dict[str, Any]:
        """Safe to return to the client -- excludes password_hash."""
        return {
            "id": self.id, "email": self.email, "username": self.username,
            "role": self.role, "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "profile": self.profile.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for the store (includes password_hash)."""
        d = self.to_public_dict()
        d["password_hash"] = self.password_hash
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "User":
        d = d or {}
        return cls(
            id=str(d.get("id", "")),
            email=str(d.get("email", "")),
            username=str(d.get("username", "")),
            password_hash=str(d.get("password_hash", "")),
            role=str(d.get("role", "student")),
            created_at=float(d.get("created_at", 0.0)),
            last_login_at=float(d.get("last_login_at", 0.0)),
            profile=UserProfile.from_dict(d.get("profile")),
        )
