#!/usr/bin/env python3
"""Text preview of a chem-lab replay vector: per-command render frames and
the event stream, so content authors can eyeball what students will see.

Usage:
    render_preview.py <vector.json> [--events]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.chem_lab.catalog import Catalog  # noqa: E402
from app.chem_lab.engine import projection  # noqa: E402
from app.chem_lab.engine.model import initial_state  # noqa: E402
from app.chem_lab.engine.reducer import apply_command, prime_visibility  # noqa: E402


def fmt_vessel(vessel: dict, frame: dict) -> str:
    parts = [f"{vessel['id']:<16}"]
    parts.append(f"vol={vessel['volume_uL']:>7} µL")
    parts.append(f"T={vessel['temperature_milli_c'] / 1000:6.1f}°C")
    parts.append(f"color={frame['liquid_color_token']:<16} op={frame['opacity_permille']:>4}")
    if frame["turbidity_permille"]:
        parts.append(f"turb={frame['turbidity_permille']}")
    if frame["precipitate"]:
        layer = frame["precipitate"]
        parts.append(f"solid[{layer['color_token']} {layer['amount_permille']}‰ {layer['texture']}]")
    if frame["bubbles"]:
        parts.append("bubbles")
    if frame["steam_permille"]:
        parts.append(f"steam={frame['steam_permille']}")
    if vessel["gases"]:
        parts.append(f"gases={vessel['gases']}")
    if vessel.get("ph_milli") is not None:
        parts.append(f"pH={vessel['ph_milli'] / 1000:.2f}")
    return " ".join(parts)


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    show_events = "--events" in sys.argv
    if not args:
        print(__doc__)
        return 1
    vector_path = Path(args[0])
    vector = json.loads(vector_path.read_text(encoding="utf-8"))
    catalog = Catalog()
    experiment_id = vector_path.parent.name
    pack = catalog.build_pack(experiment_id)
    species_defs = pack["_species_defs"]

    state = initial_state(pack, mode=vector.get("mode", "guided"),
                          session_seed=int(vector.get("session_seed", 0)),
                          language="zh")
    prime_visibility(state, pack)

    print(f"== {experiment_id} ← {vector_path.name} ==")

    def show_vessels():
        for vessel_id in sorted(state["vessels"]):
            vessel = state["vessels"][vessel_id]
            frame = projection.vessel_frame(state, vessel, species_defs)
            print("  " + fmt_vessel(vessel, frame))

    print("-- 初始 --")
    show_vessels()
    for index, command in enumerate(vector.get("script", [])):
        state, events, accepted, error = apply_command(state, pack, command, f"preview-{index}")
        label = json.dumps(command, ensure_ascii=False)
        status = "OK " if accepted else f"REJ({error})"
        print(f"-- [{index}] {status} {label}")
        show_vessels()
        if show_events:
            for event in events:
                data = json.dumps(event["data"], ensure_ascii=False)
                print(f"    · t={event['sim_time_ms']:>7} {event['kind']:<24} "
                      f"{event['vessel_id'] or '-':<14} {data}")
    print(f"-- 终态 phase={state['phase']} goals={state['goals']} "
          f"steps={state['completed_steps']} hash={state['state_hash']}")
    print(f"-- 观察: {sorted({o['key'] for o in state['observations']})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
