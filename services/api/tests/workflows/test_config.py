"""Config/runtime contracts of the Temporal lane (ADR-0013).

The load-bearing assertion is the same one as the persistence lane: with
``TEMPORAL_ADDRESS`` unset (the test sandbox strips it) the whole lane is
inert — file mode must keep its in-process job execution unchanged.
"""
from __future__ import annotations

import unittest
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase


class TemporalConfigTest(StorageSandboxTestCase):
    def test_file_mode_is_inert_without_address(self) -> None:
        from app.workflows import config

        self.assertIsNone(config.temporal_address())
        self.assertFalse(config.temporal_configured())

    def test_namespace_defaults_and_overrides(self) -> None:
        import os
        from app.workflows import config

        self.assertEqual(config.temporal_namespace(), "default")
        with mock.patch.dict(os.environ,
                                      {"TEMPORAL_NAMESPACE": "edu-prod"}):
            self.assertEqual(config.temporal_namespace(), "edu-prod")

    def test_address_enables_lane(self) -> None:
        import os
        from app.workflows import config

        with mock.patch.dict(
                os.environ, {"TEMPORAL_ADDRESS": " 127.0.0.1:7233 "}):
            self.assertEqual(config.temporal_address(), "127.0.0.1:7233")
            self.assertTrue(config.temporal_configured())


class WorkflowRuntimeTest(StorageSandboxTestCase):
    def test_task_queues_are_stable_and_distinct(self) -> None:
        from app.workflows import runtime

        self.assertEqual(runtime.ALL_TASK_QUEUES, (
            "documents", "classroom", "evaluation", "media", "maintenance"))
        # ADR-0013: five pools, independently scalable.
        self.assertEqual(len(set(runtime.ALL_TASK_QUEUES)), 5)

    def test_join_workflow_id_is_readable_and_sanitized(self) -> None:
        from app.workflows.runtime import join_workflow_id

        self.assertEqual(join_workflow_id("classroom-job", "job_123"),
                         "classroom-job:job_123")
        # Whitespace/control/path-unsafe characters collapse; empty parts
        # stay positional so ids never silently collide.
        self.assertEqual(join_workflow_id("quiz illustration", "a/b c"),
                         "quiz_illustration:a_b_c")
        self.assertEqual(join_workflow_id("", "x"), "_:x")
        self.assertLessEqual(len(join_workflow_id("p", "x" * 10_000)), 900)

    def test_client_factory_requires_address(self) -> None:
        from app.workflows import runtime

        with self.assertRaises(RuntimeError):
            runtime.get_client()
        runtime.reset_client_cache()  # no-op; cache stays empty in file mode
        self.assertEqual(runtime._client_cache, {})


class WorkerEntrypointTest(StorageSandboxTestCase):
    def test_worker_refuses_to_run_without_temporal(self) -> None:
        import worker

        self.assertEqual(worker.main(["--queues", "documents"]), 2)

    def test_worker_rejects_unknown_queue(self) -> None:
        import os
        import worker

        with mock.patch.dict(
                os.environ, {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}):
            self.assertEqual(worker.main(["--queues", "nope"]), 2)
            self.assertEqual(worker.main(["--queues", "  "]), 2)

    def test_default_arguments_cover_all_queues(self) -> None:
        import worker
        from app.workflows.runtime import ALL_TASK_QUEUES

        args = worker._parse_args([])
        self.assertEqual(args.queues, ",".join(ALL_TASK_QUEUES))
