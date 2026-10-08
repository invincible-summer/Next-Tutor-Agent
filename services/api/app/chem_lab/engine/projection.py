"""Projection: LabState → RenderFrame, public observations, species ledger.

Colors come from pack-declared concentration bands and indicator pH bands
(never free-form CSS). "No visible change" is explained from the ledger
instead of guessing "no reaction".
"""
from __future__ import annotations

from typing import Any

from . import model as m

VISIBLE_CONC_DEFAULT_UMOL_PER_UL_MILLI = 1  # 1 umol/uL ≈ 1 mol/L fallback
BUBBLE_VISIBLE_RATE_PERMILLE = 120
STEAM_VISIBLE_MILLI_C = 55_000


def compute_ph_milli(vessel: dict[str, Any], ph_model: str) -> int | None:
    """Strong-acid/strong-base teaching approximation (declared in the pack).

    Concentration umol/uL == mol/L: pH = -log10([H+]) with
    [H+] = (h_plus - oh_minus)/V when positive, else 14 - pOH. Near-neutral
    (|net| below 10 umol/uL milli-resolution) reports 7.000. Returns None
    when the pack does not declare a pH model — the UI must then say
    "pH not offered" rather than inventing a value.
    """
    if ph_model != "strong_binary_v1" or vessel["volume_uL"] <= 0:
        return None
    h_plus = vessel["contents"].get("h_plus", 0)
    oh_minus = vessel["contents"].get("oh_minus", 0)
    net = h_plus - oh_minus
    volume = vessel["volume_uL"]
    # |net|/V <= 1e-7 mol/L  ⇔  |net| * 1e7 <= V  → neutral within the model.
    if abs(net) * 10_000_000 <= volume:
        return m.PH_NEUTRAL_MILLI
    if net > 0:
        return -(m.milli_log10(net) - m.milli_log10(volume))
    return m.PH_SCALE_MILLI + (m.milli_log10(-net) - m.milli_log10(volume))


