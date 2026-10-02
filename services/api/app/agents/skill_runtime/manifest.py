"""Typed contracts for executable Agent skills.

Agent skills are deliberately separate from the learner-facing knowledge
``skill_id`` values used by M2/M5.  A manifest describes an executable
capability: when it is useful, what it needs, what it may change, and how a
runtime can validate or safely fall back from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillKind(str, Enum):
    ATOMIC = "atomic"
    COMPOSITE = "composite"
    ADVISORY = "advisory"
    VALIDATOR = "validator"


_VALID_ROLES = {"knowledge", "teaching", "assessment", "memory"}
_VALID_RISKS = {"low", "medium", "high"}
_VALID_COSTS = {"low", "medium", "high"}


@dataclass(frozen=True)
class SkillManifest:
    id: str
    version: str
    role: str
    kind: SkillKind
    display_name: str
    description: str
    intents: tuple[str, ...] = ()
    tool_name: str | None = None
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    fallback_skills: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    risk_level: str = "low"
    cost_level: str = "low"
    prompt_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.id.startswith("agent.skill."):
            raise ValueError(f"Skill id 必须以 agent.skill. 开头: {self.id}")
        if not self.version or any(ch.isspace() for ch in self.version):
            raise ValueError(f"Skill version 非法: {self.id}@{self.version!r}")
        if self.role not in _VALID_ROLES:
            raise ValueError(f"Skill role 非法: {self.id} -> {self.role}")
        if not self.display_name.strip() or not self.description.strip():
            raise ValueError(f"Skill 名称/描述不能为空: {self.id}")
        if self.risk_level not in _VALID_RISKS:
            raise ValueError(f"Skill risk_level 非法: {self.id} -> {self.risk_level}")
        if self.cost_level not in _VALID_COSTS:
            raise ValueError(f"Skill cost_level 非法: {self.id} -> {self.cost_level}")
        if self.kind == SkillKind.ATOMIC and not self.tool_name:
            raise ValueError(f"Atomic Skill 必须绑定 tool_name: {self.id}")
        if self.tool_name and not self.tool_name.replace("_", "").isalnum():
            raise ValueError(f"Skill tool_name 非法: {self.id} -> {self.tool_name}")

    def to_card(self) -> dict[str, Any]:
        """Compact, prompt-safe projection. No implementation details/secrets."""
        return {
            "id": self.id,
            "version": self.version,
            "role": self.role,
            "kind": self.kind.value,
            "name": self.display_name,
            "description": self.description,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "risk": self.risk_level,
            "cost": self.cost_level,
        }
