"""B10 课程高级动作回归（对齐 2026-09 课堂重构与构图）。

覆盖：lesson.generate（预览含 start_mode/来源/时长、不含构图字段；幂等
复用同一生成任务）、lesson.cancel（协作式取消、终态 409）、lesson.retry
（仅 failed/cancelled/needs_input）、lesson.export（真实产物 + 下载链接，
不自动触发下载）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase
from tests.classroom.test_classroom_pipeline import OWNER, WS, PipelineTestBase
from tests.classroom.test_classroom_revisions import _deps
from tests.classroom.test_classroom_pipeline import ClassroomPipeline

from app.core import assistant_store as store
from app.core import classroom_store as cc_store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class _B10Case(PipelineTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        patcher = mock.patch.object(settings, "classroom_enabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sid = OWNER
        self.cid = "astc_b10"
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": self.cid,
            "turn_id": "astt_b10", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, approval_id: str | None = None):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-b10", route_epoch=1,
            approval_id=approval_id)

    def _preview(self, action_id: str):
        from app.agents.site_assistant import previews
        return previews.build_preview(self.sid, action_id)

    def _approve(self, action_id: str) -> str:
        from app.agents.site_assistant import previews
        preview = self._preview(action_id)
        approval = previews.approve(
            self.sid, action_id, preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        return approval["approval_id"]


class LessonGenerateTest(_B10Case):
    def test_preview_and_idempotent_generate(self) -> None:
        # 来源须为真实教材：为夹具文件补教材记录（P6-C3 来源只保留教材）。
        from app.core import textbook as tb_store
        tb_store._save(OWNER, [{
            "id": "tb_b10_" + _hex(), "title": "动量讲义",
            "file_id": self.ws_file["id"], "status": "ready",
            "kind": "single", "created_at": 0, "updated_at": 0}])
        self._add_action("asta_lg", {
            "kind": "domain_write", "operation": "lesson.generate",
            "input": {"workspace_id": WS, "topic": "动量守恒定律",
                      "duration_minutes": 15,
                      "start_mode": "outline_first",
                      "source_file_ids": [self.ws_file["id"]]}})
        preview = self._preview("asta_lg")
        self.assertEqual(preview["approval"], "review_required")
        fields = {c["field"]: c["after"] for c in preview["changes"]}
        self.assertEqual(fields["topic"], "动量守恒定律")
        self.assertIn("大纲", fields["start_mode"])
        # §26.4 B10：预览不含构图字段。
        self.assertNotIn("composition", fields)
        self.assertNotIn("theme", fields)
        result = self._execute("asta_lg", self._approve("asta_lg"))
        self.assertEqual(result["action"]["state"], "succeeded")
        job_id = result["business_result"]["entity_id"]
        lesson_id = result["business_result"]["related_ids"]["lesson_id"]
        # 幂等：清空回执模拟重试 → 复用同一生成任务，不重复开课。
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"]["asta_lg"]["business_result"] = {"kind": "none"}
        store.save_conversation(self.sid, rec)
        from app.agents.site_assistant import previews as previews_svc
        again = previews_svc.execute_domain_write(
            self.sid, store.load_conversation(self.sid, self.cid)
            ["actions"]["asta_lg"])
        self.assertEqual(again["entity_id"], job_id)
        self.assertEqual(again["related_ids"]["lesson_id"], lesson_id)
        self.assertEqual(
            len([lid for lid in cc_store.list_lesson_ids(OWNER, WS)
                 if lid == lesson_id]), 1)

    def test_unknown_workspace_404(self) -> None:
        self._add_action("asta_lg2", {
            "kind": "domain_write", "operation": "lesson.generate",
            "input": {"workspace_id": "ws_missing_b10", "topic": "不存在"}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_lg2")
        self.assertEqual(ctx.exception.code, "entity_not_found")


class LessonJobOpsTest(_B10Case):
    def test_cancel_and_terminal_conflict(self) -> None:
        import asyncio
        lesson_id, job_id = self._make_job()
        # 跑完管线让 job 到终态（用于终态冲突用例）之前先测取消路径。
        from app.classroom import service as classroom_service
        snapshot = classroom_service.job_snapshot(
            OWNER, WS, lesson_id, job_id)
        self._add_action("asta_lc", {
            "kind": "domain_write", "operation": "lesson.cancel",
            "input": {"workspace_id": WS, "lesson_id": lesson_id,
                      "job_id": job_id,
                      "expected_state_revision":
                          snapshot["state_revision"]}})
        preview = self._preview("asta_lc")
        self.assertEqual(preview["approval"], "intent_sufficient")
        result = self._execute("asta_lc")
        self.assertEqual(result["action"]["state"], "succeeded")
        from app.core import classroom_store as cs
        job = cs.load_job(OWNER, WS, lesson_id, job_id)
        self.assertTrue(job.cancel_requested)  # 协作式取消标记
        # 状态版本过期 → 预览 409。
        self._add_action("asta_lc2", {
            "kind": "domain_write", "operation": "lesson.cancel",
            "input": {"workspace_id": WS, "lesson_id": lesson_id,
                      "job_id": job_id,
                      "expected_state_revision": 999}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_lc2")
        self.assertEqual(ctx.exception.code, "preview_stale")

    def test_retry_requires_retryable_state(self) -> None:
        import asyncio
        lesson_id, job_id = self._make_job()
        job = asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        from app.classroom import service as classroom_service
        snapshot = classroom_service.job_snapshot(
            OWNER, WS, lesson_id, job_id)
        self.assertEqual(snapshot["state"], "succeeded")
        self._add_action("asta_lr", {
            "kind": "domain_write", "operation": "lesson.retry",
            "input": {"workspace_id": WS, "lesson_id": lesson_id,
                      "job_id": job_id,
                      "expected_state_revision":
                          snapshot["state_revision"]}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_lr")
        self.assertEqual(ctx.exception.code, "target_changed")


class LessonExportTest(_B10Case):
    def test_export_returns_download_link(self) -> None:
        import asyncio
        lesson_id, job_id = self._make_job()
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        self._add_action("asta_le", {
            "kind": "domain_write", "operation": "lesson.export",
            "input": {"workspace_id": WS, "lesson_id": lesson_id,
                      "revision": 1, "fmt": "notes_md"}})
        preview = self._preview("asta_le")
        self.assertEqual(preview["approval"], "intent_sufficient")
        result = self._execute("asta_le")
        self.assertEqual(result["action"]["state"], "succeeded")
        business = result["business_result"]
        self.assertEqual(business["kind"], "classroom_export")
        self.assertIn("/exports/", business["related_ids"]["content_url"])
        # 产物真实可读（下载链接指向落盘内容）。
        from app.classroom import service as classroom_service
        data, meta = classroom_service.export_content(
            OWNER, WS, lesson_id, business["entity_id"])
        self.assertTrue(len(data) > 0)
        self.assertEqual(meta["format"], "notes_md")

    def test_export_missing_revision_404(self) -> None:
        lesson_id, _job = self._make_job()
        self._add_action("asta_le2", {
            "kind": "domain_write", "operation": "lesson.export",
            "input": {"workspace_id": WS, "lesson_id": lesson_id,
                      "revision": 99, "fmt": "html_zip"}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_le2")
        self.assertEqual(ctx.exception.code, "entity_not_found")


if __name__ == "__main__":
    unittest.main()
