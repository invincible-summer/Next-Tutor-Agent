"""Integer-only value model for the chem-lab engine.

Everything the rule layer touches is an integer: volumes in microlitres
(``_uL``), amounts in micromoles (``_umol``), temperatures in millidegrees
Celsius (``_milli_c``), pH in milli-pH (``_milli``), ratios in permille
(``_permille``) and time in milliseconds (``_ms``). The TypeScript domain
engine (``packages/domain/src/chem-lab``) mirrors these formulas exactly;
shared replay vectors pin both implementations to identical state hashes.

Note ``umol/uL`` is numerically equal to ``mol/L``, so concentration math
needs no unit conversion.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
DT_MS = 500
ACTION_MS = 2000  # simulated duration of pour/dispense/measure-style actions
MAX_RULE_FIRINGS_PER_COMMAND = 64
MAX_EVENTS_PER_SESSION = 2000
MAX_CHECKPOINTS_PER_SESSION = 40

ROOM_TEMPERATURE_MILLI_C = 25_000
PH_NEUTRAL_MILLI = 7_000
PH_SCALE_MILLI = 14_000

# ---------------------------------------------------------------------------
# Deterministic numeric primitives
# ---------------------------------------------------------------------------

# 1000*log10(1 + i/10) for i in 0..90 — generated once with Python's math
# module and pinned as a literal so no runtime float ever enters the rule
# layer. The TS engine embeds the identical table.
LOG10_MILLI_TABLE: tuple[int, ...] = (
    0, 41, 79, 114, 146, 176, 204, 230, 255, 279, 301, 322, 342, 362, 380,
    398, 415, 431, 447, 462, 477, 491, 505, 519, 531, 544, 556, 568, 580,
    591, 602, 613, 623, 633, 643, 653, 663, 672, 681, 690, 699, 708, 716,
    724, 732, 740, 748, 756, 763, 771, 778, 785, 792, 799, 806, 813, 820,
    826, 833, 839, 845, 851, 857, 863, 869, 875, 881, 886, 892, 898, 903,
    908, 914, 919, 924, 929, 934, 940, 944, 949, 954, 959, 964, 968, 973,
    978, 982, 987, 991, 996, 1000,
)


def milli_log10(value: int) -> int:
    """floor(1000 * log10(value)) for integer value >= 1, table-based."""
    if value < 1:
        raise ValueError("milli_log10 requires value >= 1")
    exponent = 0
    scaled = value
    while scaled >= 10:
        scaled //= 10
        exponent += 1
    if exponent >= 2:
        sig = value // (10 ** (exponent - 2))
    else:
        sig = value * (10 ** (2 - exponent))
    idx = sig // 10 - 10
    rem = sig % 10
    base = LOG10_MILLI_TABLE[idx]
    nxt = LOG10_MILLI_TABLE[idx + 1]
    return exponent * 1000 + base + (nxt - base) * rem // 10


def fnv1a64(text: str) -> str:
    """FNV-1a 64-bit over UTF-8 bytes; 16-char lowercase hex."""
    h = 14695981039346656037
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, minimal escaping.

    Only supports the value shapes the engine state uses (None/bool/int/
    str/list/dict) — floats are rejected outright so they can never leak
    into the rule layer. Mirrored by the TS ``canonicalJson``.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise TypeError("floats are not allowed in chem-lab state")
    if isinstance(value, str):
        out = ['"']
        for ch in value:
            code = ord(ch)
            if ch == '"':
                out.append('\\"')
            elif ch == "\\":
                out.append("\\\\")
            elif code < 0x20:
                out.append(f"\\u{code:04x}")
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError("chem-lab state keys must be strings")
            parts.append(canonical(key) + ":" + canonical(value[key]))
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"unsupported state value: {type(value)!r}")


def state_hash(state: dict[str, Any]) -> str:
    """Hash the chemistry-relevant projection (guidance/observations excluded)."""
    projection = {
        "schema_version": state["schema_version"],
        "experiment_id": state["experiment_id"],
        "pack_version": state["pack_version"],
        "pack_hash": state["pack_hash"],
        "session_seed": state["session_seed"],
        "mode": state["mode"],
        "phase": state["phase"],
        "revision": state["revision"],
        "seq": state["seq"],
        "sim_time_ms": state["sim_time_ms"],
        "vessels": state["vessels"],
        "equipment": state["equipment"],
        "environment": state["environment"],
        "goals": state["goals"],
        "visible": state["visible"],
    }
    return fnv1a64(canonical(projection))


# ---------------------------------------------------------------------------
# Amount helpers (all integer, floor semantics shared with the TS engine)
# ---------------------------------------------------------------------------

def sorted_species(amounts: dict[str, int]) -> dict[str, int]:
    """Re-key a species map in sorted order, dropping zero entries."""
    return {key: amounts[key] for key in sorted(amounts) if amounts[key] != 0}


def add_amount(amounts: dict[str, int], species: str, delta: int) -> None:
    if delta == 0:
        return
    value = amounts.get(species, 0) + delta
    if value < 0:
        raise ValueError(f"species amount underflow: {species}")
    if value == 0:
        amounts.pop(species, None)
    else:
        amounts[species] = value


def transfer_amounts(contents: dict[str, int], amount_uL: int, volume_uL: int) -> dict[str, int]:
    """Solute amounts carried by ``amount_uL`` of a well-mixed liquid.

    ``floor(amount * X / volume)`` per species, computed as multiply-then-
    divide so both engines agree exactly.
    """
    if amount_uL <= 0 or volume_uL <= 0:
        return {}
    moved: dict[str, int] = {}
    for species in sorted(contents):
        qty = contents[species] * amount_uL // volume_uL
        if qty > 0:
            moved[species] = qty
    return moved


def apply_transfer(source: dict[str, int], target: dict[str, int], moved: dict[str, int]) -> None:
    for species in sorted(moved):
        add_amount(source, species, -moved[species])
        add_amount(target, species, moved[species])


def total(amounts: dict[str, int]) -> int:
    return sum(amounts.values())


def interpolate_table(points: list[list[int]], x: int) -> int:
    """Linear integer interpolation over sorted [x, y] points.

    Below the first point the first y is returned, above the last the last y.
    Division floors toward zero only for positive deltas here (x sorted,
    y monotonic per content authoring).
    """
    if not points:
        raise ValueError("empty interpolation table")
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for index in range(1, len(points)):
        x1, y1 = points[index]
        if x <= x1:
            x0, y0 = points[index - 1]
            return y0 + (y1 - y0) * (x - x0) // (x1 - x0)
    return points[-1][1]


# Water-like heat capacity: 4.18 J/(g·K); 1 uL ≈ 1 mg water, so heating one
# microlitre by one millidegree costs ~4.18 microjoules. Stored as permille
# microjoules per (uL·milli_c) to stay integer: 4180 micro_j per uL per K.
HEAT_CAPACITY_MICRO_J_PER_UL_K = 4180


def temperature_delta_milli_c(vessel: dict[str, Any], micro_j: int) -> int:
    """Temperature rise from ``micro_j`` joules×10⁻⁶ dumped into the vessel.

    floor(Q / (V · c)) with water-like heat capacity; zero when there is no
    liquid to heat (an empty vessel cannot warm up its contents).
    """
    volume = vessel["volume_uL"]
    if micro_j <= 0 or volume <= 0:
        return 0
    return micro_j * 1000 // (volume * HEAT_CAPACITY_MICRO_J_PER_UL_K)


def cooling_drop_milli_c(vessel: dict[str, Any], room_milli_c: int) -> int:
    """Lumped Newton cooling per DT step: 0.6% of the excess temperature
    (floor semantics; tiny residuals never reach zero, which is fine for a
    teaching model and identical in both engines)."""
    excess = vessel["temperature_milli_c"] - room_milli_c
    if excess <= 0:
        return 0
    return excess * 6 // 1000


def make_event(state: dict[str, Any], kind: str, command_id: str, *,
               vessel_id: str = "", rule_id: str = "",
               data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a semantic event and assign its sequence number."""
    state["seq"] += 1
    return {
        "seq": state["seq"],
        "kind": kind,
        "command_id": command_id,
        "sim_time_ms": state["sim_time_ms"],
        "vessel_id": vessel_id,
        "rule_id": rule_id,
        "data": dict(data or {}),
    }


