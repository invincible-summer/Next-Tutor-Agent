"""Unified learning-activity aggregation (L1 profile layer, read-side).

Single truth source for "on which days did this student actually learn",
derived as a deterministic LOCAL-day union over five always-on ledgers:

  1. learning-evidence journal (每条来源/判分事务的 observed_at/created_at)
  2. teaching log       (M3 per-concept teaching turns, entry ts)
  3. orchestration events (M9 task/review/habit checkpoints)
  4. ux events          (M8 per-turn interaction signals)
  5. eval traces        (M7 per-turn evaluation traces)

The legacy M6 episodic log (.episodes.jsonl) stopped receiving production
writes (append_episode has no callers); it is consulted ONLY as a
compatibility fallback for existing users when the union above is empty, and
the snapshot is tagged with its source so callers/UI can label it.

Every consumer reads THIS module instead of maintaining its own parallel
derivation: M8 motivation/greeting, M9 habit tracker, /ux/activity. Zero LLM,
zero writes, never raises. Day keys are local "YYYY-MM-DD" (user-facing
correctness beats the old UTC-midnight keys M8 used).

New in v2 (plan.md §7.3/§7.4, A03): ``learning_activity_snapshot`` is the
normalized, timezone-aware, status-carrying read used by the site assistant
and (via /ux/activity extras) the dashboard. It counts ONLY qualifying
learning events (evidence receipt / actual teaching turn / actual task
completion / actual review submission), separates empty from error, and
reports per-source coverage instead of swallowing exceptions into zeros.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

_DAY_SECONDS = 86400.0


# --- day helpers (local days, mirrors habit_tracker semantics) --------------

def _day_str(ts: float) -> str:
    t = time.localtime(ts)
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"


def _day_to_epoch(day: str) -> float:
    try:
        return float(time.mktime(time.strptime(day, "%Y-%m-%d")))
    except Exception:
        return 0.0


def _parse_any_ts(value: Any) -> datetime | None:
    """ISO 字符串或 epoch 秒 → tz-aware UTC datetime；无法解析返回 None。

    GAP-03 修复：receipt.observed_at 是 ISO 字符串，旧 ``float()`` 读取会
    静默丢弃；这里统一解析并把缺失/坏值交由调用方计入 omitted。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # 兼容纯数字字符串 epoch
        try:
            ts = float(text)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)


def _add_ts(days: set[str], ts: Any) -> None:
    parsed = _parse_any_ts(ts)
    if parsed is not None:
        days.add(_day_str(parsed.timestamp()))


# --- source collectors (each returns a set of local day strings) ------------

def _days_learning_journal(student_id: str) -> set[str]:
    days: set[str] = set()
    try:
        from app.agents.student_model.evaluation.store import get_journal
        state = get_journal(student_id).state()
        for src in state.sources.values():
            _add_ts(days, getattr(src.receipt, "observed_at", 0))
            meta = src.interpretations.get(src.current_interpretation_id, {})
            if isinstance(meta, dict):
                _add_ts(days, meta.get("judged_at") or
                        meta.get("updated_at"))
    except Exception:
        pass
    return days


def _days_teaching_log(student_id: str) -> set[str]:
    days: set[str] = set()
    try:
        from .teaching_engine import teaching_log as tlog
        for entries in tlog.load_teaching_log(student_id).values():
            for e in entries or []:
                _add_ts(days, getattr(e, "ts", 0))
    except Exception:
        pass
    return days


def _days_orchestration_events(student_id: str) -> set[str]:
    days: set[str] = set()
    try:
        from .learning_orchestration import store as orch_store
        for ev in orch_store.read_events(student_id):
            _add_ts(days, getattr(ev, "ts", 0))
    except Exception:
        pass
    return days


def _days_ux_events(student_id: str) -> set[str]:
    days: set[str] = set()
    try:
        from .ux_intelligence import store as ux_store
        for ev in ux_store.read_events(student_id):
            _add_ts(days, getattr(ev, "ts", 0))
    except Exception:
        pass
    return days


def _days_eval_traces(student_id: str) -> set[str]:
    days: set[str] = set()
    try:
        from .evaluation import store as eval_store
        for tr in eval_store.read_traces(student_id):
            _add_ts(days, getattr(tr, "ts", 0))
    except Exception:
        pass
    return days


