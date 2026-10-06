"""§22.2 学习历史补齐（B08）：任务实例 ID、状态变化事件与可靠推导。

职责边界：
- DailyTask.task_instance_id 随机稳定；旧任务首次保存前补齐（ensure_*）。
- 每次任务状态变化记一条 ``task_status_changed``（同一次变化只记一次），
  与 M9 state 同一事务写入 ``event_outbox``；flush 追加到
  orchestration_events.jsonl（按 event_id 去重）后确认移除；重启补发。
- 月度有效完成按事件流推导：窗口内完成且其后无撤销（to != completed
  的后续迁移）才计入；按 completion_source 分列。
- 旧事件缺字段不补假历史：coverage_started_at + history_incomplete。
- 助手只读这些领域事实，不写。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Any

from .schema import DailyTask, DailyTaskStatus, OrchestrationEvent

STATUS_CHANGED_EVENT = "task_status_changed"


def new_instance_id() -> str:
    return "ti_" + uuid.uuid4().hex[:16]


def new_event_id() -> str:
    return "ev_" + uuid.uuid4().hex[:20]


def ensure_task_instance_ids(state) -> int:
    """为缺 instance_id 的任务补随机稳定 ID（§22.2-1 迁移）。

    在保存路径调用，随下一次 state 写入持久化；workspace_id 未知保持
    空串，不按名称猜。返回补齐数量。
    """
    added = 0
    for task in state.daily_tasks:
        if not (task.task_instance_id or "").strip():
            task.task_instance_id = new_instance_id()
            added += 1
    return added


def note_status_change(state, task: DailyTask, from_status: DailyTaskStatus,
                       *, completion_source: str = "",
                       evidence_attempt_id: str = "",
                       now: float | None = None) -> None:
    """记录一次任务状态变化到 outbox（§22.2-2；与 state 同一事务落盘）。

    同一 (task_instance_id, from, to) 的重复调用只记一次；调用方应在
    状态实际改变后调用。
    """
    now = now if now is not None else time.time()
    instance = (task.task_instance_id or "").strip()
    if not instance:
        instance = new_instance_id()
        task.task_instance_id = instance
    to_status = task.status.value if hasattr(task.status, "value") \
        else str(task.status)
    from_value = from_status.value if hasattr(from_status, "value") \
        else str(from_status)
    payload = {
        "event_id": new_event_id(),
        "task_instance_id": instance,
        "task_id": task.id,
        "workspace_id": task.workspace_id or "",
        "from_status": from_value,
        "to_status": to_status,
        "completion_source": completion_source or task.completion_source
        or "",
        "evidence_attempt_id": evidence_attempt_id
        or task.evidence_attempt_id or "",
        "observed_at": datetime.fromtimestamp(
            now, tz=dt_timezone.utc).isoformat(),
    }
    state.event_outbox.append({
        "type": STATUS_CHANGED_EVENT,
        "payload": payload,
        "ts": float(now),
    })


def _append_event_once(student_id: str, item: dict[str, Any]) -> bool:
    """追加一条 outbox 事件到事件日志；event_id 已存在（尾部 512 条扫描）则跳过。"""
    from . import store
    event_id = str((item.get("payload") or {}).get("event_id") or "")
    if not event_id:
        return False
    for prior_event in store.read_events(student_id, limit=512):
        prior = dict(getattr(prior_event, "payload", None) or {})
        if str(prior.get("event_id")) == event_id:
            return True  # 已投递：视为成功（幂等确认）
    return store.append_event(
        student_id,
        OrchestrationEvent(type=str(item.get("type") or "task_status_changed"),
                           ts=float(item.get("ts") or time.time()),
                           payload=dict(item.get("payload") or {})))


def flush_outbox(student_id: str, *, state=None) -> int:
    """把未确认 outbox 事件投递到事件日志并确认（§22.2-3）。

    返回本轮确认条数。加载时发现非空 outbox 即补发（重启恢复语义）。
    """
    from . import store
    if state is None:
        state = store.load_state(student_id)
    if not state.event_outbox:
        return 0
    pending = list(state.event_outbox)
    delivered = 0
    for item in pending:
        if _append_event_once(student_id, item):
            delivered += 1
            state.event_outbox.remove(item)
        else:
            break  # 追加失败：保留剩余项待下次补发
    if delivered:
        store.save_state(student_id, state)
    return delivered


def completed_tasks_in_window(student_id: str, *, start_at, end_at,
                              timezone_name: str = "UTC") -> dict[str, Any]:
    """§22.2-4 月度有效完成推导（事件流口径）。

    只统计窗口内完成且截至今仍有效的实例（其后没有离开 completed 的
    迁移）；按 completion_source 分列（self_report / quiz_evidence）。
    覆盖说明：coverage_started_at = 首条 task_status_changed 时间；
    窗口起点早于它或窗口内存在旧版 task_completed（无 instance 字段）
    事件时 history_incomplete=True，总数退化为已知下界。
    """
    from . import store
    events, coverage = store.read_events_with_coverage(student_id)
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    first_changed_ts: float | None = None
    legacy_in_window = False
    # instance → {"completed_in_window", "last_left_completed", "source", "day"}
    instances: dict[str, dict[str, Any]] = {}
    legacy_ids: set[str] = set()

    for ev in events:
        etype = str(getattr(ev, "type", "") or "")
        payload = dict(getattr(ev, "payload", None) or {})
        ts = float(getattr(ev, "ts", 0) or 0)
        if etype == STATUS_CHANGED_EVENT:
            if first_changed_ts is None or ts < first_changed_ts:
                first_changed_ts = ts
            instance = str(payload.get("task_instance_id") or "")
            if not instance:
                continue
            to_status = str(payload.get("to_status") or "")
            rec = instances.setdefault(instance, {
                "completed_in_window": False, "revoked": False,
                "source": "", "day": "", "workspace_id":
                    str(payload.get("workspace_id") or ""),
            })
            if to_status == "completed" and _in_window(ts, start_at, end_at):
                rec["completed_in_window"] = True
                rec["revoked"] = False  # 重新完成覆盖早先撤销
                rec["source"] = str(payload.get("completion_source")
                                    or "unknown")
                rec["day"] = _local_date(ts, tz)
            elif to_status != "completed" and rec["completed_in_window"]:
                rec["revoked"] = True  # 离开 completed：当前查询剔除
        elif etype == "task_completed":
            if _in_window(ts, start_at, end_at):
                legacy_in_window = True
                tid = str(payload.get("task_id") or "")
                if tid:
                    legacy_ids.add(tid)

    valid = {k: v for k, v in instances.items()
             if v["completed_in_window"] and not v["revoked"]}
    by_source: dict[str, int] = {}
    for rec in valid.values():
        by_source[rec["source"] or "unknown"] = \
            by_source.get(rec["source"] or "unknown", 0) + 1

    # 空历史（无任何状态事件且无 legacy 事件）= 覆盖完整（没有任何缺失）；
    # 窗口起点早于首条状态事件、或窗口内存在 legacy 事件时才是缺口。
    # （1s 容差吸收 iso 微秒舍入，窗口恰从覆盖起点开始不算缺口。）
    history_incomplete = bool(
        legacy_in_window
        or (first_changed_ts is not None
            and start_at is not None
            and _to_epoch(start_at) < first_changed_ts - 1.0))
    return {
        "valid_completed_count": len(valid),
        "by_completion_source": by_source,
        "valid_days": sorted({rec["day"] for rec in valid.values()
                              if rec["day"]}),
        "valid_instances": [{"workspace_id": rec["workspace_id"],
                             "day": rec["day"]}
                            for rec in valid.values()],
        "legacy_event_ids": sorted(legacy_ids),
        "coverage_started_at": (
            datetime.fromtimestamp(first_changed_ts,
                                   tz=dt_timezone.utc).isoformat()
            if first_changed_ts is not None else None),
        "history_incomplete": history_incomplete,
        "known_minimum": len(valid) + (len(legacy_ids) if legacy_in_window
                                       else 0),
        "events_truncated": bool(coverage.get("truncated")),
        "status": "error" if not coverage.get("readable") else "ready",
    }


def _to_epoch(moment) -> float:
    if isinstance(moment, (int, float)):
        return float(moment)
    try:
        return float(moment.timestamp())
    except AttributeError:
        return 0.0


def _in_window(ts: float, start_at, end_at) -> bool:
    if start_at is not None and ts < _to_epoch(start_at):
        return False
    if end_at is not None and ts > _to_epoch(end_at):
        return False
    return True


def _local_date(ts: float, tz) -> str:
    return datetime.fromtimestamp(ts, tz=dt_timezone.utc).astimezone(
        tz).date().isoformat()
