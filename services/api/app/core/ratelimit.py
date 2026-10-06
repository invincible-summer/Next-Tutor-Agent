"""Lightweight in-memory rate limiting (no external dependencies).

Fixed-window, per (rule name, client IP) counters. The client IP is the
ASGI-resolved peer: with `uvicorn --proxy-headers` (default
forwarded-allow-ips=127.0.0.1) the peer is rewritten to the rightmost
*untrusted* X-Forwarded-For hop, but only when the direct connection comes
from a trusted proxy. Never parse X-Forwarded-For here — an untrusted client
could spoof the header and mint a fresh bucket per request, defeating every
rate rule.

Usage as a FastAPI dependency:

    from app.core.ratelimit import rate_limit

    @router.post("/login", dependencies=[Depends(rate_limit("auth_login", 10))])
    def login(...): ...

For keys derived inside the handler (e.g. per-account login throttling) use
the imperative :func:`check_rate`.

State is process-local: multi-worker deployments need one bucket per worker
(acceptable for the current single-uvicorn deployment; swap for the RESP
# cache (Valkey, ADR-0016) if
workers scale out).
"""
from __future__ import annotations

import os
import threading
import time

from fastapi import HTTPException, Request

_LOCK = threading.Lock()
# (rule, ip) -> (window_start_monotonic, request_count)
_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}


def _disabled() -> bool:
    """RATE_LIMIT_DISABLE=1 turns every rule into a pass-through.

    E2E/CI environments fire many registrations from one IP intentionally;
    production never sets this variable."""
    return os.getenv("RATE_LIMIT_DISABLE", "") in ("1", "true", "True")


def client_ip(request: Request) -> str:
    """Best-effort client IP: the connection peer as resolved by uvicorn.

    Deliberately ignores the raw X-Forwarded-For header — see module docstring.
    """
    return request.client.host if request.client else "unknown"


def check_rate(name: str, key: str, max_requests: int,
               window_seconds: int = 60) -> None:
    """Imperative variant of rate_limit: allow max_requests per window for an
    arbitrary bucket key (e.g. per-account login throttling), else raise 429."""
    if _disabled():
        return
    with _LOCK:
        now = time.monotonic()
        window_start, count = _BUCKETS.get((name, key), (now, 0))
        if now - window_start >= window_seconds:
            window_start, count = now, 0
        count += 1
        _BUCKETS[(name, key)] = (window_start, count)
        if count > max_requests:
            raise HTTPException(status_code=429,
                                detail="请求过于频繁，请稍后再试")


def rate_limited(name: str, key: str, max_requests: int,
                 window_seconds: int = 60) -> bool:
    """只读查询某桶在当前窗口是否已用尽额度（不计数）。

    供"先检查、再付出昂贵代价"的场景早退：例如登录前的账号锁定检查
    必须跳过 bcrypt（12 rounds 的校验本身可被爆破流量当作 CPU 消耗面）。
    """
    if _disabled():
        return False
    with _LOCK:
        now = time.monotonic()
        window_start, count = _BUCKETS.get((name, key), (now, 0))
        if now - window_start >= window_seconds:
            return False
        return count >= max_requests


def reset_rate(name: str, key: str) -> None:
    """Clear one bucket. 成功登录后清空该账号的失败计数，正常用户输错
    几次后登录成功不应留下"半锁定"状态。"""
    with _LOCK:
        _BUCKETS.pop((name, key), None)


def rate_limit(name: str, max_requests: int, window_seconds: int = 60):
    """Dependency factory: allow max_requests per window per IP, else 429."""

    def _check(request: Request) -> None:
        check_rate(name, client_ip(request), max_requests, window_seconds)

    return _check


def reset_rate_limits() -> None:
    """Clear all buckets. For tests; not wired to any endpoint."""
    with _LOCK:
        _BUCKETS.clear()
