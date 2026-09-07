"""Supervisor router: capability -> tool-subset mapping.

The Supervisor plans in terms of *capabilities* (knowledge / teaching /
assessment / memory), not concrete agents. The router turns a plan step's
`agent_role` into the actual set of tools the executor (ReAct loop) is
allowed to see for that step -- this is how "explicit routing" is realized
on top of the existing function-calling mechanism (we narrow tool_schemas
rather than invent a new dispatch).

Capabilities are advisory: `teaching` exposes no tools (the LLM explains
from its own knowledge). If the plan is empty/fallback we return the full
tool set so behavior degrades gracefully to V1.
"""
from __future__ import annotations

from typing import Any

from ..core.tool_base import Tool
from .state import VALID_AGENT_ROLES, TaskPlan
from .skill_runtime.registry import (capability_tool_map, registry,
                                     tool_names_for_skills)

# Compatibility projection of the M10 Skill Registry.  Existing callers still
# reason in four broad roles, while the registry is now the single source of
# truth for which executable tools each role exposes.
CAPABILITIES: dict[str, set[str]] = capability_tool_map()

# Tools safe to keep alongside any capability (always available). Empty for
# now: recall_history only surfaces when the plan calls for it, matching V1's
# "don't call unless summary insufficient" policy.
_ALWAYS_AVAILABLE: set[str] = set()


def tools_for_role(role: str) -> set[str]:
    """Names of tools that fulfill a capability role. Unknown role -> empty."""
    # Re-project on each call so newly registered active Skill versions do not
    # require re-importing this module. CAPABILITIES remains a compatibility
    # snapshot for callers/tests that inspect the constant directly.
    return capability_tool_map().get(role, set())


def route(plan: TaskPlan | None, all_tools: list[Tool], *, step_index: int = 0) -> list[Tool]:
    """Return the tool subset the executor may use for the current plan step.

    - No plan / empty plan / step out of range -> full tool set (V1 behavior).
    - Otherwise the current step's role tools + always-available. If that is
      empty (e.g. a pure teaching step) we return [] so the LLM explains
      without tools, exactly what a teaching step wants.
    """
    if plan is None or plan.is_empty or not 0 <= step_index < len(plan.steps):
        return list(all_tools)
    step = plan.steps[step_index]
    # suggested_tools (when given) tightens the capability set for this step;
    # an empty list means "use the whole capability for this role".
    if step.skill_ids:
        allowed = tool_names_for_skills(step.skill_ids) | _ALWAYS_AVAILABLE
    elif step.suggested_tools:
        allowed = set(step.suggested_tools) | _ALWAYS_AVAILABLE
    else:
        allowed = tools_for_role(step.agent_role) | _ALWAYS_AVAILABLE
    return [t for t in all_tools if t.name in allowed]


def route_full_plan(plan: TaskPlan | None, all_tools: list[Tool]) -> list[Tool]:
    """Union of all tools referenced anywhere in the plan.

    Used when the executor runs the whole plan in one ReAct loop (single
    visible set for the turn), as opposed to per-step narrowing.
    """
    if plan is None or plan.is_empty:
        return list(all_tools)
    allowed = set(_ALWAYS_AVAILABLE)
    for step in plan.steps:
        if step.skill_ids:
            allowed |= tool_names_for_skills(step.skill_ids)
        elif step.suggested_tools:
            allowed |= set(step.suggested_tools)
        else:
            allowed |= tools_for_role(step.agent_role)
    return [t for t in all_tools if t.name in allowed]


def describe_capabilities() -> list[dict[str, Any]]:
    """Human/LLM-readable capability list for the planner prompt."""
    capabilities = capability_tool_map()
    return [
        {
            "role": role,
            "tools": sorted(capabilities[role]),
            "skills": [s.to_card() for s in registry.for_role(role)],
        }
        for role in VALID_AGENT_ROLES
    ]
