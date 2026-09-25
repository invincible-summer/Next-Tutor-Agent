"""管理员可选学习评价方式（update_plan §5–§6）。

两个互斥调度档位，默认方案 1（immediate）：
- immediate：受理即入队，worker 立即评价（阶段 B 已闭环）；
- daily_midnight：受理时只落盘，job 的 eligible_after_utc 冻结为下一个
  业务时区自然日零点（UTC instant）；零点后由 DailyPlanner 关闭上一日
  窗口并放行当日候选。

合同要点（§5.1/§5.3/§6.3）：
- 日期边界：本地 D+1 00:00 执行的是 D 日 [00:00, 24:00) 的历史；
- 时区是管理员配置的明确 IANA 业务时区（默认 Asia/Singapore），
  不依赖服务器进程 TZ 或前端浏览器；
- daily_local_time 固定 "00:00"，不提供任意 cron；
- 策略经版本化持久化（chat_history/settings/learner_evaluation_policy.json，
  core.atomic 写入）；expected_revision CAS，冲突 409；
- LEARNER_EVALUATION_MODE=active/off 是运行停用开关，与 1/2 档位正交。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
POLICY_FILE = (_PROJECT_ROOT / "chat_history" / "settings" /
               "learner_evaluation_policy.json")

SCHEDULE_IMMEDIATE = "immediate"
SCHEDULE_DAILY_MIDNIGHT = "daily_midnight"
SCHEDULES = {SCHEDULE_IMMEDIATE, SCHEDULE_DAILY_MIDNIGHT}
DEFAULT_TIMEZONE = "Asia/Singapore"      # §5.1：明确业务时区，不碰运气
DEFAULT_SCHEDULE = SCHEDULE_IMMEDIATE    # 缺失配置 → 方案 1
SCHEMA_VERSION = 1

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


class PolicyConflict(ValueError):
    """expected_revision 不匹配（API 层映射 409）。"""


class PolicyInvalid(ValueError):
    """字段白名单/时区校验失败（API 层映射 422）。"""


def default_policy(now: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "evaluation_schedule": DEFAULT_SCHEDULE,
        "timezone": DEFAULT_TIMEZONE,
        "daily_local_time": "00:00",
        "effective_at": now or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }


def load_policy(*, refresh: bool = False) -> dict[str, Any]:
    """读策略；文件缺失/损坏 → 默认（方案 1），绝不抛出阻断受理。"""
    global _cache
    with _lock:
        if _cache is not None and not refresh:
            return dict(_cache)
        data: dict[str, Any] = {}
        try:
            if POLICY_FILE.exists():
                data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        policy = _normalize(data)
        _cache = policy
        return dict(policy)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    schedule = str(data.get("evaluation_schedule") or DEFAULT_SCHEDULE)
    if schedule not in SCHEDULES:
        schedule = DEFAULT_SCHEDULE
    tz = str(data.get("timezone") or DEFAULT_TIMEZONE)
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        tz = DEFAULT_TIMEZONE
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": max(1, int(data.get("revision") or 1)),
        "evaluation_schedule": schedule,
        "timezone": tz,
        "daily_local_time": "00:00",
        "effective_at": str(data.get("effective_at") or ""),
    }


def update_policy(*, evaluation_schedule: str, timezone_name: str,
                  expected_revision: int) -> dict[str, Any]:
    """PATCH 语义：白名单字段 + expected_revision CAS；成功后读回确认。

    2 → 1 切换会触发 backlog 释放入队（由 API 层调用 release_backlog）。
    """
    if evaluation_schedule not in SCHEDULES:
        raise PolicyInvalid("evaluation_schedule 必须是 immediate 或 "
                            "daily_midnight")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise PolicyInvalid(f"未知 IANA 时区: {timezone_name!r}") from None
    with _lock:
        current = load_policy(refresh=True)
        if expected_revision != current["revision"]:
            raise PolicyConflict(
                f"策略已被他人更新（当前 revision="
                f"{current['revision']}，期望 {expected_revision}）")
        new = {
            "schema_version": SCHEMA_VERSION,
            "revision": current["revision"] + 1,
            "evaluation_schedule": evaluation_schedule,
            "timezone": timezone_name,
            "daily_local_time": "00:00",
            "effective_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        }
        POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(POLICY_FILE):
            atomic_write_text(POLICY_FILE,
                              json.dumps(new, ensure_ascii=False, indent=2))
        global _cache
        _cache = dict(new)
        return dict(new)


def reset_policy_cache() -> None:
    """测试/沙箱重置。"""
    global _cache
    with _lock:
        _cache = None


# ---------------------------------------------------------------------------
# 时间窗计算（§6.3：注入 clock 供测试；业务时区不依赖机器 TZ）
# ---------------------------------------------------------------------------

def local_date_of(utc_iso: str, tz_name: str) -> str:
    """UTC 时刻在业务时区下的自然日（YYYY-MM-DD）。"""
    dt = datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d")


def next_midnight_utc(now: datetime, tz_name: str) -> datetime:
    """下一个自然日零点对应的 UTC instant（不能 sleep(86400) 后假定）。"""
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    tomorrow = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    # DST/非整日偏移可能把 replace 推到错误的墙钟：以日期推进重算
    candidate = datetime.combine(tomorrow.date(), datetime.min.time(),
                                 tzinfo=tz)
    if candidate <= local:
        candidate += timedelta(days=1)
        candidate = datetime.combine(candidate.date(), datetime.min.time(),
                                     tzinfo=tz)
    return candidate.astimezone(timezone.utc)


def day_window_utc(local_date: str, tz_name: str) -> tuple[str, str]:
    """本地自然日 [00:00, 24:00) 对应的 UTC 起止（ISO）。"""
    tz = ZoneInfo(tz_name)
    start_local = datetime.strptime(local_date, "%Y-%m-%d").replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    # DST 折叠日的 24h 窗口仍按墙钟次日零点定义
    end_local = datetime.combine(
        (start_local + timedelta(days=1)).date(), datetime.min.time(),
        tzinfo=tz)
    fmt = lambda d: d.astimezone(timezone.utc).strftime(  # noqa: E731
        "%Y-%m-%dT%H:%M:%SZ")
    return fmt(start_local), fmt(end_local)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def scheduling_facts(observed_at_iso: str, policy: dict[str, Any],
                     now: datetime | None = None) -> dict[str, str]:
    """受理时的调度归属（§6.2 服务端字段）。

    immediate → eligible 立即；daily_midnight → eligible = 下一零点，
    local_activity_date = observed_at 所在业务自然日。
    """
    now = now or datetime.now(timezone.utc)
    mode = policy.get("evaluation_schedule") or DEFAULT_SCHEDULE
    tz_name = policy.get("timezone") or DEFAULT_TIMEZONE
    if mode == SCHEDULE_DAILY_MIDNIGHT:
        observed = parse_iso(observed_at_iso) or now
        return {
            "schedule_mode": SCHEDULE_DAILY_MIDNIGHT,
            "timezone": tz_name,
            "local_activity_date": local_date_of(observed_at_iso, tz_name),
            "eligible_after_utc": iso(next_midnight_utc(observed, tz_name)),
            "policy_revision": str(policy.get("revision") or 1),
        }
    return {
        "schedule_mode": SCHEDULE_IMMEDIATE,
        "timezone": tz_name,
        "local_activity_date": "",
        "eligible_after_utc": "",
        "policy_revision": str(policy.get("revision") or 1),
    }


def status_summary(now=None) -> dict:
    """管理面板状态：当前模式/时区/下次执行（§5.3；批量计数由调用方补）。"""
    from datetime import datetime as _dt, timezone as _tz
    now = now or _dt.now(_tz.utc)
    policy = load_policy()
    mode = policy["evaluation_schedule"]
    tz_name = policy["timezone"]
    out = {
        "policy": dict(policy),
        "service_enabled": True,   # API 层按 LEARNER_EVALUATION_MODE 修正
        "next_run_utc": "",
    }
    if mode == SCHEDULE_DAILY_MIDNIGHT:
        out["next_run_utc"] = iso(next_midnight_utc(now, tz_name))
    return out
