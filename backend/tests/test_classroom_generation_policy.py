"""课程生成成本与容错回归：关闭检查、单轮建议、逐页恢复、预算不重置。"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from unittest import mock

from tests.test_classroom_pipeline import (
    OWNER, WS, FakeLayoutReport, PipelineTestBase,
)
from tests.classroom_fake_llm import FakeClassroomLLM
from app.classroom import limits, service, validation
from app.classroom.errors import ClassroomError
from app.classroom.pipeline import PipelineCrash
from app.classroom.render.check import LayoutCheckError
from app.core import classroom_store as store, llm_async
from app.schemas import classroom as sc


class MeteredLLM(FakeClassroomLLM):
    """与真实 client 同样在请求前预留预算；不访问任何外部服务。"""

    def __init__(self, *, review="ok", crash_page=None):
        super().__init__()
        self.review = review
        self.crash_page = crash_page
        self.reviews = 0
        self.repairs = 0
        self.authored = Counter()

    async def complete(self, messages, **kwargs):
        system = messages[0]["content"]
        if "任务：单页写作" in system:
            payload, _ = json.JSONDecoder().raw_decode(messages[-1]["content"])
            order = payload["order"]
            if order == self.crash_page:
                self.crash_page = None
                raise PipelineCrash("page interrupted")
            self.authored[order] += 1
        if "任务：单页修复" in system:
            self.repairs += 1
        budget = llm_async._llm_budget_hook.get()
        reservation = budget.reserve(messages=messages, max_tokens=3000)
        if "任务：整课复核" in system:
            self.reviews += 1
            if self.review == "budget":
                budget.settle(reservation, None)
                raise ClassroomError("budget_exceeded", "test budget")
            result = ("invalid JSON" if self.review == "invalid" else json.dumps({
                "issues": [{"code": "narration_copy", "severity": "blocker",
                            "reason": "讲稿与正文相似", "slide_id": "invented"}],
                "summary": "不要发布"}))
            usage = {"prompt_tokens": 100, "completion_tokens": 50}
        else:
            result, usage = await super().complete(messages, **kwargs)
        budget.settle(reservation, usage)
        return result, usage


class GenerationPolicyTests(PipelineTestBase):
    def test_extra_checkpoint_pages_and_missing_images_do_not_block(self):
        class ExtraHostsLLM(MeteredLLM):
            def _outline(self, payload):
                result = super()._outline(payload)
                for page in result["pages"][2:6]:
                    page["layout"] = "checkpoint"
                result["pages"][1]["layout"] = "image_explain"
                return result
        fake = ExtraHostsLLM()
        _, _, revision = self._run_course(fake)
        self.assertEqual(len(revision.slides), 8)
        self.assertLessEqual(len(revision.checkpoint_templates), 2)
        self.assertFalse(validation.layout_slot_gate(revision.slides, revision.checkpoint_templates))
        self.assertEqual(fake.repairs, 0)

    def test_disabled_images_do_not_search(self):
        images = mock.Mock(available=True)
        images.search = mock.AsyncMock(side_effect=AssertionError("must not search"))
        lesson, job = self._make_job(self._brief(image_density="none", checkpoint_density="light"))
        result = asyncio.run(self._pipeline(lesson, job, self._deps(images=images)).run())
        self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
        images.search.assert_not_called()

    def _run_course(self, fake, **brief_options):
        lesson, job = self._make_job(self._brief(
            checkpoint_density="light", **brief_options))
        pipe = self._pipeline(lesson, job, self._deps(fake))
        result = asyncio.run(pipe.run())
        self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
        return pipe, result, store.load_revision(OWNER, WS, lesson, 1)

    def test_default_skips_reviewer_and_all_content_repairs(self):
        fake = MeteredLLM()
        pipe, job, revision = self._run_course(fake)
        self.assertFalse(revision.brief.content_review_enabled)
        self.assertEqual((fake.reviews, fake.repairs), (0, 0))
        self.assertEqual(job.budget.llm_calls_used, 1 + len(revision.slides))
        self.assertEqual(pipe._read_stage(sc.JobPhase.review)["content_review_status"], "skipped")

    def test_opt_in_reviews_once_and_blocker_is_advisory(self):
        fake = MeteredLLM()
        _, _, revision = self._run_course(fake, content_review_enabled=True)
        self.assertEqual((fake.reviews, fake.repairs), (1, 0))
        self.assertFalse(validation.has_blocker(revision.review_report.issues))
        self.assertTrue(any(i.code == "narration_copy" for i in revision.review_report.issues))

    def test_optional_review_errors_do_not_retry_or_discard_slides(self):
        for failure in ("invalid", "budget"):
            with self.subTest(failure=failure):
                fake = MeteredLLM(review=failure)
                pipe, _, _ = self._run_course(fake, content_review_enabled=True)
                self.assertEqual((fake.reviews, fake.repairs), (1, 0))
                self.assertEqual(pipe._read_stage(sc.JobPhase.review)["content_review_status"], "unavailable")

    def test_missing_checker_and_environment_report_do_not_rewrite(self):
        for checker in (mock.Mock(side_effect=LayoutCheckError("missing")),
                        lambda _: FakeLayoutReport(False, [{"code": "checker_error"}])):
            with self.subTest(checker=checker):
                lesson, job = self._make_job(self._brief(checkpoint_density="light"))
                fake = MeteredLLM()
                deps = self._deps(fake)
                deps.layout_check = checker
                pipe = self._pipeline(lesson, job, deps)
                result = asyncio.run(pipe.run())
                self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
                self.assertEqual(fake.repairs, 0)
                self.assertTrue(service._job_warnings(OWNER, WS, lesson, result))

    def test_resume_keeps_completed_pages_and_cumulative_usage(self):
        fake = MeteredLLM(crash_page=3)
        lesson, job = self._make_job(self._brief(checkpoint_density="light"))
        pipe = self._pipeline(lesson, job, self._deps(fake))
        with self.assertRaises(PipelineCrash):
            asyncio.run(pipe.run())
        interrupted = store.load_job(OWNER, WS, lesson, job)
        self.assertEqual(interrupted.budget.llm_calls_used, 3)
        self.assertEqual(service._job_progress(OWNER, WS, lesson, interrupted).completed_slides, 2)
        result = asyncio.run(self._pipeline(lesson, job, self._deps(fake)).run())
        self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
        self.assertTrue(all(count == 1 for count in fake.authored.values()))
        self.assertEqual(result.budget.llm_calls_used, 1 + len(fake.authored))
        self.assertGreater(result.budget.llm_input_tokens_used, interrupted.budget.llm_input_tokens_used)

    def test_resume_does_not_reset_exhausted_call_or_token_budget(self):
        for field, value in (("llm_calls_used", limits.llm_call_budget(8)),
                             ("llm_input_tokens_used", limits.LLM_INPUT_TOKEN_BUDGET),
                             ("llm_output_tokens_used", limits.LLM_OUTPUT_TOKEN_BUDGET)):
            with self.subTest(field=field):
                fake = MeteredLLM(crash_page=1)
                lesson, job = self._make_job(self._brief(checkpoint_density="light"))
                with self.assertRaises(PipelineCrash):
                    asyncio.run(self._pipeline(lesson, job, self._deps(fake)).run())
                def exhaust(target):
                    setattr(target.budget, field, value)
                store.update_job(OWNER, WS, lesson, job, exhaust)
                result = asyncio.run(self._pipeline(lesson, job, self._deps(fake)).run())
                self.assertEqual(result.state, sc.JobState.failed)
                self.assertIn("budget_exceeded", result.last_error)
                self.assertEqual(getattr(result.budget, field), value)

    def test_optional_checkpoint_budget_failure_falls_back_to_reflection(self):
        async def fail(**_):
            raise ClassroomError("budget_exceeded", "quiz budget")
        lesson, job = self._make_job()
        result = asyncio.run(self._pipeline(lesson, job, self._deps(
            checkpoint_author=fail)).run())
        self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
        revision = store.load_revision(OWNER, WS, lesson, 1)
        self.assertTrue(revision.checkpoint_templates)
        self.assertTrue(all(t.kind == sc.CheckpointKind.reflect for t in revision.checkpoint_templates))

    def test_change_review_switch_reuses_authored_content(self):
        fake = MeteredLLM()
        lesson, job = self._make_job(self._brief(checkpoint_density="light", content_review_enabled=True))
        deps = self._deps(fake)
        deps.crash_at = "review"
        pipe = self._pipeline(lesson, job, deps)
        with self.assertRaises(PipelineCrash):
            asyncio.run(pipe.run())
        store.update_job(OWNER, WS, lesson, job,
                         lambda j: setattr(j, "state", sc.JobState.failed))
        before = store.load_job(OWNER, WS, lesson, job)
        brief = pipe._load_brief().model_copy(update={"content_review_enabled": False})
        with mock.patch.object(service.caps, "user_allowed", return_value=(True, "")):
            service.patch_brief(OWNER, WS, lesson, job, sc.BriefPatchRequest(
                expected_state_revision=before.state_revision, brief_patch=brief))
        after = store.load_job(OWNER, WS, lesson, job)
        self.assertEqual(after.artifacts["author_slides"], before.artifacts["author_slides"])
        self.assertEqual(after.artifacts["checkpoints"], before.artifacts["checkpoints"])
        store.update_job(OWNER, WS, lesson, job, lambda j: setattr(j, "state", sc.JobState.queued))
        result = asyncio.run(self._pipeline(lesson, job, self._deps(fake)).run())
        self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
        self.assertEqual(fake.reviews, 0)
        self.assertTrue(all(count == 1 for count in fake.authored.values()))
