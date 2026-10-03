"""§25 主动服务：订阅、调度、去重与通知收件箱（C04/C05）。

要点：
- 四类订阅（weekly_brief/daily_tasks/due_reviews/unfinished_course），
  默认全部关闭；`SITE_ASSISTANT_PROACTIVE_ENABLED` 独立开关 + 用户逐项
  开启（§26.5），二者同时满足才调度。
- 一分钟粒度调度由 AssistantRuntime 驱动（`scheduler_tick`）；每次最多
  处理 20 项到期订阅。执行 key = subscription_id + 本地计划日期 +
  schedule_revision，`delivery_ledger.json` 原子认领，重复 tick / 重启不
  重复生成（§25.3-2）。
- 本地时间按 zoneinfo 计算；停机只补最近 48 小时内最新一次到期执行，
  更旧标 skipped（§25.3-4）。
- quiet_hours 内只准备，下一允许时段投递；每天最多 3 条主动通知
  （§25.3-5）。
- weekly_brief 用确定性事实模板（§25.3-6 允许受限模型，首版不调用）；
  报告经 reports.py 持久化（§25.4）。
- 退订立即取消未投递项；账号清理覆盖 subscriptions/notifications/
  reports/ledger（§12.3，同根目录随 purge_account 删除）。
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.assistant_store import _student_root

SUBSCRIPTION_KINDS = ("weekly_brief", "daily_tasks", "due_reviews",
                      "unfinished_course")
DEFAULT_LOCAL_TIME = {"weekly_brief": "09:00", "daily_tasks": "09:00",
                      "due_reviews": "18:00", "unfinished_course": "18:00"}
DEFAULT_WEEKDAYS = {"weekly_brief": [0], "daily_tasks": list(range(7)),
                    "due_reviews": list(range(7)),
                    "unfinished_course": list(range(7))}
MAX_DAILY_PROACTIVE = 3
MAX_SCHED_BATCH = 20
LEDGER_RETENTION_DAYS = 90
NOTIFICATION_RETENTION_DAYS = 30
CATCHUP_WINDOW = timedelta(hours=48)
STALE_RUN = timedelta(hours=72)


class SubscriptionRejected(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hh, mm = str(value).split(":", 1)
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return None


# -- 存储 ---------------------------------------------------------------------

def _subscriptions_path(student_id: str):
    return _student_root(student_id) / "subscriptions.json"


def _ledger_path(student_id: str):
    return _student_root(student_id) / "delivery_ledger.json"


def _notifications_dir(student_id: str):
    return _student_root(student_id) / "notifications"


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path, payload) -> None:
    from app.core.atomic import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))


def load_subscriptions(student_id: str) -> dict[str, dict[str, Any]]:
    data = _load_json(_subscriptions_path(student_id), {})
    return data if isinstance(data, dict) else {}


def save_subscriptions(student_id: str, subs: dict[str, Any]) -> None:
    _save_json(_subscriptions_path(student_id), subs)


def _load_ledger(student_id: str) -> dict[str, dict[str, Any]]:
    data = _load_json(_ledger_path(student_id), {})
    return data if isinstance(data, dict) else {}


def _prune_ledger(ledger: dict[str, dict[str, Any]]) -> None:
    cutoff = _utc_iso(_now() - timedelta(days=LEDGER_RETENTION_DAYS))
    stale = [k for k, v in ledger.items()
             if str(v.get("executed_at") or "") < cutoff]
    for k in stale:
        ledger.pop(k, None)


# -- 订阅 CRUD（§25.5） --------------------------------------------------------

def create_subscription(student_id: str, *, kind: str, timezone_name: str,
                        local_time: str = "", weekdays: list[int] | None = None,
                        scope: dict[str, Any] | None = None,
                        client_request_id: str = "") -> dict[str, Any]:
    if kind not in SUBSCRIPTION_KINDS:
        raise SubscriptionRejected(422, "invalid_target", "未知订阅类型。")
    subs = load_subscriptions(student_id)
    if client_request_id:
        for sub in subs.values():
            if sub.get("client_request_id") == client_request_id:
                return sub
    tz_name = str(timezone_name or "UTC")
    _tz(tz_name)  # 预检：非法回退 UTC（§6.4），但保留原名展示
    hhmm = _parse_hhmm(local_time) or _parse_hhmm(
        DEFAULT_LOCAL_TIME[kind])
    days = sorted({int(d) for d in (weekdays
                                    or DEFAULT_WEEKDAYS[kind])
                   if 0 <= int(d) <= 6})
    if kind == "weekly_brief":
        days = days[:1] or [0]
    if not days:
        raise SubscriptionRejected(422, "invalid_target",
                                   "至少需要一个有效星期。")
    sub: dict[str, Any] = {
        "subscription_id": "astu_" + uuid.uuid4().hex[:16],
        "revision": 1,
        "kind": kind,
        "enabled": True,
        "timezone": tz_name,
        "local_time": f"{hhmm[0]:02d}:{hhmm[1]:02d}",
        "weekdays": days,
        "scope": scope or {"mode": "all_workspaces"},
        "quiet_hours": {"start": "22:00", "end": "08:00"},
        "next_run_at": "",
        "last_run_at": None,
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "client_request_id": client_request_id,
    }
    sub["next_run_at"] = _next_run_iso(sub, _now())
    subs[sub["subscription_id"]] = sub
    save_subscriptions(student_id, subs)
    return sub


def update_subscription(student_id: str, subscription_id: str, *,
                        expected_revision: int,
                        patch: dict[str, Any]) -> dict[str, Any]:
    subs = load_subscriptions(student_id)
    sub = subs.get(subscription_id)
    if sub is None:
        raise SubscriptionRejected(404, "entity_not_found", "订阅不存在。")
    if int(sub.get("revision") or 0) != int(expected_revision):
        raise SubscriptionRejected(409, "revision_conflict",
                                   "订阅已被更新，请刷新后重试。")
    allowed = ("enabled", "timezone", "local_time", "weekdays", "scope",
               "quiet_hours")
    unknown = [k for k in patch if k not in allowed]
    if unknown:
        raise SubscriptionRejected(422, "invalid_target",
                                   f"不可更新字段：{unknown[:3]}")
    if "local_time" in patch:
        hhmm = _parse_hhmm(str(patch["local_time"]))
        if hhmm is None:
            raise SubscriptionRejected(422, "invalid_target",
                                       "时间格式应为 HH:mm。")
        sub["local_time"] = f"{hhmm[0]:02d}:{hhmm[1]:02d}"
    if "weekdays" in patch:
        days = sorted({int(d) for d in patch["weekdays"] if 0 <= int(d) <= 6})
        if not days:
            raise SubscriptionRejected(422, "invalid_target",
                                       "至少需要一个有效星期。")
        if sub["kind"] == "weekly_brief":
            days = days[:1]
        sub["weekdays"] = days
    if "timezone" in patch:
        _tz(str(patch["timezone"]))
        sub["timezone"] = str(patch["timezone"])
    for key in ("enabled", "scope", "quiet_hours"):
        if key in patch:
            sub[key] = patch[key]
    sub["revision"] = int(sub.get("revision") or 1) + 1
    sub["updated_at"] = _utc_iso()
    sub["next_run_at"] = _next_run_iso(sub, _now())
    sub.pop("pending_delivery", None)
    subs[subscription_id] = sub
    save_subscriptions(student_id, subs)
    return sub


def delete_subscription(student_id: str, subscription_id: str) -> None:
    """退订：立即取消未投递项（§25.3-8）。幂等：不存在也成功。"""
    subs = load_subscriptions(student_id)
    if subscription_id not in subs:
        return
    subs.pop(subscription_id)
    save_subscriptions(student_id, subs)


# -- next_run 计算（§25.3-3：本地日历 + DST 安全） -----------------------------

def _next_run_iso(sub: dict[str, Any], now: datetime) -> str:
    tz = _tz(str(sub.get("timezone") or "UTC"))
    hhmm = _parse_hhmm(str(sub.get("local_time"))) or (9, 0)
    weekdays = {int(d) for d in sub.get("weekdays") or [0]}
    local = now.astimezone(tz)
    for offset in range(0, 9):
        day = (local + timedelta(days=offset)).date()
        if day.weekday() not in weekdays:
            continue
        candidate = _local_instant(tz, day, hhmm)
        # candidate > now：今天已过时刻自然跳过；DST 重复时刻在投递后
        # 重算时同样被排除，只运行第一次（§25.3-3）。
        if candidate is not None and candidate > now:
            return candidate.isoformat()
    return (now + timedelta(days=7)).isoformat()


def _local_instant(tz: ZoneInfo, day, hhmm: tuple[int, int]) -> datetime | None:
    """构造本地时刻并归一化；DST 不存在的时刻自然顺延到合法瞬间。"""
    try:
        naive = datetime(day.year, day.month, day.day,
                         hhmm[0], hhmm[1])
        aware = naive.replace(tzinfo=tz)
        return aware.astimezone(timezone.utc)
    except Exception:
        return None


def _in_quiet_hours(sub: dict[str, Any], now: datetime) -> bool:
    quiet = sub.get("quiet_hours") or {}
    start = _parse_hhmm(str(quiet.get("start") or "22:00"))
    end = _parse_hhmm(str(quiet.get("end") or "08:00"))
    if not start or not end:
        return False
    local = now.astimezone(_tz(str(sub.get("timezone")
                                   or "UTC"))).time()
    start_t = datetime.strptime(f"{start[0]:02d}:{start[1]:02d}",
                                "%H:%M").time()
    end_t = datetime.strptime(f"{end[0]:02d}:{end[1]:02d}",
                              "%H:%M").time()
    if start_t <= end_t:
        return start_t <= local < end_t
    return local >= start_t or local < end_t  # 跨午夜窗口


# -- 收件箱（§25.4） ------------------------------------------------------------

def create_notification(student_id: str, *, kind: str, title: str,
                        summary: str, report_id: str | None = None,
                        workflow_id: str | None = None,
                        target: dict[str, Any] | None = None,
                        source_ids: list[str] | None = None,
                        created_at: str = "",
                        ) -> dict[str, Any]:
    note: dict[str, Any] = {
        "notification_id": "astn_" + uuid.uuid4().hex[:16],
        "kind": kind,
        "title": title[:120],
        "summary": summary[:160],
        "created_at": created_at or _utc_iso(),
        "read_at": None,
        "dismissed_at": None,
        "expires_at": _utc_iso(
            _now() + timedelta(days=NOTIFICATION_RETENTION_DAYS)),
        "report_id": report_id,
        "workflow_id": workflow_id,
        "target": target,
        "source_ids": source_ids or [],
    }
    path = _notifications_dir(student_id) / f"{note['notification_id']}.json"
    _save_json(path, note)
    return note


def list_notifications(student_id: str, *, offset: int = 0, limit: int = 20,
                       unread_only: bool = False) -> tuple[list, int]:
    import pathlib
    d = _notifications_dir(student_id)
    if not d.exists():
        return [], 0
    now_iso = _utc_iso()
    items: list[dict[str, Any]] = []
    for p in pathlib.Path(d).glob("*.json"):
        try:
            note = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if note.get("dismissed_at"):
            continue
        if str(note.get("expires_at") or "") < now_iso:
            continue  # 过期正文不再返回（§25.5）
        if unread_only and note.get("read_at"):
            continue
        items.append(note)
    items.sort(key=lambda n: str(n.get("created_at")), reverse=True)
    return items[offset:offset + limit], len(items)


def mark_notification(student_id: str, notification_id: str, *,
                      action: str, mute_entity: bool = False) -> dict | None:
    import pathlib
    path = _notifications_dir(student_id) / f"{notification_id}.json"
    if not path.exists():
        return None
    note = _load_json(path, {})
    if action == "read" and not note.get("read_at"):
        note["read_at"] = _utc_iso()
    elif action == "dismiss":
        note["dismissed_at"] = _utc_iso()
        if mute_entity and note.get("workflow_id"):
            # 忽略该课程提醒：登记 run/cursor 静默（§25.5 mute_entity）。
            _mute_unfinished(student_id, note)
    _save_json(path, note)
    return note


def unread_count(student_id: str) -> int:
    _items, total = list_notifications(student_id, limit=1,
                                       unread_only=True)
    return total


# -- 调度（§25.3） ---------------------------------------------------------------

def scheduler_enabled() -> bool:
    from app.core.config import settings
    return bool(settings.site_assistant_proactive_enabled)


def scheduler_tick(now: datetime | None = None) -> dict[str, int]:
    """单次调度：认领到期订阅并生成通知。返回计数（诊断用）。

    每次最多 MAX_SCHED_BATCH 项；并发上限 2 由调用方（单线程 tick）保证。
    """
    if not scheduler_enabled():
        return {"processed": 0, "delivered": 0, "held": 0, "skipped": 0}
    now = now or _now()
    processed = delivered = held = skipped = 0
    import pathlib
    from app.core.assistant_store import _ASSISTANT_DIR
    from app.core.guest_runtime import is_legacy_guest_owner
    if not _ASSISTANT_DIR.is_dir():
        return {"processed": 0, "delivered": 0, "held": 0, "skipped": 0}
    for student_dir in pathlib.Path(_ASSISTANT_DIR).iterdir():
        if not student_dir.is_dir() or processed >= MAX_SCHED_BATCH:
            continue
        student_id = student_dir.name
        if is_legacy_guest_owner(student_id):
            continue
        subs = load_subscriptions(student_id)
        due = [s for s in subs.values()
               if s.get("enabled") and _parse_dt(s.get("next_run_at"))
               and _parse_dt(s["next_run_at"]) <= now]
        # 先释放静默挂起（§25.3-5）。
        for sub in [s for s in subs.values() if s.get("pending_delivery")]:
            outcome = _release_pending(student_id, sub, now)
            if outcome == "delivered":
                delivered += 1
        for sub in due:
            processed += 1
            outcome = _run_subscription(student_id, sub, now)
            if outcome == "delivered":
                delivered += 1
            elif outcome == "held":
                held += 1
            else:
                skipped += 1
    return {"processed": processed, "delivered": delivered,
            "held": held, "skipped": skipped}


def _execution_key(sub: dict[str, Any], now: datetime) -> str:
    tz = _tz(str(sub.get("timezone") or "UTC"))
    local_day = now.astimezone(tz).date().isoformat()
    return (f"{sub.get('subscription_id')}|{local_day}"
            f"|r{sub.get('revision')}")


def _claim(ledger: dict, key: str, outcome: str) -> bool:
    if key in ledger:
        return False  # 重复 tick / 重启：同份不重复生成（§25.3-2）
    ledger[key] = {"outcome": outcome, "executed_at": _utc_iso()}
    return True


def _daily_proactive_count(student_id: str, now: datetime) -> int:
    items, _total = list_notifications(student_id, limit=100)
    day = now.date().isoformat()
    return sum(1 for n in items
               if str(n.get("created_at", "")).startswith(day)
               and n.get("kind") in ("subscription", "action_attention"))


def _run_subscription(student_id: str, sub: dict[str, Any],
                      now: datetime) -> str:
    due_at = _parse_dt(sub.get("next_run_at")) or now
    # 停机补发：只补最近 48h 内最新一次；更旧 skipped（§25.3-4）。
    if now - due_at > CATCHUP_WINDOW:
        sub["last_run_at"] = sub.get("next_run_at")
        sub["next_run_at"] = _next_run_iso(sub, now)
        _save_student_sub(student_id, sub)
        ledger = _load_ledger(student_id)
        _claim(ledger, _execution_key(sub, due_at.astimezone(
            _tz(str(sub.get("timezone") or "UTC")))), "skipped")
        _save_json(_ledger_path(student_id), ledger)
        return "skipped"

    # 生成器：无内容（如无任务/无到期复习）→ skipped，不投递（§25.1）。
    content = _generate_content(student_id, sub, now)
    ledger = _load_ledger(student_id)
    _prune_ledger(ledger)
    key = _execution_key(sub, due_at)
    if not content:
        _claim(ledger, key, "skipped_empty")
        _advance(sub, now)
        _save_student_sub(student_id, sub)
        _save_json(_ledger_path(student_id), ledger)
        return "skipped"

    if _in_quiet_hours(sub, now):
        # 只准备，不投递；下一允许时段由 pending_delivery 释放。
        if key not in ledger:
            sub["pending_delivery"] = {"execution_key": key, **content}
            _advance(sub, now)
            _save_student_sub(student_id, sub)
        return "held"

    if _daily_proactive_count(student_id, now) >= MAX_DAILY_PROACTIVE:
        sub["pending_delivery"] = {"execution_key": key, **content,
                                   "capped": True}
        _advance(sub, now)
        _save_student_sub(student_id, sub)
        return "held"

    _deliver(student_id, sub, content, key, now)
    _advance(sub, now)
    _save_student_sub(student_id, sub)
    return "delivered"


def _release_pending(student_id: str, sub: dict[str, Any],
                     now: datetime) -> str:
    pending = sub.get("pending_delivery") or {}
    if not pending:
        return "none"
    if _in_quiet_hours(sub, now):
        return "held"
    if (not pending.get("capped")
            and _daily_proactive_count(student_id, now)
            >= MAX_DAILY_PROACTIVE):
        return "held"
    content = {k: v for k, v in pending.items()
               if k not in ("execution_key", "capped")}
    _deliver(student_id, sub, content,
             str(pending.get("execution_key") or
                 _execution_key(sub, now)), now)
    sub.pop("pending_delivery", None)
    _save_student_sub(student_id, sub)
    return "delivered"


def _deliver(student_id: str, sub: dict[str, Any], content: dict[str, Any],
             key: str, now: datetime) -> None:
    ledger = _load_ledger(student_id)
    if not _claim(ledger, key, "delivered"):
        _save_json(_ledger_path(student_id), ledger)
        return
    create_notification(student_id, kind="subscription",
                        title=str(content.get("title") or ""),
                        summary=str(content.get("summary") or ""),
                        report_id=content.get("report_id"),
                        target=content.get("target"),
                        source_ids=content.get("source_ids"),
                        created_at=_utc_iso(now))
    _save_json(_ledger_path(student_id), ledger)


def _advance(sub: dict[str, Any], now: datetime) -> None:
    sub["last_run_at"] = sub.get("next_run_at")
    sub["next_run_at"] = _next_run_iso(sub, now)


def _save_student_sub(student_id: str, sub: dict[str, Any]) -> None:
    subs = load_subscriptions(student_id)
    subs[str(sub.get("subscription_id"))] = sub
    save_subscriptions(student_id, subs)


# -- 四类内容生成器（事实模板，§25.1；weekly_brief 不调用模型） -------------------

def _generate_content(student_id: str, sub: dict[str, Any],
                      now: datetime) -> dict[str, Any] | None:
    kind = str(sub.get("kind"))
    if kind == "weekly_brief":
        return _weekly_brief(student_id, sub, now)
    if kind == "daily_tasks":
        return _daily_tasks(student_id, now)
    if kind == "due_reviews":
        return _due_reviews(student_id)
    if kind == "unfinished_course":
        return _unfinished_course(student_id)
    return None


def _weekly_brief(student_id: str, sub: dict[str, Any],
                  now: datetime) -> dict[str, Any] | None:
    """上一个完整自然周（本地周一 00:00 至周日 24:00）的事实简报。"""
    from . import readers, reports
    tz = _tz(str(sub.get("timezone") or "UTC"))
    local = now.astimezone(tz)
    this_monday = local.date() - timedelta(days=local.weekday())
    last_monday = this_monday - timedelta(days=7)
    start = datetime(last_monday.year, last_monday.month, last_monday.day,
                     tzinfo=tz).astimezone(timezone.utc)
    end = start + timedelta(days=7)
    scope_mode = str((sub.get("scope") or {}).get("mode")
                     or "all_workspaces")
    workspace_ids: list[str] | None = None
    if scope_mode == "workspace":
        workspace_ids = [str(i) for i in
                         (sub.get("scope") or {}).get("workspace_ids") or []
                         if str(i)]
    summary = readers.read_learning_summary(
        student_id, start_at=start, end_at=end,
        timezone_name=str(sub.get("timezone") or "UTC"),
        workspace_ids=workspace_ids)
    data = summary.get("data") or {}
    report_id = reports.save_report(student_id, {
        "kind": "weekly_brief",
        "window": {"start_at": start.isoformat(),
                   "end_at": end.isoformat(),
                   "timezone": str(sub.get("timezone") or "UTC"),
                   "label": f"{last_monday.isoformat()} ~ "
                            f"{(this_monday - timedelta(days=1)).isoformat()}"},
        "learning_report": data,
        "subscription_id": sub.get("subscription_id"),
        "generated_at": _utc_iso(now),
        "complete": bool(summary.get("complete")),
    })
    return {
        "title": "每周学习简报",
        "summary": (f"{last_monday.month}/{last_monday.day} – "
                    f"{(this_monday - timedelta(days=1)).month}/"
                    f"{(this_monday - timedelta(days=1)).day} 学习事实已生成，"
                    f"点击查看。"),
        "report_id": report_id,
        "target": {"kind": "module", "route_id": "dashboard"},
    }


def _daily_tasks(student_id: str, now: datetime) -> dict[str, Any] | None:
    from . import readers
    try:
        import asyncio
        # now 穿透到快照：合成时钟（测试/停机补发）下"今天"的口径必须
        # 与调度时刻一致，不能退回真实系统日期。
        result = asyncio.run(readers.read_saved_tasks(student_id,
                                                      now=now.timestamp()))
    except RuntimeError:
        return None
    data = result.get("data") or {}
    today = list(data.get("today") or [])
    # readers 快照的逾期未完成任务在 "open"（day<=today 且未完成）；
    # 此前读 "overdue" 恒为空（键名漂移）。
    overdue = list(data.get("open") or [])
    pending = [t for t in today + overdue
               if str(t.get("status")) != "completed"]
    if not pending:
        return None  # 无任务不发送（§25.1）
    titles = "、".join(str(t.get("title") or "")[:12]
                      for t in pending[:3])
    return {
        "title": "今日学习任务",
        "summary": f"今天有 {len(pending)} 项任务：{titles}"
                   f"{'…' if len(pending) > 3 else ''}",
        "target": {"kind": "module", "route_id": "orchestration"},
    }


def _due_reviews(student_id: str) -> dict[str, Any] | None:
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    due_ids = [n["id"] for n in vault.notes
               if (n.get("review") or {}).get("enabled")
               and 0 < float((n.get("review") or {})
                             .get("next_review_at") or 0)
               <= datetime.now(tz=timezone.utc).timestamp()]
    if not due_ids:
        return None  # 无到期不发送
    return {
        "title": "笔记复习到期",
        "summary": f"有 {len(due_ids)} 篇笔记到了复习时间。",
        "target": {"kind": "module", "route_id": "notes"},
    }


def _mutes_path(student_id: str):
    return _student_root(student_id) / "unfinished_mutes.json"


def _load_mutes(student_id: str) -> dict[str, str]:
    data = _load_json(_mutes_path(student_id), {})
    return data if isinstance(data, dict) else {}


def _mute_unfinished(student_id: str, note: dict[str, Any]) -> None:
    key = str((note.get("target") or {}).get("mute_key") or "")
    if not key:
        return
    mutes = _load_mutes(student_id)
    mutes[key] = _utc_iso()
    _save_json(_mutes_path(student_id), mutes)


def _unfinished_course(student_id: str) -> dict[str, Any] | None:
    """本人未完成 run 连续 72h 未更新且该 run/cursor 组合未提醒过。"""
    from . import readers
    try:
        courses = readers.read_courses(student_id, resume_only=True)
    except Exception:
        return None
    resume = ((courses.get("data") or {}).get("resume")
              if courses.get("status") == "ready" else None)
    if not resume or not resume.get("lesson_id"):
        return None
    run = resume.get("run") or {}
    updated_at = _parse_dt(str(run.get("updated_at") or ""))
    if updated_at is None or _now() - updated_at < STALE_RUN:
        return None
    cursor = str(run.get("cursor") or "")
    mute_key = (f"{resume.get('lesson_id')}|{run.get('run_id')}"
                f"|{cursor}")
    if mute_key in _load_mutes(student_id):
        return None  # 该 run/cursor 组合已提醒过（§25.1）
    workspace_id = str(resume.get("workspace_id") or "")
    lesson_id = str(resume.get("lesson_id"))
    target = {"kind": "module", "route_id": "course",
              "mute_key": mute_key}
    if workspace_id and lesson_id:
        target = {"kind": "lesson", "workspace_id": workspace_id,
                  "lesson_id": lesson_id, "mute_key": mute_key}
    return {
        "title": "继续未完成的课程",
        "summary": f"「{str(resume.get('title') or '课程')[:20]}」"
                   f"已 3 天未继续，可从上次位置接着上。",
        "target": target,
    }
