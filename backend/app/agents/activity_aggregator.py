"""Unified learning-activity aggregation (L1 profile layer, read-side).

Single truth source for "on which days did this student actually learn",
derived as a deterministic LOCAL-day union over five always-on ledgers:

  1. learning_records   (every question asked/graded; created_at/updated_at)
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
"""
from __future__ import annotations

import time
from typing import Any

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


def _add_ts(days: set[str], ts: Any) -> None:
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return
    if ts > 0:
        days.add(_day_str(ts))


# --- source collectors (each returns a set of local day strings) ------------

def _days_learning_records(student_id: str) -> set[str]:
    days: set[str] = set()
    try:
        from app.core import learning_records as lr
        for r in lr.list_records(student_id):
            _add_ts(days, r.get("created_at"))
            _add_ts(days, r.get("updated_at"))
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
    for collect in (_days_learning_records, _days_teaching_log,
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
        from app.core import learning_records as lr
        for r in lr.list_records(student_id):
            kp = str(r.get("knowledge_point") or "").strip()
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
        for collect in (_days_learning_records, _days_teaching_log,
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
            from app.core import learning_records as lr
            for r in lr.list_records(student_id):
                if not r.get("verdict"):
                    continue  # asked-but-ungraded rows are not answers yet
                try:
                    ts = float(r.get("updated_at") or 0)
                except (TypeError, ValueError):
                    continue
                d = _day_str(ts)
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
