"""Async engine / session / transaction factory for the enterprise lane.

One engine per ``DATABASE_URL`` per process (lazy, cached). File mode
(``DATABASE_URL`` unset) never constructs an engine; :func:`enterprise_mode`
gates every caller. Tests retarget the engine by patching ``DATABASE_URL`` and
calling :func:`reset_engine`, or by passing an explicit ``url``.

The URL must use an async driver (``postgresql+asyncpg://…`` or
``sqlite+aiosqlite://…`` for the unit-test lane).
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import AsyncIterator

log = logging.getLogger(__name__)

_DATABASE_URL_ENV = "DATABASE_URL"

# Engine cache keyed by URL string; a single process talks to one database.
_engine_cache: dict[str, "object"] = {}
_session_factory_cache: dict[str, "object"] = {}


def database_url() -> str | None:
    """The configured enterprise database URL, or None (file mode)."""
    raw = os.getenv(_DATABASE_URL_ENV, "").strip()
    return raw or None


def enterprise_mode() -> bool:
    """True when the process runs against PostgreSQL-backed persistence."""
    return database_url() is not None


def _normalize(url: str) -> str:
    # Alembic offline tooling and compose files sometimes hand over the sync
    # driver; the async engine requires the async one.
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def create_engine(url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    normalized = _normalize(url)
    connect_args: dict[str, object] = {}
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "connect_args": connect_args,
        "json_serializer": _json_dumps,
    }
    if normalized.startswith("sqlite"):
        # Test lane: TestClient spawns a fresh event loop per request; a
        # pooled aiosqlite connection would hop loops. NullPool keeps every
        # connection inside the request that opened it.
        kwargs["poolclass"] = NullPool
        kwargs.pop("pool_pre_ping")
    engine = create_async_engine(normalized, **kwargs)
    return engine


def _json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def get_engine(url: str | None = None):
    """The cached async engine for ``url`` (default: DATABASE_URL)."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    resolved = _normalize(url or database_url() or "")
    if not resolved:
        raise RuntimeError(
            "persistence engine requested in file mode: DATABASE_URL is not "
            "configured — callers must gate on enterprise_mode() first.")
    cached = _engine_cache.get(resolved)
    if cached is None:
        cached = create_engine(resolved)
        _engine_cache[resolved] = cached
        _session_factory_cache.pop(resolved, None)
    assert isinstance(cached, AsyncEngine)
    return cached


def session_factory(url: str | None = None):
    """The cached async_sessionmaker bound to the engine for ``url``."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = get_engine(url)
    key = str(engine.url)
    factory = _session_factory_cache.get(key)
    if factory is None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        _session_factory_cache[key] = factory
    return factory


@contextlib.asynccontextmanager
async def session(url: str | None = None) -> AsyncIterator[object]:
    """One session; caller commits (or the block's exception rolls back)."""
    async with session_factory(url)() as sess:
        yield sess


@contextlib.asynccontextmanager
async def transaction(url: str | None = None) -> AsyncIterator[object]:
    """One transactional scope: commit on clean exit, rollback on error."""
    async with session_factory(url)() as sess:
        async with sess.begin():
            yield sess


async def create_all(url: str | None = None) -> None:
    """Create every mapped table (unit tests / fresh dev databases only).

    Production schema management goes through Alembic migrations; startup
    never runs this (no implicit DDL against a live database).
    """
    from .models import Base

    engine = get_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def reset_engine() -> None:
    """Dispose cached engines (tests switching DATABASE_URL, shutdown)."""
    import warnings

    for engine in _engine_cache.values():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                engine.sync_engine.dispose()
            except Exception:  # pragma: no cover - shutdown best effort
                pass
    _engine_cache.clear()
    _session_factory_cache.clear()


async def dispose_engine_async() -> None:
    """Async dispose for graceful shutdown paths (lifespan finally-block)."""
    for engine in list(_engine_cache.values()):
        try:
            await engine.dispose()
        except Exception:  # pragma: no cover - shutdown best effort
            log.warning("engine dispose failed during shutdown", exc_info=True)
    _engine_cache.clear()
    _session_factory_cache.clear()


async def ping(url: str | None = None) -> bool:
    """Cheap connectivity probe (SELECT 1) for health reporting."""
    from sqlalchemy import text

    try:
        engine = get_engine(url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("database ping failed: %s", type(exc).__name__)
        return False
