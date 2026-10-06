"""S3-compatible ObjectStore adapter (boto3, Apache-2.0 — ADR-0018).

Targets any S3-compatible endpoint (AWS S3, self-hosted MinIO/Garage,
etc.) configured through the OBJECT_STORE_* environment. Design notes:

- boto3 is synchronous; every call runs on a worker thread
  (``asyncio.to_thread``) so the event loop never blocks on I/O.
- Integrity is implementation-agnostic: the SHA-256 hex rides as object
  metadata (``sha256``) — every S3-compatible server stores metadata —
  and ``get`` recomputes and refuses to return tampered bytes. Native
  ``ChecksumSHA256`` request params are AWS-specific and skipped on
  purpose for compatibility.
- SSE-S3 (``AES256``) server-side encryption is on by default: keys stay
  provider-managed, no KMS dependency. ``OBJECT_STORE_S3_SSE=off``
  disables it for endpoints without SSE support.
- Retries/timeouts come from botocore's standard retry mode (bounded
  attempts, exponential backoff) plus explicit connect/read timeouts.
- Uploads larger than the multipart threshold go through the transfer
  manager (multipart upload with automatic abort on failure).

The client is injected in tests (moto); the ``build_s3_store`` factory
reads the environment and fails loudly on missing configuration — never
silently falling back to another tier.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
from typing import Any

from .base import ObjectStore, StoredObject

# Above this size uploads switch from PutObject to the multipart transfer
# manager (default boto3 chunk discipline, 8 MiB parts).
_MULTIPART_THRESHOLD = 16 * 1024 * 1024
_SHARD = 1024 * 1024


class ObjectIntegrityError(RuntimeError):
    """Downloaded bytes do not match the stored SHA-256 metadata."""


def _no_such_key(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("404", "NoSuchKey", "NotFound")


class S3ObjectStore(ObjectStore):
    """Asynchronous facade over a boto3 S3 client (thread-offloaded)."""

    def __init__(self, client: Any, bucket: str, *,
                 sse: str = "AES256",
                 multipart_threshold: int = _MULTIPART_THRESHOLD) -> None:
        self._client = client
        self._bucket = bucket
        self._sse = sse or ""
        self._multipart_threshold = multipart_threshold

    @property
    def bucket(self) -> str:
        return self._bucket

    async def put(self, key: str, data: bytes, *,
                  content_type: str = "") -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()

        def _upload() -> None:
            extra: dict[str, Any] = {
                "ContentType": content_type or "application/octet-stream",
                "Metadata": {"sha256": digest},
            }
            if self._sse:
                extra["ServerSideEncryption"] = self._sse
            if len(data) <= self._multipart_threshold:
                self._client.put_object(Bucket=self._bucket, Key=key,
                                        Body=data, **extra)
                return
            # Large payloads: multipart via the transfer manager; metadata
            # and SSE ride the initiate call (upload_fileobj accepts them).
            from boto3.s3.transfer import TransferConfig

            config = TransferConfig(
                multipart_threshold=self._multipart_threshold,
                multipart_chunksize=8 * _SHARD)
            self._client.upload_fileobj(
                io.BytesIO(data), self._bucket, key, ExtraArgs=extra,
                Config=config)

        await asyncio.to_thread(_upload)
        return StoredObject(key=key, size=len(data), content_hash=digest,
                            content_type=content_type)

    async def get(self, key: str) -> bytes | None:
        def _download() -> bytes | None:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=key)
            except Exception as exc:  # botocore ClientError
                if _no_such_key(exc):
                    return None
                raise
            return resp["Body"].read()

        data = await asyncio.to_thread(_download)
        if data is None:
            return None
        head = await self._head(key)
        expected = (head or {}).get("Metadata", {}).get("sha256", "")
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise ObjectIntegrityError(
                f"object_integrity_mismatch:{key}")
        return data

    async def exists(self, key: str) -> bool:
        return await self._head(key) is not None

    async def size(self, key: str) -> int | None:
        head = await self._head(key)
        return int(head["ContentLength"]) if head else None

    async def delete(self, key: str) -> bool:
        def _delete() -> bool:
            # S3's DeleteObject is idempotent; head first to honor the
            # boolean contract of the ObjectStore protocol.
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _no_such_key(exc):
                    return False
                raise
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True

        return await asyncio.to_thread(_delete)

    async def _head(self, key: str) -> dict[str, Any] | None:
        def _head_sync() -> dict[str, Any] | None:
            try:
                return self._client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _no_such_key(exc):
                    return None
                raise

        return await asyncio.to_thread(_head_sync)


def build_s3_store() -> S3ObjectStore:
    """Environment-driven factory: OBJECT_STORE_ENDPOINT/BUCKET plus
    credentials. Missing configuration is a loud startup error — data must
    never silently land in another tier."""
    import boto3
    from botocore.config import Config

    endpoint = (os.getenv("OBJECT_STORE_ENDPOINT") or "").strip()
    bucket = (os.getenv("OBJECT_STORE_BUCKET") or "").strip()
    access_key = (os.getenv("OBJECT_STORE_ACCESS_KEY") or "").strip()
    secret_key = (os.getenv("OBJECT_STORE_SECRET_KEY") or "").strip()
    if not (endpoint and bucket and access_key and secret_key):
        missing = [name for name, value in (
            ("OBJECT_STORE_ENDPOINT", endpoint),
            ("OBJECT_STORE_BUCKET", bucket),
            ("OBJECT_STORE_ACCESS_KEY", access_key),
            ("OBJECT_STORE_SECRET_KEY", secret_key)) if not value]
        raise RuntimeError(
            "OBJECT_STORE_BACKEND=s3 requires "
            + ", ".join(missing)
            + " — refusing to fall back to another storage tier")

    region = (os.getenv("OBJECT_STORE_REGION") or "").strip() or "us-east-1"
    sse = (os.getenv("OBJECT_STORE_S3_SSE") or "AES256").strip()
    if sse.lower() in ("off", "none", "0", "false"):
        sse = ""
    timeout = int(os.getenv("OBJECT_STORE_TIMEOUT_SECONDS", "30") or 30)
    path_style = (os.getenv("OBJECT_STORE_S3_FORCE_PATH_STYLE", "1")
                  not in ("0", "false", "off"))

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            retries={"mode": "standard", "max_attempts": 5},
            connect_timeout=timeout,
            read_timeout=timeout,
            s3={"addressing_style":
                "path" if path_style else "auto"},
        ),
    )
    return S3ObjectStore(client, bucket, sse=sse)
