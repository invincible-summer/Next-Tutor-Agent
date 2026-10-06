"""Synchronous bridge onto the async document repositories.

Domain stores are sync APIs called from dozens of modules (agents, tools,
endpoints). Rather than infecting every caller with async, SQL-mode domain
access runs on one dedicated worker thread with its own event loop and its
own engine — asyncpg connections are loop-bound and the shared engine
belongs to the API request loop, so the worker keeps a separate one.

Every :func:`call` submits a coroutine and blocks the caller for the DB
roundtrip (bounded, local-network latency, same profile as a sync driver).
The worker starts lazily on the first SQL-mode access and never exists in
file mode.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from .protocols import DocumentRepositoryError

_START_TIMEOUT = 30.0
_CALL_TIMEOUT = 60.0


class _Worker:
    """One thread + loop + dedicated engine for domain document access."""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.start_error: BaseException | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.engine: Any = None
        self.factory: Any = None
        self.thread = threading.Thread(target=self._run,
                                       name="domain-documents", daemon=True)
        self.thread.start()
        if not self.ready.wait(_START_TIMEOUT) or self.start_error:
            raise RuntimeError(
                "domain document worker failed to start"
            ) from self.start_error

    def _run(self) -> None:
        from .. import db
        from sqlalchemy.ext.asyncio import async_sessionmaker

        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            url = db.database_url()
            if not url:
                raise RuntimeError(
                    "domain document worker started in file mode")
            self.engine = db.create_engine(url)
            self.factory = async_sessionmaker(self.engine,
                                              expire_on_commit=False)
        except BaseException as exc:  # noqa: BLE001 - surfaced to starter
            self.start_error = exc
            self.ready.set()
            return
        self.ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def alive(self) -> bool:
        return self.thread.is_alive() and self.loop is not None

    async def _dispose(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()

    def stop(self) -> None:
        if self.loop is not None and self.alive():
            future = asyncio.run_coroutine_threadsafe(self._dispose(),
                                                      self.loop)
            try:
                future.result(_START_TIMEOUT)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)


_lock = threading.Lock()
_worker: _Worker | None = None


def _get_worker() -> _Worker:
    global _worker
    with _lock:
        if _worker is None or not _worker.alive():
            if _worker is not None:
                # Dead worker (engine/loop crash): drop it and rebuild; the
                # document store is stateless per call, rebuild is safe.
                try:
                    _worker.stop()
                except Exception:
                    pass
            _worker = _Worker()
        return _worker


def call(coro_fn: Callable[..., Any], *args: Any,
         timeout: float = _CALL_TIMEOUT) -> Any:
    """Run ``coro_fn(*args)`` on the worker loop; block for the result.

    Timeouts surface as DocumentRepositoryError; everything else re-raises
    the coroutine's own exception unchanged — domain closures (journal
    generation conflicts, CAS losses) carry domain semantics that callers
    match on, and infrastructure failures are no less actionable raw.
    """
    worker = _get_worker()
    future = asyncio.run_coroutine_threadsafe(coro_fn(*args), worker.loop)
    try:
        return future.result(timeout)
    except asyncio.TimeoutError as exc:
        future.cancel()
        raise DocumentRepositoryError(
            f"document store call timed out after {timeout}s") from exc


def repository(domain: str):
    """A SqlDocumentRepository bound to the worker loop's engine."""
    from .repository import SqlDocumentRepository

    worker = _get_worker()
    return SqlDocumentRepository(domain, worker.factory)


def reset_worker() -> None:
    """Tests/shutdown: stop the worker and drop its engine."""
    global _worker
    with _lock:
        if _worker is not None:
            try:
                _worker.stop()
            except Exception:
                pass
            _worker = None
