"""Administrator OCR policy and system-wide generation limiter tests."""
from __future__ import annotations
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from app.api.v1.admin import OCRPolicyRequest
from app.core import ocr_policy


class TestOCRPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(ocr_policy, "_POLICY_FILE", Path(self.tmp.name) / "ocr.json")
        self.path_patch.start()
        self.old_runtime = ocr_policy._RUNTIME
        ocr_policy._RUNTIME = ocr_policy._Runtime()

    def tearDown(self):
        ocr_policy._RUNTIME = self.old_runtime
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_request_bounds(self):
        first = OCRPolicyRequest(concurrency=1)
        self.assertEqual(first.concurrency, 1)
        self.assertEqual(first.failure_mode, "persistent_api")
        self.assertEqual(OCRPolicyRequest(concurrency=100).concurrency, 100)
        self.assertEqual(OCRPolicyRequest(
            concurrency=20, retry_interval_seconds=0).retry_interval_seconds, 0)
        for value in (0, 101):
            with self.assertRaises(ValidationError):
                OCRPolicyRequest(concurrency=value)
        with self.assertRaises(ValidationError):
            OCRPolicyRequest(concurrency=20, failure_mode="unknown")
        with self.assertRaises(ValidationError):
            OCRPolicyRequest(concurrency=20, retry_interval_seconds=-1)

    def test_v1_policy_migrates_to_persistent_defaults(self):
        ocr_policy._POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
        ocr_policy._POLICY_FILE.write_text('{"concurrency": 7, "version": 1}')
        runtime = ocr_policy._Runtime()
        self.assertEqual(runtime.configured, 7)
        self.assertEqual(runtime.retry_policy()["failure_mode"], "persistent_api")
        self.assertEqual(runtime.retry_policy()["retry_interval_seconds"], 10)

    def test_retry_policy_hot_update(self):
        snapshot = asyncio.run(ocr_policy.set_policy(
            4, failure_mode="bounded_then_local", max_attempts=8,
            retry_interval_seconds=90, request_timeout_seconds=75))
        self.assertEqual(snapshot["failure_mode"], "bounded_then_local")
        self.assertEqual(snapshot["max_attempts"], 8)
        self.assertEqual(snapshot["retry_interval_seconds"], 90)
        self.assertEqual(snapshot["request_timeout_seconds"], 75)
        persisted = ocr_policy._read_policy()
        self.assertEqual(persisted["version"], 2)

    def test_runtime_bounds_allow_zero_interval_and_concurrency_100(self):
        snapshot = asyncio.run(ocr_policy.set_policy(
            100, failure_mode="persistent_api", max_attempts=3,
            retry_interval_seconds=0, request_timeout_seconds=60))
        self.assertEqual(snapshot["configured_concurrency"], 100)
        self.assertEqual(snapshot["retry_interval_seconds"], 0)
        with self.assertRaises(ValueError):
            asyncio.run(ocr_policy.set_policy(
                101, failure_mode="persistent_api", max_attempts=3,
                retry_interval_seconds=0, request_timeout_seconds=60))
        with self.assertRaises(ValueError):
            asyncio.run(ocr_policy.set_policy(
                20, failure_mode="persistent_api", max_attempts=3,
                retry_interval_seconds=-1, request_timeout_seconds=60))

    def test_system_wide_page_cap(self):
        async def scenario():
            await ocr_policy.set_policy(2)
            active = 0
            maximum = 0
            guard = asyncio.Lock()
            async def page():
                nonlocal active, maximum
                async with guard:
                    active += 1
                    maximum = max(maximum, active)
                await asyncio.sleep(0.01)
                async with guard:
                    active -= 1
                return "ok"
            async with ocr_policy.textbook_ocr_job() as first, \
                       ocr_policy.textbook_ocr_job() as second:
                results = await asyncio.gather(*[
                    ocr_policy.run_page(first if i % 2 else second, page) for i in range(10)])
            return maximum, results
        maximum, results = asyncio.run(scenario())
        self.assertLessEqual(maximum, 2)
        self.assertEqual(results, ["ok"] * 10)

    def test_new_generation_waits_for_old_job(self):
        async def scenario():
            await ocr_policy.set_policy(3)
            old = await ocr_policy._RUNTIME.begin_job()
            await ocr_policy.set_policy(1)
            started = asyncio.Event()
            async def new_job():
                job = await ocr_policy._RUNTIME.begin_job()
                started.set()
                await ocr_policy._RUNTIME.end_job(job)
                return job.limit
            task = asyncio.create_task(new_job())
            await asyncio.sleep(0.02)
            before = started.is_set()
            await ocr_policy._RUNTIME.end_job(old)
            limit = await task
            return before, limit, ocr_policy.get_policy()
        before, limit, snapshot = asyncio.run(scenario())
        self.assertFalse(before)
        self.assertEqual(limit, 1)
        self.assertEqual(snapshot["effective_concurrency"], 1)
        self.assertIsNone(snapshot["pending_concurrency"])


if __name__ == "__main__": unittest.main()