def _dominant_color(vessel: dict[str, Any], species_defs: dict[str, Any]) -> tuple[str, int]:
    """(color_token, opacity_permille) from concentration bands.

    Indicator species take precedence when their concentration is visible
    and a pH is available; otherwise the strongest visible tinted solute
    wins. Opacity scales with concentration relative to the visible
    threshold (capped at 1000 permille).
    """
    volume = vessel["volume_uL"]
    if volume <= 0:
        return ("clear", 0)
    best_token, best_opacity, best_strength = "clear", 0, 0
    for species_id in sorted(vessel["contents"]):
        species = species_defs.get(species_id)
        # Solid-phase species (crystals, powders) tint their precipitate
        # layer, never the liquid — dissolved KNO₃ is colorless.
        if not species or species.get("state") == "solid":
            continue
        if not species.get("color_token") or species["color_token"] == "clear":
            continue
        conc_milli = vessel["contents"][species_id] * 1000 // volume  # milli (umol/uL)
        threshold = max(1, species.get("visible_conc_umol_per_ul_milli") or VISIBLE_CONC_DEFAULT_UMOL_PER_UL_MILLI)
        if conc_milli < threshold:
            continue
        strength = species.get("color_strength_permille", 500)
        if strength < best_strength:
            continue
        opacity = min(1000, 300 + conc_milli * 700 // (threshold * 4))
        if species.get("ph_bands") and vessel["ph_milli"] is not None:
            token = species["color_token"]
            for band in species["ph_bands"]:
                if vessel["ph_milli"] <= int(band[0]):
                    token = str(band[1])
                    break
            best_token, best_opacity, best_strength = token, min(1000, opacity + 200), strength
        else:
            best_token, best_opacity, best_strength = species["color_token"], opacity, strength
    return (best_token, best_opacity)


def _turbidity(vessel: dict[str, Any], species_defs: dict[str, Any]) -> int:
    """Suspended (not yet settled) solid fraction → turbidity permille."""
    if vessel["volume_uL"] <= 0:
        return 0
    suspended_umol = 0
    for species_id, amount in vessel["solids"].items():
        species = species_defs.get(species_id)
        if species and species.get("solubility_table"):
            continue  # crystals project through the crystal layer instead
        suspended_umol += amount
    if suspended_umol <= 0:
        return 0
    concentration_milli = suspended_umol * 1_000_000 // vessel["volume_uL"]
    base = min(900, concentration_milli // 20)
    return base * (1000 - vessel["settled_permille"]) // 1000


def _precipitate_layer(vessel: dict[str, Any], species_defs: dict[str, Any]) -> dict[str, Any] | None:
    total_solid = 0
    token = "precipitate.white"
    texture = "powder"
    for species_id in sorted(vessel["solids"]):
        species = species_defs.get(species_id)
        amount = vessel["solids"][species_id]
        if amount <= 0:
            continue
        if species and species.get("solubility_table"):
            texture = "flakes"
        total_solid += amount
        if species and species.get("color_token") and species["color_token"] != "clear":
            token = species["color_token"]
    if total_solid <= 0:
        return None
    threshold = 1
    for species_id in vessel["solids"]:
        species = species_defs.get(species_id)
        if species and species.get("visible_amount_umol"):
            threshold = species["visible_amount_umol"]
            break
    amount_permille = min(1000, total_solid * 1000 // (threshold * 4))
    return {"amount_permille": amount_permille, "color_token": token,
            "texture": "layer" if vessel["settled_permille"] >= 700 else texture}


def _temperature_band(milli_c: int) -> str:
    if milli_c < 10_000:
        return "cold"
    if milli_c < 35_000:
        return "room"
    if milli_c < 65_000:
        return "warm"
    return "hot"


def vessel_frame(state: dict[str, Any], vessel: dict[str, Any],
                 species_defs: dict[str, Any]) -> dict[str, Any]:
    token, opacity = _dominant_color(vessel, species_defs)
    bubbles = None
    gas_total = m.total(vessel["gases"])
    if vessel["gas_rate_permille"] >= BUBBLE_VISIBLE_RATE_PERMILLE and gas_total > 0:
        gas_species = sorted(vessel["gases"])[0]
        bubbles = {
            "rate_permille": min(1000, vessel["gas_rate_permille"]),
            "size_permille": min(1000, 200 + gas_total * 100 // 1000),
            "gas_label": gas_species,
        }
    steam = 0
    if vessel["temperature_milli_c"] >= STEAM_VISIBLE_MILLI_C and vessel["volume_uL"] > 0:
        steam = min(1000, (vessel["temperature_milli_c"] - STEAM_VISIBLE_MILLI_C) // 40)
    return {
        "fill_ratio_permille": min(1000, vessel["volume_uL"] * 1000 // max(1, vessel["capacity_uL"])),
        "liquid_color_token": token,
        "liquid_label": token,
        "opacity_permille": opacity,
        "turbidity_permille": _turbidity(vessel, species_defs),
        "precipitate": _precipitate_layer(vessel, species_defs),
        "bubbles": bubbles,
        "steam_permille": steam,
        "temperature_band": _temperature_band(vessel["temperature_milli_c"]),
    }


def render_frame(state: dict[str, Any], species_defs: dict[str, Any]) -> dict[str, Any]:
    vessels = {}
    for vessel_id in sorted(state["vessels"]):
        vessels[vessel_id] = vessel_frame(state, state["vessels"][vessel_id], species_defs)
    instruments: dict[str, Any] = {}
    for equipment_id in sorted(state["equipment"]):
        equipment = state["equipment"][equipment_id]
        reading = equipment.get("reading")
        if reading:
            instruments[equipment_id] = {
                "reading": reading.get("display", ""),
                "unit": reading.get("unit", ""),
                "status": reading.get("status", "idle"),
            }
        else:
            instruments[equipment_id] = {"reading": "", "unit": "", "status": "idle"}
    highlights: list[dict[str, Any]] = []
    for event in reversed(state.get("_recent_events", [])):
        if event["kind"] in {"precipitate_formed", "gas_released", "safety_warning",
                             "safety_locked", "crystal_formed"} and event["vessel_id"]:
            highlights.append({"vessel_id": event["vessel_id"], "kind": event["kind"]})
    return {"vessels": vessels, "instruments": instruments, "highlights": highlights[:6]}


# ---------------------------------------------------------------------------
# Species ledger (物质层) and "no visible change" (可解释的无现象)
# ---------------------------------------------------------------------------

def species_ledger(state: dict[str, Any], vessel_id: str,
                   species_defs: dict[str, Any]) -> dict[str, Any]:
    vessel = state["vessels"][vessel_id]
    rows = []
    for species_id in sorted(set(vessel["contents"]) | set(vessel["solids"]) | set(vessel["gases"])):
        species = species_defs.get(species_id, {})
        rows.append({
            "species_id": species_id,
            "dissolved_umol": vessel["contents"].get(species_id, 0),
            "solid_umol": vessel["solids"].get(species_id, 0),
            "gas_umol": vessel["gases"].get(species_id, 0),
            "spectator": bool(species.get("spectator")),
        })
    return {
        "vessel_id": vessel_id,
        "volume_uL": vessel["volume_uL"],
        "ph_milli": vessel["ph_milli"],
        "temperature_milli_c": vessel["temperature_milli_c"],
        "mix_permille": vessel["mix_permille"],
        "rows": rows,
    }


def explain_no_change(state: dict[str, Any], vessel_id: str,
                      species_defs: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    """Ordered, checkable reasons a visible change may be absent."""
    vessel = state["vessels"][vessel_id]
    reasons: list[str] = []
    has_indicator = any(
        (species_defs.get(s) or {}).get("role") == "indicator"
        for s in vessel["contents"])
    if not has_indicator and any(
            (species_defs.get(s) or {}).get("role") in {"acid", "base"} for s in vessel["contents"]):
        reasons.append("no_indicator")
    volume = vessel["volume_uL"]
    if volume > 0:
        below = True
        for species_id in vessel["contents"]:
            species = species_defs.get(species_id) or {}
            threshold = species.get("visible_conc_umol_per_ul_milli") or VISIBLE_CONC_DEFAULT_UMOL_PER_UL_MILLI
            if species.get("color_token", "clear") != "clear" and \
                    vessel["contents"][species_id] * 1000 // volume >= max(1, threshold):
                below = False
                break
        if below and any((species_defs.get(s) or {}).get("color_token", "clear") != "clear"
                         for s in vessel["contents"]):
            reasons.append("below_visible_threshold")
    if vessel["mix_permille"] < 300:
        reasons.append("not_mixed")
    reacted = False
    for rule in rules:
        for condition in rule["when"]:
            if condition["op"] == "species_at_least":
                species = condition["species"]
                if vessel["contents"].get(species, 0) + vessel["solids"].get(species, 0) \
                        < condition["amount_umol"]:
                    reacted = True
                    break
        if reacted:
            break
    if reacted:
        reasons.append("reactant_exhausted")
    escaped = state["environment"]["escaped_gas_umol"]
    if m.total(escaped) > 0 and not vessel["gases"]:
        reasons.append("gas_escaped")
    known_species = {s for rule in rules for e in rule["then"] for s in [e.get("species", "")]}
    if not any(s in known_species for s in vessel["contents"]):
        reasons.append("not_modeled")
    return reasons
