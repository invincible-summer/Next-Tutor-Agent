"""Temporal worker process entrypoint (ADR-0013).

Runs the durable workflow lanes alongside — never inside — the HTTP app,
sharing the same ``app.*`` domain packages; only the process ownership of
long jobs changes::

    python worker.py                          # serve all task queues
    python worker.py --queues documents       # serve a subset (independent scaling)

Requires ``TEMPORAL_ADDRESS``. File-mode deployments (no Temporal) keep
job execution inside the API process and need no worker — this entrypoint
fails fast instead of silently doing nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from dataclasses import dataclass
from typing import Any, Callable, Sequence

# Import order matters: app.core.config loads the repo .env so
# TEMPORAL_ADDRESS / NEXT_TUTOR_DATA_DIR resolve exactly like the API
# process (data roots are read lazily by app.core.paths at first use).
from app.core import config  # noqa: F401
from app.workflows.config import temporal_address, temporal_namespace
from app.workflows.runtime import ALL_TASK_QUEUES

log = logging.getLogger("worker")


@dataclass(frozen=True)
class Lane:
    """The workflow/activity set served on one task queue."""

    workflows: tuple[type, ...] = ()
    activities: tuple[Callable[..., Any], ...] = ()
    notes: str = ""


def build_lanes() -> dict[str, Lane]:
    """Register per-domain lanes.

    Lanes land per migration batch (ADR-0013: textbook → classroom →
    illustration → evaluation → maintenance). Domain modules are imported
    inside this function so a lane's heavyweight dependencies stay out of
    ``--help`` and file-mode imports.
    """
    lanes: dict[str, Lane] = {}
    from app.workflows.runtime import (
        TASK_QUEUE_CLASSROOM,
        TASK_QUEUE_DOCUMENTS,
        TASK_QUEUE_MEDIA,
    )
    from app.workflows.textbook import TEXTBOOK_ACTIVITIES, TEXTBOOK_WORKFLOWS
    lanes[TASK_QUEUE_DOCUMENTS] = Lane(
        workflows=TEXTBOOK_WORKFLOWS, activities=TEXTBOOK_ACTIVITIES,
        notes="textbook build intents + manual refresh (ADR-0013 C1)")
    from app.workflows.classroom import (
        CLASSROOM_ACTIVITIES,
        CLASSROOM_WORKFLOWS,
    )
    lanes[TASK_QUEUE_CLASSROOM] = Lane(
        workflows=CLASSROOM_WORKFLOWS, activities=CLASSROOM_ACTIVITIES,
        notes="classroom generation supervisor (ADR-0013 C2)")
    from app.workflows.illustration_quiz import (
        QUIZ_ILLUSTRATION_ACTIVITIES,
        QUIZ_ILLUSTRATION_WORKFLOWS,
    )
    from app.workflows.illustration_scenario import (
        SCENARIO_ILLUSTRATION_ACTIVITIES,
        SCENARIO_ILLUSTRATION_WORKFLOWS,
    )
    lanes[TASK_QUEUE_MEDIA] = Lane(
        workflows=QUIZ_ILLUSTRATION_WORKFLOWS + SCENARIO_ILLUSTRATION_WORKFLOWS,
        activities=(QUIZ_ILLUSTRATION_ACTIVITIES
                    + SCENARIO_ILLUSTRATION_ACTIVITIES),
        notes="quiz + scenario illustration jobs (ADR-0013 C3)")
    return lanes


async def _bootstrap_documents_recovery() -> None:
    """Worker 启动恢复（durable 模式下 API 不再执行，见 main.py 门控）。

    与文件模式 API lifespan 同一步骤：重启对账 → OCR 续跑 → 中断构建
    intent 重入队（worker 进程角色使 enqueue 走进程内域队列）。
    """
    from app.core.textbook import reconcile_stale_builds
    from app.core.textbook_ocr import resume_pending_textbook_ocr
    reconcile_stale_builds()
    resume_pending_textbook_ocr()
    from app.agents.knowledge.textbook_builder import (
        resume_interrupted_textbook_builds)
    resumed = await resume_interrupted_textbook_builds()
    if resumed:
        log.info("resumed %d interrupted textbook build(s)", resumed)


async def _bootstrap_media_recovery() -> None:
    """media lane 启动对账（ADR-0013 C3）。

    磁盘上仍 queued/running、且没有存活 workflow 的 illustration 记录，
    一律结算为 run_interrupted：覆盖 cutover 前的进程内残留与「持久化了
    job 但派发未达」的窗口。此后单个 job 的崩溃结算由其 workflow 的
    settle activity 兜底；worker 只在启动时做一次全量对账。
    """
    import json

    from app.illustration import orchestrator, persistence, scenario
    from app.workflows.illustration_common import (
        quiz_workflow_id,
        scenario_workflow_id,
        workflow_alive,
    )

    settled = 0
    for owner in persistence.iter_owners():
        for kind in ("jobs", "scenario_jobs"):
            base = persistence.owner_dir(owner) / kind
            if not base.is_dir():
                continue
            for path in base.glob("*.json"):
                try:
                    row = json.loads(path.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(row, dict) or \
                        row.get("status") not in {"queued", "running"}:
                    continue
                job_id = path.stem
                if kind == "jobs":
                    if not await workflow_alive(quiz_workflow_id(owner, job_id)):
                        orchestrator._settle_interrupted(owner, job_id)
                        settled += 1
                    continue
                if not await workflow_alive(
                        scenario_workflow_id(owner, job_id)):
                    try:
                        scenario._settle_interrupted(
                            owner, row["session_id"], job_id)
                        settled += 1
                    except (KeyError, scenario.SceneError):
                        pass  # session 已删：记录失去挂载点，无需结算
    if settled:
        log.info("media recovery: settled %d interrupted illustration job(s)",
                 settled)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="worker.py",
        description="Temporal durable workflow worker (ADR-0013)")
    parser.add_argument(
        "--queues", default=",".join(ALL_TASK_QUEUES),
        help=f"comma-separated task queue subset of {','.join(ALL_TASK_QUEUES)}")
    return parser.parse_args(list(argv))


async def _serve(queues: Sequence[str]) -> int:
    from temporalio.worker import Worker

    from app.workflows.runtime import TASK_QUEUE_DOCUMENTS, TASK_QUEUE_MEDIA, get_client

    client = await get_client()
    if TASK_QUEUE_DOCUMENTS in queues:
        try:
            await _bootstrap_documents_recovery()
        except Exception:
            log.exception("documents recovery failed; queued intents will "
                          "be picked up on the next worker restart")
    if TASK_QUEUE_MEDIA in queues:
        try:
            await _bootstrap_media_recovery()
        except Exception:
            log.exception("media recovery failed; interrupted jobs without a "
                          "live workflow stay queued until the next restart")
    lanes = build_lanes()
    workers: list[Worker] = []
    for queue in queues:
        lane = lanes.get(queue)
        if lane is None or not (lane.workflows or lane.activities):
            log.warning("task queue %s has no registered lane yet — skipping",
                        queue)
            continue
        workers.append(Worker(
            client, task_queue=queue,
            workflows=list(lane.workflows),
            activities=list(lane.activities)))
    if not workers:
        log.error("no registered lanes for queues %s — refusing to run an "
                  "idle worker", list(queues))
        return 1

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stop.set)

    log.info("temporal worker serving %s on %s (namespace %s)",
             [w.task_queue for w in workers], temporal_address(),
             temporal_namespace())
    runs = [asyncio.create_task(worker.run()) for worker in workers]
    stop_waiter = asyncio.create_task(stop.wait())
    done, _pending = await asyncio.wait(
        {*runs, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
    if stop_waiter not in done:
        # A worker died unexpectedly (server unreachable, fatal internal
        # error) — shut the siblings down and exit non-zero.
        log.error("a worker task exited unexpectedly", exc_info=True)
        stop.set()
    for worker in workers:
        try:
            await asyncio.wait_for(worker.shutdown(), timeout=30)
        except Exception:
            log.warning("worker shutdown on %s timed out", worker.task_queue)
    await asyncio.gather(*runs, return_exceptions=True)
    return 0 if stop_waiter in done else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not temporal_address():
        print("TEMPORAL_ADDRESS is not configured — file-mode deployments run "
              "job execution inside the API process and need no worker",
              file=sys.stderr)
        return 2
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    unknown = [q for q in queues if q not in ALL_TASK_QUEUES]
    if unknown:
        print(f"unknown task queue(s) {unknown}; known: "
              f"{','.join(ALL_TASK_QUEUES)}", file=sys.stderr)
        return 2
    if not queues:
        print("no task queues selected", file=sys.stderr)
        return 2
    # 进程角色标记必须先于任何域 enqueue（activity 内复用进程内域队列）。
    from app.workflows.config import mark_worker_process
    mark_worker_process()
    try:
        return asyncio.run(_serve(queues))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
