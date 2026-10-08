"""Rule matching and stoichiometry for the chem-lab engine.

Mirrored by ``packages/domain/src/chem-lab/reaction.ts``. All arithmetic is
integer with floor semantics. Rules fire in a fixed order (priority desc,
rule id asc); each firing of ``consume_min_ratio`` consumes the limiting
reagent times the declared ratio, optionally capped per firing.
"""
from __future__ import annotations

from typing import Any

from . import model as m


def ordered_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rules, key=lambda rule: (-rule["priority"], rule["id"]))


def _vessel(state: dict[str, Any], scope: str, target_id: str) -> dict[str, Any]:
    # v1 rules only address the command's target vessel.
    return state["vessels"][target_id]


def _condition_holds(state: dict[str, Any], condition: dict[str, Any], target_id: str) -> bool:
    vessel = _vessel(state, condition.get("vessel", "$target"), target_id)
    op = condition["op"]
    if op == "species_at_least":
        species = condition["species"]
        pool = vessel["contents"].get(species, 0) + vessel["solids"].get(species, 0)
        return pool >= condition["amount_umol"]
    if op == "volume_between":
        return condition["min_value"] < vessel["volume_uL"] < condition["max_value"]
    if op == "temperature_between":
        return condition["min_value"] < vessel["temperature_milli_c"] < condition["max_value"]
    if op == "ph_between":
        ph = vessel["ph_milli"]
        return ph is not None and condition["min_value"] < ph < condition["max_value"]
    if op == "mix_at_least":
        return vessel["mix_permille"] >= condition["min_value"]
    if op == "vessel_connected":
        for equipment in state["equipment"].values():
            connected = equipment.get("connected")
            if connected and vessel["id"] in (connected.get("source"), connected.get("target")):
                return True
        return False
    if op == "equipment_state":
        equipment = state["equipment"].get(condition["target"])
        if equipment is None:
            return False
        expected = condition["expected"]
        if expected == "loaded":
            return equipment["load"]["volume_uL"] > 0 or bool(equipment["load"]["contents"])
        if expected == "clean":
            return bool(equipment["load"]["clean"])
        if expected == "connected":
            return equipment.get("connected") is not None
        reading = equipment.get("reading") or {}
        return reading.get("status", "idle") == expected
    return False


def conditions_hold(state: dict[str, Any], rule: dict[str, Any], target_id: str) -> bool:
    return all(_condition_holds(state, condition, target_id) for condition in rule["when"])


def _consume_min_ratio(vessel: dict[str, Any], pairs: list[list[Any]], cap_umol: int) -> int:
    """Consume species in stoichiometric ratio; returns the base extent (umol).

    The extent is the limiting pool // its ratio (capped when the rule sets
    ``cap_umol`` — finite-rate reactions). Pools look at dissolved contents
    first, then solids, so e.g. undissolved NaHCO3 can react as it
    dissolves.
    """
    extent: int | None = None
    for species, ratio in pairs:
        pool = vessel["contents"].get(species, 0) + vessel["solids"].get(species, 0)
        available = pool // int(ratio)
        extent = available if extent is None else min(extent, available)
    if extent is None:
        return 0
    if cap_umol > 0:
        extent = min(extent, cap_umol)
    if extent <= 0:
        return 0
    for species, ratio in pairs:
        remaining = extent * int(ratio)
        from_contents = min(vessel["contents"].get(species, 0), remaining)
        if from_contents:
            m.add_amount(vessel["contents"], species, -from_contents)
            remaining -= from_contents
        if remaining:
            m.add_amount(vessel["solids"], species, -remaining)
    return extent