def _days_legacy_episodes(student_id: str) -> set[str]:
    """Compatibility fallback only: the retired M6 episodic audit log."""
    days: set[str] = set()
    try:
        from .memory import store as mem_store
        for ep in mem_store.read_episodes(student_id, limit=400):
            _add_ts(days, getattr(ep, "ts", 0))
    except Exception:
        pass
    return days


# --- public API --------------------------------------------------------------

def active_days(student_id: str) -> set[str]:
    """The union day set across the five live ledgers, falling back to the
    legacy episodic audit log when (and only when) that union is empty.
    Never raises."""
    if not student_id:
        return set()
    days: set[str] = set()
    for collect in (_days_learning_journal, _days_teaching_log,
                    _days_orchestration_events, _days_ux_events,
                    _days_eval_traces):
        days |= collect(student_id)
    if not days:
        days = _days_legacy_episodes(student_id)
    return days


def streak_from_days(days: set[str], *, now: float | None = None
                     ) -> tuple[int, int, str, int]:
    """(current_streak, longest_streak, last_active_day, total_days) from a
    day-string set. Pure function, never raises. Same semantics the M9 habit
    tracker has always used (count from today or yesterday)."""
    if not days:
        return 0, 0, "", 0
    now = now if now is not None else time.time()
    today = _day_str(now)
    yesterday = _day_str(now - _DAY_SECONDS)

    sorted_days = sorted(days, reverse=True)

    current = 0
    cursor = today if today in days else (yesterday if yesterday in days else None)
    if cursor is not None:
        check = _day_to_epoch(cursor)
        while check > 0 and _day_str(check) in days:
            current += 1
            check -= _DAY_SECONDS

    longest = 1
    run = 1
    for i in range(1, len(sorted_days)):
        prev = _day_to_epoch(sorted_days[i - 1])
        cur = _day_to_epoch(sorted_days[i])
        if prev > 0 and cur > 0 and abs(prev - cur - _DAY_SECONDS) < 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    return current, longest, sorted_days[0], len(days)


def streak_stats(student_id: str, *, now: float | None = None
                 ) -> tuple[int, int, str, int]:
    """Streak stats over the unified day union. Never raises."""
    return streak_from_days(active_days(student_id), now=now)


