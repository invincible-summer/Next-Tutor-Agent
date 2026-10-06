"""CachePrimitives protocol + shared result types + factory.

The protocol is deliberately small: a fixed-window rate limiter, short TTL
get/set/delete, and a try-acquire lease (single-flight / short-lock assist).
Callers compose richer patterns (result sharing via get/set after winning a
lease, idempotency acceleration) from these atoms — the abstraction stays
honest instead of pretending full singleflight semantics it cannot guarantee
across backends.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

_CACHE_URL_ENV = "CACHE_URL"
_REDIS_URL_ENV = "REDIS_URL"  # deprecated alias, one-version compat (ADR-0016)

_log = logging.getLogger(__name__)
_alias_warned = False


def cache_url() -> str | None:
    """Preferred CACHE_URL; falls back to the deprecated REDIS_URL alias."""
    global _alias_warned
    raw = os.getenv(_CACHE_URL_ENV, "").strip()
    if raw:
        return raw
    raw = os.getenv(_REDIS_URL_ENV, "").strip()
    if raw and not _alias_warned:
        _alias_warned = True
        _log.warning("REDIS_URL is deprecated; rename to CACHE_URL "
                     "(the cache server is Valkey since ADR-0016)")
    return raw or None


def cache_configured() -> bool:
    return cache_url() is not None


# Compat shims for the pre-ADR-0016 names (internal callers/tests).
def redis_url() -> str | None:
    return cache_url()


def redis_configured() -> bool:
    return cache_configured()


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class LeaseHandle:
    """A held lease. ``release`` is best-effort; TTL is the safety net."""

    def __init__(self, key: str) -> None:
        self.key = key
        self._released = False

    async def release(self) -> None:
        raise NotImplementedError


@runtime_checkable
class CachePrimitives(Protocol):
    """Short-lived operational state only — never business truth."""

    async def rate_limit(self, key: str, limit: int,
                         window_seconds: float) -> RateLimitResult:
        """Fixed-window counter. ``retry_after_seconds`` is 0 when allowed."""
        ...

    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl_seconds: float) -> None: ...
    async def delete(self, key: str) -> None: ...

    def lease(self, key: str, ttl_seconds: float) -> AsyncIterator[LeaseHandle | None]:
        """Async context manager: yields a held lease or None when taken.

        Winners must do the work inside the block and publish the result via
        ``set``; losers poll ``get`` — that composes single-flight without
        pretending cross-backend guarantees the atoms can't provide.
        """
        ...


def get_cache_primitives() -> CachePrimitives:
    """CACHE_URL set -> RESP server backend (Valkey; memory fallback on failure).

    Server errors never propagate to callers: the primitives fall back to the
    in-process implementation and log once, because losing the shared tier
    must degrade coordination, not availability.
    """
    if not cache_configured():
        from .memory import MemoryCachePrimitives

        return MemoryCachePrimitives()
    from .resp import RespCachePrimitives

    return RespCachePrimitives(cache_url() or "")
