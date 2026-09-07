"""Bounded, session-attributed prompt memory.

This store is deliberately separate from M2 mastery and M6 learning records.
Only four compact categories may be injected into future prompts: overall
learning situation, current level, tone preference, and explanation preference.
No concept names, question text, answers, or transcript summaries are stored.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock
from ...prompts.registry import get as get_prompt

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"
_POLICY_PATH = _STUDENTS_DIR / "prompt_memory_policy.json"
_DEFAULT_POLICY = {"default_window": 15, "max_window": 30,
                   "core_char_limit": 1800, "directive_char_limit": 2600}


def _safe(value: str) -> str:
    return Path(str(value or "")).name


def _path(student_id: str) -> Path:
    return _STUDENTS_DIR / f"{_safe(student_id)}.prompt_memory.json"


def _pref_path(student_id: str) -> Path:
    return _STUDENTS_DIR / f"{_safe(student_id)}.prompt_memory_pref.json"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def get_policy() -> dict[str, int]:
    raw = _read(_POLICY_PATH, {})
    out = dict(_DEFAULT_POLICY)
    if isinstance(raw, dict):
        out.update(raw)
    out["default_window"] = max(5, min(30, int(out.get("default_window", 15))))
    out["max_window"] = max(5, min(30, int(out.get("max_window", 30))))
    out["default_window"] = min(out["default_window"], out["max_window"])
    out["core_char_limit"] = max(400, min(5000, int(out.get("core_char_limit", 1800))))
    out["directive_char_limit"] = max(600, min(6000, int(out.get("directive_char_limit", 2600))))
    return out


def set_policy(**fields: Any) -> dict[str, int]:
    policy = get_policy()
    for key in _DEFAULT_POLICY:
        if fields.get(key) is not None:
            policy[key] = int(fields[key])
    policy["default_window"] = max(5, min(30, policy["default_window"]))
    policy["max_window"] = max(5, min(30, policy["max_window"]))
    policy["default_window"] = min(policy["default_window"], policy["max_window"])
    policy["core_char_limit"] = max(400, min(5000, policy["core_char_limit"]))
    policy["directive_char_limit"] = max(600, min(6000, policy["directive_char_limit"]))
    _write(_POLICY_PATH, policy)
    return policy


def get_user_window(student_id: str) -> int:
    policy = get_policy()
    pref = _read(_pref_path(student_id), {})
    requested = int((pref or {}).get("window_size", policy["default_window"]))
    return max(5, min(policy["max_window"], requested))


def set_user_window(student_id: str, window_size: int) -> int:
    value = max(5, min(get_policy()["max_window"], int(window_size)))
    _write(_pref_path(student_id), {"window_size": value, "updated_at": time.time()})
    # Apply the smaller window immediately; overflow is deterministically folded.
    state = load_state(student_id)
    state["window_size"] = value
    _trim_overflow(state)
    save_state(student_id, state)
    return value


def _empty_state(student_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "student_id": _safe(student_id),
        "window_size": get_user_window(student_id),
        "core_profile": {
            "learning_summary": "", "current_level": "",
            "tone_preference": "", "explanation_preference": "",
        },
        "recent_sessions": [],
        # Attribution metadata only: no transcript/content. It lets deletion
        # report whether one specific chat was folded into the aggregate.
        "compacted_session_ids": [],
        "compacted_session_count": 0,
        "compaction_generation": 0,
        "last_compacted_at": 0.0,
        "core_needs_llm": False,
        "updated_at": time.time(),
    }


def load_state(student_id: str) -> dict[str, Any]:
    data = _read(_path(student_id), None)
    if not isinstance(data, dict):
        return _empty_state(student_id)
    base = _empty_state(student_id)
    base.update(data)
    profile = dict(base["core_profile"] or {})
    for key in ("learning_summary", "current_level", "tone_preference", "explanation_preference"):
        profile[key] = str(profile.get(key) or "")
    base["core_profile"] = profile
    base["recent_sessions"] = [x for x in (base.get("recent_sessions") or [])
                               if isinstance(x, dict) and _safe(x.get("session_id", ""))]
    compacted_ids: list[str] = []
    for raw_sid in (base.get("compacted_session_ids") or []):
        sid = _safe(raw_sid)
        if sid and sid not in compacted_ids:
            compacted_ids.append(sid)
    base["compacted_session_ids"] = compacted_ids
    base["window_size"] = get_user_window(student_id)
    return base


def save_state(student_id: str, state: dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    _write(_path(student_id), state)


def _generic_summary(contribution: dict[str, Any]) -> str:
    stats = contribution.get("outcomes") or {}
    correct, wrong = int(stats.get("correct", 0)), int(stats.get("wrong", 0))
    engaged = int(stats.get("engaged", 0))
    if correct + wrong:
        if wrong > correct:
            return "近期学习中仍需要更多基础巩固与分步反馈。"
        if correct >= wrong * 2 and correct >= 2:
            return "近期学习表现较稳定，可在保持反馈的同时逐步提高挑战。"
    if engaged:
        return "近期保持学习参与，适合持续提供清晰反馈。"
    return ""


def _level_from(contribution: dict[str, Any]) -> str:
    stats = contribution.get("outcomes") or {}
    correct, wrong = int(stats.get("correct", 0)), int(stats.get("wrong", 0))
    if wrong > correct:
        return "当前整体水平偏基础，关键步骤需要更充分的支架。"
    if correct >= 2 and correct > wrong:
        return "当前整体水平处于稳步发展阶段，可适度增加综合性。"
    return ""


def _detect_preferences(message: str) -> tuple[str, str]:
    text = str(message or "")[:1000]
    tone = ""
    explanation = ""
    if re.search(r"简洁|简短|直接说|不要废话|只要结论", text):
        tone = "偏好简洁直接、少铺垫的表达。"
    elif re.search(r"耐心|温柔|鼓励|别太严厉", text):
        tone = "偏好耐心、鼓励式的表达。"
    if re.search(r"一步一步|分步骤|详细步骤|慢慢讲", text):
        explanation = "偏好分步骤、显式展示关键推理节点。"
    elif re.search(r"举例|例子|例题|实际应用", text):
        explanation = "偏好用短例子或例题带动解释。"
    elif re.search(r"先.*结论|结论.*先", text):
        explanation = "偏好先给结论，再补充理由。"
    return tone, explanation


def _merge_text(old: str, new: str, limit: int = 500) -> str:
    old, new = str(old or "").strip(), str(new or "").strip()
    if not new or new in old:
        return old[:limit]
    if not old:
        return new[:limit]
    return (old.rstrip("。") + "；" + new)[:limit]


def _fold_into_core(state: dict[str, Any], contribution: dict[str, Any]) -> None:
    profile = state["core_profile"]
    profile["learning_summary"] = _merge_text(
        profile.get("learning_summary", ""), _generic_summary(contribution))
    profile["current_level"] = _merge_text(
        profile.get("current_level", ""), _level_from(contribution))
    profile["tone_preference"] = _merge_text(
        profile.get("tone_preference", ""), contribution.get("tone_preference", ""))
    profile["explanation_preference"] = _merge_text(
        profile.get("explanation_preference", ""), contribution.get("explanation_preference", ""))
    state["compacted_session_count"] = int(state.get("compacted_session_count", 0)) + 1
    state["compaction_generation"] = int(state.get("compaction_generation", 0)) + 1
    state["last_compacted_at"] = time.time()
    state["core_needs_llm"] = True


def _trim_overflow(state: dict[str, Any]) -> list[str]:
    recent = state.get("recent_sessions") or []
    limit = int(state.get("window_size") or 15)
    compacted: list[str] = []
    while len(recent) > limit:
        old = recent.pop(0)
        if old.get("has_contribution"):
            _fold_into_core(state, old)
            sid = _safe(old.get("session_id", ""))
            ids = state.setdefault("compacted_session_ids", [])
            if sid and sid not in ids:
                ids.append(sid)
        compacted.append(str(old.get("session_id") or ""))
    state["recent_sessions"] = recent
    return compacted


def register_session(student_id: str, session_id: str, workspace_id: str = "") -> dict[str, Any]:
    """Register a globally-counted conversation boundary, idempotently."""
    sid = _safe(session_id)
    if not sid:
        return {"registered": False, "needs_compaction": False}
    path = _path(student_id)
    with file_lock(path):
        state = load_state(student_id)
        if any(x.get("session_id") == sid for x in state["recent_sessions"]):
            return {"registered": False, "needs_compaction": bool(state.get("core_needs_llm"))}
        state["recent_sessions"].append({
            "session_id": sid, "workspace_id": _safe(workspace_id),
            "created_at": time.time(), "updated_at": time.time(),
            "has_contribution": False,
            "outcomes": {"correct": 0, "wrong": 0, "engaged": 0},
            "tone_preference": "", "explanation_preference": "",
        })
        compacted = _trim_overflow(state)
        save_state(student_id, state)
        return {"registered": True, "compacted_session_ids": compacted,
                "needs_compaction": bool(state.get("core_needs_llm"))}


def record_contribution(student_id: str, session_id: str, *, workspace_id: str = "",
                        events: list[dict[str, Any]] | None = None,
                        user_message: str = "", strategy_outcome: str = "") -> dict[str, Any]:
    """Update one recent session contribution without storing teaching content."""
    register_session(student_id, session_id, workspace_id)
    sid = _safe(session_id)
    path = _path(student_id)
    with file_lock(path):
        state = load_state(student_id)
        item = next((x for x in state["recent_sessions"] if x.get("session_id") == sid), None)
        if item is None:
            # The configured window may be smaller than concurrent session creation.
            return {"status": "compacted", "needs_compaction": bool(state.get("core_needs_llm"))}
        outcomes = item.setdefault("outcomes", {"correct": 0, "wrong": 0, "engaged": 0})
        signals = [str((e or {}).get("event_type") or (e or {}).get("type") or "").lower()
                   for e in (events or [])]
        payloads = [(e or {}).get("payload") or {} for e in (events or [])]
        outcome_text = str(strategy_outcome or "").lower()
        payload_correct = any(p.get("correct") is True for p in payloads if isinstance(p, dict))
        payload_wrong = any(p.get("correct") is False for p in payloads if isinstance(p, dict))
        if outcome_text == "correct" or payload_correct or any("correct" in x or "master" in x for x in signals):
            outcomes["correct"] = int(outcomes.get("correct", 0)) + 1
        elif outcome_text == "wrong" or payload_wrong or any("wrong" in x or "struggle" in x for x in signals):
            outcomes["wrong"] = int(outcomes.get("wrong", 0)) + 1
        else:
            outcomes["engaged"] = int(outcomes.get("engaged", 0)) + 1
        tone, explanation = _detect_preferences(user_message)
        item["tone_preference"] = _merge_text(item.get("tone_preference", ""), tone, 300)
        item["explanation_preference"] = _merge_text(
            item.get("explanation_preference", ""), explanation, 300)
        item["has_contribution"] = True
        item["updated_at"] = time.time()
        save_state(student_id, state)
        return {"status": "updated", "needs_compaction": bool(state.get("core_needs_llm"))}


def session_forget_status(student_id: str, session_id: str) -> str:
    """Return exact attribution status where the persisted generation allows."""
    state = load_state(student_id)
    sid = _safe(session_id)
    recent = next((x for x in state["recent_sessions"]
                   if x.get("session_id") == sid), None)
    if recent is not None:
        return "recent" if recent.get("has_contribution") else "none"
    if sid in set(state.get("compacted_session_ids") or []):
        return "compacted"
    # Version-1 states written before compacted_session_ids only retained a
    # total count. Do not falsely claim this particular chat was compacted.
    known = len(state.get("compacted_session_ids") or [])
    return "legacy_unknown" if int(state.get("compacted_session_count", 0)) > known else "none"


def forget_session_contribution(student_id: str, session_id: str) -> str:
    """Remove exact recent attribution or unlink an already-folded chat id."""
    sid = _safe(session_id)
    path = _path(student_id)
    with file_lock(path):
        state = load_state(student_id)
        before = len(state["recent_sessions"])
        state["recent_sessions"] = [x for x in state["recent_sessions"]
                                    if x.get("session_id") != sid]
        if len(state["recent_sessions"]) != before:
            save_state(student_id, state)
            return "forgotten"
        compacted_ids = list(state.get("compacted_session_ids") or [])
        if sid in compacted_ids:
            # The aggregate influence cannot be decomposed, but the permanent
            # purge must still remove this chat's identifying attribution.
            state["compacted_session_ids"] = [x for x in compacted_ids if x != sid]
            save_state(student_id, state)
            return "compacted_unavailable"
        known = len(compacted_ids)
        if int(state.get("compacted_session_count", 0)) > known:
            return "legacy_unknown"
        return "not_found"


def _render_profile(profile: dict[str, Any], recent: list[dict[str, Any]]) -> str:
    latest_learning = ""
    latest_level = ""
    tone = str(profile.get("tone_preference") or "")
    explanation = str(profile.get("explanation_preference") or "")
    for item in recent:
        if not item.get("has_contribution"):
            continue
        latest_learning = _merge_text(latest_learning, _generic_summary(item), 700)
        latest_level = _merge_text(latest_level, _level_from(item), 500)
        tone = _merge_text(tone, item.get("tone_preference", ""), 500)
        explanation = _merge_text(explanation, item.get("explanation_preference", ""), 500)
    fields = [
        ("总体学习情况", _merge_text(str(profile.get("learning_summary") or ""), latest_learning, 800)),
        ("当前水平", _merge_text(str(profile.get("current_level") or ""), latest_level, 600)),
        ("语气偏好", tone),
        ("讲解偏好", explanation),
    ]
    lines = [f"- {label}：{value}" for label, value in fields if value]
    return "[提示词记忆·精简画像]\n" + "\n".join(lines) if lines else ""


def build_directive(student_id: str) -> str:
    state = load_state(student_id)
    text = _render_profile(state["core_profile"], state["recent_sessions"])
    return text[:get_policy()["directive_char_limit"]]


async def maybe_compact_core(student_id: str, llm: Any | None) -> dict[str, Any]:
    """Run one whole-profile LLM compression for the current generation."""
    if llm is None:
        return {"status": "deferred"}
    state = load_state(student_id)
    if not state.get("core_needs_llm"):
        return {"status": "not_due"}
    generation = int(state.get("compaction_generation", 0))
    raw_profile = _render_profile(state["core_profile"], [])
    prompt = get_prompt("prompt_memory_compact").text.format(profile=raw_profile)
    try:
        result, _ = await llm.complete(
            [{"role": "user", "content": prompt}], temperature=0.0,
            max_tokens=1000, disable_thinking=True)
        match = re.search(r"\{.*\}", result or "", re.S)
        parsed = json.loads(match.group(0) if match else (result or "{}"))
    except Exception:
        return {"status": "llm_failed"}
    latest = load_state(student_id)
    if int(latest.get("compaction_generation", 0)) != generation:
        return {"status": "stale"}
    if not latest.get("core_needs_llm"):
        return {"status": "already_compacted"}
    profile = latest["core_profile"]
    allowed = ("learning_summary", "current_level", "tone_preference", "explanation_preference")
    for key in allowed:
        profile[key] = str(parsed.get(key) or profile.get(key) or "")[:700]
    # Hard cap is enforced after parsing, independent of model compliance.
    while len(json.dumps(profile, ensure_ascii=False)) > get_policy()["core_char_limit"]:
        longest = max(allowed, key=lambda k: len(profile[k]))
        profile[longest] = profile[longest][:-50]
        if not profile[longest]:
            break
    latest["core_needs_llm"] = False
    save_state(student_id, latest)
    return {"status": "compacted", "generation": generation}


def public_view(student_id: str) -> dict[str, Any]:
    state = load_state(student_id)
    return {
        "window_size": state["window_size"],
        "max_window": get_policy()["max_window"],
        "core_profile": state["core_profile"],
        "recent_sessions": [{
            "session_id": x.get("session_id"), "workspace_id": x.get("workspace_id", ""),
            "created_at": x.get("created_at"), "updated_at": x.get("updated_at"),
            "has_contribution": bool(x.get("has_contribution")),
        } for x in state["recent_sessions"]],
        "compacted_session_count": int(state.get("compacted_session_count", 0)),
        "compacted_attribution_count": len(state.get("compacted_session_ids") or []),
        "legacy_compacted_attribution_unknown": max(
            0, int(state.get("compacted_session_count", 0))
            - len(state.get("compacted_session_ids") or [])),
        "compaction_generation": int(state.get("compaction_generation", 0)),
        "last_compacted_at": float(state.get("last_compacted_at", 0) or 0),
        "directive_chars": len(build_directive(student_id)),
    }
