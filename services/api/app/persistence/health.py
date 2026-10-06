"""Persistence health reporting for the bootstrap /ready surface.

File mode reports a single ok check (nothing to connect to). Enterprise
mode probes the database (critical) and the cache server (non-critical, coordination
tier only). Object store defaults to the local data root — writability is
implied by the data root bootstrap, so no extra probe is invented for it.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def collect_persistence_health() -> dict[str, object]:
    """Synchronous snapshot for /ready consumers (no network calls)."""
    from . import db
    from .cache import cache_configured

    mode = "enterprise" if db.enterprise_mode() else "file"
    return {
        "mode": mode,
        "database": "configured" if db.enterprise_mode() else "not_configured",
        "cache": "configured" if cache_configured() else "not_configured",
    }


async def probe_persistence() -> dict[str, dict[str, object]]:
    """Async connectivity probes; used by the lifespan bootstrap step.

    Returns a per-dependency result map suitable for check details. Raises
    on database failure in enterprise mode (the caller marks the step
    critical) — a configured-but-unreachable database must fail startup.
    """
    from . import db
    from .cache import cache_configured

    results: dict[str, dict[str, object]] = {}
    if not db.enterprise_mode():
        results["mode"] = {"status": "file"}
        return results

    ok = await db.ping()
    results["database"] = {"status": "ok" if ok else "unreachable"}
    if not ok:
        raise RuntimeError("DATABASE_URL is configured but the database is "
                           "unreachable — refusing to start in enterprise "
                           "mode with a broken persistence tier.")
    if cache_configured():
        from .cache import cache_url
        from .cache.resp import RespCachePrimitives

        probe = RespCachePrimitives(cache_url() or "")
        try:
            results["cache"] = {
                "status": "ok" if await probe.ping() else "unreachable"}
        finally:
            await probe.aclose()
    return results
