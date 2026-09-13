"""Bootstrap observability: structured startup report + readiness.

plan.md §15-§19: the process being alive is not the same as its data
maintenance having succeeded.  ``_lifespan`` used to swallow most startup
failures with ``except Exception: pass`` and ``/health`` was always ok, so a
broken textbook recovery or admin bootstrap was invisible.

This module gives every startup step a named check with a status
(pending/ok/degraded/failed), logs failures with ``log.exception`` (never
silent), and feeds ``GET /api/v1/ready``:

  ready        every step ok (non-critical degraded still serves traffic)
  degraded     ≥1 non-critical step failed or degraded — HTTP 200
  not_ready    a critical step failed — HTTP 503

The report is a process-local singleton (runtime health, not business state).
Steps must never log secrets (JWT / passwords / tickets / textbook text /
conversation content).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"


@dataclass
class BootstrapCheck:
    name: str
    critical: bool
    status: str = STATUS_PENDING   # pending | ok | degraded | failed
    detail: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class BootstrapReport:
    started_at: float = field(default_factory=time.time)
    checks: dict[str, BootstrapCheck] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """False until at least one check ran, all finished, and no critical
        one failed. A report with zero checks is NOT ready — bootstrap
        hasn't run; a still-pending step is NOT ready either."""
        if not self.checks:
            return False
        if any(c.status == STATUS_PENDING for c in self.checks.values()):
            return False
        return not any(c.status == STATUS_FAILED and c.critical
                       for c in self.checks.values())

    @property
    def degraded(self) -> bool:
        """Any non-critical failure/degradation (service still serves)."""
        return any(c.status in (STATUS_FAILED, STATUS_DEGRADED)
                   for c in self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "checks": {
                name: {"status": c.status, "critical": c.critical,
                       "detail": c.detail}
                for name, c in self.checks.items()
            },
        }

    def status_label(self) -> str:
        if not self.ready:
            return "not_ready"
        return "degraded" if self.degraded else "ready"


async def run_bootstrap_step(
    report: BootstrapReport,
    name: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    critical: bool = False,
    to_thread: bool = False,
) -> Any:
    """Run one bootstrap step under observation.

    - success -> check ok, return value passed through;
    - failure -> ``log.exception`` with the step name (never silent), check
      failed with the exception text as detail;
    - non-critical failures are swallowed (degradable features must not take
      the whole service down — plan.md §17);
    - critical failures re-raise after being recorded so the app fails fast.
    """
    check = BootstrapCheck(name=name, critical=critical,
                           started_at=time.time())
    report.checks[name] = check
    try:
        if to_thread:
            import asyncio
            result = await asyncio.to_thread(fn)
        else:
            result = await fn()
        check.status = STATUS_OK
        check.finished_at = time.time()
        return result
    except Exception as exc:
        check.status = STATUS_FAILED
        check.detail = f"{type(exc).__name__}: {exc}"[:300]
        check.finished_at = time.time()
        log.exception("bootstrap step failed",
                      extra={"bootstrap_step": name})
        if critical:
            raise
        return None


# Process-local singleton: runtime health only, never persisted.
_REPORT: BootstrapReport | None = None


def get_bootstrap_report() -> BootstrapReport:
    """The process's bootstrap report (created empty pre-startup)."""
    global _REPORT
    if _REPORT is None:
        _REPORT = BootstrapReport()
    return _REPORT


def reset_bootstrap_report() -> BootstrapReport:
    """Fresh report (tests / explicit re-run)."""
    global _REPORT
    _REPORT = BootstrapReport()
    return _REPORT
