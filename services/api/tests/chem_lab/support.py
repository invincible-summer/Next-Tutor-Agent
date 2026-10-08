"""Shared fixtures for chem-lab tests: a minimal synthetic pack dict in the
exact shape the reducer consumes (what ``catalog.build_pack`` returns)."""
from __future__ import annotations

from typing import Any


def build_test_pack() -> dict[str, Any]:
    species = {
        "water": {"id": "water", "state": "liquid", "role": "solvent", "color_token": "clear"},
        "h_plus": {"id": "h_plus", "state": "solute", "role": "acid", "color_token": "clear"},
        "oh_minus": {"id": "oh_minus", "state": "solute", "role": "base", "color_token": "clear"},
        "na_plus": {"id": "na_plus", "state": "solute", "role": "spectator",
                    "color_token": "clear", "spectator": True},
        "cl_minus": {"id": "cl_minus", "state": "solute", "role": "spectator",
                     "color_token": "clear", "spectator": True},
    }
    rules = [{
        "id": "rule.test_neutralization",
        "priority": 100,
        "triggers": ["on_dispense", "on_pour", "on_stir", "on_wait"],
        "when": [
            {"op": "species_at_least", "vessel": "$target", "species": "h_plus", "amount_umol": 1},
            {"op": "species_at_least", "vessel": "$target", "species": "oh_minus", "amount_umol": 1},
        ],
        "then": [
            {"op": "consume_min_ratio", "vessel": "$target", "pairs": [["h_plus", 1], ["oh_minus", 1]]},
            {"op": "produce", "vessel": "$target", "species": "water",
             "from_consumed": True, "ratio_to_consumed": 1000},
            {"op": "emit_temperature", "vessel": "$target", "micro_j_per_umol": 57000},
        ],
        "limits": {"max_firings_per_evaluation": 8},
    }]
    equipment_defs = {
        "beaker": {"id": "beaker", "category": "vessel", "heat_compatible": True},
        "pipette": {"id": "pipette", "category": "instrument", "capacity_uL": 10000},
        "probe": {"id": "probe", "category": "instrument", "measures": ["ph", "temperature"]},
    }
    return {
        "id": "chem.test",
        "pack_version": "1.0.0",
        "pack_hash": "test-hash",
        "language": "zh",
        "ph_model": "strong_binary_v1",
        "model_fidelity": "teaching_semi_quantitative",
        "safety_profile": {
            "id": "test",
            "warn_temperature_milli_c": 60000,
            "lock_temperature_milli_c": 95000,
            "notes": {"zh": "x", "en": "x"},
        },
        "starting_state": {
            "room_temperature_milli_c": 25000,
            "slots": [{"id": "s1", "x": 0, "y": 0, "w": 100, "h": 100},
                      {"id": "s2", "x": 100, "y": 0, "w": 100, "h": 100},
                      {"id": "s3", "x": 200, "y": 0, "w": 100, "h": 100}],
            "vessels": [
                {"id": "acid", "kind": "beaker", "slot": "s1", "capacity_uL": 250000,
                 "volume_uL": 15000, "contents": {"h_plus": 1000, "cl_minus": 1000}},
                {"id": "base", "kind": "beaker", "slot": "s2", "capacity_uL": 250000,
                 "volume_uL": 20000, "contents": {"oh_minus": 2000, "na_plus": 2000}},
            ],
            "equipment": [
                {"id": "pip-1", "kind": "pipette", "slot": "s3"},
                {"id": "probe-1", "kind": "probe", "slot": "s3"},
            ],
        },
        "procedure": [],
        "goals": [],
        "_rules": rules,
        "_species_defs": species,
        "_equipment_defs": equipment_defs,
    }
