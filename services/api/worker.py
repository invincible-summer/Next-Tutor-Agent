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
    return lanes


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

    from app.workflows.runtime import get_client

    client = await get_client()
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
    try:
        return asyncio.run(_serve(queues))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
