"""Distributed cache/rate-limit/single-flight primitives.

Redis is never a source of truth (§12.3 semantics): it holds only short-lived
operational state — distributed rate limits, short capability caches,
single-flight/lease keys, ephemeral idempotency acceleration, short locks.
REDIS_URL unset (or the client failing) degrades to the in-process
implementation, which preserves single-instance correctness and simply loses
cross-instance coordination.
"""
from __future__ import annotations

from .base import (CachePrimitives, LeaseHandle, RateLimitResult,
                   get_cache_primitives, redis_configured)
from .memory import MemoryCachePrimitives

__all__ = ["CachePrimitives", "LeaseHandle", "RateLimitResult",
           "MemoryCachePrimitives", "get_cache_primitives",
           "redis_configured"]
