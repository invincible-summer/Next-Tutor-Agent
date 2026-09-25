"""M2 画像持久化：`students/<id>.json`（profile-only blob）。

旧 mastery/memory/事件黑盒（.events.jsonl）已随统一评价删除；历史文件
由迁移工具处理（§16）。读取对旧字段宽容（忽略），写出不再包含它们。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock
from .state import StudentProfile

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

DEFAULT_STUDENT_ID = "student_default"


def _resolve(student_id: str, suffix: str = ".json") -> Path:
    name = Path(str(student_id or DEFAULT_STUDENT_ID)).name
    return _STUDENTS_DIR / f"{name}{suffix}"


class StudentStateBlob:
    """兼容外壳：老调用方读 .profile。"""

    def __init__(self, profile: StudentProfile) -> None:
        self.profile = profile


def load_blob(student_id: str) -> StudentStateBlob:
    path = _resolve(student_id)
    if not path.exists():
        return StudentStateBlob(profile=StudentProfile(id=student_id))
    with file_lock(path):
        data = json.loads(path.read_text(encoding="utf-8"))
    profile = StudentProfile.from_dict(data.get("profile") or data)
    profile.id = student_id or profile.id
    return StudentStateBlob(profile=profile)


def save_blob(student_id: str, blob: StudentStateBlob | StudentProfile
              ) -> None:
    path = _resolve(student_id)
    profile = blob.profile if isinstance(blob, StudentStateBlob) else blob
    _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        atomic_write_text(path, json.dumps(
            {"version": 2, "profile": profile.to_dict()},
            ensure_ascii=False, indent=2))


def read_events(student_id: str) -> list[Any]:
    """旧事件黑盒已删除；返回空（调用方兼容读取）。"""
    return []


def append_events(student_id: str, events: list[Any]) -> None:
    return None
