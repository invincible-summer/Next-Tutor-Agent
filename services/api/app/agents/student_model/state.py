"""M2 学生档案状态（plan §13.2：只保留身份/学段/可变偏好/活动）。

旧数值学习状态、概念状态机、事件评价
事件链已随统一学习评价（journal）删除；学习评价读取一律经
`student_model/evaluation` 的 EvaluationReader（ports.py）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LearningStyle:
    """How the student prefers to be taught（可变偏好，不是认知类型）。"""
    preference: str = "balanced"          # step_by_step | examples_first | balanced
    explanation_depth: str = "adaptive"   # basic | deep | adaptive

    def to_dict(self) -> dict[str, Any]:
        return {"preference": self.preference,
                "explanation_depth": self.explanation_depth}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LearningStyle":
        d = d or {}
        return cls(
            preference=str(d.get("preference", "balanced")) or "balanced",
            explanation_depth=str(d.get("explanation_depth", "adaptive"))
            or "adaptive",
        )


@dataclass
class StudentProfile:
    """长期画像：身份、学段、学科活动、可变偏好与自述目标。

    weak/strong 能力字段已删除（A12）——学习评价只在统一评价域中。
    """
    id: str = "student_default"
    grade: str = "本科"
    subjects: list[str] = field(default_factory=list)
    learning_style: LearningStyle = field(default_factory=LearningStyle)
    goals: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    events_processed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "grade": self.grade,
            "subjects": list(self.subjects),
            "learning_style": self.learning_style.to_dict(),
            "goals": list(self.goals),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_active": self.last_active,
            "events_processed": self.events_processed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "StudentProfile":
        d = d or {}
        return cls(
            id=str(d.get("id", "student_default") or "student_default"),
            grade=str(d.get("grade", "本科") or "本科"),
            subjects=[str(s) for s in (d.get("subjects") or [])][:12],
            learning_style=LearningStyle.from_dict(d.get("learning_style")),
            goals=[str(g) for g in (d.get("goals") or [])][:12],
            created_at=float(d.get("created_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            last_active=float(d.get("last_active") or time.time()),
            events_processed=int(d.get("events_processed") or 0),
        )


def cap_list(items: list[str], limit: int = 12) -> list[str]:
    return [str(i) for i in items if i][:limit]
