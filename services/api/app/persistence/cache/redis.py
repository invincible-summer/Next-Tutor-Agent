"""Redis-backed CachePrimitives.

One lazy client per process; every operation falls back to the in-process
implementation when Redis errors (connection loss, timeouts). Redis is not a
source of truth — a Redis outage may over-admit rate limits or duplicate
work briefly, it must never fail requests. Failures log at most once per
backoff window to avoid log storms.
"""
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator

from .base import LeaseHandle, RateLimitResult
from .memory import MemoryCachePrimitives

log = logging.getLogger(__name__)

_NAMESPACE = "nt:"

_LOG_BACKOFF_SECONDS = 60.0


class _RedisLease(LeaseHandle):
    def __init__(self, key: str, token: str, owner: "RedisCachePrimitives") -> None:
        super().__init__(key)
        self._token = token
        self._owner = owner

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        # Compare-and-delete via Lua keeps releases from freeing a lease
        # that already expired and was re-acquired by someone else.
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end")
        try:
            client = await self._owner._client()
            await client.eval(script, 1, self.key, self._token)
        except Exception:
            log.debug("lease release failed for %s (ttl will expire)",
                      self.key, exc_info=True)


class RedisCachePrimitives:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client_obj = None
        self._fallback = MemoryCachePrimitives()
        self._last_error_log = 0.0

    async def _client(self):
        if self._client_obj is None:
            import redis.asyncio as aioredis

            self._client_obj = aioredis.from_url(
                self._url, socket_timeout=2.0, socket_connect_timeout=2.0)
        return self._client_obj

    def _note_failure(self, scope: str, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_error_log > _LOG_BACKOFF_SECONDS:
            self._last_error_log = now
            log.warning("redis %s failed (%s): degrading to in-process "
                        "primitives", scope, type(exc).__name__)

    async def rate_limit(self, key: str, limit: int,
                         window_seconds: float) -> RateLimitResult:
        window = max(window_seconds, 0.001)
        now = time.time()
        window_id = int(now // window)
        rkey = f"{_NAMESPACE}rl:{key}:{window_id}"
        ttl = int(window * 2)
        try:
            client = await self._client()
            count = await client.incr(rkey)
            if count == 1:
                await client.expire(rkey, ttl)
            if count > limit:
                retry_after = max(0.0, (window_id + 1) * window - now)
                return RateLimitResult(False, 0, retry_after)
            return RateLimitResult(True, max(0, limit - count), 0.0)
        except Exception as exc:  # redis-py raises redis.RedisError subclasses
            self._note_failure("rate_limit", exc)
            return await self._fallback.rate_limit(key, limit, window_seconds)

    async def get(self, key: str) -> bytes | None:
        try:
            client = await self._client()
            value = await client.get(f"{_NAMESPACE}v:{key}")
            return value if value is None else bytes(value)
        except Exception as exc:
            self._note_failure("get", exc)
            return await self._fallback.get(key)

    async def set(self, key: str, value: bytes, ttl_seconds: float) -> None:
        try:
            client = await self._client()
            await client.set(f"{_NAMESPACE}v:{key}", value,
                             ex=max(1, int(ttl_seconds)))
        except Exception as exc:
            self._note_failure("set", exc)
            await self._fallback.set(key, value, ttl_seconds)

    async def delete(self, key: str) -> None:
        try:
            client = await self._client()
            await client.delete(f"{_NAMESPACE}v:{key}")
        except Exception as exc:
            self._note_failure("delete", exc)
            await self._fallback.delete(key)

    @contextlib.asynccontextmanager
    async def lease(self, key: str,
                    ttl_seconds: float) -> AsyncIterator[LeaseHandle | None]:
        lkey = f"{_NAMESPACE}lease:{key}"
        import secrets

        token = secrets.token_urlsafe(12)
        try:
            client = await self._client()
            won = await client.set(lkey, token, ex=max(1, int(ttl_seconds)),
                                   nx=True)
            if not won:
                yield None
                return
            handle = _RedisLease(lkey, token, self)
        except Exception as exc:
            self._note_failure("lease", exc)
            async with self._fallback.lease(key, ttl_seconds) as fallback_handle:
                yield fallback_handle
            return
        try:
            yield handle
        finally:
            await handle.release()

    async def ping(self) -> bool:
        """Health probe (non-critical readiness reporting)."""
        try:
            client = await self._client()
            return bool(await client.ping())
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._client_obj is not None:
            try:
                await self._client_obj.aclose()
            except Exception:  # pragma: no cover - shutdown best effort
                pass
            self._client_obj = None
