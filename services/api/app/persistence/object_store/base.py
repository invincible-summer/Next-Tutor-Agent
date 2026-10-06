"""ObjectStore protocol and key discipline.

Object keys are opaque identifiers (namespace + random id); user emails and
original file names never become key segments. A display filename may ride
alongside as metadata in PostgreSQL, not in the key.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Namespace vocabulary: one bucket-namespace per artifact family so retention
# and quota policies can be applied per family.
NAMESPACES = frozenset({
    "uploads",          # original user uploads
    "ocr",              # extracted/OCR artifacts
    "bm25",             # serialized BM25 index/chunk artifacts
    "illustrations",    # quiz & scenario illustration artifacts
    "classroom",        # compiled courseware assets / exports
    "tts",              # TTS audio cache
    "avatars",          # user avatars
})

_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9._-]+")
_MAX_SEGMENT = 120


def build_object_key(namespace: str, *parts: str, opaque_id: str | None = None,
                     suffix: str = "") -> str:
    """Build a sanitized, traversal-free object key.

    ``opaque_id`` defaults to a fresh urlsafe token — callers that need
    deterministic keys (migration imports) pass their own. ``suffix`` is a
    sanitized file extension (e.g. ``.svg``); ``parts`` are sanitized path
    segments (opaque ids, hashes — never emails or raw filenames).
    """
    if namespace not in NAMESPACES:
        raise ValueError(f"unknown_object_namespace:{namespace}")
    segments = [namespace]
    for part in parts:
        cleaned = _SAFE_SEGMENT.sub("-", part).strip("-")[:_MAX_SEGMENT]
        if not cleaned:
            raise ValueError("empty_object_key_segment")
        segments.append(cleaned)
    tail = opaque_id or secrets.token_urlsafe(16)
    tail = _SAFE_SEGMENT.sub("-", tail)[:_MAX_SEGMENT]
    if suffix:
        suffix = _SAFE_SEGMENT.sub("-", suffix)
        tail = f"{tail}{suffix}"
    segments.append(tail)
    return "/".join(segments)


@dataclass(slots=True)
class StoredObject:
    key: str
    size: int
    content_hash: str = ""   # sha256 hex when the backend computes one
    content_type: str = ""


@runtime_checkable
class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, *,
                  content_type: str = "") -> StoredObject:
        """Store ``data`` under ``key`` (overwrite is allowed for the same
        key only when the caller consciously re-uploads an immutable
        artifact with identical content)."""
        ...

    async def get(self, key: str) -> bytes | None: ...
    async def exists(self, key: str) -> bool: ...
    async def size(self, key: str) -> int | None: ...
    async def delete(self, key: str) -> bool: ...


def default_store() -> ObjectStore:
    """The configured store: OBJECT_STORE_BACKEND selects the backend.

    Only ``local`` (default: data-root ``object_store/`` directory) exists
    today; azure/s3 adapters are explicit interface slots until an
    enterprise deployment needs them.
    """
    import os

    from app.core import paths

    backend = (os.getenv("OBJECT_STORE_BACKEND", "local").strip().lower()
               or "local")
    if backend == "local":
        # Lazy import: local.py imports this module's names, so the edge
        # stays one-directional at import time.
        from .local import LocalObjectStore

        # runtime_paths() re-resolves under set_runtime_root/NEXT_TUTOR_DATA_DIR
        # (no repeated bind_storage_path registration on every call).
        root = paths.runtime_paths().object_store
        return LocalObjectStore(root)
    from .remote import build_remote_store

    return build_remote_store(backend)
