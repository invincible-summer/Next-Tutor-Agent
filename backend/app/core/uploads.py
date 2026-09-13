"""Bounded upload reading (plan.md §32-§35).

The upload endpoints used to ``raw = await f.read()`` and only then compare
against the size limit — an oversized (potentially hostile) file was fully
materialized in memory before being rejected.  ``read_upload_limited`` reads
in chunks and raises as soon as the cumulative size crosses the limit, so at
most ``limit + chunk_size`` bytes are ever read.

This is deliberately phase 1: the parsers still consume ``bytes``, so a legal
256MB PDF is still fully read — but a legal file was always going to be read;
the fix targets the rejection path.  Streaming file-handle parsers are phase
2 and out of scope here.
"""
from __future__ import annotations

from typing import Any


class UploadTooLarge(ValueError):
    """Raised as soon as an upload crosses its size limit mid-read."""

    def __init__(self, limit: int) -> None:
        self.limit = int(limit)
        super().__init__(f"upload exceeds {self.limit} bytes")


async def read_upload_limited(
    upload: Any,
    max_bytes: int,
    *,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    """Read an UploadFile-like object in chunks, capped at ``max_bytes``.

    Raises UploadTooLarge once the cumulative size exceeds the limit (after
    at most one extra chunk was fetched). Chunked reads keep the rejection
    path O(limit + chunk_size) instead of O(file size).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)
