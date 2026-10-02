"""课堂 job 控制面回归（plan.md D06 / §14.1/§14.4）。

覆盖：GET J snapshot（进度/警告/next_actions）；cancel 的 CAS 与终态幂等；
retry 保留产物重新排队；continue 从 awaiting_outline/needs_input 续跑；
PATCH outline 替换大纲并失效下游产物；PATCH brief 白名单/阶段失效/修订
任务拒绝；SSE 首连全量 snapshot、状态变化事件、终态收尾、断流不影响
worker（事实源在磁盘）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.classroom_fake_llm import FakeClassroomLLM  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import service as classroom_service  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.classroom.pipeline import (ClassroomPipeline,  # noqa: E402
                                    PipelineDeps)
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, PipelineTestBase, WS  # noqa: E402


@dataclass
class _OkReport:
    ok: bool = True
    issues: list = field(default_factory=list)


def _deps(**overrides) -> PipelineDeps:
    values = dict(llm=FakeClassroomLLM(),
                  layout_check=lambda html: _OkReport())
    values.update(overrides)
    return PipelineDeps(**values)


class JobControlTests(PipelineTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        patcher = mock.patch.object(settings, "classroom_enabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _published(self, *, start_mode: str = "automatic",
                   ) -> tuple[str, str]:
        lesson_id, job_id = self._make_job(start_mode=start_mode)
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        return lesson_id, job_id

    def test_snapshot_progress_and_actions(self) -> None:
        lesson_id, job_id = self._published()
        snap = classroom_service.job_snapshot(OWNER, WS, lesson_id, job_id)
        self.assertEqual(snap["state"], "succeeded")
        self.assertGreater(snap["progress"]["total_slides"], 0)
        self.assertEqual(snap["progress"]["completed_slides"],
                         snap["progress"]["total_slides"])
        self.assertEqual(snap["next_actions"], [])

    def test_cancel_cas_and_terminal_idempotent(self) -> None:
        lesson_id, job_id = self._make_job()
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        rev = job.state_revision
        # 错误的 expected_state_revision → 409 语义
        with self.assertRaises(ClassroomError) as ctx:
            classroom_service.cancel_job(OWNER, WS, lesson_id, job_id,
                                         rev + 5)
        self.assertEqual(ctx.exception.code, "revision_conflict")
        snap = classroom_service.cancel_job(OWNER, WS, lesson_id, job_id,
                                            rev)
        self.assertTrue(snap["cancel_requested"])
        # 终态后 cancel 返回当前状态（不报错）
        lesson2, job2 = self._published()
        store.update_job(OWNER, WS, lesson2, job2,
                         lambda j: setattr(j, "state", sc.JobState.failed))
        snap2 = classroom_service.cancel_job(OWNER, WS, lesson2, job2, 2)
        self.assertEqual(snap2["state"], "failed")

    def test_retry_keeps_artifacts_and_reruns(self) -> None:
        lesson_id, job_id = self._make_job()
        # 先让它失败（坏 JSON）
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id,
            _deps(llm=FakeClassroomLLM(bad_json=True))).run())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertEqual(job.state, sc.JobState.failed)
        artifacts_before = dict(job.artifacts)
        woke = []
        classroom_service.enqueue_job = (
            lambda o, w, l, j: woke.append(j))
        try:
            snap = classroom_service.retry_job(
                OWNER, WS, lesson_id, job_id, job.state_revision)
        finally:
            classroom_service.enqueue_job = None
        self.assertEqual(snap["state"], "queued")
        self.assertEqual(woke, [job_id])
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertEqual(job.attempts, 2)
        self.assertTrue(
            all(job.artifacts.get(k) == v
                for k, v in artifacts_before.items()),
            "已校验产物必须保留")
        # 修复 LLM 后重跑 → 成功（resolve 阶段复用，不重复扣预算）
        final = asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        self.assertEqual(final.state, sc.JobState.succeeded)

    def test_continue_after_outline_review(self) -> None:
        lesson_id, job_id = self._make_job(start_mode="outline_first")
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertEqual(job.state, sc.JobState.awaiting_outline)
        self.assertIn("continue", classroom_service.job_snapshot(
            OWNER, WS, lesson_id, job_id)["next_actions"])
        snap = classroom_service.continue_job(
            OWNER, WS, lesson_id, job_id, job.state_revision)
        self.assertEqual(snap["state"], "queued")
        final = asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        self.assertEqual(final.state, sc.JobState.succeeded)
        self.assertEqual(
            store.load_lesson(OWNER, WS, lesson_id).latest_ready_revision, 1)

    def test_patch_outline_invalidates_downstream(self) -> None:
        lesson_id, job_id = self._make_job(start_mode="outline_first")
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        outline_payload = json.loads(
            (store.stages_dir(OWNER, WS, lesson_id, job_id)
             / "outline.json").read_text(encoding="utf-8"))
        plan = outline_payload["plan"]
        plan["pages"] = plan["pages"][:2]  # 用户砍到两页
        request = sc.OutlinePatchRequest(
            expected_state_revision=job.state_revision,
            outline=sc.OutlinePlan.model_validate(plan))
        snap = classroom_service.patch_outline(
            OWNER, WS, lesson_id, job_id, request)
        self.assertEqual(snap["state"], "awaiting_outline")
        job2 = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertNotIn("author_slides", job2.artifacts)
        final = asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        self.assertEqual(final.state, sc.JobState.succeeded)
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        self.assertEqual(len(revision.slides), 2)

    def test_patch_brief_whitelist_and_invalidation(self) -> None:
        lesson_id, job_id = self._make_job(start_mode="outline_first")
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        brief = json.loads(
            store.job_brief_path(OWNER, WS, lesson_id, job_id)
            .read_text(encoding="utf-8"))
        brief["custom_requirements"] = "多举生活例子"
        request = sc.BriefPatchRequest(
            expected_state_revision=job.state_revision,
            brief_patch=sc.LessonBrief.model_validate(brief))
        snap = classroom_service.patch_brief(
            OWNER, WS, lesson_id, job_id, request)
        self.assertEqual(snap["state"], "awaiting_outline")
        updated = json.loads(
            store.job_brief_path(OWNER, WS, lesson_id, job_id)
            .read_text(encoding="utf-8"))
        self.assertEqual(updated["custom_requirements"], "多举生活例子")
        job2 = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertNotIn("outline", job2.artifacts,
                         "brief 变化必须失效大纲（含 custom_requirements）")

    def test_patch_brief_rejected_for_revision_jobs(self) -> None:
        from app.classroom import revisions as rev
        lesson_id, _job0 = self._published()
        op = sc.ChangeThemeOperation(theme_id="chalk_focus@1")
        result = rev.create_revision_job(
            OWNER, WS, lesson_id,
            sc.CreateRevisionRequest(base_revision=1, operation=op),
            idempotency_key="d06-rev-op")
        job = store.load_job(OWNER, WS, lesson_id, result["job_id"])
        brief = json.loads(
            store.job_brief_path(OWNER, WS, lesson_id, result["job_id"])
            .read_text(encoding="utf-8"))
        request = sc.BriefPatchRequest(
            expected_state_revision=job.state_revision,
            brief_patch=sc.LessonBrief.model_validate(brief))
        with self.assertRaises(ClassroomError):
            classroom_service.patch_brief(
                OWNER, WS, lesson_id, result["job_id"], request)


class JobEventsSSETests(PipelineTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        patcher = mock.patch.object(settings, "classroom_enabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stream_snapshots_and_terminal(self) -> None:
        lesson_id, job_id = self._make_job()
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())

        async def consume() -> list[str]:
            events: list[str] = []
            generator = classroom_service.job_events(
                OWNER, WS, lesson_id, job_id, heartbeat_seconds=15.0)
            async for chunk in generator:
                events.append(chunk)
            return events

        events = asyncio.run(consume())
        self.assertTrue(events)
        first = events[0]
        self.assertTrue(first.startswith("id: "))
        # 已终态的 job：首帧即 terminal snapshot（§14.4 连接即发完整快照）
        self.assertIn("event: terminal", first)
        self.assertIn('"state":"succeeded"', first)

    def test_disconnect_does_not_touch_job(self) -> None:
        lesson_id, job_id = self._make_job()

        async def consume_partial() -> None:
            generator = classroom_service.job_events(
                OWNER, WS, lesson_id, job_id)
            async for _chunk in generator:  # 只取首帧即"断开"
                return

        asyncio.run(consume_partial())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertEqual(job.state, sc.JobState.queued,
                         "SSE 断开不得改变 job 状态")

    def test_running_then_terminal_sequence(self) -> None:
        lesson_id, job_id = self._make_job()

        async def scenario() -> list[str]:
            seen: list[str] = []

            async def drain() -> None:
                async for chunk in classroom_service.job_events(
                        OWNER, WS, lesson_id, job_id):
                    seen.append(chunk)

            task = asyncio.ensure_future(drain())
            await asyncio.sleep(0.2)
            await ClassroomPipeline(
                OWNER, WS, lesson_id, job_id, _deps()).run()
            await asyncio.wait_for(task, timeout=8)
            return seen

        seen = asyncio.run(scenario())
        self.assertTrue(any("event: snapshot" in e for e in seen))
        self.assertTrue(any("event: terminal" in e for e in seen))
        states = [json.loads(e.split("data: ", 1)[1])["state"]
                  for e in seen if e.startswith("id: ")]
        self.assertIn("succeeded", states)


class LessonDetailTests(PipelineTestBase):
    """GET L 投影（E01）：已发布给 revision；未完成给 pending brief；答案不泄漏。"""

    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        patcher = mock.patch.object(settings, "classroom_enabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_published_detail_projection(self) -> None:
        lesson_id, _job_id = self._make_job()
        job_id = store.load_lesson(OWNER, WS, lesson_id).latest_job_id
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        detail = classroom_service.lesson_detail(OWNER, WS, lesson_id)
        self.assertEqual(detail["lifecycle"], "active")
        self.assertIsNotNone(detail["revision"])
        rev = detail["revision"]
        self.assertGreater(len(rev["slides"]), 0)
        self.assertGreaterEqual(len(rev["source_records"]), 1)
        self.assertEqual(rev["brief"]["topic"], self._brief().topic)
        self.assertIn("render", json.dumps(rev))  # 投影可序列化
        # 题模板（含答案）绝不进入 public 投影（§4.3/I03）
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        spec = store.load_revision(OWNER, WS, lesson_id,
                                   lesson.latest_ready_revision)
        has_templates = len(spec.checkpoint_templates) > 0
        for ckpt in rev["checkpoints"]:
            self.assertNotIn("verified_question_template", ckpt)
        if has_templates:
            for ckpt in rev["checkpoints"]:
                self.assertIsNone(ckpt.get("question"))
        # 指定 revision 同样可取
        again = classroom_service.lesson_detail(
            OWNER, WS, lesson_id,
            revision=lesson.latest_ready_revision)
        self.assertEqual(again["revision"]["revision"],
                         lesson.latest_ready_revision)

    def test_list_summary_carries_brief_and_progress(self) -> None:
        lesson_id, _ = self._make_job()
        job_id = store.load_lesson(OWNER, WS, lesson_id).latest_job_id
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        res = classroom_service.list_lessons(OWNER, WS)
        item = next(i for i in res["items"]
                    if i["lesson_id"] == lesson_id)
        self.assertEqual(item["status"], "ready")
        self.assertIsNotNone(item["brief"])
        self.assertEqual(item["brief"]["duration_minutes"], 15)
        self.assertGreaterEqual(item["extra"]["slide_count"], 1)
        self.assertEqual(item["latest_job"]["progress"]["total_slides"],
                         item["extra"]["slide_count"])
        # 状态筛选：生成中/可上课
        ready = classroom_service.list_lessons(OWNER, WS, status="ready")
        self.assertTrue(all(i["status"] == "ready" for i in ready["items"]))
        gen = classroom_service.list_lessons(OWNER, WS, status="generating")
        self.assertFalse(any(i["lesson_id"] == lesson_id
                             for i in gen["items"]))

    def test_unfinished_detail_returns_pending_brief_only(self) -> None:
        lesson_id, _ = self._make_job()
        detail = classroom_service.lesson_detail(OWNER, WS, lesson_id)
        self.assertIsNone(detail["revision"])
        self.assertIsNotNone(detail["pending"])
        self.assertEqual(detail["pending"]["brief"]["topic"],
                         self._brief().topic)
        self.assertEqual(detail["pending"]["state"], "queued")

    def test_preview_draft_watermark_and_no_answers(self) -> None:
        from app.classroom import service as svc
        lesson_id, _ = self._make_job()
        job_id = store.load_lesson(OWNER, WS, lesson_id).latest_job_id
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        preview = svc.job_preview(OWNER, WS, lesson_id, job_id)
        self.assertEqual(preview["state"], "succeeded")
        self.assertGreater(len(preview["slides"]), 0)
        self.assertIsNotNone(preview["html"])
        self.assertIn("草稿 · DRAFT", preview["html"])
        # 题模板答案绝不进入投影
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        spec = store.load_revision(OWNER, WS, lesson_id,
                                   lesson.latest_ready_revision)
        if spec.checkpoint_templates:
            blob = str(preview["slides"]) + str(preview["html"])
            for tmpl in spec.checkpoint_templates:
                answer_blob = str(
                    (tmpl.verified_question_template or {}).get("answer", ""))
                if answer_blob:
                    self.assertNotIn(answer_blob, blob)
        # slide_id 过滤
        one = svc.job_preview(OWNER, WS, lesson_id, job_id,
                              slide_id=preview["slides"][0]["slide_id"])
        self.assertEqual(len(one["slides"]), 1)

    def test_preview_without_stages_is_structured_only(self) -> None:
        from app.classroom import service as svc
        lesson_id, job_id = self._make_job()
        preview = svc.job_preview(OWNER, WS, lesson_id, job_id)
        self.assertEqual(preview["slides"], [])
        self.assertIsNone(preview["html"])
        self.assertIsNone(preview["outline"])

    def test_foreign_or_missing_lesson_404(self) -> None:
        with self.assertRaises(ClassroomError) as ctx:
            classroom_service.lesson_detail("usr_other_user", WS,
                                            "les_doesnotexist")
        self.assertEqual(ctx.exception.code, "source_not_found")
        lesson_id, _ = self._make_job()
        with self.assertRaises(ClassroomError):
            classroom_service.lesson_detail("usr_other_user", WS, lesson_id)


if __name__ == "__main__":
    unittest.main()