# ---------------------------------------------------------------------------
# State constructors
# ---------------------------------------------------------------------------

def make_vessel(entry: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "kind": entry["kind"],
        "slot": entry["slot"],
        "capacity_uL": entry["capacity_uL"],
        "volume_uL": entry.get("volume_uL", 0),
        "temperature_milli_c": (entry["temperature_milli_c"]
                                if entry.get("temperature_milli_c") is not None
                                else defaults["room_temperature_milli_c"]),
        "ph_milli": None,
        "mix_permille": 0,
        "contents": sorted_species(dict(entry.get("contents", {}))),
        "solids": sorted_species(dict(entry.get("solids", {}))),
        "gases": sorted_species(dict(entry.get("gases", {}))),
        "settled_permille": 0,
        "gas_rate_permille": 0,
        "collects_gas": bool(entry.get("collects_gas", False)),
        "contamination": sorted(entry.get("contamination", [])),
        "heat": None,
        "stir": None,
        "reading": None,
    }


def make_equipment(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "kind": entry["kind"],
        "slot": entry["slot"],
        "load": {"volume_uL": 0, "contents": {}, "solids": {}, "source": None, "clean": True},
        "connected": None,
        "reading": None,
    }


def initial_state(pack: dict[str, Any], *, session_seed: int, mode: str, language: str) -> dict[str, Any]:
    """Build the session LabState from a validated experiment pack."""
    start = pack["starting_state"]
    environment = {
        "room_temperature_milli_c": start.get("room_temperature_milli_c", ROOM_TEMPERATURE_MILLI_C),
        "escaped_gas_umol": {},
        "disposed_volume_uL": 0,
    }
    vessels = {}
    for entry in start["vessels"]:
        vessels[entry["id"]] = make_vessel(entry, environment)
    equipment = {}
    for entry in start["equipment"]:
        equipment[entry["id"]] = make_equipment(entry)
    goals = [{"id": goal["id"], "status": "pending"} for goal in pack.get("goals", [])]
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": pack["id"],
        "pack_version": pack["pack_version"],
        "pack_hash": pack["pack_hash"],
        "session_seed": session_seed,
        "mode": mode,
        "language": language,
        "phase": "ready",
        "revision": 0,
        "seq": 0,
        "sim_time_ms": 0,
        "held": None,
        "vessels": vessels,
        "equipment": equipment,
        "environment": environment,
        "slots": [dict(slot) for slot in start["slots"]],
        "goals": goals,
        "completed_steps": [],
        "step_completion_seq": {},
        "step_visits": {},
        "checkpoints": [],
        "observations": [],
        "visible": {},
        "guidance": None,
        "safety": None,
        "rule_firings": {},
        "state_hash": "",
    }
