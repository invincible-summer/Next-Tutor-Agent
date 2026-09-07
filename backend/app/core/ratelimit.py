"""Lightweight in-memory rate limiting (no external dependencies).

Fixed-window, per (rule name, client IP) counters. Client IP prefers the
first X-Forwarded-For hop (for reverse-proxy deployments with uvicorn
--proxy-headers); falls back to the direct peer address.

Usage as a FastAPI dependency:

    from app.core.ratelimit import rate_limit

    @router.post("/login", dependencies=[Depends(rate_limit("auth_login", 10))])
    def login(...): ...

State is process-local: multi-worker deployments need one bucket per worker
(acceptable for the current single-uvicorn deployment; swap for Redis if
workers scale out).
"""
from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

_LOCK = threading.Lock()
# (rule, ip) -> (window_start_monotonic, request_count)
_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}


def client_ip(request: Request) -> str:
    """Best-effort client IP: first X-Forwarded-For entry, else the peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, max_requests: int, window_seconds: int = 60):
    """Dependency factory: allow max_requests per window per IP, else 429."""

    def _check(request: Request) -> None:
        key = (name, client_ip(request))
        now = time.monotonic()
        with _LOCK:
            window_start, count = _BUCKETS.get(key, (now, 0))
            if now - window_start >= window_seconds:
                window_start, count = now, 0
            count += 1
            _BUCKETS[key] = (window_start, count)
            if count > max_requests:
                raise HTTPException(status_code=429,
                                    detail="请求过于频繁，请稍后再试")

    return _check


def reset_rate_limits() -> None:
    """Clear all buckets. For tests; not wired to any endpoint."""
    with _LOCK:
        _BUCKETS.clear()
