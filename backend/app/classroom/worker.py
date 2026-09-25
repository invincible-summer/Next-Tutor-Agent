"""课堂生成 worker：lifespan 后台 asyncio supervisor（plan.md §15.3，D04）。

单 uvicorn worker，不引入 Redis/Celery。持久化 job 是事实源；内存队列只
是加速（pending 登记表 + 唤醒事件）。调度约束：
  - 全局并发 ``CLASSROOM_JOB_CONCURRENCY``（默认 2），每 owner 同时 1；
  - owner 轮转挑任务防饥饿；同 lesson 串行；
  - 重启扫描：running job → queued + recovery_count+1（≤3 次自动恢复，
    超限 failed 提供显式重试）；pending publish 用 store 恢复钩子补齐；
  - shutdown：停止接受新任务、给在途任务至多 10 秒保存检查点，超时取消，
    不把可恢复 job 标成永久失败。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from ..core import classroom_store as store
from ..core.config import settings
from ..schemas import classroom as sc
from . import limits
from .pipeline import ClassroomPipeline, PipelineCrash, PipelineDeps

log = logging.getLogger(__name__)

MAX_AUTO_RECOVERY = 3
SHUTDOWN_GRACE_SECONDS = 10.0
SCAN_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True)
class _JobKey:
    owner: str
    workspace: str
    lesson: str
    job: str


def production_deps() -> PipelineDeps:
    """真实依赖：classroom purpose LLM + 已配置的检索/图库。"""
    from ..core.llm_async import get_llm
    from .media.service import ImageSearchService, build_image_providers
    from .research import build_research_provider

    return PipelineDeps(
        llm=get_llm("classroom"),
        research=build_research_provider(),
        images=ImageSearchService(build_image_providers()),
    )


class ClassroomWorker:
    def __init__(self, *, deps_factory: Callable[[], PipelineDeps] | None = None,
                 ) -> None:
        self._deps_factory = deps_factory or production_deps
        self._pending: dict[str, deque[_JobKey]] = {}
        self._tasks: dict[_JobKey, asyncio.Task] = {}
        self._owner_running: set[str] = set()
        self._lesson_running: set[tuple[str, str, str]] = set()
        self._owner_last_start: dict[str, float] = {}
        self._wake = asyncio.Event()
        self._stopping = False
        self._loop_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 生命周期

    async def start(self) -> None:
        await self._startup_scan()
        self._loop_task = asyncio.get_running_loop().create_task(
            self._scheduler(), name="classroom-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._tasks:
            done, pending = await asyncio.wait(
                list(self._tasks.values()), timeout=SHUTDOWN_GRACE_SECONDS)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None
        # 在途任务被取消/中断的 job 保持可恢复状态（重启扫描接手），
        # 绝不在退出时标 failed。

    def enqueue(self, owner: str, workspace: str, lesson: str,
                job_id: str) -> None:
        """service 层入队钩子（同步，Event.set 线程安全）。"""
        if self._stopping:
            return
        key = _JobKey(owner, workspace, lesson, job_id)
        queue = self._pending.setdefault(owner, deque())
        if key not in queue:  # 重复登记（扫描 + 显式入队）幂等
            queue.append(key)
        self._wake.set()

    # ------------------------------------------------------------------ 启动扫描

    async def _startup_scan(self) -> None:
        root = store.classroom_root()
        if not root.is_dir():
            return
        recovered = 0
        for owner_dir in sorted(root.iterdir()):
            if not owner_dir.is_dir() or owner_dir.name.startswith("."):
                continue
            owner = owner_dir.name
            ws_root = owner_dir / "workspaces"
            if not ws_root.is_dir():
                continue
            for ws_dir in sorted(ws_root.iterdir()):
                if not ws_dir.is_dir():
                    continue
                workspace = ws_dir.name
                for lesson_id in store.list_lesson_ids(owner, workspace):
                    lesson = store.load_lesson(owner, workspace, lesson_id)
                    if lesson is None:
                        continue
                    jobs_dir = store.jobs_root(owner, workspace, lesson_id)
                    if not jobs_dir.is_dir():
                        continue
                    for job_dir in sorted(jobs_dir.iterdir()):
                        meta = job_dir / "job.json"
                        if not meta.is_file():
                            continue
                        job = store.load_job(owner, workspace, lesson_id,
                                             job_dir.name)
                        if job is None:
                            continue
                        try:
                            if store.recover_pending_publish(
                                    owner, workspace, lesson_id, job):
                                log.info(
                                    "classroom: recovered pending publish "
                                    "%s/%s rev by scan", lesson_id,
                                    job.job_id)
                        except Exception:
                            log.warning("classroom: publish recovery failed",
                                        exc_info=True)
                        if job.state == sc.JobState.running:
                            self._recover_running(job)
                            recovered += 1
                        elif job.state == sc.JobState.queued:
                            self.enqueue(owner, workspace, lesson_id,
                                         job.job_id)
        if recovered:
            log.info("classroom: %d running job(s) re-queued after restart",
                     recovered)

    def _recover_running(self, job: sc.GenerationJob) -> None:
        def mutate(target: sc.GenerationJob) -> None:
            target.recovery_count += 1
            if target.recovery_count > MAX_AUTO_RECOVERY:
                target.state = sc.JobState.failed
                target.last_error = "重启自动恢复超过上限，请显式重试"
                target.phase = None
            else:
                target.state = sc.JobState.queued
                target.phase = None

        try:
            updated = store.update_job(
                job.owner_id, job.workspace_id, job.lesson_id,
                job.job_id, mutate)
        except store.CasConflictError:
            return
        if updated.state == sc.JobState.queued:
            self.enqueue(job.owner_id, job.workspace_id, job.lesson_id,
                         job.job_id)

    # ------------------------------------------------------------------ 调度

    async def _scheduler(self) -> None:
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(),
                                       timeout=SCAN_INTERVAL_SECONDS)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._wake.clear()
            if self._stopping:
                break
            try:
                self._spawn_runnable()
            except Exception:
                log.warning("classroom: scheduler iteration failed",
                            exc_info=True)

    def _spawn_runnable(self) -> None:
        capacity = settings.classroom_job_concurrency - len(self._tasks)
        if capacity <= 0:
            return
        # owner 轮转：按上次启动时间升序遍历，防止大 owner 饿死小 owner
        owners = sorted(
            (o for o, q in self._pending.items() if q),
            key=lambda o: self._owner_last_start.get(o, 0.0))
        picked = 0
        for owner in owners:
            if picked >= capacity or owner in self._owner_running:
                continue
            queue = self._pending[owner]
            skipped: list[_JobKey] = []
            while queue and picked < capacity:
                key = queue.popleft()
                job = store.load_job(key.owner, key.workspace, key.lesson,
                                     key.job)
                if job is None or job.state != sc.JobState.queued:
                    continue  # 事实源已变：丢弃登记
                if job.next_retry_at is not None and \
                        job.next_retry_at > store.utcnow():
                    skipped.append(key)
                    continue
                lesson_key = (key.owner, key.workspace, key.lesson)
                if lesson_key in self._lesson_running:
                    skipped.append(key)
                    continue
                try:
                    store.assert_owner_writable(key.owner)
                except store.ClassroomStorageError:
                    continue  # 账号已注销：丢弃登记（job 冻结在 queued）
                self._start_task(key)
                picked += 1
                break  # 每 owner 同时 1 个：本轮该 owner 到此为止
            queue.extend(skipped)
            if picked >= capacity:
                break

    def _start_task(self, key: _JobKey) -> None:
        self._owner_running.add(key.owner)
        self._lesson_running.add((key.owner, key.workspace, key.lesson))
        self._owner_last_start[key.owner] = time.monotonic()
        task = asyncio.get_running_loop().create_task(
            self._run_job(key), name=f"classroom-job-{key.job}")
        self._tasks[key] = task
        task.add_done_callback(lambda _t, k=key: self._finish_task(k))

    def _finish_task(self, key: _JobKey) -> None:
        self._tasks.pop(key, None)
        self._owner_running.discard(key.owner)
        self._lesson_running.discard((key.owner, key.workspace, key.lesson))
        self._wake.set()

    # ------------------------------------------------------------------ 执行

    async def _run_job(self, key: _JobKey) -> None:
        try:
            job = store.load_job(key.owner, key.workspace, key.lesson,
                                 key.job)
            if job is None or job.state != sc.JobState.queued:
                return
            deps = self._deps_factory()
            pipeline = ClassroomPipeline(
                key.owner, key.workspace, key.lesson, key.job, deps)
            try:
                await pipeline.run()
            except PipelineCrash:
                # 进程级中断模拟/真实崩溃：job 停在 running，重启扫描恢复
                log.warning("classroom: job %s interrupted", key.job)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 意外失败 → 有界自动恢复
            log.warning("classroom: job %s crashed: %s", key.job, exc,
                        exc_info=True)
            try:
                await self._handle_unexpected(key, exc)
            except Exception:
                log.warning("classroom: recovery bookkeeping failed",
                            exc_info=True)

    async def _handle_unexpected(self, key: _JobKey, exc: Exception) -> None:
        def mutate(job: sc.GenerationJob) -> None:
            if job.state in (sc.JobState.succeeded, sc.JobState.failed,
                             sc.JobState.cancelled):
                return
            job.recovery_count += 1
            if job.recovery_count > MAX_AUTO_RECOVERY:
                job.state = sc.JobState.failed
                job.last_error = f"自动恢复超限：{type(exc).__name__}"[:2000]
                job.phase = None
                return
            job.state = sc.JobState.queued
            job.phase = None
            delay = min(60.0, 5.0 * (2 ** (job.recovery_count - 1)))
            from datetime import timedelta
            job.next_retry_at = store.utcnow() + timedelta(seconds=delay)
            job.last_error = f"{type(exc).__name__}: {exc}"[:2000]

        try:
            store.update_job(key.owner, key.workspace, key.lesson, key.job,
                             mutate)
        except store.CasConflictError:
            return
        job = store.load_job(key.owner, key.workspace, key.lesson, key.job)
        if job is not None and job.state == sc.JobState.queued:
            self.enqueue(key.owner, key.workspace, key.lesson, key.job)


_worker: ClassroomWorker | None = None


def get_worker() -> ClassroomWorker:
    global _worker
    if _worker is None:
        _worker = ClassroomWorker()
    return _worker


def set_worker(worker: ClassroomWorker | None) -> None:
    """测试替换/清理。"""
    global _worker
    _worker = worker


def count_queued_jobs(owner: str) -> int:
    """某 owner 的 queued job 数（排队上限检查，§15.4）。"""
    root = store.classroom_root()
    if not root.is_dir():
        return 0
    total = 0
    for ws_dir in sorted((root / owner / "workspaces").glob("*")):
        if not ws_dir.is_dir():
            continue
        for lesson_id in store.list_lesson_ids(owner, ws_dir.name):
            jobs_dir = store.jobs_root(owner, ws_dir.name, lesson_id)
            if not jobs_dir.is_dir():
                continue
            for meta in jobs_dir.glob("*/job.json"):
                try:
                    import json as _json
                    data = _json.loads(meta.read_text(encoding="utf-8"))
                    if data.get("state") == "queued":
                        total += 1
                except (OSError, ValueError):
                    continue
    return total


def count_queued_jobs_global() -> int:
    root = store.classroom_root()
    if not root.is_dir():
        return 0
    return sum(count_queued_jobs(d.name)
               for d in root.iterdir()
               if d.is_dir() and not d.name.startswith("."))


def assert_queue_capacity(owner: str) -> None:
    from .errors import ClassroomError

    if count_queued_jobs(owner) >= limits.QUEUE_PER_OWNER:
        raise ClassroomError("quota_exceeded",
                             f"待处理任务已达上限（{limits.QUEUE_PER_OWNER}）")
    if count_queued_jobs_global() >= limits.QUEUE_GLOBAL:
        raise ClassroomError(
            "quota_exceeded", f"全局生成队列已满（{limits.QUEUE_GLOBAL}）")
