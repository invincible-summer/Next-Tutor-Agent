"""In-process CachePrimitives (default when REDIS_URL is unset).

Single-instance correct: one asyncio lock guards the window counters, values
carry monotonic expiry checked lazily. This is the fallback semantics the
pre-enterprise runtime already had (process-local state), so choosing it is
never an outage — only a loss of cross-instance coordination.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from .base import LeaseHandle, RateLimitResult


class MemoryCachePrimitives:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._values: dict[str, tuple[bytes, float]] = {}
        self._windows: dict[str, tuple[float, int]] = {}  # key -> (window_id, count)
        self._leases: dict[str, float] = {}               # key -> expires_monotonic

    async def rate_limit(self, key: str, limit: int,
                         window_seconds: float) -> RateLimitResult:
        now = time.time()
        window_id = int(now // max(window_seconds, 0.001))
        async with self._lock:
            started_at, count = self._windows.get(key, (window_id, 0))
            if started_at != window_id:
                count = 0
            if count >= limit:
                retry_after = max(
                    0.0, (window_id + 1) * max(window_seconds, 0.001) - now)
                return RateLimitResult(False, 0, retry_after)
            self._windows[key] = (window_id, count + 1)
            return RateLimitResult(True, limit - count - 1, 0.0)

    async def get(self, key: str) -> bytes | None:
        async with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at and expires_at <= time.monotonic():
                self._values.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: bytes, ttl_seconds: float) -> None:
        expires_at = time.monotonic() + max(ttl_seconds, 0.0) if ttl_seconds else 0.0
        async with self._lock:
            self._values[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._values.pop(key, None)
            self._windows.pop(key, None)
            self._leases.pop(key, None)

    @contextlib.asynccontextmanager
    async def lease(self, key: str,
                    ttl_seconds: float) -> AsyncIterator[LeaseHandle | None]:
        outer = self

        class _MemLease(LeaseHandle):
            async def release(self) -> None:
                async with outer._lock:
                    outer._leases.pop(key, None)

        async with self._lock:
            expires = self._leases.get(key)
            if expires is None or expires <= time.monotonic():
                self._leases[key] = time.monotonic() + max(ttl_seconds, 0.001)
                handle: Any = _MemLease(key)
            else:
                handle = None
        try:
            yield handle
        finally:
            if handle is not None:
                await handle.release()
