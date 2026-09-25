"""阶段C（update_plan §5–§6）：每日零点批次的规划与状态。

DailyPlanner（进程内任务，lifespan 启动）：
- 每 TICK 秒检查一次策略；daily_midnight 模式下计算业务时区的自然日
  窗口（不能 sleep(86400) 后假定仍是零点，§6.3.2）；
- 本地零点过后为每个有当日活动的账号关闭该日窗口：全部候选来源得到
  最终处置（解释成功 / 明确失败）才记录 OpDailyBatchClosed，仍有在途
  的下一个 tick 重试；无候选评价的窗口记录 no_observation，不编造
  综合（§6.3.3）；
- 重启恢复：批次事实按 (student, local_date) 幂等——已关闭的日期直接
  跳过，漏掉的日期按窗口补跑（eligible_after 已在受理时冻结，零点后
  worker 自然可认领；规划器只负责对账与关闭，§5.4"零点停机"行）；
- 2 → 1 切换：release_backlog 把未到期 job 的 eligible_after 清空并
  唤醒 worker（按 user/workspace/observed_at 顺序自然成立——worker
  本就按最旧可认领作业排序）。

时钟可注入（tests 传 fake tick）；失败永不抛出——规划器崩溃不能影响
对话主链路。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import schema as S
from .store import get_journal

log = logging.getLogger(__name__)

TICK_SECONDS = 30.0
CATCHUP_MAX_DAYS = 14          # 补跑回看上限（防全量历史重扫）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def close_due_day_windows(*, now: datetime | None = None) -> int:
    """为所有学生关闭已到期且全部处置完毕的日窗口。返回关闭数。

    幂等：state.daily_batches 已记录的 (local_date) 跳过。
    """
    from app.core.learner_evaluation_policy import (SCHEDULE_DAILY_MIDNIGHT,
                                                    day_window_utc,
                                                    load_policy,
                                                    parse_iso)
    now = now or _now()
    policy = load_policy()
    if policy["evaluation_schedule"] != SCHEDULE_DAILY_MIDNIGHT:
        return 0
    tz_name = policy["timezone"]
    today_local = now.astimezone(__import__("zoneinfo").ZoneInfo(tz_name)
                                 ).strftime("%Y-%m-%d")
    from . import store as store_mod
    if not store_mod.STUDENTS_DIR.exists():
        return 0
    closed = 0
    for path in store_mod.STUDENTS_DIR.glob(
            f"*{store_mod.JOURNAL_SUFFIX}"):
        sid = path.name[: -len(store_mod.JOURNAL_SUFFIX)]
        try:
            if _close_windows_for(sid, tz_name, today_local):
                closed += 1
        except Exception:
            log.exception("daily planner close failed for %s", sid)
    return closed


def _close_windows_for(sid: str, tz_name: str, today_local: str) -> bool:
    """关闭单个学生所有到期窗口；有在途候选时返回 False（下轮重试）。"""
    journal = get_journal(sid)
    state = journal.state()
    if not state.jobs:
        return True
    # 日窗口按 job.local_activity_date 归属（受理时冻结）
    by_date: dict[str, list[Any]] = {}
    source_dates: dict[str, str] = {}
    for rt in state.jobs.values():
        job = rt.job
        if job.local_activity_date and job.schedule_mode == \
                S_DAILY:
            by_date.setdefault(job.local_activity_date, []).append(rt)
            source_dates.setdefault(job.source_id, job.local_activity_date)
    if not by_date:
        return True
    changed = False
    for local_date, rts in sorted(by_date.items()):
        if local_date >= today_local:
            continue          # 窗口未关闭（今天还没过完）
        if local_date in state.daily_batches:
            continue          # 幂等：已对账
        terminal = (S.JobState.SUCCEEDED, S.JobState.ABSTAINED,
                    S.JobState.FAILED, S.JobState.CANCELLED)
        if any(rt.job.state not in terminal for rt in rts):
            return False      # 在途：下一轮再关
        start_utc, end_utc = _window(local_date, tz_name)
        evaluated = sum(1 for rt in rts
                        if rt.job.state in (S.JobState.SUCCEEDED,))
        failed = sum(1 for rt in rts
                     if rt.job.state == S.JobState.FAILED)
        journal.append([S.OpDailyBatchClosed(
            local_date=local_date, timezone=tz_name,
            window_start_utc=start_utc, window_end_utc=end_utc,
            candidate_source_ids=sorted(set(
                rt.job.source_id for rt in rts if rt.job.source_id)),
            evaluated_count=evaluated, failed_count=failed,
            no_observation=(evaluated == 0 and failed == 0))])
        changed = True
    return changed


def _window(local_date: str, tz_name: str) -> tuple[str, str]:
    from app.core.learner_evaluation_policy import day_window_utc
    return day_window_utc(local_date, tz_name)


S_DAILY = "daily_midnight"


def release_backlog() -> int:
    """2 → 1 切换：已受理未到期 job 立即释放入队（§5.4）。

    返回释放数量；唤醒 worker 由调用方完成。
    """
    from . import store as store_mod
    if not store_mod.STUDENTS_DIR.exists():
        return 0
    released = 0
    for path in store_mod.STUDENTS_DIR.glob(
            f"*{store_mod.JOURNAL_SUFFIX}"):
        sid = path.name[: -len(store_mod.JOURNAL_SUFFIX)]
        try:
            journal = get_journal(sid)
            state = journal.state()
            ops = []
            for rt in state.jobs.values():
                if rt.job.state == S.JobState.QUEUED and \
                        rt.job.eligible_after_utc:
                    ops.append(S.OpJobRescheduled(
                        job_id=rt.job.job_id, reason="policy_immediate"))
                    released += 1
            if ops:
                journal.append(ops)
        except Exception:
            log.exception("release backlog failed for %s", sid)
    return released


def pending_daily_status() -> dict[str, Any]:
    """管理面板的待评价量统计（§5.3）：按 journal 扫描（单实例可接受）。"""
    from . import store as store_mod
    pending = 0
    oldest = ""
    last_batch: dict[str, Any] | None = None
    if store_mod.STUDENTS_DIR.exists():
        for path in store_mod.STUDENTS_DIR.glob(
                f"*{store_mod.JOURNAL_SUFFIX}"):
            sid = path.name[: -len(store_mod.JOURNAL_SUFFIX)]
            try:
                state = get_journal(sid).state()
            except Exception:
                continue
            for rt in state.jobs.values():
                if rt.job.state == S.JobState.QUEUED and \
                        rt.job.eligible_after_utc:
                    pending += 1
            for src in state.sources.values():
                if src.availability == "available" and \
                        not src.current_interpretation_id:
                    observed = src.receipt.observed_at
                    if not oldest or observed < oldest:
                        oldest = observed
            for local_date, batch in state.daily_batches.items():
                if last_batch is None or local_date > last_batch["local_date"]:
                    last_batch = dict(batch, student_id=sid)
    return {"pending_source_count": pending,
            "oldest_pending_observed_at": oldest,
            "last_batch": last_batch}


class DailyPlanner:
    """进程内单例；lifespan 启动/停止；测试可直接调 close_due_day_windows."""

    def __init__(self, tick_seconds: float = TICK_SECONDS) -> None:
        self.tick = tick_seconds
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._last_closed_at: datetime | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="eval-planner")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    def status(self) -> dict[str, Any]:
        return {"running": self._task is not None
                and not self._task.done(),
                "last_closed_at": (self._last_closed_at.isoformat()
                                   if self._last_closed_at else "")}

    async def _run(self) -> None:
        log.info("daily evaluation planner started")
        while not self._stopping:
            try:
                closed = close_due_day_windows(now=_now())
                if closed:
                    self._last_closed_at = _now()
                    try:
                        from .worker import notify_evaluation_worker
                        notify_evaluation_worker()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("daily planner tick crashed")
            await asyncio.sleep(self.tick)


_PLANNER: DailyPlanner | None = None


def get_daily_planner() -> DailyPlanner:
    global _PLANNER
    if _PLANNER is None:
        _PLANNER = DailyPlanner()
    return _PLANNER


def set_daily_planner(planner: DailyPlanner | None) -> None:
    global _PLANNER
    _PLANNER = planner