def fire_rule(state: dict[str, Any], rule: dict[str, Any], target_id: str,
              events: list[dict[str, Any]], command_id: str) -> bool:
    """Apply one firing of ``rule`` on the target vessel. Returns False when
    the rule could not fire (conditions unmet or nothing to consume)."""
    if not conditions_hold(state, rule, target_id):
        return False
    vessel = state["vessels"][target_id]
    extent = 0
    consumed_map: dict[str, int] = {}
    for effect in rule["then"]:
        if effect["op"] == "consume_min_ratio":
            pairs = [[row[0], int(row[1])] for row in effect["pairs"]]
            extent = _consume_min_ratio(vessel, pairs, int(effect.get("cap_umol", 0)))
            if extent <= 0:
                return False
            for species, ratio in pairs:
                consumed_map[species] = extent * int(ratio)
    fired_chemistry = False
    for effect in rule["then"]:
        op = effect["op"]
        if op == "consume_min_ratio":
            fired_chemistry = True
            for species in sorted(consumed_map):
                events.append(m.make_event(state, "species_consumed", command_id,
                                           vessel_id=target_id, rule_id=rule["id"],
                                           data={"species": species, "amount_umol": consumed_map[species]}))
        elif op == "produce":
            species = effect["species"]
            if effect.get("from_consumed"):
                amount = extent * int(effect.get("ratio_to_consumed", 1000)) // 1000
            else:
                amount = int(effect.get("amount_umol", 0))
            if amount > 0:
                m.add_amount(vessel["contents"], species, amount)
                m.add_amount(vessel["contents"], "water", 0)  # no-op keep key stable
                fired_chemistry = True
                events.append(m.make_event(state, "species_produced", command_id,
                                           vessel_id=target_id, rule_id=rule["id"],
                                           data={"species": species, "amount_umol": amount}))
        elif op == "transfer_to_solid":
            species = effect["species"]
            amount = extent * int(effect.get("ratio_to_consumed", 1000)) // 1000
            if amount > 0:
                m.add_amount(vessel["contents"], species, -min(amount, vessel["contents"].get(species, 0)))
                m.add_amount(vessel["solids"], species, amount)
                vessel["settled_permille"] = min(vessel["settled_permille"], 200)
                fired_chemistry = True
                events.append(m.make_event(state, "precipitate_formed", command_id,
                                           vessel_id=target_id, rule_id=rule["id"],
                                           data={"species": species, "amount_umol": amount}))
        elif op == "transfer_to_gas":
            species = effect["species"]
            amount = extent * int(effect.get("ratio_to_consumed", 1000)) // 1000
            if amount <= 0 and effect.get("amount_umol"):
                amount = min(int(effect["amount_umol"]), vessel["contents"].get(species, 0))
            if amount > 0:
                m.add_amount(vessel["contents"], species, -min(amount, vessel["contents"].get(species, 0)))
                m.add_amount(vessel["gases"], species, amount)
                vessel["gas_rate_permille"] = min(1000, vessel["gas_rate_permille"] + 400)
                fired_chemistry = True
                events.append(m.make_event(state, "gas_released", command_id,
                                           vessel_id=target_id, rule_id=rule["id"],
                                           data={"species": species, "amount_umol": amount}))
        elif op == "emit_temperature":
            micro_j = extent * int(effect["micro_j_per_umol"])
            delta = m.temperature_delta_milli_c(vessel, micro_j)
            if delta > 0:
                vessel["temperature_milli_c"] += delta
                events.append(m.make_event(state, "temperature_changed", command_id,
                                           vessel_id=target_id, rule_id=rule["id"],
                                           data={"delta_milli_c": delta, "cause": "reaction"}))
        elif op == "mark_observation":
            events.append(m.make_event(state, "observation_emitted", command_id,
                                       vessel_id=target_id, rule_id=rule["id"],
                                       data={"key": effect["key"]}))
        elif op == "mark_step":
            events.append(m.make_event(state, "procedure_step_completed", command_id,
                                       vessel_id=target_id, rule_id=rule["id"],
                                       data={"step_id": effect["key"]}))
    return fired_chemistry


def run_rules(state: dict[str, Any], rules: list[dict[str, Any]], target_id: str,
              trigger: str, events: list[dict[str, Any]], command_id: str) -> None:
    """Fixed-order rule evaluation with per-rule and global firing caps."""
    budget = m.MAX_RULE_FIRINGS_PER_COMMAND
    for rule in rules:
        if budget <= 0:
            events.append(m.make_event(state, "rule_limit_reached", command_id,
                                       vessel_id=target_id, rule_id="",
                                       data={"scope": "command"}))
            return
        if trigger not in rule["triggers"]:
            continue
        limit = int(rule["limits"]["max_firings_per_evaluation"])
        firings = 0
        while firings < limit and budget > 0:
            if not fire_rule(state, rule, target_id, events, command_id):
                break
            firings += 1
            budget -= 1
        if firings >= limit and not any(
                e["kind"] == "rule_limit_reached" and e["rule_id"] == rule["id"] for e in events):
            # At most one rule-scope limit notice per rule per command, so a
            # rate-capped reaction does not flood the event feed every step.
            events.append(m.make_event(state, "rule_limit_reached", command_id,
                                       vessel_id=target_id, rule_id=rule["id"],
                                       data={"scope": "rule"}))
        if firings:
            key = rule["id"]
            state["rule_firings"][key] = state["rule_firings"].get(key, 0) + firings
