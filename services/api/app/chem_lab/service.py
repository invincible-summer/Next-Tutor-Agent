"""Chem-lab session service: locks, idempotency, versioning, branches.

The route layer only adapts HTTP; all chemistry goes through the engine and
all persistence through ``persistence.py``. Concurrency: one
file lock per owner serializes every read-modify-write; acceptance checks
run inside the lock (owner epoch, tombstone, pack_hash, base_revision,
command_id); a command without a complete ACK is never auto-replayed.
"""
from __future__ import annotations

import copy
import time
import uuid

from app.core.config import settings

from . import persistence
from .catalog import get_catalog
from .engine.guidance import goals_status
from .engine.model import initial_state, state_hash
from .engine.projection import render_frame
from .engine.reducer import apply_command, prime_visibility
from .engine.replay import replay_to_revision
from .errors import ChemLabError
from .providers import CatalogScenarioProvider, DeterministicGuidanceProvider

_PAGE_SIZE = 20
_MAX_STORED_ACKS = 32
_SESSION_ID_PREFIX = "clab_"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_enabled() -> None:
    if not settings.chem_lab_enabled:
        raise ChemLabError("disabled", "模拟实验台暂不可用", status=403)


def _runtime_pack(doc: dict) -> dict:
    return get_catalog().build_pack(doc["experiment_id"], doc["pack_version"])


def _visibility(pack: dict, mode: str) -> set[str] | None:
    table = pack.get("observation_visibility") or {}
    keys = table.get(mode)
    return set(keys) if isinstance(keys, list) else None


def _public_events(pack: dict, mode: str, events: list[dict]) -> list[dict]:
    visible = _visibility(pack, mode)
    if visible is None:
        return [dict(event) for event in events]
    return [
        dict(event) for event in events
        if not (event.get("kind") == "observation_emitted"
                and event.get("data", {}).get("key") not in visible)
    ]


def _public_observations(pack: dict, mode: str, observations: list[dict]) -> list[dict]:
    visible = _visibility(pack, mode)
    if visible is None:
        return [dict(obs) for obs in observations]
    return [dict(obs) for obs in observations if obs.get("key") in visible]


def _guidance(pack: dict, doc: dict, last_events: list[dict]) -> dict:
    provider = DeterministicGuidanceProvider(pack)
    return provider.explain(
        {"state": doc["state"], "language": doc["language"]}, last_events)


def _frame(pack: dict, doc: dict) -> dict:
    return render_frame(doc["state"], pack["_species_defs"])


def _session_summary(doc: dict) -> dict | None:
    if doc.get("deleted"):
        return None
    pack = get_catalog().get(doc["experiment_id"], doc["pack_version"])
    return {
        "session_id": doc["session_id"],
        "experiment_id": doc["experiment_id"],
        "pack_version": doc["pack_version"],
        "pack_hash": doc["pack_hash"],
        "mode": doc["mode"],
        "phase": doc["state"]["phase"],
        "revision": doc["state"]["revision"],
        "finished": bool(doc.get("finished")),
        "title": dict(pack.title.model_dump(mode="json")),
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }


def _refresh_index(owner: str) -> None:
    items = [summary for summary in
             (_session_summary(doc) for doc in persistence.list_sessions(owner))
             if summary is not None]
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    persistence.write_index(owner, items)


def _checkpoint_public(item: dict) -> dict:
    return {
        "checkpoint_id": item["checkpoint_id"],
        "label": item["label"],
        "revision": item["revision"],
        "seq": item["seq"],
        "sim_time_ms": item["sim_time_ms"],
        "created_at": item.get("created_at", 0.0),
    }


