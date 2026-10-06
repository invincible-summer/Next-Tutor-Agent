"""S3-compatible ObjectStore adapter against an in-process moto server.

moto (Apache-2.0) runs a real S3 API surface in-process — the tests
exercise the boto3 code path end to end without network access or a
production compose service.
"""
from __future__ import annotations

import hashlib
import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


def _moto_bucket(bucket: str = "next-tutor"):
    import boto3

    # No custom endpoint: moto 5 intercepts the default AWS endpoints only.
    client = boto3.client(
        "s3", region_name="us-east-1",
        aws_access_key_id="testing", aws_secret_access_key="testing")
    client.create_bucket(Bucket=bucket)
    return client


class S3ObjectStoreTest(StorageSandboxTestCase,
                        unittest.IsolatedAsyncioTestCase):

    def _store(self, client, **kwargs):
        from app.persistence.object_store import S3ObjectStore

        return S3ObjectStore(client, "next-tutor", **kwargs)

    async def test_roundtrip_with_sse_content_type_and_checksum(self) -> None:
        from moto import mock_aws

        with mock_aws():
            client = _moto_bucket()
            store = self._store(client)
            payload = b"lesson-plan-bytes" * 3
            obj = await store.put("uploads/unit-1.bin", payload,
                                  content_type="image/png")
            self.assertEqual(obj.size, len(payload))
            self.assertEqual(obj.content_hash,
                             hashlib.sha256(payload).hexdigest())
            self.assertTrue(await store.exists("uploads/unit-1.bin"))
            self.assertEqual(await store.size("uploads/unit-1.bin"),
                             len(payload))
            self.assertEqual(await store.get("uploads/unit-1.bin"), payload)
            head = client.head_object(Bucket="next-tutor",
                                      Key="uploads/unit-1.bin")
            self.assertEqual(head["ServerSideEncryption"], "AES256")
            self.assertEqual(head["ContentType"], "image/png")
            self.assertEqual(head["Metadata"]["sha256"], obj.content_hash)
            self.assertTrue(await store.delete("uploads/unit-1.bin"))
            self.assertFalse(await store.delete("uploads/unit-1.bin"))
            self.assertIsNone(await store.get("uploads/unit-1.bin"))
            self.assertFalse(await store.exists("uploads/unit-1.bin"))
            self.assertIsNone(await store.size("uploads/unit-1.bin"))

    async def test_tampered_download_refused(self) -> None:
        from moto import mock_aws

        from app.persistence.object_store import ObjectIntegrityError

        with mock_aws():
            client = _moto_bucket()
            store = self._store(client)
            payload = b"integrity-check"
            obj = await store.put("uploads/tamper.bin", payload)
            # Out-of-band overwrite keeping the ORIGINAL sha256 metadata:
            # the adapter must refuse to hand back the tampered bytes.
            client.put_object(
                Bucket="next-tutor", Key="uploads/tamper.bin",
                Body=b"tampered!!",
                Metadata={"sha256": obj.content_hash})
            with self.assertRaises(ObjectIntegrityError):
                await store.get("uploads/tamper.bin")

    async def test_large_payload_uses_multipart_lane(self) -> None:
        from moto import mock_aws

        with mock_aws():
            client = _moto_bucket()
            # Threshold injection keeps the payload small while forcing the
            # transfer-manager (multipart) code path.
            store = self._store(client, multipart_threshold=64)
            payload = os.urandom(256 * 1024)
            obj = await store.put("classroom/export.zip", payload,
                                  content_type="application/zip")
            self.assertEqual(obj.content_hash,
                             hashlib.sha256(payload).hexdigest())
            self.assertEqual(await store.get("classroom/export.zip"),
                             payload)
            head = client.head_object(Bucket="next-tutor",
                                      Key="classroom/export.zip")
            self.assertEqual(head["Metadata"]["sha256"], obj.content_hash)
            self.assertEqual(head["ContentType"], "application/zip")


class S3StoreFactoryTest(StorageSandboxTestCase):
    _ENV = ("OBJECT_STORE_ENDPOINT", "OBJECT_STORE_BUCKET",
            "OBJECT_STORE_ACCESS_KEY", "OBJECT_STORE_SECRET_KEY",
            "OBJECT_STORE_REGION", "OBJECT_STORE_S3_SSE",
            "OBJECT_STORE_BACKEND")

    def setUp(self) -> None:
        super().setUp()
        self._saved = {k: os.environ.get(k) for k in self._ENV}
        for k in self._ENV:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, old in self._saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        super().tearDown()

    def test_missing_config_fails_loudly(self) -> None:
        from app.persistence.object_store.s3 import build_s3_store

        with self.assertRaises(RuntimeError) as ctx:
            build_s3_store()
        self.assertIn("OBJECT_STORE_ENDPOINT", str(ctx.exception))
        self.assertIn("refusing to fall back", str(ctx.exception))

    def test_env_builds_s3_store(self) -> None:
        from app.persistence.object_store import S3ObjectStore
        from app.persistence.object_store.s3 import build_s3_store

        os.environ.update({
            "OBJECT_STORE_ENDPOINT": "http://127.0.0.1:9000",
            "OBJECT_STORE_BUCKET": "custom-bucket",
            "OBJECT_STORE_ACCESS_KEY": "testing",
            "OBJECT_STORE_SECRET_KEY": "testing",
        })
        store = build_s3_store()
        self.assertIsInstance(store, S3ObjectStore)
        self.assertEqual(store.bucket, "custom-bucket")

    def test_default_store_routes_s3_backend(self) -> None:
        from app.persistence.object_store import (LocalObjectStore,
                                                  S3ObjectStore,
                                                  default_store)

        os.environ["OBJECT_STORE_BACKEND"] = "local"
        self.assertIsInstance(default_store(), LocalObjectStore)
        os.environ.update({
            "OBJECT_STORE_BACKEND": "s3",
            "OBJECT_STORE_ENDPOINT": "http://127.0.0.1:9000",
            "OBJECT_STORE_BUCKET": "next-tutor",
            "OBJECT_STORE_ACCESS_KEY": "testing",
            "OBJECT_STORE_SECRET_KEY": "testing",
        })
        self.assertIsInstance(default_store(), S3ObjectStore)

    def test_azure_slot_stays_loud(self) -> None:
        from app.persistence.object_store.remote import (
            RemoteStoreUnavailableError, build_remote_store)

        with self.assertRaises(RemoteStoreUnavailableError):
            build_remote_store("azure")
