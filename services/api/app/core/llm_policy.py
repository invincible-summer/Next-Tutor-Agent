"""管理员可在线调整的 LLM 运行参数（「运行参数」面板）。

精简后的分工（2026-09）：
- env/.env 只承载密钥、base URL、模型名等部署必填项；
- 上下文预算、生成参数、出题校验模式等运行参数以内置默认值运行，
  管理员经 ``/admin/llm-policy`` 在线调整，写 ``chat_history/settings/
  llm_policy.json``（file_lock + atomic_write_text），进程内立即生效、
  无需重启——消费点都在调用时读取（见各 getter 的使用方）；
- 策略文件缺失/损坏时回退 `_bootstrap_policy()`：以 env-backed
  ``settings`` 的值为初值，因此老部署 .env 里保留的同名键仍然生效；
  策略文件存在时优先于 env。

值语义：均为「下一次 LLM 调用/agent 回合」生效；进行中的调用不受影响。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock
from .config import settings

from app.core import paths

POLICY_FILE = paths.bind_storage_path(__name__, "POLICY_FILE", "policies", "llm_policy.json")

# --- 边界（快照一并下发，前端镜像到 input min/max） ------------------------
_MIN_WINDOW, _MAX_WINDOW = 8192, 1_000_000
_MIN_OUTPUT, _MAX_OUTPUT = 1024, 200_000
_MIN_CALL_TOKENS, _MAX_CALL_TOKENS = 512, 100_000
_MIN_STEPS, _MAX_STEPS = 1, 30
_MIN_TEMP, _MAX_TEMP = 0.0, 2.0
_VERIFY_MODES = {"critic", "basic", "off"}
_VERSION = 1


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _bootstrap_policy() -> dict[str, Any]:
    base = {
        "context_window": _clamp_int(
            getattr(settings, "llm_context_window", 165536),
            _MIN_WINDOW, _MAX_WINDOW, 165536),
        "max_output_tokens": _clamp_int(
            getattr(settings, "llm_max_output_tokens", 20000),
            _MIN_OUTPUT, _MAX_OUTPUT, 20000),
        "temperature": _clamp_float(
            getattr(settings, "llm_temperature", 0.3), _MIN_TEMP, _MAX_TEMP, 0.3),
        "agent_max_steps": _clamp_int(
            getattr(settings, "agent_max_steps", 6), _MIN_STEPS, _MAX_STEPS, 6),
        "llm_max_tokens": _clamp_int(
            getattr(settings, "llm_max_tokens", 4000),
            _MIN_CALL_TOKENS, _MAX_CALL_TOKENS, 4000),
        "quiz_verify_mode": "critic",
    }
    mode = str(getattr(settings, "quiz_verify_mode", "critic")).strip().lower()
    if mode in _VERIFY_MODES:
        base["quiz_verify_mode"] = mode
    base["updated_at"] = 0.0
    base["version"] = _VERSION
    return base


def _normalize_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _bootstrap_policy()
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("quiz_verify_mode") or base["quiz_verify_mode"]).strip().lower()
    if mode not in _VERIFY_MODES:
        mode = base["quiz_verify_mode"]
    policy = {
        "context_window": _clamp_int(
            data.get("context_window"), _MIN_WINDOW, _MAX_WINDOW,
            base["context_window"]),
        "max_output_tokens": _clamp_int(
            data.get("max_output_tokens"), _MIN_OUTPUT, _MAX_OUTPUT,
            base["max_output_tokens"]),
        "temperature": _clamp_float(
            data.get("temperature"), _MIN_TEMP, _MAX_TEMP, base["temperature"]),
        "agent_max_steps": _clamp_int(
            data.get("agent_max_steps"), _MIN_STEPS, _MAX_STEPS,
            base["agent_max_steps"]),
        "llm_max_tokens": _clamp_int(
            data.get("llm_max_tokens"), _MIN_CALL_TOKENS, _MAX_CALL_TOKENS,
            base["llm_max_tokens"]),
        "quiz_verify_mode": mode,
        "updated_at": float(data.get("updated_at") or 0.0),
        "version": _VERSION,
    }
    # 预算自洽：输出上限不得吞掉整个窗口。
    policy["max_output_tokens"] = min(
        policy["max_output_tokens"], max(_MIN_OUTPUT, policy["context_window"] // 2))
    return policy


def _read_policy() -> dict[str, Any]:
    try:
        return _normalize_policy(json.loads(POLICY_FILE.read_text(encoding="utf-8")))
    except Exception:
        return _bootstrap_policy()


def _write_policy(policy: dict[str, Any]) -> dict[str, Any]:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_policy(policy)
    payload["updated_at"] = time.time()
    with file_lock(POLICY_FILE):
        atomic_write_text(POLICY_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


# 懒加载的进程内状态：消费点调用时才首次读文件，storage sandbox 因此可以在
# 测试 setUp 里先改写 POLICY_FILE 再触发加载；写路径整体替换 dict（GIL 下
# 原子），读方永远看到完整一致的一份。
_state_cache: dict[str, Any] | None = None


def _state() -> dict[str, Any]:
    global _state_cache
    if _state_cache is None:
        _state_cache = _read_policy()
    return _state_cache


def reset_policy_cache() -> None:
    """测试钩子（storage sandbox 用）：清空缓存，下一个 getter 重新读
    （被 sandbox 改写过的）文件。"""
    global _state_cache
    _state_cache = None


def get_policy() -> dict[str, Any]:
    p = _state()
    return {
        "context_window": p["context_window"],
        "max_output_tokens": p["max_output_tokens"],
        "temperature": p["temperature"],
        "agent_max_steps": p["agent_max_steps"],
        "llm_max_tokens": p["llm_max_tokens"],
        "quiz_verify_mode": p["quiz_verify_mode"],
        "updated_at": p["updated_at"],
        "version": p["version"],
        "min_context_window": _MIN_WINDOW,
        "max_context_window": _MAX_WINDOW,
        "min_max_output_tokens": _MIN_OUTPUT,
        "max_max_output_tokens": _MAX_OUTPUT,
        "min_temperature": _MIN_TEMP,
        "max_temperature": _MAX_TEMP,
        "min_agent_max_steps": _MIN_STEPS,
        "max_agent_max_steps": _MAX_STEPS,
        "min_llm_max_tokens": _MIN_CALL_TOKENS,
        "max_llm_max_tokens": _MAX_CALL_TOKENS,
        "verify_modes": sorted(_VERIFY_MODES),
        "scope": "llm_runtime_params",
    }


def set_policy(*, context_window: int, max_output_tokens: int, temperature: float,
               agent_max_steps: int, llm_max_tokens: int,
               quiz_verify_mode: str) -> dict[str, Any]:
    if not (_MIN_WINDOW <= int(context_window) <= _MAX_WINDOW):
        raise ValueError(
            f"context_window must be between {_MIN_WINDOW} and {_MAX_WINDOW}")
    if not (_MIN_OUTPUT <= int(max_output_tokens) <= _MAX_OUTPUT):
        raise ValueError(
            f"max_output_tokens must be between {_MIN_OUTPUT} and {_MAX_OUTPUT}")
    if not (_MIN_TEMP <= float(temperature) <= _MAX_TEMP):
        raise ValueError(
            f"temperature must be between {_MIN_TEMP} and {_MAX_TEMP}")
    if not (_MIN_STEPS <= int(agent_max_steps) <= _MAX_STEPS):
        raise ValueError(
            f"agent_max_steps must be between {_MIN_STEPS} and {_MAX_STEPS}")
    if not (_MIN_CALL_TOKENS <= int(llm_max_tokens) <= _MAX_CALL_TOKENS):
        raise ValueError(
            f"llm_max_tokens must be between {_MIN_CALL_TOKENS} and {_MAX_CALL_TOKENS}")
    mode = str(quiz_verify_mode).strip().lower()
    if mode not in _VERIFY_MODES:
        raise ValueError(f"Unsupported quiz_verify_mode: {quiz_verify_mode}")
    payload = _write_policy({
        "context_window": int(context_window),
        "max_output_tokens": int(max_output_tokens),
        "temperature": float(temperature),
        "agent_max_steps": int(agent_max_steps),
        "llm_max_tokens": int(llm_max_tokens),
        "quiz_verify_mode": mode,
        "updated_at": time.time(),
        "version": _VERSION,
    })
    global _state_cache
    _state_cache = payload
    return get_policy()


# --- 热路径 getter：调用时读取，管理员改动即时生效 -------------------------

def context_window() -> int:
    return int(_state()["context_window"])


def max_output_tokens() -> int:
    return int(_state()["max_output_tokens"])


def temperature() -> float:
    return float(_state()["temperature"])


def agent_max_steps() -> int:
    return int(_state()["agent_max_steps"])


def llm_max_tokens() -> int:
    return int(_state()["llm_max_tokens"])


def quiz_verify_mode() -> str:
    return str(_state()["quiz_verify_mode"])
