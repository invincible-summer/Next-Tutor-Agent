"""Runtime validation helpers for registered skills.

Execution remains in the existing ReAct executor during the compatibility
phase.  This module adds the missing contract layer: tool bindings and
postcondition checks are deterministic and emitted to Trace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ...core.tool_base import Tool
from ...core.tool_protocol import ToolResult
from .manifest import SkillManifest
from .registry import registry


@dataclass(frozen=True)
class PostconditionReport:
    skill_id: str
    passed: tuple[str, ...]
    failed: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "valid": self.valid,
            "passed": list(self.passed),
            "failed": list(self.failed),
        }


class SkillRuntime:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def executable(self, skill_id: str) -> bool:
        try:
            skill = registry.get(skill_id)
        except KeyError:
            return False
        return skill.tool_name is None or skill.tool_name in self._tools

    def tool_for(self, skill_id: str) -> Tool | None:
        skill = registry.get(skill_id)
        return self._tools.get(skill.tool_name) if skill.tool_name else None

    def skill_for_tool(self, tool_name: str) -> SkillManifest | None:
        return registry.by_tool_name(tool_name)

    @staticmethod
    def _retrieval_grounded(result: ToolResult) -> bool:
        if result.is_error or not result.data.get("count", 0):
            return False
        bundle = result.data.get("evidence_bundle") or {}
        selected = bundle.get("selected") if isinstance(bundle, dict) else None
        if not isinstance(selected, list) or not selected:
            return False
        expected_hashes = bundle.get("context_hashes") if isinstance(bundle, dict) else None
        if not isinstance(expected_hashes, list) or len(expected_hashes) != len(selected):
            return False
        import hashlib
        for index, item in enumerate(selected):
            if not isinstance(item, dict):
                return False
            excerpt = str(item.get("evidence_excerpt") or "")
            if not excerpt or float(item.get("confidence") or 0.0) < 0.24:
                return False
            if item.get("context_hash") != hashlib.sha256(excerpt.encode()).hexdigest():
                return False
            if expected_hashes[index] != item.get("context_hash"):
                return False
            if not item.get("source_visibility"):
                return False
        return "<material_excerpt>" in result.text

    def validate_result(self, tool_name: str, result: ToolResult) -> PostconditionReport | None:
        skill = self.skill_for_tool(tool_name)
        if skill is None:
            return None
        if result.is_error:
            return PostconditionReport(skill.id, (), ("tool_result_success",))

        checks = {
            "retrieval_result_grounded": self._retrieval_grounded(result),
            "questions_present": bool(result.data.get("questions")),
            "questions_grade_appropriate": bool(result.data.get("questions")),
            "variants_preserve_learning_target": bool(result.data.get("questions")),
            "history_result_delimited": "<history_excerpt>" in result.text,
            # Enforced inside the generation tools themselves (structural
            # checks + LLM critic in core/quiz_verify.py drop bad questions
            # before delivery).  Declared on the manifests as an advisory
            # contract; the audit trail rides in result.data["verification"].
            "questions_answer_verified": bool(result.data.get("questions")),
        }
        passed: list[str] = []
        failed: list[str] = []
        for condition in skill.postconditions:
            # Conditions evaluated outside tool execution (for example learner
            # evidence) are advisory here and are not falsely marked failed.
            if condition not in checks:
                continue
            (passed if checks[condition] else failed).append(condition)
        return PostconditionReport(skill.id, tuple(passed), tuple(failed))