def _snapshot(owner: str, doc: dict, *, last_events: list[dict] | None = None) -> dict:
    pack = _runtime_pack(doc)
    state = doc["state"]
    mode = doc["mode"]
    events = last_events if last_events is not None else state.get("_recent_events", [])
    checkpoints = [_checkpoint_public(item)
                   for item in persistence.read_checkpoints(owner, doc["session_id"])["items"]]
    return {
        "session_id": doc["session_id"],
        "experiment_id": doc["experiment_id"],
        "pack_version": doc["pack_version"],
        "pack_hash": doc["pack_hash"],
        "mode": mode,
        "language": doc["language"],
        "phase": state["phase"],
        "revision": state["revision"],
        "seq": state["seq"],
        "sim_time_ms": state["sim_time_ms"],
        "state_hash": state["state_hash"],
        "engine_state": state,
        "goals": goals_status(pack, state),
        "completed_steps": list(state["completed_steps"]),
        "observations": _public_observations(pack, mode, state["observations"]),
        "guidance": _guidance(pack, doc, events),
        "render_frame": _frame(pack, doc),
        "recent_events": _public_events(pack, mode, events),
        "checkpoints": checkpoints,
        "sync_required": bool(doc.get("sync_required")),
        "finished": bool(doc.get("finished")),
        "branch": doc.get("branch"),
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }


def _load_live_doc(owner: str, session_id: str) -> dict:
    doc = persistence.read_session(owner, session_id)
    if doc is None or doc.get("deleted"):
        raise ChemLabError("session_missing", "会话不存在或已删除", status=404)
    return doc


# ---------------------------------------------------------------------------
# Catalog endpoints
# ---------------------------------------------------------------------------

def list_catalog() -> dict:
    _ensure_enabled()
    catalog = get_catalog()
    return {
        "experiments": catalog.list_summaries(),
        "capabilities": {
            "commands": True,
            "checkpoints": True,
            "fork": True,
            "self_check": True,
        },
    }


def get_experiment(experiment_id: str, pack_version: str | None) -> dict:
    _ensure_enabled()
    return get_catalog().public_projection(experiment_id, pack_version)


def get_engine_pack(experiment_id: str, pack_version: str | None) -> dict:
    """Runtime pack for the client-side engine mirror: identical to what the
    Python engine interprets, minus prediction answer flags. The server stays
    authoritative — it re-runs every command itself."""
    _ensure_enabled()
    runtime = CatalogScenarioProvider().get_pack(experiment_id, pack_version or "")
    for prediction in runtime.get("predictions", []):
        for option in prediction.get("options", []):
            option.pop("correct", None)
    return {"pack_hash": runtime["pack_hash"], "pack": runtime}


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def create_session(owner: str, body) -> dict:
    _ensure_enabled()
    catalog = get_catalog()
    pack = catalog.get(body.experiment_id, body.pack_version)  # missing -> 404
    if body.mode not in pack.modes:
        raise ChemLabError("bad_request", f"实验不支持模式 {body.mode}", status=422)
    with persistence.lock(owner):
        epoch = persistence.epoch(owner)
        live = [doc for doc in persistence.list_sessions(owner) if not doc.get("deleted")]
        if len(live) >= settings.chem_lab_max_sessions_per_owner:
            raise ChemLabError("limit_exceeded", "会话数量已达上限，请先删除旧会话", status=409)
        runtime = catalog.build_pack(pack.id, pack.pack_version)
        state = initial_state(runtime, session_seed=body.session_seed,
                              mode=body.mode, language=body.language)
        prime_visibility(state, runtime)
        state["_recent_events"] = []
        state["state_hash"] = state_hash(state)
        session_id = _SESSION_ID_PREFIX + uuid.uuid4().hex[:24]
        now = time.time()
        doc = {
            "session_id": session_id,
            "owner": persistence.safe(owner),
            "experiment_id": pack.id,
            "pack_version": pack.pack_version,
            "pack_hash": catalog.pack_hash(pack),
            "mode": body.mode,
            "language": body.language,
            "session_seed": body.session_seed,
            "state": state,
            "script": [],
            "acks": {},
            "branch": None,
            "finished": False,
            "result_card": None,
            "sync_required": False,
            "created_at": now,
            "updated_at": now,
        }
        persistence.write_session(owner, session_id, doc, expected_epoch=epoch)
        _refresh_index(owner)
        return _snapshot(owner, doc)


