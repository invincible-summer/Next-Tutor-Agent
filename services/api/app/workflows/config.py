"""Temporal lane configuration (ADR-0013).

Same degradation contract as the other enterprise lanes
(``DATABASE_URL`` / ``CACHE_URL`` / OTel):

- ``TEMPORAL_ADDRESS`` unset → file/self-hosted mode. Every domain keeps
  its existing in-process job execution and this package is inert.
- ``TEMPORAL_ADDRESS`` set → the API process stops owning background
  jobs; the dedicated worker process (``services/api/worker.py``)
  consumes the task queues defined in :mod:`app.workflows.runtime`.

Enterprise config stays at the point of use (plain ``os.getenv``), it is
not part of ``app/core/config.py``.
"""
from __future__ import annotations

import os

_ADDRESS_ENV = "TEMPORAL_ADDRESS"
_NAMESPACE_ENV = "TEMPORAL_NAMESPACE"

DEFAULT_NAMESPACE = "default"

# Process role: the API process dispatches workflows; the worker process
# (services/api/worker.py) executes them. Domain seams branch on this so an
# activity running inside the worker keeps using the domain's own in-process
# machinery (queues/locks) instead of dispatching another workflow.
_worker_process = False


def mark_worker_process() -> None:
    """Flag the current process as the durable worker (worker.py boot)."""
    global _worker_process
    _worker_process = True


def is_worker_process() -> bool:
    return _worker_process


def temporal_address() -> str | None:
    """The configured Temporal frontend ``host:port``, or None (file mode)."""
    raw = os.getenv(_ADDRESS_ENV, "").strip()
    return raw or None


def temporal_namespace() -> str:
    raw = os.getenv(_NAMESPACE_ENV, "").strip()
    return raw or DEFAULT_NAMESPACE


def temporal_configured() -> bool:
    """True when durable workflow execution is enabled for this process."""
    return temporal_address() is not None
