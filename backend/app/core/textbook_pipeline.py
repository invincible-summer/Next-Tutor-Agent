"""Runtime administrator policy for textbook build pipeline scheduling.

只治理「执行调度」：不改变解析方式、提示词或任何产出内容——独立的 LLM
调用、教材组内的卷、排队中的教材在时间上如何重叠，全部由此处限流。

legacy 模式把所有有效并发强制为 1：逐章/逐卷/逐书恢复与历史严格串行
实现完全一致的执行顺序（FIFO 门下每次只放行一个调用）。

The global LLM gate is condition-based and resizable at runtime: in-flight
calls admitted under an old limit keep running; new admissions observe the
current limit immediately (no restart needed).  管理员经
``PUT /api/v1/admin/textbook-pipeline`` 在线调整，策略落盘
``chat_history/settings/textbook_pipeline_policy.json``（优先于 env 默认）。
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from .atomic import atomic_write_text, file_lock
from .config import settings

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_POLICY_FILE = _PROJECT_ROOT / "chat_history" / "settings" / "textbook_pipeline_policy.json"

_MODES = {"parallel", "legacy"}
_MIN_BUILD, _MAX_BUILD = 1, 4
_MIN_VOLUME, _MAX_VOLUME = 1, 4
_MIN_LLM, _MAX_LLM = 1, 8
_DEFAULT_MODE = "parallel"
_DEFAULT_BUILD = 2
_DEFAULT_VOLUME = 2
_DEFAULT_LLM = 4


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _bootstrap_policy() -> dict[str, Any]:
    mode = str(getattr(settings, "textbook_parse_mode", _DEFAULT_MODE)
               or _DEFAULT_MODE).strip().lower()
    if mode not in _MODES:
        mode = _DEFAULT_MODE
    return {
        "mode": mode,
        "build_concurrency": _clamp(
            getattr(settings, "textbook_build_concurrency", _DEFAULT_BUILD),
            _MIN_BUILD, _MAX_BUILD, _DEFAULT_BUILD),
        "volume_concurrency": _clamp(
            getattr(settings, "textbook_volume_concurrency", _DEFAULT_VOLUME),
            _MIN_VOLUME, _MAX_VOLUME, _DEFAULT_VOLUME),
        "llm_concurrency": _clamp(
            getattr(settings, "textbook_llm_concurrency", _DEFAULT_LLM),
            _MIN_LLM, _MAX_LLM, _DEFAULT_LLM),
        "updated_at": 0.0,
        "version": 1,
    }


def _normalize_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _bootstrap_policy()
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or base["mode"]).strip().lower()
    if mode not in _MODES:
        mode = _DEFAULT_MODE
    return {
        "mode": mode,
        "build_concurrency": _clamp(
            data.get("build_concurrency"), _MIN_BUILD, _MAX_BUILD,
            base["build_concurrency"]),
        "volume_concurrency": _clamp(
            data.get("volume_concurrency"), _MIN_VOLUME, _MAX_VOLUME,
            base["volume_concurrency"]),
        "llm_concurrency": _clamp(
            data.get("llm_concurrency"), _MIN_LLM, _MAX_LLM,
            base["llm_concurrency"]),
        "updated_at": float(data.get("updated_at") or 0.0),
        "version": 1,
    }


def _read_policy() -> dict[str, Any]:
    try:
        return _normalize_policy(json.loads(_POLICY_FILE.read_text(encoding="utf-8")))
    except Exception:
        return _bootstrap_policy()


def _write_policy(policy: dict[str, Any]) -> None:
    _POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_policy(policy)
    payload["updated_at"] = time.time()
    with file_lock(_POLICY_FILE):
        atomic_write_text(_POLICY_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


def effective_limits(policy: dict[str, Any] | None = None) -> dict[str, int]:
    """策略的有效并发。legacy 模式全部强制 1（历史严格串行行为）。"""
    p = policy if policy is not None else _RUNTIME.policy
    legacy = str(p.get("mode") or "") == "legacy"
    return {
        "build": 1 if legacy else int(p["build_concurrency"]),
        "volume": 1 if legacy else int(p["volume_concurrency"]),
        "llm": 1 if legacy else int(p["llm_concurrency"]),
    }


class _Runtime:
    def __init__(self) -> None:
        policy = _read_policy()
        self.policy = policy
        self.gate_active = 0
        self.gate_waiting = 0
        self.condition: asyncio.Condition | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _condition(self) -> asyncio.Condition:
        loop = asyncio.get_running_loop()
        if self.condition is None or self.loop is not loop:
            # 新事件循环（测试频繁建 loop）：门状态重置，没有在途持有者。
            self.loop = loop
            self.condition = asyncio.Condition()
            self.gate_active = 0
            self.gate_waiting = 0
        return self.condition

    def snapshot(self) -> dict[str, Any]:
        limits = effective_limits(self.policy)
        return {
            "mode": self.policy["mode"],
            "build_concurrency": int(self.policy["build_concurrency"]),
            "volume_concurrency": int(self.policy["volume_concurrency"]),
            "llm_concurrency": int(self.policy["llm_concurrency"]),
            "effective_limits": limits,
            "gate_active": self.gate_active,
            "gate_waiting": self.gate_waiting,
            "min_build_concurrency": _MIN_BUILD,
            "max_build_concurrency": _MAX_BUILD,
            "min_volume_concurrency": _MIN_VOLUME,
            "max_volume_concurrency": _MAX_VOLUME,
            "min_llm_concurrency": _MIN_LLM,
            "max_llm_concurrency": _MAX_LLM,
            "modes": sorted(_MODES),
            "updated_at": self.policy.get("updated_at") or 0.0,
            "scope": "textbook_build_scheduling_only",
        }

    async def set_policy(self, *, mode: str, build_concurrency: int,
                         volume_concurrency: int, llm_concurrency: int) -> dict[str, Any]:
        if mode not in _MODES:
            raise ValueError(f"Unsupported textbook pipeline mode: {mode}")
        if not (_MIN_BUILD <= int(build_concurrency) <= _MAX_BUILD):
            raise ValueError(
                f"build_concurrency must be between {_MIN_BUILD} and {_MAX_BUILD}")
        if not (_MIN_VOLUME <= int(volume_concurrency) <= _MAX_VOLUME):
            raise ValueError(
                f"volume_concurrency must be between {_MIN_VOLUME} and {_MAX_VOLUME}")
        if not (_MIN_LLM <= int(llm_concurrency) <= _MAX_LLM):
            raise ValueError(
                f"llm_concurrency must be between {_MIN_LLM} and {_MAX_LLM}")
        new_policy = {
            "mode": mode,
            "build_concurrency": int(build_concurrency),
            "volume_concurrency": int(volume_concurrency),
            "llm_concurrency": int(llm_concurrency),
            "updated_at": time.time(),
            "version": 1,
        }
        _write_policy(new_policy)
        self.policy = _normalize_policy(new_policy)
        # 在线调整 LLM 门：门每次准入动态读取生效限额，唤醒等待者重新判定。
        condition = self._condition()
        async with condition:
            condition.notify_all()
        return self.snapshot()


_RUNTIME = _Runtime()


def get_policy() -> dict[str, Any]:
    return _RUNTIME.snapshot()


async def set_policy(mode: str, build_concurrency: int, volume_concurrency: int,
                     llm_concurrency: int) -> dict[str, Any]:
    return await _RUNTIME.set_policy(
        mode=str(mode), build_concurrency=int(build_concurrency),
        volume_concurrency=int(volume_concurrency),
        llm_concurrency=int(llm_concurrency))


def build_concurrency() -> int:
    return effective_limits()["build"]


def volume_concurrency() -> int:
    return effective_limits()["volume"]


def llm_concurrency() -> int:
    return effective_limits()["llm"]


@asynccontextmanager
async def llm_gate() -> AsyncIterator[None]:
    """全局图谱 LLM 并发门（FIFO 准入，限额动态读取当前策略）。

    asyncio.Condition 的等待队列是 FIFO：limit=1 时唤醒顺序与任务创建顺序
    一致，legacy 模式下逐章调用顺序与历史串行 for 循环完全相同。策略在线
    调整（含 legacy 切换）对新准入立即生效；在途调用按旧限额跑完。
    """
    condition = _RUNTIME._condition()
    async with condition:
        _RUNTIME.gate_waiting += 1
        try:
            while _RUNTIME.gate_active >= effective_limits()["llm"]:
                await condition.wait()
        finally:
            _RUNTIME.gate_waiting = max(0, _RUNTIME.gate_waiting - 1)
        _RUNTIME.gate_active += 1
    try:
        yield
    finally:
        async with condition:
            _RUNTIME.gate_active = max(0, _RUNTIME.gate_active - 1)
            condition.notify(1)
