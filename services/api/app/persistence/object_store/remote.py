"""Remote object-store adapters: s3-compatible (implemented) / azure blob
(interface slot).

Requesting an unimplemented backend fails loudly instead of silently
falling back to the local backend (data must never land in the wrong
tier).
"""
from __future__ import annotations

from .base import ObjectStore


class RemoteStoreUnavailableError(RuntimeError):
    pass


def build_remote_store(backend: str) -> ObjectStore:
    if backend == "s3":
        from .s3 import build_s3_store

        return build_s3_store()
    raise RemoteStoreUnavailableError(
        f"OBJECT_STORE_BACKEND={backend} is a reserved interface slot: the "
        "azure adapter is not implemented yet. Use the default local "
        "backend (inside the runtime data root) or the s3-compatible "
        "adapter (OBJECT_STORE_BACKEND=s3).")
