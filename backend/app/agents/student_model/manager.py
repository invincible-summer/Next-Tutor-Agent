"""StudentModel facade：M2 只剩画像（plan §13.2）。

旧数值掌握链（tracker/状态机/事件写入口）
已删除；学习评价（claims/judgments）唯一事实在
`students/<sid>.learning_evidence.jsonl`，读取经 evaluation.ports。
"""
from __future__ import annotations

import os
import time

from .state import LearningStyle, StudentProfile
from .store import DEFAULT_STUDENT_ID, load_blob, save_blob


def is_enabled() -> bool:
    """Whether the student-profile layer is active (default on)."""
    return os.getenv("STUDENT_MODEL_MODE", "1") not in ("0", "false",
                                                        "False", "off")


class StudentModel:
    """Per-student profile（身份/学段/可变偏好/活动）。"""

    def __init__(self, student_id: str = DEFAULT_STUDENT_ID) -> None:
        self.student_id = student_id
        self._loaded = False
        self.profile: StudentProfile = StudentProfile(id=student_id)

    def load(self) -> "StudentModel":
        if self._loaded:
            return self
        try:
            blob = load_blob(self.student_id)
            self.profile = blob.profile
        except Exception:
            self.profile = StudentProfile(id=self.student_id)
        self._loaded = True
        return self

    def _persist(self) -> None:
        try:
            self.profile.updated_at = time.time()
            save_blob(self.student_id, self.profile)
        except Exception:
            pass

    def update_learning_style(self, *, preference: str = "",
                              explanation_depth: str = "") -> bool:
        """learning_style 的唯一生产写入入口（M8 体验反馈折叠结果）。"""
        ls = self.profile.learning_style
        changed = False
        if preference and preference != ls.preference:
            ls.preference = preference
            changed = True
        if explanation_depth and explanation_depth != ls.explanation_depth:
            ls.explanation_depth = explanation_depth
            changed = True
        if changed:
            self._persist()
        return changed

    def snapshot(self, *, grade: str = "", current_subject: str = "",
                 has_materials: bool = False, material_count: int = 0,
                 material_names: list[str] | None = None,
                 recent_quiz_count: int = 0,
                 recent_weak_points: list[str] | None = None,
                 evaluation_context: dict | None = None) -> dict:
        """StudentSnapshot（supervisor 消费的轻量视图；无能力镜像）。

        evaluation_context：当前 workspace 的统一评价只读投影（§13.1），
        由 supervisor 经 EvaluationReader 注入。
        """
        self.load()
        grade = grade or self.profile.grade or "本科"
        return {
            "grade": grade,
            "has_materials": has_materials,
            "material_count": material_count,
            "material_names": list(material_names or []),
            "recent_quiz_count": recent_quiz_count,
            "recent_weak_points": list(recent_weak_points or []),
            "conversation_topic_hint": None,
            "goals": list(self.profile.goals[-6:]),
            "current_subject": current_subject,
            "learning_style": self.profile.learning_style.to_dict(),
            "evaluation_context": evaluation_context or {},
        }


_CACHE: dict[str, StudentModel] = {}


def get_student_model(student_id: str = DEFAULT_STUDENT_ID) -> StudentModel:
    sm = _CACHE.get(student_id)
    if sm is None:
        sm = StudentModel(student_id).load()
        _CACHE[student_id] = sm
    return sm
