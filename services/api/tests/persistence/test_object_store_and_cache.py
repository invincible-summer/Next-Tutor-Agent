"""Unit lane: object store key discipline + local backend, cache primitives."""
from __future__ import annotations

import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class ObjectKeyTest(StorageSandboxTestCase):
    def test_namespaces_are_enforced_and_keys_sanitized(self) -> None:
        from app.persistence.object_store.base import build_object_key

        key = build_object_key("illustrations", "owner-1", opaque_id="abc",
                               suffix=".svg")
        self.assertEqual(key, "illustrations/owner-1/abc.svg")
        with self.assertRaises(ValueError):
            build_object_key("not-a-namespace", opaque_id="x")
        # traversal and unsafe characters never survive segment cleaning
        key2 = build_object_key("uploads", "../etc/passwd", opaque_id="i..d",
                                suffix=".png")
        # sanitizer keeps the segment benign: no exact ".." path segment,
        # separators collapsed (traversal needs a whole ".." segment)
        self.assertNotIn("..", key2.split("/"))
        self.assertTrue(key2.startswith("uploads/"))

    def test_keys_are_opaque_by_default(self) -> None:
        from app.persistence.object_store.base import build_object_key

        a = build_object_key("tts")
        b = build_object_key("tts")
        self.assertNotEqual(a.split("/")[-1], b.split("/")[-1])


class LocalObjectStoreTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_put_get_delete_roundtrip(self) -> None:
        from app.persistence.object_store.local import LocalObjectStore

        store = LocalObjectStore(self.root / "object_store")
        stored = await store.put("uploads/u1/file.bin", b"\x00\x01payload",
                                 content_type="application/octet-stream")
        self.assertEqual(stored.size, 9)
        self.assertEqual(len(stored.content_hash), 64)
        self.assertTrue(await store.exists("uploads/u1/file.bin"))
        self.assertEqual(await store.get("uploads/u1/file.bin"),
                         b"\x00\x01payload")
        self.assertEqual(await store.size("uploads/u1/file.bin"), 9)
        self.assertTrue(await store.delete("uploads/u1/file.bin"))
        self.assertFalse(await store.exists("uploads/u1/file.bin"))
        self.assertIsNone(await store.get("uploads/u1/file.bin"))

    async def test_rejects_traversal_keys(self) -> None:
        from app.persistence.object_store.local import LocalObjectStore

        store = LocalObjectStore(self.root / "object_store")
        for bad in ("../escape", "/abs/path", "a/../../b"):
            with self.assertRaises(ValueError):
                await store.put(bad, b"x")


class MemoryCachePrimitivesTest(StorageSandboxTestCase,
                                unittest.IsolatedAsyncioTestCase):
    async def test_fixed_window_rate_limit(self) -> None:
        from app.persistence.cache.memory import MemoryCachePrimitives

        cache = MemoryCachePrimitives()
        results = [await cache.rate_limit("login:u1", 2, window_seconds=60)
                   for _ in range(3)]
        self.assertTrue(results[0].allowed)
        self.assertTrue(results[1].allowed)
        self.assertFalse(results[2].allowed)
        self.assertGreater(results[2].retry_after_seconds, 0)

    async def test_set_get_ttl_and_delete(self) -> None:
        from app.persistence.cache.memory import MemoryCachePrimitives

        cache = MemoryCachePrimitives()
        await cache.set("k", b"v", ttl_seconds=60)
        self.assertEqual(await cache.get("k"), b"v")
        await cache.delete("k")
        self.assertIsNone(await cache.get("k"))

    async def test_lease_exclusive_until_released(self) -> None:
        from app.persistence.cache.memory import MemoryCachePrimitives

        cache = MemoryCachePrimitives()
        async with cache.lease("job-1", ttl_seconds=30) as first:
            self.assertIsNotNone(first)
            async with cache.lease("job-1", ttl_seconds=30) as second:
                self.assertIsNone(second)
        async with cache.lease("job-1", ttl_seconds=30) as third:
            self.assertIsNotNone(third)


class CacheFactoryTest(StorageSandboxTestCase):
    def test_unconfigured_redis_yields_memory_primitives(self) -> None:
        import os

        from app.persistence.cache import (MemoryCachePrimitives,
                                           get_cache_primitives,
                                           redis_configured)
        from app.persistence.cache.base import redis_url

        saved = os.environ.pop("REDIS_URL", None)
        try:
            self.assertIsNone(redis_url())
            self.assertFalse(redis_configured())
            self.assertIsInstance(get_cache_primitives(),
                                  MemoryCachePrimitives)
        finally:
            if saved is not None:
                os.environ["REDIS_URL"] = saved

    async def test_redis_primitives_fall_back_on_connection_error(self) -> None:
        from app.persistence.cache.redis import RedisCachePrimitives

        # Point at a closed local port: every op must degrade, never raise.
        cache = RedisCachePrimitives("redis://127.0.0.1:1/15")
        await cache.set("k", b"v", ttl_seconds=10)
        self.assertEqual(await cache.get("k"), b"v")  # via memory fallback
        async with cache.lease("l", ttl_seconds=10) as handle:
            self.assertIsNotNone(handle)
        self.assertFalse(await cache.ping())
        await cache.aclose()


if __name__ == "__main__":
    unittest.main()