def list_sessions(owner: str, cursor: str | None) -> dict:
    _ensure_enabled()
    try:
        offset = max(0, int(cursor)) if cursor else 0
    except ValueError:
        raise ChemLabError("bad_request", "cursor 不合法", status=422) from None
    docs = [doc for doc in persistence.list_sessions(owner) if not doc.get("deleted")]
    summaries = [summary for summary in (_session_summary(doc) for doc in docs)
                 if summary is not None]
    summaries.sort(key=lambda item: item["updated_at"], reverse=True)
    page = summaries[offset:offset + _PAGE_SIZE]
    next_cursor = str(offset + _PAGE_SIZE) if offset + _PAGE_SIZE < len(summaries) else None
    return {"items": page, "total": len(summaries), "next_cursor": next_cursor}


def get_session(owner: str, session_id: str) -> dict:
    _ensure_enabled()
    doc = _load_live_doc(owner, session_id)
    return _snapshot(owner, doc)


def delete_session(owner: str, session_id: str) -> dict:
    _ensure_enabled()
    with persistence.lock(owner):
        doc = persistence.read_session(owner, session_id)
        if doc is None or doc.get("deleted"):
            raise ChemLabError("session_missing", "会话不存在或已删除", status=404)
        # 先阻断写入（锁内墓碑），再清理派生文件。
        persistence.delete_session_files(owner, session_id)
        _refresh_index(owner)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _store_ack(doc: dict, ack: dict) -> None:
    acks = doc.setdefault("acks", {})
    stored = {key: value for key, value in ack.items() if key != "events"}
    acks[ack["command_id"]] = stored
    while len(acks) > _MAX_STORED_ACKS:
        acks.pop(next(iter(acks)))


def _replay_ack_events(owner: str, doc: dict, stored: dict) -> dict:
    """Rebuild a duplicate ACK's event slice from the append-only log."""
    events = persistence.read_events(owner, doc["session_id"],
                                     after_seq=max(0, stored["seq_from"] - 1),
                                     max_events=500)
    events = [event for event in events if event["seq"] <= stored["seq_to"]]
    return {**stored, "events": events}


def post_command(owner: str, session_id: str, body) -> dict:
    _ensure_enabled()
    with persistence.lock(owner):
        epoch = persistence.epoch(owner)
        doc = _load_live_doc(owner, session_id)
        if doc["pack_hash"] != body.pack_hash:
            raise ChemLabError("pack_conflict", "实验包版本已变化，请重新拉取会话", status=409)
        stored = doc.get("acks", {}).get(body.command_id)
        if stored is not None:
            # 同一 command_id 重试：返回原始 ACK（事件从日志重建）。
            return _replay_ack_events(owner, doc, stored)
        state = doc["state"]
        if body.base_revision != state["revision"]:
            raise ChemLabError("revision_conflict",
                               f"base_revision 过期（当前 {state['revision']}），请重新同步",
                               status=409)
        if persistence.count_events(owner, session_id) >= persistence.MAX_SESSION_EVENTS:
            raise ChemLabError("limit_exceeded", "会话事件日志已达上限，请分支或新开会话",
                               status=409)
        pack = _runtime_pack(doc)
        command = body.command.model_dump(mode="json", exclude_none=True)
        observations_before = len(state["observations"])
        state, events, accepted, error = apply_command(state, pack, command, body.command_id)
        doc["script"].append(command)
        doc["updated_at"] = time.time()
        persistence.append_events(owner, session_id, events)
        ack = {
            "accepted": accepted,
            "command_id": body.command_id,
            "revision": state["revision"],
            "seq_from": events[0]["seq"] if events else state["seq"],
            "seq_to": events[-1]["seq"] if events else state["seq"],
            "state_hash": state["state_hash"],
            "error_code": error,
            "events": _public_events(pack, doc["mode"], events),
            "observations": _public_observations(
                pack, doc["mode"], state["observations"][observations_before:]),
            "guidance": _guidance(pack, doc, events),
            "render_frame": _frame(pack, doc),
            "snapshot": None,
        }
        _store_ack(doc, ack)
        persistence.write_session(owner, session_id, doc, expected_epoch=epoch)
        _refresh_index(owner)
        return ack


