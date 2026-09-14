"""M2 学生画像（plan §13.2）：只保留身份/学段/可变偏好/活动。

学习评价（掌握度、概念状态、能力结论）的唯一事实源是统一学习评价
journal（`evaluation/`）；本包不再拥有任何评价算法或写入旁路。
"""
from __future__ import annotations

from .manager import StudentModel, get_student_model, is_enabled
from .state import LearningStyle, StudentProfile

__all__ = ["StudentModel", "StudentProfile", "LearningStyle",
           "get_student_model", "is_enabled"]
