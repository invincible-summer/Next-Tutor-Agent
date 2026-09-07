"""Single-slot background scheduler for optional vector indexing jobs."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_LOCK: asyncio.Lock | None = None
_TASKS: dict[str, asyncio.Task] = {}


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


async def _run(key: str, scope: str, chunks: list[Any], embed_client: Any,
               callback: Callable[[bool], Any] | None) -> None:
    ok = False
    try:
        async with _lock():
            from . import vector_store
            ok = bool(await vector_store.ensure_indexed(scope, chunks, embed_client))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("background vector job failed for %s: %s", key, exc)
    finally:
        if callback is not None:
            try:
                value = callback(ok)
                if inspect.isawaitable(value):
                    await value
            except Exception as exc:
                log.warning("vector job callback failed for %s: %s", key, exc)
        _TASKS.pop(key, None)


def schedule_index(scope: str, chunks: list[Any], embed_client: Any, *,
                   key: str | None = None,
                   callback: Callable[[bool], Any] | None = None) -> bool:
    """Queue a copied chunk snapshot; duplicate live keys are coalesced."""
    if embed_client is None or not chunks:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    job_key = key or scope
    prior = _TASKS.get(job_key)
    if prior is not None and not prior.done():
        return False
    task = loop.create_task(_run(job_key, scope, list(chunks), embed_client, callback))
    _TASKS[job_key] = task
    return True


def cancel_all() -> None:
    for task in list(_TASKS.values()):
        if not task.done():
            task.cancel()
    _TASKS.clear()