def get_events(owner: str, session_id: str, after_seq: int,
               max_events: int = 200) -> dict:
    _ensure_enabled()
    doc = _load_live_doc(owner, session_id)
    pack = _runtime_pack(doc)
    events = persistence.read_events(owner, session_id, after_seq=after_seq,
                                     max_events=max_events)
    items = _public_events(pack, doc["mode"], events)
    return {
        "items": items,
        "next_after_seq": items[-1]["seq"] if items else after_seq,
        "truncated": len(events) >= max_events,
    }


# ---------------------------------------------------------------------------
# Checkpoints / branches
# ---------------------------------------------------------------------------

def create_checkpoint(owner: str, session_id: str, body) -> dict:
    _ensure_enabled()
    label = body.label.strip()[:80]
    if not label:
        raise ChemLabError("bad_request", "label 不能为空", status=422)
    with persistence.lock(owner):
        epoch = persistence.epoch(owner)
        doc = _load_live_doc(owner, session_id)
        pack = _runtime_pack(doc)
        state = doc["state"]
        command_id = f"cpcmd_{uuid.uuid4().hex[:20]}"
        observations_before = len(state["observations"])
        state, events, accepted, error = apply_command(
            state, pack, {"kind": "checkpoint", "label": label}, command_id)
        if not accepted:
            raise ChemLabError("invalid_command",
                               f"无法创建检查点（{error}）", status=409)
        doc["script"].append({"kind": "checkpoint", "label": label})
        doc["updated_at"] = time.time()
        persistence.append_events(owner, session_id, events)
        checkpoint_event = next(
            event for event in events if event["kind"] == "checkpoint_created")
        item = {
            "checkpoint_id": checkpoint_event["data"]["checkpoint_id"],
            "label": label,
            "revision": state["revision"],
            "seq": state["seq"],
            "sim_time_ms": state["sim_time_ms"],
            "script_len": len(doc["script"]),
            "state": copy.deepcopy(state),
            "created_at": time.time(),
        }
        store = persistence.read_checkpoints(owner, session_id)
        store["items"].append(item)
        persistence.write_checkpoints(owner, session_id, store)
        ack = {
            "accepted": True,
            "command_id": command_id,
            "revision": state["revision"],
            "seq_from": events[0]["seq"] if events else state["seq"],
            "seq_to": events[-1]["seq"] if events else state["seq"],
            "state_hash": state["state_hash"],
            "error_code": "",
            "events": _public_events(pack, doc["mode"], events),
            "observations": _public_observations(
                pack, doc["mode"], state["observations"][observations_before:]),
            "guidance": _guidance(pack, doc, events),
            "render_frame": _frame(pack, doc),
            "snapshot": None,
        }
        persistence.write_session(owner, session_id, doc, expected_epoch=epoch)
        _refresh_index(owner)
        return ack


def _create_branch(owner: str, doc: dict, *, script: list[dict], state: dict,
                   fork_revision: int) -> dict:
    epoch = persistence.epoch(owner)
    live = [d for d in persistence.list_sessions(owner) if not d.get("deleted")]
    if len(live) >= settings.chem_lab_max_sessions_per_owner:
        raise ChemLabError("limit_exceeded", "会话数量已达上限，请先删除旧会话", status=409)
    branch_state = copy.deepcopy(state)
    branch_state["_recent_events"] = []
    session_id = _SESSION_ID_PREFIX + uuid.uuid4().hex[:24]
    now = time.time()
    branch_doc = {
        "session_id": session_id,
        "owner": persistence.safe(owner),
        "experiment_id": doc["experiment_id"],
        "pack_version": doc["pack_version"],
        "pack_hash": doc["pack_hash"],
        "mode": doc["mode"],
        "language": doc["language"],
        "session_seed": doc["session_seed"],
        "state": branch_state,
        "script": copy.deepcopy(script),
        "acks": {},
        "branch": {"parent_session_id": doc["session_id"], "fork_revision": fork_revision},
        "finished": False,
        "result_card": None,
        "sync_required": False,
        "created_at": now,
        "updated_at": now,
    }
    persistence.write_session(owner, session_id, branch_doc, expected_epoch=epoch)
    _refresh_index(owner)
    return _snapshot(owner, branch_doc)


