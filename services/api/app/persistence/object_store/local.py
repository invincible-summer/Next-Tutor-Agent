"""Local filesystem object store (default backend, inside the data root).

Layout mirrors the flat opaque-key contract: ``<root>/<namespace>/<id>`` via
the sanitized key string. Writes are atomic (temp + rename) with the same
discipline as core/atomic.py; permissions tighten to 0600 best-effort since
uploads may contain private teaching material.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .base import StoredObject


def _resolve(root: Path, key: str) -> Path:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"invalid_object_key:{key!r}")
    target = (root / key).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"invalid_object_key:{key!r}")
    return target


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    async def put(self, key: str, data: bytes, *,
                  content_type: str = "") -> StoredObject:
        import asyncio

        def _write() -> StoredObject:
            target = _resolve(self._root, key)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            digest = hashlib.sha256(data).hexdigest()
            return StoredObject(key=key, size=len(data), content_hash=digest,
                                content_type=content_type)

        return await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes | None:
        import asyncio

        def _read() -> bytes | None:
            target = _resolve(self._root, key)
            try:
                return target.read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_read)

    async def exists(self, key: str) -> bool:
        import asyncio

        return await asyncio.to_thread(
            lambda: _resolve(self._root, key).exists())

    async def size(self, key: str) -> int | None:
        import asyncio

        def _stat() -> int | None:
            target = _resolve(self._root, key)
            try:
                return target.stat().st_size
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_stat)

    async def delete(self, key: str) -> bool:
        import asyncio

        def _delete() -> bool:
            target = _resolve(self._root, key)
            try:
                target.unlink()
                return True
            except FileNotFoundError:
                return False

        return await asyncio.to_thread(_delete)
