"""Virtual safety envelope: capacity, contamination, temperature, devices.

Safety outcomes are deterministic state results (warning events or a
session lock), never uncaught exceptions. A lock can only be cleared by
viewing the reason and branching/resetting from a checkpoint.
"""
from __future__ import annotations

from typing import Any

from . import model as m


def check_capacity(vessel: dict[str, Any]) -> str | None:
    if vessel["volume_uL"] > vessel["capacity_uL"]:
        return "overflow"
    return None


def check_temperature(vessel: dict[str, Any], profile: dict[str, Any]) -> str | None:
    if vessel["volume_uL"] <= 0:
        return None
    if vessel["temperature_milli_c"] >= profile["lock_temperature_milli_c"]:
        return "lock_temperature"
    if vessel["temperature_milli_c"] >= profile["warn_temperature_milli_c"]:
        return "warn_temperature"
    return None


def heat_compatible(state: dict[str, Any], device_id: str, vessel_id: str,
                    equipment_defs: dict[str, Any]) -> bool:
    device = state["equipment"].get(device_id)
    vessel = state["vessels"].get(vessel_id)
    if device is None or vessel is None:
        return False
    device_def = equipment_defs.get(device["kind"], {})
    vessel_def = equipment_defs.get(vessel["kind"], {})
    return bool(device_def.get("rated_micro_j_per_step")) and bool(vessel_def.get("heat_compatible"))


def evaluate(state: dict[str, Any], pack: dict[str, Any], events: list[dict[str, Any]],
             command_id: str) -> None:
    """Scan all vessels after a command; emit warnings or lock the session."""
    if state["phase"] == "safety_locked":
        return
    profile = pack["safety_profile"]
    locked_reason: str | None = None
    for vessel_id in sorted(state["vessels"]):
        vessel = state["vessels"][vessel_id]
        issue = check_temperature(vessel, profile)
        if issue == "lock_temperature":
            locked_reason = "lock_temperature"
            events.append(m.make_event(state, "safety_locked", command_id,
                                       vessel_id=vessel_id, rule_id="",
                                       data={"reason": issue,
                                             "temperature_milli_c": vessel["temperature_milli_c"]}))
        elif issue == "warn_temperature":
            already = any(e["kind"] == "safety_warning" and e["vessel_id"] == vessel_id
                          and e["data"].get("reason") == issue for e in events)
            if not already:
                events.append(m.make_event(state, "safety_warning", command_id,
                                           vessel_id=vessel_id, rule_id="",
                                           data={"reason": issue,
                                                 "temperature_milli_c": vessel["temperature_milli_c"]}))
    if locked_reason:
        state["phase"] = "safety_locked"
        state["safety"] = {"reason": locked_reason, "profile": profile["id"]}
