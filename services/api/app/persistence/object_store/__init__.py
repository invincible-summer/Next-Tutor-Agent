"""Object storage: interface + local filesystem backend (default).

Enterprise deployments keep large/immutable blobs (original uploads, OCR
artifacts, serialized BM25 indexes, illustration SVG/PNG renders, classroom
exports, TTS audio cache, avatars) out of PostgreSQL; the database stores
only metadata: object key, content hash, size, owner/retention. The blob
store itself never makes authorization decisions.
"""
from __future__ import annotations

from .base import ObjectStore, StoredObject, build_object_key, default_store
from .local import LocalObjectStore

__all__ = ["ObjectStore", "StoredObject", "build_object_key",
           "LocalObjectStore", "default_store"]
