"""Shared runtime for the Temporal worker lane: client factory, task queues,
workflow-id mapping.

Task queues follow ADR-0013 (one pool per resource profile, all served by
the same code base): ``documents`` (textbook ingest/OCR/graph build),
``classroom`` (lesson generation), ``evaluation`` (learner evaluation),
``media`` (illustration compose/render) and ``maintenance`` (retention,
briefing ticks, purge).

Workflow ids must be **stably derivable from domain job ids** so an API
process can always find / re-start the run that owns a job
(:func:`join_workflow_id` keeps them readable and Temporal-safe).
"""
from __future__ import annotations

import re
import asyncio

from app.workflows.config import temporal_address, temporal_namespace

TASK_QUEUE_DOCUMENTS = "documents"
TASK_QUEUE_CLASSROOM = "classroom"
TASK_QUEUE_EVALUATION = "evaluation"
TASK_QUEUE_MEDIA = "media"
TASK_QUEUE_MAINTENANCE = "maintenance"

ALL_TASK_QUEUES: tuple[str, ...] = (
    TASK_QUEUE_DOCUMENTS,
    TASK_QUEUE_CLASSROOM,
    TASK_QUEUE_EVALUATION,
    TASK_QUEUE_MEDIA,
    TASK_QUEUE_MAINTENANCE,
)

# One client per process (lazy, cached). The Temporal client is safe for
# concurrent use; tests retarget it via reset_client_cache().
_client_cache: dict[tuple[str, str], object] = {}

_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._:-]+")
_MAX_ID_PART = 160
_MAX_WORKFLOW_ID = 900


def join_workflow_id(*parts: str) -> str:
    """Build a stable, readable workflow id from domain identifiers.

    Each part is sanitized (control/whitespace/unsafe characters collapse
    to ``_``) and capped; the caller owns semantic uniqueness — ids are
    derived from persisted job ids, never minted here (workflow code and
    id derivation must stay deterministic and side-effect free).
    """
    cleaned: list[str] = []
    for part in parts:
        text = str(part).strip()
        text = _UNSAFE_ID_CHARS.sub("_", text)[:_MAX_ID_PART]
        cleaned.append(text or "_")
    workflow_id = ":".join(cleaned)
    if len(workflow_id) > _MAX_WORKFLOW_ID:
        workflow_id = workflow_id[:_MAX_WORKFLOW_ID]
    return workflow_id


def get_client():
    """Lazily connect the cached Temporal client (requires TEMPORAL_ADDRESS)."""
    from temporalio.client import Client

    address = temporal_address()
    if not address:
        raise RuntimeError(
            "TEMPORAL_ADDRESS is not configured — the durable workflow lane "
            "is disabled; run the in-process job execution instead")
    key = (address, temporal_namespace())
    cached = _client_cache.get(key)
    if cached is None:
        # ``Client.connect`` is a coroutine.  Cache a task rather than the
        # coroutine itself so concurrent callers and later workflow dispatches
        # can await the same connection repeatedly.
        cached = asyncio.create_task(
            Client.connect(address, namespace=key[1]))
        _client_cache[key] = cached
    return cached


def reset_client_cache() -> None:
    """Drop cached clients (test isolation; also registered in
    tests/support/storage_sandbox.py::reset_shared_caches)."""
    _client_cache.clear()
