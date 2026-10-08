"""Command reducer: LabCommand → (new state, events), fully deterministic.

Evaluation order: normalize → validate → transfer → rules by
priority → phase stepping → projection → procedure/goals → state hash.
Rejections append a ``command_rejected`` event (seq still advances) and
never raise through the session.
"""
from __future__ import annotations

from typing import Any

from . import model as m
from . import phase as phase_mod
from . import projection
from . import reaction
from . import safety

COMMAND_KINDS = {
    "pick_up", "place", "aspirate", "dispense", "pour", "heat", "stir",
    "wait", "measure", "connect", "filter", "wash", "dispose", "checkpoint",
}
TRANSFER_TRIGGERS = {
    "aspirate": "on_aspirate", "dispense": "on_dispense", "pour": "on_pour",
}
MEASURE_QUANTITIES = {"temperature", "ph", "volume", "mass"}
POUR_RATES = {"slow", "normal"}


class _Reject(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _vessel(state: dict[str, Any], vessel_id: str) -> dict[str, Any]:
    vessel = state["vessels"].get(vessel_id)
    if vessel is None:
        raise _Reject("unknown_object")
    return vessel


def _equipment(state: dict[str, Any], equipment_id: str) -> dict[str, Any]:
    equipment = state["equipment"].get(equipment_id)
    if equipment is None:
        raise _Reject("unknown_object")
    return equipment


def _positive(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _Reject("invalid_params")
    return value


def _permille(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (0 < value <= 1000):
        raise _Reject("invalid_params")
    return value


def _free_slot(state: dict[str, Any], slot_id: str) -> None:
    if not any(slot["id"] == slot_id for slot in state["slots"]):
        raise _Reject("unknown_object")
    for vessel in state["vessels"].values():
        if vessel["slot"] == slot_id:
            raise _Reject("invalid_params")
    for equipment in state["equipment"].values():
        if equipment["slot"] == slot_id:
            raise _Reject("invalid_params")


# ---------------------------------------------------------------------------
# Command handlers (each returns the sim duration to advance afterwards)
# ---------------------------------------------------------------------------

def _do_pick_up(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    object_id = str(command.get("object_id", ""))
    if state["held"] is not None:
        raise _Reject("invalid_params")
    if object_id in state["vessels"] or object_id in state["equipment"]:
        state["held"] = object_id
    else:
        raise _Reject("unknown_object")
    events.append(m.make_event(state, "object_picked_up", command_id, data={"object_id": object_id}))
    return 0


def _do_place(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    object_id = str(command.get("object_id", ""))
    slot_id = str(command.get("slot_id", ""))
    if state["held"] != object_id:
        raise _Reject("invalid_params")
    _free_slot(state, slot_id)
    if object_id in state["vessels"]:
        state["vessels"][object_id]["slot"] = slot_id
    else:
        state["equipment"][object_id]["slot"] = slot_id
    state["held"] = None
    events.append(m.make_event(state, "object_placed", command_id,
                               data={"object_id": object_id, "slot_id": slot_id}))
    return 0


def _do_aspirate(state: dict[str, Any], command: dict[str, Any], events, command_id: str,
                 pack: dict[str, Any]) -> int:
    source = _vessel(state, str(command.get("source_id", "")))
    instrument = _equipment(state, str(command.get("instrument_id", "")))
    amount = _positive(command.get("amount_uL"))
    load = instrument["load"]
    if source["volume_uL"] < amount:
        raise _Reject("invalid_params")
    capacity = int(pack["_equipment_defs"].get(instrument["kind"], {}).get("capacity_uL", 0))
    if capacity > 0 and load["volume_uL"] + amount > capacity:
        raise _Reject("capacity_exceeded")
    if load["volume_uL"] > 0 and not load["clean"] and load["source"] != source["id"]:
        raise _Reject("not_clean")
    moved = m.transfer_amounts(source["contents"], amount, source["volume_uL"])
    source["volume_uL"] -= amount
    m.apply_transfer(source["contents"], load["contents"], moved)
    load["volume_uL"] += amount
    load["source"] = source["id"]
    load["clean"] = False
    events.append(m.make_event(state, "reagent_aspirated", command_id,
                               vessel_id=source["id"],
                               data={"instrument_id": instrument["id"], "amount_uL": amount}))
    return m.ACTION_MS


def _do_dispense(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    instrument = _equipment(state, str(command.get("instrument_id", "")))
    target = _vessel(state, str(command.get("target_id", "")))
    amount = _positive(command.get("amount_uL"))
    load = instrument["load"]
    if load["volume_uL"] <= 0:
        raise _Reject("instrument_empty")
    amount = min(amount, load["volume_uL"])
    if target["volume_uL"] + amount > target["capacity_uL"]:
        raise _Reject("capacity_exceeded")
    moved = m.transfer_amounts(load["contents"], amount, load["volume_uL"])
    load["volume_uL"] -= amount
    m.apply_transfer(load["contents"], target["contents"], moved)
    target["volume_uL"] += amount
    if load["volume_uL"] == 0:
        load["source"] = None
        load["clean"] = True
    _mark_contamination(state, target, instrument, events, command_id)
    events.append(m.make_event(state, "reagent_dispensed", command_id,
                               vessel_id=target["id"],
                               data={"instrument_id": instrument["id"], "amount_uL": amount}))
    events.append(m.make_event(state, "volume_changed", command_id,
                               vessel_id=target["id"],
                               data={"volume_uL": target["volume_uL"]}))
    return m.ACTION_MS


def _do_pour(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    source = _vessel(state, str(command.get("source_id", "")))
    target = _vessel(state, str(command.get("target_id", "")))
    amount = _positive(command.get("amount_uL"))
    if command.get("rate", "normal") not in POUR_RATES:
        raise _Reject("invalid_params")
    if source["id"] == target["id"]:
        raise _Reject("invalid_params")
    if source["volume_uL"] < amount:
        raise _Reject("invalid_params")
    if target["volume_uL"] + amount > target["capacity_uL"]:
        raise _Reject("capacity_exceeded")
    moved = m.transfer_amounts(source["contents"], amount, source["volume_uL"])
    source["volume_uL"] -= amount
    m.apply_transfer(source["contents"], target["contents"], moved)
    target["volume_uL"] += amount
    target["mix_permille"] = min(1000, target["mix_permille"] + 120)
    events.append(m.make_event(state, "material_poured", command_id,
                               vessel_id=target["id"],
                               data={"source_id": source["id"], "amount_uL": amount,
                                     "rate": command.get("rate", "normal")}))
    events.append(m.make_event(state, "volume_changed", command_id,
                               vessel_id=target["id"],
                               data={"volume_uL": target["volume_uL"]}))
    return m.ACTION_MS


def _do_heat(state: dict[str, Any], command: dict[str, Any], events, command_id: str,
             pack: dict[str, Any]) -> int:
    device = _equipment(state, str(command.get("device_id", "")))
    vessel = _vessel(state, str(command.get("vessel_id", "")))
    power = _permille(command.get("power_permille"))
    duration = _positive(command.get("duration_ms"))
    if not safety.heat_compatible(state, device["id"], vessel["id"], pack["_equipment_defs"]):
        raise _Reject("incompatible_device")
    rated = int(pack["_equipment_defs"][device["kind"]].get("rated_micro_j_per_step", 0))
    vessel["heat"] = {"device_id": device["id"], "power_permille": power,
                      "rated_micro_j_per_step": rated,
                      "until_ms": state["sim_time_ms"] + duration}
    events.append(m.make_event(state, "heating_started", command_id,
                               vessel_id=vessel["id"],
                               data={"device_id": device["id"], "power_permille": power,
                                     "duration_ms": duration}))
    return duration


def _do_stir(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    vessel = _vessel(state, str(command.get("vessel_id", "")))
    speed = _permille(command.get("speed_permille"))
    duration = _positive(command.get("duration_ms"))
    vessel["stir"] = {"speed_permille": speed, "until_ms": state["sim_time_ms"] + duration}
    events.append(m.make_event(state, "mixing_changed", command_id,
                               vessel_id=vessel["id"],
                               data={"speed_permille": speed, "duration_ms": duration}))
    return duration


def _do_wait(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    return _positive(command.get("duration_ms"))


def _do_measure(state: dict[str, Any], command: dict[str, Any], events, command_id: str,
                pack: dict[str, Any]) -> int:
    instrument_id = str(command.get("instrument_id", ""))
    vessel = _vessel(state, str(command.get("vessel_id", "")))
    quantity = str(command.get("quantity", ""))
    if quantity not in MEASURE_QUANTITIES:
        raise _Reject("invalid_params")
    # Instruments live in equipment; graduated vessels (e.g. the cylinder)
    # may measure themselves.
    if instrument_id in state["equipment"]:
        instrument = state["equipment"][instrument_id]
    elif instrument_id in state["vessels"]:
        instrument = state["vessels"][instrument_id]
    else:
        raise _Reject("unknown_object")
    equipment_def = pack["_equipment_defs"].get(instrument["kind"], {})
    if quantity not in equipment_def.get("measures", []):
        raise _Reject("incompatible_device")
    reading: dict[str, Any] = {"status": "done", "quantity": quantity, "unit": "", "display": ""}
    if quantity == "temperature":
        milli_c = vessel["temperature_milli_c"]
        reading.update({"unit": "°C", "value_milli": milli_c,
                        "display": _format_milli(milli_c, 1)})
    elif quantity == "ph":
        if vessel["ph_milli"] is None:
            raise _Reject("not_modeled")
        reading.update({"unit": "pH", "value_milli": vessel["ph_milli"],
                        "display": _format_milli(vessel["ph_milli"], 2)})
    elif quantity == "volume":
        reading.update({"unit": "mL", "value_milli": vessel["volume_uL"],
                        "display": _format_milli(vessel["volume_uL"], 1)})
    else:  # mass — no mass model in v1 packs
        raise _Reject("not_modeled")
    instrument["reading"] = reading
    events.append(m.make_event(state, "measurement_taken", command_id,
                               vessel_id=vessel["id"],
                               data={"instrument_id": instrument["id"], "quantity": quantity,
                                     "display": reading["display"], "unit": reading["unit"]}))
    # A deliberate reading is a macro-layer observation fact, so procedure
    # steps like "量取并读数" can complete.
    _record_observation(state, pack, events, command_id, vessel["id"], f"measured_{quantity}")
    return m.ACTION_MS


def _do_connect(state: dict[str, Any], command: dict[str, Any], events, command_id: str,
                pack: dict[str, Any]) -> int:
    """Attach tubing ``instrument_id`` from a gas-producing ``vessel_id`` to a
    collection ``target_id`` vessel. Gas phase dynamics read this link."""
    vessel = _vessel(state, str(command.get("vessel_id", "")))
    instrument = _equipment(state, str(command.get("instrument_id", "")))
    target = _vessel(state, str(command.get("target_id", "")))
    if vessel["id"] == target["id"]:
        raise _Reject("invalid_params")
    instrument["connected"] = {"source": vessel["id"], "target": target["id"]}
    events.append(m.make_event(state, "instrument_connected", command_id,
                               vessel_id=vessel["id"],
                               data={"instrument_id": instrument["id"],
                                     "target_id": target["id"]}))
    _record_observation(state, pack, events, command_id, vessel["id"], "tubing_connected")
    return 0


def _do_filter(state: dict[str, Any], command: dict[str, Any], events, command_id: str,
               pack: dict[str, Any]) -> int:
    vessel = _vessel(state, str(command.get("vessel_id", "")))
    apparatus = _vessel(state, str(command.get("apparatus_id", "")))
    if vessel["id"] == apparatus["id"]:
        raise _Reject("invalid_params")
    # Liquid (and dissolved species) passes; solids stay behind on the filter.
    moved_volume = vessel["volume_uL"]
    if apparatus["volume_uL"] + moved_volume > apparatus["capacity_uL"]:
        raise _Reject("capacity_exceeded")
    if moved_volume > 0:
        moved = m.transfer_amounts(vessel["contents"], moved_volume, vessel["volume_uL"])
        vessel["volume_uL"] = 0
        m.apply_transfer(vessel["contents"], apparatus["contents"], moved)
        apparatus["volume_uL"] += moved_volume
    events.append(m.make_event(state, "phase_changed", command_id,
                               vessel_id=apparatus["id"],
                               data={"operation": "filter", "source_id": vessel["id"],
                                     "filtrate_uL": moved_volume,
                                     "residue_umol": dict(vessel["solids"])}))
    _record_observation(state, pack, events, command_id, apparatus["id"], "filtered")
    return m.ACTION_MS


def _do_wash(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    instrument = _equipment(state, str(command.get("instrument_id", "")))
    load = instrument["load"]
    disposed = load["volume_uL"]
    state["environment"]["disposed_volume_uL"] += disposed
    instrument["load"] = {"volume_uL": 0, "contents": {}, "solids": {}, "source": None, "clean": True}
    events.append(m.make_event(state, "instrument_washed", command_id,
                               data={"instrument_id": instrument["id"], "disposed_uL": disposed}))
    return m.ACTION_MS


def _do_dispose(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    vessel = _vessel(state, str(command.get("vessel_id", "")))
    amount = _positive(command.get("amount_uL"))
    if vessel["volume_uL"] < amount:
        raise _Reject("invalid_params")
    moved = m.transfer_amounts(vessel["contents"], amount, vessel["volume_uL"])
    vessel["volume_uL"] -= amount
    for species in sorted(moved):
        m.add_amount(vessel["contents"], species, -moved[species])
        m.add_amount(state["environment"]["escaped_gas_umol"], f"waste:{species}", moved[species])
    state["environment"]["disposed_volume_uL"] += amount
    events.append(m.make_event(state, "volume_changed", command_id,
                               vessel_id=vessel["id"],
                               data={"volume_uL": vessel["volume_uL"], "disposed_uL": amount}))
    return m.ACTION_MS


def _do_checkpoint(state: dict[str, Any], command: dict[str, Any], events, command_id: str) -> int:
    label = str(command.get("label", "")).strip()[:80]
    if not label:
        raise _Reject("invalid_params")
    if len(state["checkpoints"]) >= m.MAX_CHECKPOINTS_PER_SESSION:
        raise _Reject("invalid_params")
    checkpoint = {
        "id": f"cp_{len(state['checkpoints']) + 1}",
        "label": label,
        "revision": state["revision"],
        "seq": state["seq"],
        "sim_time_ms": state["sim_time_ms"],
    }
    state["checkpoints"].append(checkpoint)
    events.append(m.make_event(state, "checkpoint_created", command_id,
                               data={"checkpoint_id": checkpoint["id"], "label": label}))
    return 0


def _mark_contamination(state: dict[str, Any], vessel: dict[str, Any],
                        instrument: dict[str, Any], events, command_id: str) -> None:
    source = instrument["load"].get("source")
    if not source or source == vessel["id"]:
        return
    if source not in vessel["contamination"]:
        vessel["contamination"] = sorted(vessel["contamination"] + [source])
        events.append(m.make_event(state, "contamination_changed", command_id,
                                   vessel_id=vessel["id"],
                                   data={"via": instrument["id"], "source": source}))


def _format_milli(value: int, decimals: int) -> str:
    sign = "-" if value < 0 else ""
    scaled = abs(value)
    if decimals == 1:
        return f"{sign}{scaled // 1000}.{(scaled % 1000) // 100}"
    return f"{sign}{scaled // 1000}.{(scaled % 1000) // 10:02d}"


# ---------------------------------------------------------------------------
# Observations, procedure and goals
# ---------------------------------------------------------------------------

def _visible_scan(vessel: dict[str, Any], frame: dict[str, Any],
                  species_defs: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """Macro-layer visibility facts for one vessel (shared by priming and by
    per-command auto-observations)."""
    seen: dict[str, Any] = {}
    if frame["opacity_permille"] >= 250 and frame["liquid_color_token"] != "clear":
        seen["color_visible"] = frame["liquid_color_token"]
        # Any newly visible token — first appearance or a switch — is a
        # color change (e.g. colorless → pink at the titration endpoint).
        if before.get("color_visible") != frame["liquid_color_token"]:
            seen["color_changed"] = frame["liquid_color_token"]
    precipitate = frame["precipitate"]
    if precipitate and precipitate["amount_permille"] >= 120:
        seen["precipitate_visible"] = True
        if precipitate.get("texture") == "layer":
            seen["sediment_layer"] = True
    if frame["bubbles"]:
        seen["bubbles_visible"] = True
    if frame["steam_permille"] >= 250:
        seen["steam_visible"] = True
    if frame["turbidity_permille"] >= 300:
        seen["turbid_visible"] = True
    # Indicator at working concentration is a macro fact even when its
    # current pH band renders it colorless (e.g. phenolphthalein in acid).
    if vessel["volume_uL"] > 0:
        for species_id in sorted(vessel["contents"]):
            species = species_defs.get(species_id)
            if not species or species.get("role") != "indicator":
                continue
            conc_milli = vessel["contents"][species_id] * 1000 // vessel["volume_uL"]
            threshold = max(1, species.get("visible_conc_umol_per_ul_milli") or 1)
            if conc_milli >= threshold:
                seen["indicator_present"] = True
                break
    if vessel["gases"]:
        seen["gas_collected"] = True
    return seen


def prime_visibility(state: dict[str, Any], pack: dict[str, Any]) -> None:
    """Fill ``state[\"visible\"]`` with the starting layout's appearances so
    they are not reported as \"new\" observations on the first command."""
    species_defs = pack["_species_defs"]
    ph_model = pack.get("ph_model", "none")
    visible: dict[str, Any] = {}
    for vessel_id in sorted(state["vessels"]):
        vessel = state["vessels"][vessel_id]
        vessel["ph_milli"] = projection.compute_ph_milli(vessel, ph_model)
        frame = projection.vessel_frame(state, vessel, species_defs)
        visible[vessel_id] = _visible_scan(vessel, frame, species_defs, {})
    state["visible"] = visible


def _auto_observations(state: dict[str, Any], pack: dict[str, Any],
                       events: list[dict[str, Any]], command_id: str,
                       previous_visible: dict[str, Any]) -> None:
    """Emit observation facts for newly-visible phenomena (macro layer)."""
    species_defs = pack["_species_defs"]
    for vessel_id in sorted(state["vessels"]):
        vessel = state["vessels"][vessel_id]
        frame = projection.vessel_frame(state, vessel, species_defs)
        before = previous_visible.get(vessel_id, {})
        seen = _visible_scan(vessel, frame, species_defs, before)
        # Disappearance facts: a previously visible color/bed that is gone.
        for gone_key, was_key in (("color_faded", "color_visible"),
                                  ("precipitate_dissolved", "precipitate_visible")):
            if before.get(was_key) and was_key not in seen:
                _record_observation(state, pack, events, command_id, vessel_id, gone_key)
        previous_visible[vessel_id] = seen
        for key in sorted(seen):
            if before.get(key) in (None, False) or (key == "color_changed"):
                _record_observation(state, pack, events, command_id, vessel_id, key)


def _record_observation(state: dict[str, Any], pack: dict[str, Any],
                        events: list[dict[str, Any]], command_id: str,
                        vessel_id: str, key: str, rule_id: str = "") -> None:
    event = m.make_event(state, "observation_emitted", command_id,
                         vessel_id=vessel_id, rule_id=rule_id, data={"key": key})
    events.append(event)
    concept_ids: list[str] = []
    for step in pack.get("procedure", []):
        if key in step.get("expected_observations", []):
            concept_ids = list(step.get("concept_ids", []))
            break
    state["observations"].append({
        "seq": event["seq"], "key": key, "vessel_id": vessel_id,
        "rule_id": rule_id, "sim_time_ms": state["sim_time_ms"],
        "concept_ids": concept_ids,
    })


def _rule_observations(state: dict[str, Any], pack: dict[str, Any],
                       events: list[dict[str, Any]], command_id: str) -> None:
    """Convert rule-emitted observation events into observation facts."""
    for event in events:
        if event["kind"] == "observation_emitted" and event["data"].get("key"):
            key = event["data"]["key"]
            if not any(o["key"] == key and o["seq"] == event["seq"] for o in state["observations"]):
                state["observations"].append({
                    "seq": event["seq"], "key": key, "vessel_id": event["vessel_id"],
                    "rule_id": event["rule_id"], "sim_time_ms": event["sim_time_ms"],
                    "concept_ids": [],
                })


def _update_procedure(state: dict[str, Any], pack: dict[str, Any],
                      events: list[dict[str, Any]], command_id: str) -> None:
    """Sequential step completion: a step's expected observations must be
    emitted *after* the previous step completed, so measuring the same
    quantity twice can gate two different steps."""
    completion = state["step_completion_seq"]
    cursor = 0
    for step in pack.get("procedure", []):
        if step["id"] in state["completed_steps"]:
            cursor = completion.get(step["id"], cursor)
            continue
        expected = step.get("expected_observations", [])
        marked = any(e["kind"] == "procedure_step_completed"
                     and e["data"].get("step_id") == step["id"] for e in events)
        fresh_keys = {o["key"] for o in state["observations"] if o["seq"] > cursor}
        if marked or (expected and all(key in fresh_keys for key in expected)):
            state["completed_steps"].append(step["id"])
            completion[step["id"]] = state["seq"]
            cursor = state["seq"]
            if not marked:
                events.append(m.make_event(state, "procedure_step_completed", command_id,
                                           data={"step_id": step["id"]}))
            continue
        # Strictly sequential: later steps wait for this one.
        break
    step = None
    for candidate in pack.get("procedure", []):
        if candidate["id"] not in state["completed_steps"]:
            step = candidate
            break
    if step is not None:
        state["step_visits"][step["id"]] = state["step_visits"].get(step["id"], 0) + 1
    else:
        state["step_visits"] = {}


def _update_goals(state: dict[str, Any], pack: dict[str, Any]) -> None:
    status = projection_goals(pack, state)
    state["goals"] = [{"id": g["id"], "status": g["status"]} for g in status]
    if status and all(g["status"] == "met" for g in status) and state["phase"] == "running":
        state["phase"] = "completed"


def projection_goals(pack: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    emitted = {o["key"] for o in state["observations"]}
    status = []
    for goal in pack.get("goals", []):
        met = all(key in emitted for key in goal["requires_observations"])
        status.append({"id": goal["id"], "status": "met" if met else "pending"})
    return status


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def apply_command(state: dict[str, Any], pack: dict[str, Any],
                  command: dict[str, Any], command_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], bool, str]:
    """Apply one LabCommand. Returns (state, events, accepted, error_code)."""
    events: list[dict[str, Any]] = []
    kind = str(command.get("kind", ""))
    if kind not in COMMAND_KINDS:
        return _reject(state, events, command_id, "invalid_params", kind)
    if state["phase"] == "safety_locked" and kind not in {"checkpoint"}:
        return _reject(state, events, command_id, "safety_locked", kind)
    if state["phase"] in {"setup", "completed"} and kind not in {"checkpoint", "measure", "wait"}:
        if state["phase"] == "completed" and kind in {"pick_up", "place", "measure", "wait", "checkpoint"}:
            pass  # read-only inspection stays possible after completion
        elif state["phase"] == "setup":
            return _reject(state, events, command_id, "phase", kind)

    if state["phase"] == "ready":
        state["phase"] = "running"

    previous_visible = state["visible"]
    duration = 0
    trigger = TRANSFER_TRIGGERS.get(kind, f"on_{kind}")
    try:
        if kind == "pick_up":
            duration = _do_pick_up(state, command, events, command_id)
        elif kind == "place":
            duration = _do_place(state, command, events, command_id)
        elif kind == "aspirate":
            duration = _do_aspirate(state, command, events, command_id, pack)
        elif kind == "dispense":
            duration = _do_dispense(state, command, events, command_id)
        elif kind == "pour":
            duration = _do_pour(state, command, events, command_id)
        elif kind == "heat":
            duration = _do_heat(state, command, events, command_id, pack)
        elif kind == "stir":
            duration = _do_stir(state, command, events, command_id)
        elif kind == "wait":
            duration = _do_wait(state, command, events, command_id)
        elif kind == "measure":
            duration = _do_measure(state, command, events, command_id, pack)
        elif kind == "connect":
            duration = _do_connect(state, command, events, command_id, pack)
        elif kind == "filter":
            duration = _do_filter(state, command, events, command_id, pack)
        elif kind == "wash":
            duration = _do_wash(state, command, events, command_id)
        elif kind == "dispose":
            duration = _do_dispose(state, command, events, command_id)
        elif kind == "checkpoint":
            duration = _do_checkpoint(state, command, events, command_id)
    except _Reject as exc:
        return _reject(state, events, command_id, exc.reason, kind)

    ordered = reaction.ordered_rules(pack.get("_rules", []))
    target_id = str(command.get("target_id") or command.get("vessel_id")
                    or command.get("source_id") or "")
    if target_id and target_id in state["vessels"] and kind in TRANSFER_TRIGGERS:
        reaction.run_rules(state, ordered, target_id, trigger, events, command_id)

    if duration > 0:
        phase_mod.advance(state, duration, events, command_id,
                          pack["_species_defs"], pack.get("_rules", []),
                          "on_wait" if kind == "wait" else trigger, pack)

    _rule_observations(state, pack, events, command_id)
    for vessel_id in sorted(state["vessels"]):
        vessel = state["vessels"][vessel_id]
        vessel["ph_milli"] = projection.compute_ph_milli(vessel, pack.get("ph_model", "none"))
    _auto_observations(state, pack, events, command_id, previous_visible)
    safety.evaluate(state, pack, events, command_id)
    _update_procedure(state, pack, events, command_id)
    _update_goals(state, pack)

    state["revision"] += 1
    state["_recent_events"] = events[-8:]
    state["state_hash"] = m.state_hash(state)
    return state, events, True, ""


def _reject(state: dict[str, Any], events: list[dict[str, Any]],
            command_id: str, reason: str, kind: str) -> tuple[dict[str, Any], list[dict[str, Any]], bool, str]:
    events.append(m.make_event(state, "command_rejected", command_id,
                               data={"reason": reason, "command_kind": kind}))
    state["revision"] += 1
    state["_recent_events"] = events[-8:]
    state["state_hash"] = m.state_hash(state)
    return state, events, False, f"chem_lab_{reason}" if not reason.startswith("chem_lab_") else reason
