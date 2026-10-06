"""Distributed cache/rate-limit/single-flight primitives.

The RESP server (Valkey, ADR-0016) is never a source of truth: it holds only
short-lived operational state — distributed rate limits, short capability
caches, single-flight/lease keys, ephemeral idempotency acceleration, short
locks. CACHE_URL unset (or the client failing) degrades to the in-process
implementation, which preserves single-instance correctness and simply loses
cross-instance coordination.
"""
from __future__ import annotations

from .base import (CachePrimitives, LeaseHandle, RateLimitResult,
                   cache_configured, cache_url, get_cache_primitives,
                   redis_configured, redis_url)
from .memory import MemoryCachePrimitives
from .resp import RespCachePrimitives

__all__ = ["CachePrimitives", "LeaseHandle", "RateLimitResult",
           "MemoryCachePrimitives", "RespCachePrimitives",
           "cache_configured", "cache_url", "get_cache_primitives",
           "redis_configured", "redis_url"]
