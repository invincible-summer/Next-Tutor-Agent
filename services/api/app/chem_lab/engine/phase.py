"""Discrete-time phase dynamics: heat, mixing, settling, gas, solubility.

All processes advance in fixed ``DT_MS`` steps with integer math only, so
the TS mirror reproduces every step bit-for-bit. Rates are deliberately
simple permille-of-remainder models — teaching-semi-quantitative, not
molecular dynamics.
"""
from __future__ import annotations

from typing import Any

from . import model as m
from . import reaction
from . import safety


def _heat_step(state: dict[str, Any], vessel: dict[str, Any], events: list[dict[str, Any]],
               command_id: str) -> None:
    heat = vessel.get("heat")
    if heat and heat["power_permille"] > 0 and state["sim_time_ms"] < heat["until_ms"]:
        # Heater dumps power_permille of its rated microjoules per step.
        micro_j = heat["rated_micro_j_per_step"] * heat["power_permille"] // 1000
        delta = m.temperature_delta_milli_c(vessel, micro_j)
        if delta > 0:
            vessel["temperature_milli_c"] += delta
    drop = m.cooling_drop_milli_c(vessel, state["environment"]["room_temperature_milli_c"])
    if drop > 0:
        vessel["temperature_milli_c"] -= drop


def _mix_step(state: dict[str, Any], vessel: dict[str, Any]) -> None:
    # Stirring raises mix toward 1000; otherwise it decays toward 0.
    stir = vessel.get("stir")
    if stir and stir["speed_permille"] > 0 and state["sim_time_ms"] < stir["until_ms"]:
        gain = (1000 - vessel["mix_permille"]) * (200 + stir["speed_permille"] * 400 // 1000) // 1000
        vessel["mix_permille"] = min(1000, vessel["mix_permille"] + gain)
    else:
        vessel["mix_permille"] -= vessel["mix_permille"] * 20 // 1000


def _settle_step(state: dict[str, Any], vessel: dict[str, Any]) -> None:
    if not vessel["solids"]:
        vessel["settled_permille"] = 0
        return
    if vessel["mix_permille"] >= 500:
        # Vigorous stirring resuspends the bed.
        vessel["settled_permille"] = max(0, vessel["settled_permille"] - 60)
    else:
        vessel["settled_permille"] = min(1000, vessel["settled_permille"] + 30)


def _gas_step(state: dict[str, Any], vessel: dict[str, Any], events: list[dict[str, Any]],
              command_id: str, species_defs: dict[str, Any]) -> None:
    gases = vessel["gases"]
    if not gases:
        vessel["gas_rate_permille"] = max(0, vessel["gas_rate_permille"] - 80)
        return
    vessel["gas_rate_permille"] = max(200, vessel["gas_rate_permille"] - 40)
    if vessel.get("collects_gas"):
        # Inverted collection cylinder: gas accumulates instead of venting.
        return
    outlet = _gas_outlet(state, vessel["id"])
    for species in sorted(gases):
        amount = gases.get(species, 0)
        if amount <= 0:
            continue
        moved = max(1, amount * 250 // 1000) if amount > 1 else amount
        if outlet is not None:
            target = state["vessels"].get(outlet)
            if target is None:
                outlet = None
        if outlet is not None:
            m.add_amount(gases, species, -moved)
            m.add_amount(state["vessels"][outlet]["gases"], species, moved)
            events.append(m.make_event(state, "gas_released", command_id,
                                       vessel_id=vessel["id"], rule_id="",
                                       data={"species": species, "amount_umol": moved,
                                             "destination": outlet}))
        else:
            m.add_amount(gases, species, -moved)
            escaped = state["environment"]["escaped_gas_umol"]
            m.add_amount(escaped, species, moved)
            events.append(m.make_event(state, "gas_released", command_id,
                                       vessel_id=vessel["id"], rule_id="",
                                       data={"species": species, "amount_umol": moved,
                                             "destination": "atmosphere"}))


def _gas_outlet(state: dict[str, Any], vessel_id: str) -> str | None:
    for equipment in state["equipment"].values():
        connected = equipment.get("connected")
        if connected and connected.get("source") == vessel_id:
            return connected.get("target")
    return None


def _dissolve_step(state: dict[str, Any], vessel: dict[str, Any],
                   events: list[dict[str, Any]], command_id: str,
                   species_defs: dict[str, Any]) -> None:
    """Solubility-table equilibrium with finite rate, both directions."""
    volume = vessel["volume_uL"]
    if volume <= 0:
        return
    for species_id in sorted(set(vessel["solids"]) | set(vessel["contents"])):
        species = species_defs.get(species_id)
        if species is None or not species.get("solubility_table"):
            continue
        limit_umol_per_ul_milli = m.interpolate_table(
            [[p["temperature_milli_c"], p["solubility_umol_per_ul_milli"]]
             for p in species["solubility_table"]],
            vessel["temperature_milli_c"])
        capacity_umol = limit_umol_per_ul_milli * volume // 1000
        dissolved = vessel["contents"].get(species_id, 0)
        rate = 60 + vessel["mix_permille"] * 240 // 1000  # permille of deficit per step
        if dissolved < capacity_umol and vessel["solids"].get(species_id, 0) > 0:
            deficit = capacity_umol - dissolved
            moved = min(vessel["solids"][species_id], max(1, deficit * rate // 1000))
            m.add_amount(vessel["solids"], species_id, -moved)
            m.add_amount(vessel["contents"], species_id, moved)
            events.append(m.make_event(state, "phase_changed", command_id,
                                       vessel_id=vessel["id"], rule_id="",
                                       data={"species": species_id, "amount_umol": moved,
                                             "direction": "dissolve"}))
        elif dissolved > capacity_umol:
            excess = dissolved - capacity_umol
            moved = max(1, excess * rate // 1000)
            m.add_amount(vessel["contents"], species_id, -moved)
            m.add_amount(vessel["solids"], species_id, moved)
            events.append(m.make_event(state, "crystal_formed", command_id,
                                       vessel_id=vessel["id"], rule_id="",
                                       data={"species": species_id, "amount_umol": moved}))


def advance(state: dict[str, Any], duration_ms: int, events: list[dict[str, Any]],
            command_id: str, species_defs: dict[str, Any], rules: list[dict[str, Any]],
            trigger: str, pack: dict[str, Any]) -> None:
    """Advance every vessel by ``duration_ms`` in DT steps, re-firing rules
    after each step so rate-capped reactions keep pace with time. Safety is
    evaluated per step so a warming vessel crosses the warning band before
    the lock band, not both at once."""
    steps = duration_ms // m.DT_MS
    if steps <= 0:
        return
    ordered = reaction.ordered_rules(rules)
    for _ in range(steps):
        state["sim_time_ms"] += m.DT_MS
        for vessel_id in sorted(state["vessels"]):
            vessel = state["vessels"][vessel_id]
            _heat_step(state, vessel, events, command_id)
            _mix_step(state, vessel)
        for vessel_id in sorted(state["vessels"]):
            vessel = state["vessels"][vessel_id]
            _settle_step(state, vessel)
            _gas_step(state, vessel, events, command_id, species_defs)
            _dissolve_step(state, vessel, events, command_id, species_defs)
        for vessel_id in sorted(state["vessels"]):
            reaction.run_rules(state, ordered, vessel_id, trigger, events, command_id)
        safety.evaluate(state, pack, events, command_id)
