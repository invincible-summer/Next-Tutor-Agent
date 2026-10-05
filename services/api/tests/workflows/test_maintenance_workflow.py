"""Maintenance durable ticks + 账号删除 workflow（ADR-0013 C5）。

确定性测试（time-skipping test server + 同名假 activity）覆盖 workflow
编排；Schedule 注册逻辑以替身 client 验证幂等与开关门控；账号删除双模式
入口（file 线程内 / durable workflow）覆盖路由接缝。真服务器上的 Schedule
行为（模板 id、trigger、describe）由集成车道验证。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase

try:
    from temporalio import activity
    from temporalio.client import ScheduleAlreadyRunningError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from app.workflows.maintenance import (
        MAINTENANCE_WORKFLOWS,
        AccountPurgeIntent,
        AccountPurgeWorkflow,
        MaintenanceTickIntent,
        MaintenanceTickWorkflow,
        ensure_schedules,
        purge_account_durable,
    )
    _TEMPORALIO_AVAILABLE = True
except Exception:  # pragma: no cover
    _TEMPORALIO_AVAILABLE = False


@unittest.skipUnless(_TEMPORALIO_AVAILABLE, "temporalio not installed")
class MaintenanceWorkflowEnvTest(StorageSandboxTestCase,
                                 unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        try:
            self.env = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"temporal test server unavailable: {exc}")
        self.ticks: list[MaintenanceTickIntent] = []
        self.purges: list[AccountPurgeIntent] = []

        @activity.defn(name="maintenance.tick.run")
        async def fake_tick(intent: MaintenanceTickIntent) -> str:
            self.ticks.append(intent)
            return "done"

        @activity.defn(name="account.purge.run")
        async def fake_purge(intent: AccountPurgeIntent) -> dict:
            self.purges.append(intent)
            return {"status": "purged", "user_id": intent.user_id}

        self.worker = Worker(
            self.env.client, task_queue="maintenance",
            workflows=list(MAINTENANCE_WORKFLOWS),
            activities=[fake_tick, fake_purge])

    async def asyncTearDown(self) -> None:
        await self.worker.shutdown()
        await self.env.shutdown()
        await super().asyncTearDown()

    async def test_tick_workflow_runs_activity(self) -> None:
        async with self.worker:
            handle = await self.env.client.start_workflow(
                MaintenanceTickWorkflow.run,
                MaintenanceTickIntent(kind="trash"),
                id="wf-test-tick-1", task_queue="maintenance")
            self.assertEqual(await handle.result(), "done")
        self.assertEqual([i.kind for i in self.ticks], ["trash"])

    async def test_purge_workflow_returns_report(self) -> None:
        async with self.worker:
            handle = await self.env.client.start_workflow(
                AccountPurgeWorkflow.run,
                AccountPurgeIntent(user_id="usr_9"),
                id="wf-test-purge-1", task_queue="maintenance")
            report = await handle.result()
        self.assertEqual(report, {"status": "purged", "user_id": "usr_9"})
        self.assertEqual([i.user_id for i in self.purges], ["usr_9"])


class _FakeScheduleClient:
    def __init__(self, fail_on: set[str] | None = None):
        self.created: list[str] = []
        self.fail_on = fail_on or set()

    async def create_schedule(self, schedule_id, schedule):
        if schedule_id in self.fail_on:
            raise ScheduleAlreadyRunningError()
        self.created.append(schedule_id)
        return mock.Mock(id=schedule_id)


class EnsureSchedulesTest(StorageSandboxTestCase):
    def test_creates_gated_specs_idempotently(self) -> None:
        from datetime import timedelta

        from app.core.config import settings
        with mock.patch.object(settings, "site_assistant_proactive_enabled",
                               True), \
                mock.patch.object(settings, "site_assistant_enabled", True):
            client = _FakeScheduleClient()
            first = asyncio.run(ensure_schedules(client=client))
            self.assertEqual(
                first,
                {"maintenance-briefing": "created",
                 "maintenance-trash": "created",
                 "maintenance-drafts": "created"})
            # 二次注册（已存在）幂等：已建的不报错、不重建。
            second_client = _FakeScheduleClient(
                fail_on={"maintenance-briefing", "maintenance-trash",
                         "maintenance-drafts"})
            second = asyncio.run(ensure_schedules(client=second_client))
            self.assertEqual(
                sorted(second.values()), ["existing"] * 3)
            self.assertEqual(second_client.created, [])

    def test_disabled_assistant_skips_briefing_and_drafts(self) -> None:
        from app.core.config import settings
        with mock.patch.object(settings, "site_assistant_proactive_enabled",
                               False), \
                mock.patch.object(settings, "site_assistant_enabled", False):
            client = _FakeScheduleClient()
            outcome = asyncio.run(ensure_schedules(client=client))
        self.assertEqual(list(outcome), ["maintenance-trash"])


class PurgeSeamTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_file_mode_runs_domain_purge_in_thread(self) -> None:
        from app.core import account_data
        with mock.patch.object(account_data, "purge_account",
                               return_value={"status": "purged"}) as purge:
            report = await purge_account_durable("usr_file")
        self.assertEqual(report, {"status": "purged"})
        purge.assert_called_once_with("usr_file")

    async def test_temporal_mode_awaits_workflow_result(self) -> None:
        from app.workflows import maintenance as wf_maintenance

        class FakeHandle:
            async def result(self) -> dict:
                return {"status": "purged", "via": "workflow"}

        captured: list[wf_maintenance.AccountPurgeIntent] = []

        async def fake_start(intent, client=None) -> FakeHandle:
            captured.append(intent)
            return FakeHandle()

        with mock.patch.dict("os.environ",
                             {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch.object(wf_maintenance, "start_account_purge",
                                  fake_start):
            report = await purge_account_durable("usr_durable")
        self.assertEqual(report["via"], "workflow")
        self.assertEqual(captured[0].user_id, "usr_durable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
