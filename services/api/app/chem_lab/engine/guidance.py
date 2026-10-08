"""Deterministic teaching guidance (no AI): hints, evidence, explanations.

Reads the pack's procedure, recent semantic events and the current state
diff; never mutates chemistry. Levels: on_track /
try_again / hint / explain / safety / complete. Hints climb a ladder —
they never perform the step for the student.
"""
from __future__ import annotations

from typing import Any

#: Observation keys the guidance layer treats as "visible evidence".
_LEVEL_ORDER = ("on_track", "try_again", "hint", "explain", "safety", "complete")


def _l10n(pair: dict[str, str], language: str) -> str:
    return pair.get(language) or pair.get("zh") or pair.get("en") or ""


def current_step(pack: dict[str, Any], completed: list[str]) -> dict[str, Any] | None:
    done = set(completed)
    for step in pack.get("procedure", []):
        if step["id"] not in done:
            return step
    return None


def goals_status(pack: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    emitted = {o["key"] for o in state["observations"]}
    status = []
    for goal in state["goals"]:
        spec = next((g for g in pack.get("goals", []) if g["id"] == goal["id"]), None)
        met = spec is not None and all(key in emitted for key in spec["requires_observations"])
        status.append({"id": goal["id"], "status": "met" if met else "pending"})
    return status


def all_goals_met(pack: dict[str, Any], state: dict[str, Any]) -> bool:
    status = goals_status(pack, state)
    return bool(status) and all(g["status"] == "met" for g in status)


def build_guidance(pack: dict[str, Any], state: dict[str, Any], *,
                   last_events: list[dict[str, Any]], language: str) -> dict[str, Any]:
    """Compute the guidance card for the current state."""
    if state["phase"] == "safety_locked":
        return {
            "level": "safety",
            "text": _profile_note(pack, language),
            "evidence_event_seq": state["seq"],
            "concept_ids": [],
            "model_scope": pack["model_fidelity"],
        }

    step = current_step(pack, state["completed_steps"]) if state["mode"] != "explore" else None
    rejected = [e for e in last_events if e["kind"] == "command_rejected"]
    new_observations = [e for e in last_events if e["kind"] == "observation_emitted"]

    if state["phase"] == "completed" or (pack.get("goals") and all_goals_met(pack, state)):
        return {
            "level": "complete",
            "text": _l10n({"zh": "实验目标已达成，可以查看结果卡或从检查点创建分支继续探索。",
                           "en": "Experiment goals met. Review the result card or branch from a checkpoint."}, language),
            "evidence_event_seq": state["seq"],
            "concept_ids": [],
            "model_scope": pack["model_fidelity"],
        }

    if rejected:
        reason = rejected[-1]["data"].get("reason", "invalid")
        return {
            "level": "try_again",
            "text": _rejection_text(reason, language),
            "evidence_event_seq": rejected[-1]["seq"],
            "concept_ids": step["concept_ids"] if step else [],
            "model_scope": pack["model_fidelity"],
        }

    if step is not None:
        visits = state["step_visits"].get(step["id"], 0)
        ladder = step["hint_ladder"]
        index = min(visits, len(ladder) - 1)
        if new_observations or visits == 0:
            text = _l10n(step["objective"], language)
            level = "on_track"
        else:
            text = _l10n(ladder[index], language)
            level = "hint"
        return {
            "level": level,
            "text": text,
            "step_id": step["id"],
            "hint_index": index,
            "evidence_event_seq": state["seq"],
            "concept_ids": step["concept_ids"],
            "model_scope": pack["model_fidelity"],
        }

    if new_observations:
        return {
            "level": "explain",
            "text": _l10n({"zh": "观察到了新的现象，可以在观察记录中查看证据链。",
                           "en": "New phenomenon observed — open the log for the evidence chain."}, language),
            "evidence_event_seq": new_observations[-1]["seq"],
            "concept_ids": [],
            "model_scope": pack["model_fidelity"],
        }

    return {
        "level": "on_track",
        "text": _l10n({"zh": "继续你的实验；已建模范围见实验说明。",
                       "en": "Keep going; the modeled scope is described in the brief."}, language),
        "evidence_event_seq": state["seq"],
        "concept_ids": [],
        "model_scope": pack["model_fidelity"],
    }


def _profile_note(pack: dict[str, Any], language: str) -> str:
    return _l10n(pack["safety_profile"]["notes"], language)


def _rejection_text(reason: str, language: str) -> str:
    table = {
        "invalid_params": ("这条操作的参数不合法，请检查对象和数量。",
                           "That operation's parameters were invalid — check the object and amount."),
        "unknown_object": ("找不到这个器材或容器，请重新选择。",
                           "That object could not be found — pick it again."),
        "capacity_exceeded": ("目标容器装不下这么多液体，请减少用量或换更大的容器。",
                              "The target vessel cannot hold that much — use less or a larger vessel."),
        "instrument_empty": ("这个仪器现在是空的，请先吸取或装入试剂。",
                             "The instrument is empty — draw up a reagent first."),
        "not_clean": ("滴管里残留着其他试剂；请先清洗或换一支，避免污染。",
                      "The dropper still holds another reagent — wash it or use a fresh one."),
        "incompatible_device": ("这个容器不能放在该装置上加热。",
                                "This vessel cannot be heated on that device."),
        "not_connected": ("还没有连接导管，气体无法被收集。",
                          "No delivery tube connected — the gas cannot be collected."),
        "safety_locked": ("实验已因安全原因锁定，请查看原因并从检查点继续。",
                          "The bench is safety-locked — read the reason and continue from a checkpoint."),
        "phase": ("当前状态下不能执行这个操作。",
                  "That operation is not available right now."),
        "not_modeled": ("当前实验还没有描述这组物质混合后的变化。可以返回上一步或选择已支持的组合。",
                        "This combination is not described by the current model. Step back or choose a supported one."),
    }
    zh, en = table.get(reason, table["invalid_params"])
    return _l10n({"zh": zh, "en": en}, language)


def evidence_chain(pack: dict[str, Any], state: dict[str, Any],
                   observation: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Observation → events → rule → concept cards (证据层)."""
    seq = observation["seq"]
    related = [e for e in events if e["seq"] <= seq and e["vessel_id"] == observation.get("vessel_id")]
    rule_id = observation.get("rule_id", "")
    rule = next((r for r in pack.get("_rules", []) if r["id"] == rule_id), None)
    concepts = [c for c in pack.get("_concepts", []) if c["id"] in observation.get("concept_ids", [])]
    return {
        "observation": observation,
        "events": related[-12:],
        "rule": {"id": rule["id"], "public_note": rule["public_note"]} if rule else None,
        "concepts": concepts,
        "model_scope": pack["model_fidelity"],
    }
