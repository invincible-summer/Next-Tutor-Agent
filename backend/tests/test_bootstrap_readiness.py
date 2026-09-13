"""Contract tests: Bootstrap 可观测性与 Readiness（plan.md §15-§20, Phase 1）。

当前行为（必须先失败证明 gap）：
  main._lifespan 里多处 except Exception: pass 把启动维护失败完全吞掉；
  /health 恒 ok，没有 /ready。

目标行为：
  BootstrapReport 记录每个 step 的 pending/ok/degraded/failed；
  非 critical 失败 → /ready 返回 200 degraded；critical 失败 → 503；
  失败必须落 log.exception（不再无痕）。

Phase 5 实现后全绿。
"""
from __future__ import annotations

import asyncio
import unittest

from tests.storage_sandbox import StorageSandboxTestCase


class TestBootstrapReportContract(StorageSandboxTestCase):

    def test_report_lifecycle(self):
        from app.core.bootstrap import BootstrapCheck, BootstrapReport
        report = BootstrapReport()
        self.assertFalse(report.ready)  # 无检查时不算 ready
        check = BootstrapCheck(name="demo", critical=False, status="pending")
        report.checks["demo"] = check
        self.assertFalse(report.ready)
        check.status = "ok"
        self.assertTrue(report.ready)
        self.assertFalse(report.degraded)
        check.status = "degraded"
        self.assertTrue(report.degraded)
        self.assertTrue(report.ready, "非关键 degraded 仍算 ready（200）")
        crit = BootstrapCheck(name="crit", critical=True, status="pending")
        report.checks["crit"] = crit
        crit.status = "failed"
        self.assertFalse(report.ready, "critical failed 必须 not_ready")

    def test_run_bootstrap_step_success_and_failure(self):
        from app.core.bootstrap import BootstrapReport, run_bootstrap_step

        async def ok_fn():
            return 1

        async def bad_fn():
            raise RuntimeError("boom")

        report = BootstrapReport()
        asyncio.run(run_bootstrap_step(report, "ok_step", ok_fn))
        self.assertEqual(report.checks["ok_step"].status, "ok")

        with self.assertLogs("app.core.bootstrap", level="ERROR") as cm:
            asyncio.run(run_bootstrap_step(report, "bad_step", bad_fn))
        self.assertTrue(any("bad_step" in line or "bootstrap" in line
                            for line in cm.output),
                        "bootstrap 失败必须留下 ERROR 日志（不再静默）")
        self.assertEqual(report.checks["bad_step"].status, "failed")
        self.assertIn("boom", report.checks["bad_step"].detail)

    def test_critical_failure_raises(self):
        from app.core.bootstrap import BootstrapReport, run_bootstrap_step

        async def bad_fn():
            raise RuntimeError("critical boom")

        report = BootstrapReport()
        with self.assertRaises(RuntimeError):
            asyncio.run(run_bootstrap_step(report, "crit_step", bad_fn,
                                           critical=True))
        self.assertEqual(report.checks["crit_step"].status, "failed")


class TestReadyEndpointContract(StorageSandboxTestCase):
    """/health 保持 liveness；/ready 暴露 bootstrap 状态。"""

    def _client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_ready_endpoint_exists(self):
        with self._client() as client:
            resp = client.get("/api/v1/ready")
            self.assertIn(resp.status_code, (200, 503))
            body = resp.json()
            self.assertIn(body.get("status"), ("ready", "degraded", "not_ready"))
            self.assertIn("checks", body)

    def test_health_stays_liveness_only(self):
        with self._client() as client:
            resp = client.get("/api/v1/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json().get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