def fork_session(owner: str, session_id: str, body) -> dict:
    _ensure_enabled()
    return _fork(owner, session_id, checkpoint_id=body.checkpoint_id,
                 at_revision=body.at_revision)


def reset_session(owner: str, session_id: str, body) -> dict:
    """从起点（默认）或检查点创建新的运行分支。"""
    _ensure_enabled()
    if body.checkpoint_id:
        return _fork(owner, session_id, checkpoint_id=body.checkpoint_id,
                     at_revision=None)
    return _fork(owner, session_id, checkpoint_id=None, at_revision=0)


def _fork(owner: str, session_id: str, *, checkpoint_id: str | None,
          at_revision: int | None) -> dict:
    with persistence.lock(owner):
        doc = _load_live_doc(owner, session_id)
        script = doc["script"]
        if checkpoint_id:
            store = persistence.read_checkpoints(owner, session_id)
            item = next((entry for entry in store["items"]
                         if entry["checkpoint_id"] == checkpoint_id), None)
            if item is None:
                raise ChemLabError("checkpoint_missing", "检查点不存在", status=404)
            fork_revision = item["script_len"]
            snapshot = _create_branch(owner, doc, script=script[:fork_revision],
                                      state=item["state"], fork_revision=fork_revision)
        else:
            revision = at_revision if at_revision is not None else 0
            if revision > len(script):
                raise ChemLabError("revision_out_of_range",
                                   f"revision 需在 0..{len(script)} 之间", status=409)
            if revision == len(script):
                state = doc["state"]
            else:
                pack = _runtime_pack(doc)
                state, _ = replay_to_revision(
                    pack, script, mode=doc["mode"],
                    session_seed=doc["session_seed"], revision=revision)
            snapshot = _create_branch(owner, doc, script=script[:revision],
                                      state=state, fork_revision=revision)
        return {
            "session": snapshot,
            "source_session_id": session_id,
            "fork_revision": fork_revision if checkpoint_id else revision,
        }


# ---------------------------------------------------------------------------
# Finish
# ---------------------------------------------------------------------------

def finish_session(owner: str, session_id: str) -> dict:
    _ensure_enabled()
    with persistence.lock(owner):
        epoch = persistence.epoch(owner)
        doc = _load_live_doc(owner, session_id)
        if doc.get("result_card") is not None:
            return doc["result_card"]
        pack = _runtime_pack(doc)
        state = doc["state"]
        mode = doc["mode"]
        observations = _public_observations(pack, mode, state["observations"])
        concept_ids: list[str] = []
        for obs in observations:
            for cid in obs.get("concept_ids", []):
                if cid not in concept_ids:
                    concept_ids.append(cid)
        concepts = [pack["_concepts"][cid] for cid in concept_ids if cid in pack["_concepts"]]
        card = {
            "session_id": session_id,
            "experiment_id": doc["experiment_id"],
            "pack_version": doc["pack_version"],
            "mode": mode,
            "goals": goals_status(pack, state),
            "observations": observations,
            "concepts": concepts,
            "completed_steps": list(state["completed_steps"]),
            "sim_time_ms": state["sim_time_ms"],
            "revision": state["revision"],
            "state_hash": state["state_hash"],
            "model_fidelity": pack["model_fidelity"],
            "model_scope": pack["model_scope"],
            "finished_at": time.time(),
        }
        doc["finished"] = True
        doc["result_card"] = card
        doc["updated_at"] = time.time()
        persistence.write_session(owner, session_id, doc, expected_epoch=epoch)
        _refresh_index(owner)
        return card


def purge_owner(owner: str) -> None:
    persistence.purge(owner)