def last_learned_concept(student_id: str) -> str:
    """The most recent thing this student was actually taught/asked.

    Primary source is the M3 teaching log (live, one entry per teaching
    turn); the display name resolves through the M2 graph when the log key
    is a node id. Falls back to the newest learning-record knowledge point.
    The retired M6 episodic log is no longer consulted. Lives HERE (not in
    M8) so the M8 package stays import-clean of M3. Never raises."""
    try:
        from .teaching_engine import teaching_log as tlog
        best_key, best_ts = "", 0.0
        for key, entries in tlog.load_teaching_log(student_id).items():
            for e in entries or []:
                try:
                    ts = float(getattr(e, "ts", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if ts > best_ts:
                    best_key, best_ts = key, ts
        if best_key:
            try:
                from .student_model import get_student_model
                node = get_student_model(student_id).graph.nodes.get(best_key)
                if node is not None and getattr(node, "name", ""):
                    return str(node.name)
            except Exception:
                pass
            return best_key
    except Exception:
        pass
    try:
        # GAP-03 修复：废弃的 lr.list_records 读取已移除，改为统一
        # evidence journal 的最新受理来源（按 observed_at），从其解释中
        # 取概念引用。
        from app.agents.student_model.evaluation.store import get_journal
        state = get_journal(student_id).state()
        best_src, best_dt = None, None
        for src in state.sources.values():
            parsed = _parse_any_ts(src.receipt.observed_at)
            if parsed is None:
                continue
            if best_dt is None or parsed > best_dt:
                best_src, best_dt = src, parsed
        if best_src is not None:
            for interp in (best_src.interpretations.get(
                    best_src.current_interpretation_id, {}),
                    best_src.interpretations.get("", {})):
                if not isinstance(interp, dict):
                    continue
                for ref in interp.get("concept_refs") or []:
                    key = str((ref or {}).get("key") or "").strip()
                    if key:
                        return key
                kp = str(interp.get("knowledge_point") or "").strip()
                if kp:
                    return kp
    except Exception:
        pass
    return ""


def activity_snapshot(student_id: str, *, now: float | None = None) -> dict[str, Any]:
    """API-facing summary: streak/active-day numbers + which source produced
    them ("aggregated" live ledgers, "legacy_episodes" fallback, or "none").
    Never raises."""
    try:
        live: set[str] = set()
        for collect in (_days_learning_journal, _days_teaching_log,
                        _days_orchestration_events, _days_ux_events,
                        _days_eval_traces):
            live |= collect(student_id)
        source = "aggregated" if live else "none"
        days = live
        if not live:
            legacy = _days_legacy_episodes(student_id)
            if legacy:
                days = legacy
                source = "legacy_episodes"
        current, longest, last_active, total = streak_from_days(
            days, now=now)
        return {
            "source": source,
            "streak_days": current,
            "longest_streak": longest,
            "last_active_day": last_active,
            "active_days": total,
        }
    except Exception:
        return {"source": "none", "streak_days": 0, "longest_streak": 0,
                "last_active_day": "", "active_days": 0}


def daily_counts(student_id: str, *, days: int = 14,
                 now: float | None = None) -> list[dict[str, Any]]:
    """Per-day classified activity counts for the dashboard chart:
    answers (graded ledger records) / teachings (M3 turns) /
    reviews+tasks (M9 orchestration events). Days window ends today.
    Deterministic, never raises."""
    try:
        now = now if now is not None else time.time()
        days = max(1, min(int(days), 90))
        by_date: dict[str, dict[str, int]] = {}
        for i in range(days):
            d = _day_str(now - i * _DAY_SECONDS)
            by_date[d] = {"answers": 0, "teachings": 0, "reviews": 0}

        try:
            # GAP-03 修复：经统一 evidence journal 统计有判分的作答，
            # 按 observed_at 归日。
            from app.agents.student_model.evaluation.store import get_journal
            state = get_journal(student_id).state()
            for src in state.sources.values():
                if src.availability != "available":
                    continue
                interp = src.interpretations.get(
                    src.current_interpretation_id) or {}
                if not isinstance(interp, dict):
                    interp = {}
                if not isinstance(
                        interp.get("task_result")
                        or src.interpretations.get("", {}).get("task_result"),
                        dict):
                    continue  # asked-but-ungraded rows are not answers yet
                parsed = _parse_any_ts(src.receipt.observed_at)
                if parsed is None:
                    continue
                d = _day_str(parsed.timestamp())
                if d in by_date:
                    by_date[d]["answers"] += 1
        except Exception:
            pass

        try:
            from .teaching_engine import teaching_log as tlog
            for entries in tlog.load_teaching_log(student_id).values():
                for e in entries or []:
                    try:
                        ts = float(getattr(e, "ts", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    d = _day_str(ts)
                    if d in by_date:
                        by_date[d]["teachings"] += 1
        except Exception:
            pass

        try:
            from .learning_orchestration import store as orch_store
            for ev in orch_store.read_events(student_id):
                try:
                    ts = float(getattr(ev, "ts", 0) or 0)
                except (TypeError, ValueError):
                    continue
                d = _day_str(ts)
                if d in by_date:
                    by_date[d]["reviews"] += 1
        except Exception:
            pass

        out: list[dict[str, Any]] = []
        for i in range(days - 1, -1, -1):
            d = _day_str(now - i * _DAY_SECONDS)
            row = {"date": d, **by_date.get(d, {"answers": 0, "teachings": 0, "reviews": 0})}
            out.append(row)
        return out
    except Exception:
        return []


# --- normalized v2 snapshot (plan.md §7.3/§7.4, site assistant A03) ---------

_VALID_VERDICTS = {"correct", "partial", "wrong"}
# 窗口内计入学习日/任务口径的 M9 事件闭集（§7.4-8）：仅真实完成与真实
# 复习提交；task_launched / quiz_evidence 等不在此列。
_TASK_COMPLETION_EVENT = "task_completed"
_REVIEW_SUBMISSION_EVENT = "srs_review"


def _local_date(dt_utc: datetime, tz: ZoneInfo) -> str:
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d")


def _in_window(dt: datetime, start_at: datetime, end_at: datetime) -> bool:
    return start_at <= dt < end_at


def _source_task_result(src: Any) -> dict | None:
    """当前解释（或 MC 仅判分的 "" 解释）里的 TaskResult。"""
    for key in (src.current_interpretation_id, ""):
        interp = src.interpretations.get(key)
        if isinstance(interp, dict):
            result = interp.get("task_result")
            if isinstance(result, dict):
                return result
    return None


def _latest_job_state(state: Any, source_id: str) -> str | None:
    best, best_ts = None, ""
    for rt in state.jobs.values():
        job = rt.job
        if job.source_id != source_id:
            continue
        if job.job_id > best_ts:
            best, best_ts = job.state.value, job.job_id
    return best


def learning_activity_snapshot(
    student_id: str,
    *,
    start_at: datetime,
    end_at: datetime,
    timezone: str,
    workspace_ids: list[str] | None = None,
) -> dict[str, Any]:
    """规范化学习活动快照（plan.md §7.4，metric_version=2）。

    - 半开区间 [start_at, end_at)，时间先统一为 tz-aware UTC 再按请求
      时区取本地日（不按 86400 秒递减，夏令时安全）。
    - workspace_ids=None（或空）为全局查询：保留无法归属工作区的真实
      记录并单列为 unscoped；非空列表时只统计能证实归属的记录。
    - 每类数据源带 ready/empty/partial/error 状态与覆盖说明；异常不被
      当作零活动（GAP-03）。
    - 只读：不物化任务、不触发评价、不写任何存储。
    """
    notices: list[dict[str, str]] = []
    try:
        tz = ZoneInfo(timezone)
        effective_tz = timezone
    except Exception:
        tz = dt_timezone.utc
        effective_tz = "UTC"
        notices.append({
            "code": "timezone_invalid",
            "message": f"无法识别时区 {timezone!r}，已回退 UTC。",
        })

    filtered = bool(workspace_ids)
    allowed_ws = set(workspace_ids or [])

    # ---- 1) 学习证据 journal（作答统计 + 受理学习日） --------------------
    evidence: dict[str, Any] = {
        "status": "empty", "records": 0, "omitted": 0,
        "archived_skipped": 0, "note": ""}
    days_by_ws: dict[str, set[str]] = {}
    unscoped_days: set[str] = set()
    answers = {
        "status": "empty",
        "answer_attempt_count": 0,
        "graded_answer_count": 0,
        "pending_answer_count": 0,
        "pending_buckets": {
            "pending": 0, "failed": 0, "disabled": 0, "disputed": 0,
            "indeterminate": 0, "unverified": 0},
    }
    attempt_keys: set[str] = set()
    try:
        from app.agents.student_model.evaluation.store import get_journal
        state = get_journal(student_id).state()
        if getattr(state, "corrupt", False):
            evidence["status"] = "partial"
            evidence["note"] = "证据账本存在损坏事务，已按可重放部分统计。"
            notices.append({"code": "journal_corrupt",
                            "message": "学习证据账本部分损坏，统计可能不完整。"})
        from app.core.config import settings as _settings
        evaluation_disabled = (
            getattr(_settings, "learner_evaluation_mode", "active") == "off")
        review_active = getattr(state, "review_active_by_source", {}) or {}

        for sid, src in state.sources.items():
            if src.availability != "available":
                evidence["archived_skipped"] += 1
                continue
            parsed = _parse_any_ts(src.receipt.observed_at)
            if parsed is None:
                evidence["omitted"] += 1
                continue
            if not _in_window(parsed, start_at, end_at):
                continue
            evidence["records"] += 1

            ws = src.receipt.workspace_id_at_observation or ""
            if filtered and (not ws or ws not in allowed_ws):
                continue  # 单工作区查询排除不能证实归属的记录
            day = _local_date(parsed, tz)
            if ws:
                days_by_ws.setdefault(ws, set()).add(day)
            else:
                unscoped_days.add(day)

            # 作答口径（§7.3）：当前记录按 (source_id, source_revision)
            # 天然去重；attempt_id 再消重复导入。
            attempt_key = src.receipt.attempt_id or sid
            if attempt_key in attempt_keys:
                continue
            attempt_keys.add(attempt_key)
            answers["answer_attempt_count"] += 1

            result = _source_task_result(src)
            bucket = "pending"
            if sid in review_active:
                bucket = "disputed"
            elif result is not None:
                # journal 的 model_dump() 保留 Enum 成员，统一取值比较。
                grading = getattr(result.get("grading_status"), "value",
                                  result.get("grading_status")) or ""
                verdict = getattr(result.get("verdict"), "value",
                                  result.get("verdict"))
                if grading == "graded" and verdict in _VALID_VERDICTS:
                    bucket = "graded"
                elif grading in ("indeterminate", "unverified"):
                    bucket = grading
                else:
                    bucket = "pending"
            else:
                job_state = _latest_job_state(state, sid)
                if job_state == "failed":
                    bucket = "failed"
                elif evaluation_disabled:
                    bucket = "disabled"
                else:
                    bucket = "pending"
            if bucket == "graded":
                answers["graded_answer_count"] += 1
            else:
                answers["pending_buckets"][bucket] = (
                    answers["pending_buckets"].get(bucket, 0) + 1)
        answers["pending_answer_count"] = (
            answers["answer_attempt_count"] - answers["graded_answer_count"])
        if evidence["status"] != "partial":
            evidence["status"] = "ready" if evidence["records"] else "empty"
        answers["status"] = evidence["status"]
        if evidence["omitted"]:
            evidence["status"] = "partial" if evidence["records"] else evidence["status"]
            evidence["note"] = (evidence["note"]
                                + f"{evidence['omitted']} 条记录时间缺失或无法解析，未计入。")
    except Exception:
        evidence["status"] = "error"
        answers["status"] = "error"
        notices.append({"code": "learning_evidence_unavailable",
                        "message": "学习证据读取失败，作答统计不可用。"})

    # ---- 2) 教学日志（M3 实际教学轮） ------------------------------------
    teaching: dict[str, Any] = {"status": "empty", "turns": 0, "note": ""}
    teaching_days_global: set[str] = set()
    try:
        from .teaching_engine import teaching_log as tlog
        loaded = tlog.load_teaching_log(student_id)
        in_window = 0
        for entries in loaded.values():
            for e in entries or []:
                parsed = _parse_any_ts(getattr(e, "ts", 0))
                if parsed is None or not _in_window(parsed, start_at, end_at):
                    continue
                in_window += 1
                teaching_days_global.add(_local_date(parsed, tz))
        teaching["turns"] = in_window
        if in_window:
            teaching["status"] = "partial"
            teaching["note"] = ("教学日志按概念仅保留最近若干轮，"
                                "长期窗口可能少计。")
        if filtered:
            teaching["note"] = (teaching["note"]
                                + "教学日志无工作区归属，仅计入全局统计。")
    except Exception:
        teaching["status"] = "error"
        notices.append({"code": "teaching_log_unavailable",
                        "message": "教学日志读取失败。"})
    if not filtered:
        unscoped_days |= teaching_days_global

    # ---- 3) M9 事件（任务完成 + 真实复习提交，严格读取） ------------------
    tasks: dict[str, Any] = {
        "status": "empty", "completed_task_count": None,
        "known_minimum": 0, "complete": False, "review_submissions": 0,
        "coverage": {}}
    try:
        from .learning_orchestration import store as orch_store
        events, coverage = orch_store.read_events_with_coverage(student_id)
        tasks["coverage"] = coverage
        if not coverage["readable"]:
            tasks["status"] = "error"
            notices.append({"code": "orchestration_events_unavailable",
                            "message": "学习编排事件读取失败，任务统计不可用。"})
        else:
            task_ws: dict[str, str] = {}
            try:
                orch_state = orch_store.load_state(student_id)
                task_ws = {t.id: (t.workspace_id or "")
                           for t in orch_state.daily_tasks}
            except Exception:
                task_ws = {}
            completed_ids: set[str] = set()
            review_days_ws: list[tuple[str, str]] = []  # (day, ws|"")
            for ev in events:
                parsed = _parse_any_ts(getattr(ev, "ts", 0))
                if parsed is None or not _in_window(parsed, start_at, end_at):
                    continue
                if ev.type == _TASK_COMPLETION_EVENT:
                    tid = str(ev.payload.get("task_id") or "")
                    if tid:
                        completed_ids.add(tid)
                    ws = task_ws.get(tid, "")
                    day = _local_date(parsed, tz)
                    if filtered and (not ws or ws not in allowed_ws):
                        continue
                    if ws:
                        days_by_ws.setdefault(ws, set()).add(day)
                    else:
                        unscoped_days.add(day)
                elif ev.type == _REVIEW_SUBMISSION_EVENT:
                    tasks["review_submissions"] += 1
                    day = _local_date(parsed, tz)
                    review_days_ws.append((day, ""))
            # 复习提交无可靠工作区归属：全局计入 unscoped，单工作区排除。
            if not filtered:
                for day, _ws in review_days_ws:
                    unscoped_days.add(day)
            # ---- §22.2-4（B08）：优先用 task_status_changed 事件流推导 ----
            # 窗口内完成且其后未撤销的实例才计数；学习日归属用事件里的
            # workspace_id；旧 task_completed 事件只作为已知下界。
            try:
                from .learning_orchestration import history as orch_history
                derived = orch_history.completed_tasks_in_window(
                    student_id, start_at=start_at, end_at=end_at,
                    timezone_name=effective_tz)
            except Exception:
                derived = None
            if derived and derived.get("status") == "ready":
                by_source = dict(derived.get("by_completion_source") or {})
                valid_count = int(derived.get("valid_completed_count") or 0)
                tasks["by_completion_source"] = by_source
                tasks["coverage_started_at"] = derived.get(
                    "coverage_started_at")
                tasks["history_incomplete"] = bool(
                    derived.get("history_incomplete"))
                tasks["known_minimum"] = max(
                    valid_count, len(completed_ids))
                # 派生实例的学习日归属（事件带真实 workspace_id）。
                for inst in derived.get("valid_instances") or []:
                    day = str(inst.get("day") or "")
                    if not day:
                        continue
                    ws = str(inst.get("workspace_id") or "")
                    if filtered and (not ws or ws not in allowed_ws):
                        continue
                    if ws:
                        days_by_ws.setdefault(ws, set()).add(day)
                    else:
                        unscoped_days.add(day)
                if derived.get("history_incomplete") \
                        or coverage["truncated"]:
                    # 历史缺口诚实呈现：只给已知下界，不给伪装总数。
                    tasks["completed_task_count"] = None
                    tasks["complete"] = False
                    notices.append({
                        "code": "history_incomplete",
                        "message": "任务完成记录覆盖不完整，仅显示已知下界。"})
                else:
                    tasks["completed_task_count"] = valid_count
                    tasks["complete"] = bool(
                        coverage["readable"] and not coverage["truncated"]
                        and not coverage["invalid_count"])
            else:
                tasks["known_minimum"] = len(completed_ids)
                tasks["complete"] = bool(
                    coverage["readable"] and not coverage["truncated"]
                    and not coverage["invalid_count"])
                tasks["completed_task_count"] = (
                    len(completed_ids) if tasks["complete"] else None)
            tasks["status"] = "ready" if (
                tasks["completed_task_count"] or completed_ids
                or (derived and derived.get("status") == "ready")) else (
                    "ready" if coverage["readable"] else "error")
            if coverage["truncated"]:
                notices.append({
                    "code": "orchestration_events_truncated",
                    "message": "编排事件超出读取预算，任务完成数为已知下界。"})
    except Exception:
        tasks["status"] = "error"
        notices.append({"code": "orchestration_events_unavailable",
                        "message": "学习编排事件读取失败，任务统计不可用。"})

    scoped_days: set[str] = set()
    for ws, days in days_by_ws.items():
        scoped_days |= days
    all_days = scoped_days | (set() if filtered else unscoped_days)
    complete = (
        evidence["status"] in ("ready", "empty")
        and teaching["status"] in ("ready", "empty", "partial")
        and tasks["status"] in ("ready", "empty")
        and tasks["complete"])

    return {
        "metric_version": 2,
        "window": {
            "start_at": start_at.astimezone(dt_timezone.utc).isoformat(),
            "end_at": end_at.astimezone(dt_timezone.utc).isoformat(),
            "timezone": effective_tz,
        },
        "sources": {
            "learning_evidence": evidence,
            "teaching_log": teaching,
            "orchestration_events": tasks,
        },
        "recorded_learning_days": len(all_days),
        "days_by_workspace": {ws: len(days) for ws, days in days_by_ws.items()},
        "unscoped": {
            "included": not filtered,
            "days": len(unscoped_days) if not filtered else 0,
        },
        "answers": answers,
        "teaching_turns": teaching["turns"],
        "completed_tasks": {
            "status": tasks["status"],
            "value": tasks["completed_task_count"],
            "known_minimum": tasks["known_minimum"],
            "complete": tasks["complete"],
            # §22.2（B08）：来源分列与历史覆盖说明。
            "by_completion_source": tasks.get("by_completion_source"),
            "coverage_started_at": tasks.get("coverage_started_at"),
            "history_incomplete": tasks.get("history_incomplete", False),
        },
        "complete": complete,
        "notices": notices,
    }
