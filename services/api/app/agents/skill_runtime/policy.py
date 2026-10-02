"""Deterministic Skill precondition policy.

The policy is intentionally small and auditable.  LLMs may propose a skill,
but only this layer decides whether its declared preconditions are satisfied.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .manifest import SkillManifest


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    passed: tuple[str, ...]
    failed: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "passed": list(self.passed), "failed": list(self.failed)}


def evaluate_preconditions(skill: SkillManifest, context: dict[str, Any]) -> PolicyResult:
    checks = {
        "materials_available": bool(context.get("has_materials")),
        "reference_question_available": bool(context.get("has_reference_question")),
        "history_available": bool(context.get("has_history")),
        "grade_available": bool(context.get("grade")),
        "concept_available": bool(context.get("concept")),
    }
    passed: list[str] = []
    failed: list[str] = []
    for name in skill.preconditions:
        if checks.get(name, False):
            passed.append(name)
        else:
            failed.append(name)
    return PolicyResult(not failed, tuple(passed), tuple(failed))
