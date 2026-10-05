"""Temporal durable workflow layer (ADR-0013).

File mode (``TEMPORAL_ADDRESS`` unset) never imports beyond
:mod:`config`/:mod:`runtime` — domain job execution stays in-process and
this package is inert.
"""
from app.workflows.config import (
    DEFAULT_NAMESPACE,
    temporal_address,
    temporal_configured,
    temporal_namespace,
)
from app.workflows.runtime import (
    ALL_TASK_QUEUES,
    TASK_QUEUE_CLASSROOM,
    TASK_QUEUE_DOCUMENTS,
    TASK_QUEUE_EVALUATION,
    TASK_QUEUE_MAINTENANCE,
    TASK_QUEUE_MEDIA,
    get_client,
    join_workflow_id,
    reset_client_cache,
)

__all__ = [
    "ALL_TASK_QUEUES",
    "DEFAULT_NAMESPACE",
    "TASK_QUEUE_CLASSROOM",
    "TASK_QUEUE_DOCUMENTS",
    "TASK_QUEUE_EVALUATION",
    "TASK_QUEUE_MAINTENANCE",
    "TASK_QUEUE_MEDIA",
    "get_client",
    "join_workflow_id",
    "reset_client_cache",
    "temporal_address",
    "temporal_configured",
    "temporal_namespace",
]
