"""课堂幂等与配额（plan.md §14.3/§15.4）。

幂等映射存 owner.json（沙箱 patch 自动生效，无进程级缓存）：
- owner + endpoint scope + key 定位；canonical body hash 不一致 → 409
  idempotency_conflict；一致 → 返回原接受结果，不重复执行、不扣额。
- 保留 ≥7 天；课程仍在时与 lesson/job 同寿命；账号清理一并清理。

配额：单用户新建/重生成 3 次/10 分钟、20 次/日（滑动窗口时间戳）。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from ..core import classroom_store as store
from . import limits
from .errors import ClassroomError

_MAX_ENTRIES = 256


def _owner_file_lock(owner_id: str):
    return store.file_lock(store.owner_meta_path(owner_id))


def _load(owner_id: str) -> dict:
    record = store.read_json(store.owner_meta_path(owner_id)) or {}
    if "idempotency" not in record:
        record["idempotency"] = {}
    if "quota" not in record:
        record["quota"] = {}
    return record


def _save(owner_id: str, record: dict) -> None:
    store.write_json(store.owner_meta_path(owner_id), record)


def lookup(owner_id: str, scope: str, key: str,
           body_hash: str) -> dict | None:
    """已存在的同 key 映射；hash 不一致抛 409。"""
    record = _load(owner_id)
    entry = (record.get("idempotency") or {}).get(f"{scope}:{key}")
    if not entry:
        return None
    if str(entry.get("body_hash") or "") != body_hash:
        raise ClassroomError("idempotency_conflict",
                             "同一 Idempotency-Key 的请求体不一致")
    return dict(entry.get("result") or {})


def remember(owner_id: str, scope: str, key: str, body_hash: str,
             result: dict[str, Any]) -> None:
    with _owner_file_lock(owner_id):
        record = _load(owner_id)
        entries = record.setdefault("idempotency", {})
        entries[f"{scope}:{key}"] = {
            "body_hash": body_hash,
            "result": dict(result),
            "created_at": time.time(),
        }
        # 有界：超出按 created_at 淘汰最旧
        if len(entries) > _MAX_ENTRIES:
            ordered = sorted(entries.items(),
                             key=lambda kv: kv[1].get("created_at", 0))
            for stale_key, _ in ordered[:len(entries) - _MAX_ENTRIES]:
                entries.pop(stale_key, None)
        _save(owner_id, record)


def body_hash_of(obj: Any) -> str:
    return store.canonical_hash(obj)


# ---------------------------------------------------------------------------
# 配额
# ---------------------------------------------------------------------------

def _prune(stamps: list[float], now: float, window: float) -> list[float]:
    return [t for t in stamps if now - t < window]


def check_generation_quota(owner_id: str) -> None:
    """超限抛 quota_exceeded（429）；不消耗额度。"""
    now = time.time()
    record = _load(owner_id)
    quota = record.get("quota") or {}
    window_events = _prune(list(quota.get("generation_window") or []),
                           now, limits.GENERATION_WINDOW_SECONDS)
    daily_events = _prune(list(quota.get("generation_daily") or []),
                          now, 86400)
    if len(window_events) >= limits.GENERATION_WINDOW_COUNT:
        raise ClassroomError(
            "quota_exceeded",
            f"生成请求过于频繁（{limits.GENERATION_WINDOW_SECONDS // 60} 分钟内"
            f"最多 {limits.GENERATION_WINDOW_COUNT} 次）",
            retryable=True)
    if len(daily_events) >= limits.GENERATION_DAILY_COUNT:
        raise ClassroomError("quota_exceeded",
                             "今日生成次数已达上限，请明天再试",
                             retryable=True)


def consume_generation_quota(owner_id: str) -> None:
    with _owner_file_lock(owner_id):
        record = _load(owner_id)
        now = time.time()
        quota = record.setdefault("quota", {})
        quota["generation_window"] = _prune(
            list(quota.get("generation_window") or []),
            now, limits.GENERATION_WINDOW_SECONDS) + [now]
        quota["generation_daily"] = _prune(
            list(quota.get("generation_daily") or []),
            now, 86400) + [now]
        _save(owner_id, record)
