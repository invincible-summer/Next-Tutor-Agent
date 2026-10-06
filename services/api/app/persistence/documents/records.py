"""Plain-data record exchanged between domain services and document stores.

A document is one JSON resource owned by one principal inside one tenant:
chat session, note thread, classroom lesson, illustration job, … The domain
owns the payload schema (and its versioning); this layer owns identity,
isolation keys, timestamps and the optimistic-concurrency epoch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DocumentRecord:
    doc_id: str
    owner_id: str
    kind: str = "doc"
    # Empty string = legacy pre-tenant scope (see ADR-0017 / WS5c wiring).
    tenant_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    epoch: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
