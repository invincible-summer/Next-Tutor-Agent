"""Deterministic replay: fold a command script through the reducer and
collect per-step state hashes. Shared by session playback (`at` revision)
and by the replay-vector fixtures that keep the TS/Python engines aligned."""
from __future__ import annotations

from typing import Any

from ..errors import ChemLabError
from .model import initial_state, state_hash
from .reducer import apply_command, prime_visibility


def run_script(
    pack: dict[str, Any],
    script: list[dict[str, Any]],
    *,
    mode: str = "guided",
    session_seed: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[bool]]:
    """Fold ``script`` from the pack's starting state.

    Returns ``(state, events, hashes, accepted_mask)`` where ``hashes[0]`` is
    the initial-state hash and ``hashes[i+1]`` follows command ``i``.
    """
    state = initial_state(pack, mode=mode, session_seed=session_seed,
                          language=pack.get("language", "zh-CN"))
    prime_visibility(state, pack)
    events: list[dict[str, Any]] = []
    hashes = [state_hash(state)]
    accepted_mask: list[bool] = []
    for index, command in enumerate(script):
        if not isinstance(command, dict):
            raise ChemLabError("bad_request", f"script[{index}] 必须是对象")
        state, cmd_events, accepted, _error = apply_command(state, pack, command, f"replay-{index}")
        events.extend(cmd_events)
        hashes.append(state_hash(state))
        accepted_mask.append(accepted)
    return state, events, hashes, accepted_mask


def replay_to_revision(
    pack: dict[str, Any],
    script: list[dict[str, Any]],
    *,
    mode: str,
    session_seed: int,
    revision: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay ``script`` and return the state at ``revision`` plus the events
    emitted up to that point. Revision 0 is the initial state."""
    if revision < 0 or revision > len(script):
        raise ChemLabError("revision_out_of_range", f"revision 需在 0..{len(script)} 之间")
    state = initial_state(pack, mode=mode, session_seed=session_seed,
                          language=pack.get("language", "zh-CN"))
    prime_visibility(state, pack)
    events: list[dict[str, Any]] = []
    for index in range(revision):
        state, cmd_events, _accepted, _error = apply_command(state, pack, script[index], f"replay-{index}")
        events.extend(cmd_events)
    state["_recent_events"] = events[-8:]
    return state, events


def verify_vector(pack: dict[str, Any], vector: dict[str, Any]) -> list[str]:
    """Check one replay vector against the engine; returns a list of problems
    (empty when the vector matches)."""
    problems: list[str] = []
    script = vector.get("script", [])
    if not isinstance(script, list):
        return ["script 必须是数组"]
    try:
        state, events, hashes, accepted_mask = run_script(
            pack,
            script,
            mode=vector.get("mode", "guided"),
            session_seed=int(vector.get("session_seed", 0)),
        )
    except ChemLabError as exc:
        return [f"回放失败: {exc.code}: {exc}"]
    expect_hashes = vector.get("state_hashes")
    if isinstance(expect_hashes, list) and expect_hashes != hashes:
        for i, (got, want) in enumerate(zip(hashes, expect_hashes)):
            if got != want:
                problems.append(f"state_hashes[{i}] 不一致: got {got} want {want}")
                break
        if len(expect_hashes) != len(hashes):
            problems.append(f"state_hashes 长度不一致: got {len(hashes)} want {len(expect_hashes)}")
    final_hash = vector.get("final_hash")
    if isinstance(final_hash, str) and hashes[-1] != final_hash:
        problems.append(f"final_hash 不一致: got {hashes[-1]} want {final_hash}")
    expect_accepted = vector.get("accepted")
    if isinstance(expect_accepted, list) and expect_accepted != accepted_mask:
        problems.append(f"accepted 不一致: got {accepted_mask} want {expect_accepted}")
    expect_events = vector.get("expect_event_kinds")
    if isinstance(expect_events, list):
        kinds = [event["kind"] for event in events]
        for kind in expect_events:
            if kind not in kinds:
                problems.append(f"缺少事件 {kind}")
    expect_phase = vector.get("expect_final_phase")
    if isinstance(expect_phase, str) and state.get("phase") != expect_phase:
        problems.append(f"终态 phase 不一致: got {state.get('phase')} want {expect_phase}")
    expect_keys = vector.get("expect_observation_keys")
    if isinstance(expect_keys, list):
        emitted = {o["key"] for o in state["observations"]}
        for key in expect_keys:
            if key not in emitted:
                problems.append(f"缺少观察 {key}")
    return problems
