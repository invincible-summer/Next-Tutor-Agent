"""Remote object-store adapters (azure blob / s3-compatible) — interface slots.

Wiring these backends requires per-deployment credentials and retry policy
decisions that have no owner yet; requesting them fails loudly instead of
silently falling back to the local backend (data must never land in the
wrong tier).
"""
from __future__ import annotations

from .base import ObjectStore


class RemoteStoreUnavailableError(RuntimeError):
    pass


def build_remote_store(backend: str) -> ObjectStore:
    raise RemoteStoreUnavailableError(
        f"OBJECT_STORE_BACKEND={backend} is a reserved interface slot: the "
        "azure/s3 adapters are not implemented yet. Use the default local "
        "backend (inside the runtime data root) until they are.")
