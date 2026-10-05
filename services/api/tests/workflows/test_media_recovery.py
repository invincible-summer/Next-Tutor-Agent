"""Media recovery distinguishes a missing workflow from unknown liveness."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from tests.support.storage_sandbox import StorageSandboxTestCase


class WorkflowLivenessTest(unittest.IsolatedAsyncioTestCase):
    async def test_running_and_confirmed_terminal_states(self):
        from app.workflows.illustration_common import workflow_alive

        client = mock.Mock()
        handle = client.get_workflow_handle.return_value
        for status in WorkflowExecutionStatus:
            with self.subTest(status=status):
                handle.describe = mock.AsyncMock(
                    return_value=SimpleNamespace(status=status))
                self.assertIs(await workflow_alive("synthetic-job", client),
                              status == WorkflowExecutionStatus.RUNNING)

    async def test_only_not_found_confirms_absence(self):
        from app.workflows.illustration_common import workflow_alive

        client = mock.Mock()
        handle = client.get_workflow_handle.return_value
        for status in (RPCStatusCode.NOT_FOUND, RPCStatusCode.UNAVAILABLE,
                       RPCStatusCode.DEADLINE_EXCEEDED,
                       RPCStatusCode.PERMISSION_DENIED):
            with self.subTest(status=status):
                handle.describe = mock.AsyncMock(
                    side_effect=RPCError("synthetic failure", status, b""))
                self.assertIs(await workflow_alive("synthetic-job", client),
                              False if status == RPCStatusCode.NOT_FOUND
                              else None)

    async def test_unknown_status_and_connection_errors_stay_unknown(self):
        from app.workflows.illustration_common import workflow_alive

        client = mock.Mock()
        handle = client.get_workflow_handle.return_value
        handle.describe = mock.AsyncMock(
            return_value=SimpleNamespace(status=None, raw_info=None))
        self.assertIsNone(await workflow_alive("synthetic-job", client))
        with mock.patch("app.workflows.illustration_common.get_client",
                        new=mock.AsyncMock(side_effect=TimeoutError)):
            self.assertIsNone(await workflow_alive("synthetic-job"))


class MediaBootstrapRecoveryTest(StorageSandboxTestCase,
                                 unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from app.illustration import persistence

        self.persistence = persistence
        self.owner = "stu_media_recovery"
        persistence.write(self.owner, "jobs", "quiz_job",
                          {"status": "running"})
        persistence.write(self.owner, "scenario_jobs", "scene_job",
                          {"status": "queued", "session_id": "scene_session"})

    async def test_unknown_or_running_workflows_preserve_pending_records(self):
        import worker as worker_entry

        for state in (None, True):
            with self.subTest(state=state), \
                    mock.patch("app.workflows.illustration_common.workflow_alive",
                               new=mock.AsyncMock(return_value=state)), \
                    mock.patch("app.illustration.orchestrator._settle_interrupted") as quiz, \
                    mock.patch("app.illustration.scenario._settle_interrupted") as scene:
                await worker_entry._bootstrap_media_recovery()
                quiz.assert_not_called()
                scene.assert_not_called()
                self.assertEqual(self.persistence.read(
                    self.owner, "jobs", "quiz_job")["status"], "running")
                self.assertEqual(self.persistence.read(
                    self.owner, "scenario_jobs", "scene_job")["status"],
                    "queued")

    async def test_confirmed_absence_settles_both_workflow_kinds(self):
        import worker as worker_entry

        with mock.patch("app.workflows.illustration_common.workflow_alive",
                        new=mock.AsyncMock(return_value=False)), \
                mock.patch("app.illustration.orchestrator._settle_interrupted") as quiz, \
                mock.patch("app.illustration.scenario._settle_interrupted") as scene:
            await worker_entry._bootstrap_media_recovery()
        quiz.assert_called_once_with(self.owner, "quiz_job")
        scene.assert_called_once_with(self.owner, "scene_session", "scene_job")
