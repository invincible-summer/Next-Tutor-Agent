"""备课失败收敛：真实 client 记账、无损格式整理、自动续跑与预算终态。"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from types import SimpleNamespace
from unittest import mock

import httpx

from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_classroom_pipeline import OWNER, WS, PipelineTestBase
from tests.classroom_fake_llm import FakeClassroomLLM
from app.classroom import limits, service
from app.classroom.errors import ClassroomError
from app.classroom.generation_normalize import normalize_authored_slide
from app.classroom.llm_budget import LLMUsageBudget
from app.classroom.llm_io import generate_json
from app.classroom.pipeline import PipelineCrash, _SlideModel
from app.classroom.worker import ClassroomWorker
from app.core import classroom_store as store, llm_async
from app.core.config import settings
from app.schemas import classroom as sc


def response(content="{}", *, usage=True):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        if usage else None)


class TransportAccountingTests(StorageSandboxTestCase):
    def test_rejected_requests_do_not_spend_output_and_toggle_is_remembered(self):
        async def scenario():
            for status in (400, 429):
                client = llm_async.AsyncLLMClient(api_key="test", max_tokens=20000,
                                                sdk_max_retries=0, retry_max=2,
                                                retry_base_delay=0)
                budget = LLMUsageBudget(call_budget=4)
                requests = []

                async def create(**kwargs):
                    requests.append(kwargs)
                    if len(requests) == 1:
                        error_cls = llm_async.RateLimitError if status == 429 else llm_async.APIStatusError
                        raise error_cls("rejected", response=httpx.Response(
                            status, request=httpx.Request("POST", "https://model.invalid")), body={})
                    return response()

                token = llm_async.set_llm_budget_hook(budget)
                try:
                    with mock.patch.object(client.client.chat.completions, "create", create):
                        await client.complete([], disable_thinking=True, max_tokens=6000)
                        await client.complete([], disable_thinking=True, max_tokens=6000)
                    self.assertEqual((budget.calls_used, budget.output_used), (3, 100))
                    self.assertEqual(budget.input_used, 200)
                    if status == 400:
                        self.assertNotIn("extra_body", requests[-1])
                finally:
                    llm_async.reset_llm_budget_hook(token)
                    await client.client.close()
        asyncio.run(scenario())

    def test_success_without_usage_uses_response_size_not_chat_maximum(self):
        async def scenario():
            client = llm_async.AsyncLLMClient(api_key="test", max_tokens=20000)
            budget = LLMUsageBudget(call_budget=2)
            token = llm_async.set_llm_budget_hook(budget)
            try:
                with mock.patch.object(client.client.chat.completions, "create",
                                       mock.AsyncMock(return_value=response("正文" * 100, usage=False))):
                    await client.complete([], max_tokens=6000)
                self.assertEqual(budget.output_used, 200)
            finally:
                llm_async.reset_llm_budget_hook(token)
                await client.client.close()
        asyncio.run(scenario())

    def test_timeout_remains_conservatively_charged(self):
        async def scenario():
            client = llm_async.AsyncLLMClient(api_key="test", sdk_max_retries=0,
                                            retry_max=2, retry_base_delay=0)
            budget = LLMUsageBudget(call_budget=4)
            token = llm_async.set_llm_budget_hook(budget)
            try:
                error = llm_async.APITimeoutError(request=httpx.Request("POST", "https://model.invalid"))
                with mock.patch.object(client.client.chat.completions, "create",
                                       mock.AsyncMock(side_effect=error)):
                    with self.assertRaises(llm_async.LLMRequestError) as caught:
                        await client.complete([], max_tokens=1200)
                self.assertTrue(caught.exception.retryable)
                self.assertEqual((budget.calls_used, budget.output_used), (2, 2400))
            finally:
                llm_async.reset_llm_budget_hook(token)
                await client.client.close()
        asyncio.run(scenario())


class GenerationRecoveryTests(PipelineTestBase):
    def test_new_prompt_version_is_frozen_in_published_revision(self):
        lesson, job_id = self._make_job(
            self._brief(theme_id="academic_clear@2", checkpoint_density="none"),
            renderer_version="2.0.0")
        store.update_job(OWNER, WS, lesson, job_id,
                         lambda job: setattr(job, "slide_prompt_version", "2.1.0"))
        result = asyncio.run(self._pipeline(lesson, job_id, self._deps()).run())
        self.assertEqual(result.state, sc.JobState.succeeded, result.last_error)
        revision = store.load_revision(OWNER, WS, lesson, 1)
        self.assertEqual(revision.prompt_versions["classroom_slide"], "2.1.0")

    def test_long_bullets_and_narration_keep_all_text_without_llm_repair(self):
        text = "动量守恒要求系统所受合外力为零，判断前先确定系统边界。" * 4
        speech = "先确定系统，再分析外力，最后建立守恒方程。" * 30
        data = {"slide": {
            "slide_id": "s_" + "a" * 12, "order": 1, "title": "动量守恒",
            "layout": "key_points", "blocks": [{"kind": "bullets", "id": "b1",
                "items": [[{"kind": "text", "text": text}]]}],
            "segments": [{"segment_id": "s1", "role": "explain", "display_text": "系统与外力",
                          "spoken_text": speech, "show_block_ids": ["b1"],
                          "focus_block_ids": ["b1"]}]},
            "claims": [{"block_id": "b1", "claim_kind": "author_explanation", "text": "分析前提"}]}
        fake = SimpleNamespace(complete=mock.AsyncMock(return_value=(json.dumps(data), None)))
        result, _ = asyncio.run(generate_json(
            fake, prompt_id="classroom_slide", user_text="test", model_cls=_SlideModel,
            pre_validate=normalize_authored_slide))
        self.assertEqual(fake.complete.await_count, 1)
        self.assertEqual(result.slide.blocks[0].kind, "paragraph")
        self.assertIn(text, "".join(s.text for s in result.slide.blocks[0].spans))
        self.assertEqual("".join(s.spoken_text for s in result.slide.segments), speech)
        block_id = result.slide.blocks[0].id
        self.assertEqual(result.claims[0].block_id, block_id)
        self.assertTrue(all(s.show_block_ids == [block_id] for s in result.slide.segments))
        self.assertEqual(len({s.segment_id for s in result.slide.segments}), len(result.slide.segments))

    def test_twenty_four_pages_fit_original_budget_with_stage_caps(self):
        class Metered(FakeClassroomLLM):
            limits_seen = []

            def _outline(self, payload):
                plan = super()._outline(payload)
                plan["pages"] = [{**plan["pages"][1], "order": i + 1} for i in range(24)]
                return plan

            async def complete(self, messages, **kwargs):
                cap = kwargs["max_tokens"]
                self.limits_seen.append(cap)
                budget = llm_async._llm_budget_hook.get()
                reservation = budget.reserve(messages=messages, max_tokens=cap)
                content, _ = await super().complete(messages, **kwargs)
                # 模拟每页接近分配上限的真实输出，而非原 fake 的 100 token。
                usage = {"prompt_tokens": 2500, "completion_tokens": int(cap * .8)}
                budget.settle(reservation, usage)
                return content, usage

        fake = Metered()
        lesson, job_id = self._make_job(self._brief(
            duration_minutes=30, theme_id="academic_clear@2", checkpoint_density="none"),
            renderer_version="2.0.0")
        job = asyncio.run(self._pipeline(lesson, job_id, self._deps(fake)).run())
        self.assertEqual(job.state, sc.JobState.succeeded, job.last_error)
        self.assertEqual(len(store.load_revision(OWNER, WS, lesson, 1).slides), 24)
        self.assertEqual(job.budget.llm_calls_used, 25)
        self.assertLess(job.budget.llm_output_tokens_used, limits.LLM_OUTPUT_TOKEN_BUDGET)
        self.assertLessEqual(max(fake.limits_seen), 6000)

    def test_worker_recovers_transient_failure_without_rewriting_saved_pages(self):
        class OnceUnavailable(FakeClassroomLLM):
            authored = Counter()

            async def complete(self, messages, **kwargs):
                if "任务：单页写作" in messages[0]["content"]:
                    data, _ = json.JSONDecoder().raw_decode(messages[-1]["content"])
                    self.authored[data["order"]] += 1
                    if data["order"] == 3 and self.authored[3] == 1:
                        raise llm_async.LLMRequestError("模型服务暂时不可用", retryable=True)
                return await super().complete(messages, **kwargs)

        async def scenario():
            fake = OnceUnavailable()
            lesson, job_id = self._make_job(self._brief(checkpoint_density="none"))
            worker = ClassroomWorker(deps_factory=lambda: self._deps(fake))
            try:
                await worker.start()
                from tests.test_classroom_worker import _wait_terminal
                job = await _wait_terminal(OWNER, WS, lesson, job_id, timeout=10)
                self.assertEqual(job.state, sc.JobState.succeeded, job.last_error)
                self.assertEqual((fake.authored[1], fake.authored[2], fake.authored[3]), (1, 1, 2))
                self.assertEqual(job.recovery_count, 1)
            finally:
                await worker.stop()
        asyncio.run(scenario())

    def test_exhausted_retry_starts_a_fresh_budget_window(self):
        lesson, job_id = self._make_job()

        def failed(job):
            job.state = sc.JobState.failed
            job.last_error = "ClassroomErrorCode.budget_exceeded: 全课 LLM 输出 token 预算耗尽"
            job.budget.llm_output_tokens_used = 39039
        store.update_job(OWNER, WS, lesson, job_id, failed)
        before = store.load_job(OWNER, WS, lesson, job_id)
        with mock.patch.object(settings, "classroom_enabled", True), mock.patch.object(service, "_wake") as wake:
            snapshot = service.job_snapshot(OWNER, WS, lesson, job_id)
            self.assertIn("retry", snapshot["next_actions"])
            result = service.retry_job(OWNER, WS, lesson, job_id,
                                       before.state_revision)
            self.assertEqual(result["state"], "queued")
            wake.assert_called_once_with(OWNER, WS, lesson, job_id)
        after = store.load_job(OWNER, WS, lesson, job_id)
        self.assertEqual(after.budget, sc.JobBudget())
        self.assertEqual(after.prior_attempts_budget, before.budget)
        self.assertEqual(after.attempts, before.attempts + 1)

    def test_old_overreservation_failure_can_retry_when_balance_suffices(self):
        lesson, job_id = self._make_job()
        def failed(job):
            job.state = sc.JobState.failed
            job.last_error = "budget_exceeded: 全课 LLM 输出 token 预算耗尽"
            job.budget.llm_output_tokens_used = 21000
        store.update_job(OWNER, WS, lesson, job_id, failed)
        job = store.load_job(OWNER, WS, lesson, job_id)
        with mock.patch.object(settings, "classroom_enabled", True), mock.patch.object(service, "_wake"):
            snap = service.retry_job(OWNER, WS, lesson, job_id, job.state_revision)
        self.assertEqual(snap["state"], "queued")
        self.assertIsNone(snap["last_error"])
        after = store.load_job(OWNER, WS, lesson, job_id)
        self.assertEqual(after.budget.llm_output_tokens_used, 0)
        self.assertEqual(after.prior_attempts_budget.llm_output_tokens_used, 21000)

    def test_cached_invalid_layout_is_fixed_without_new_llm_calls(self):
        lesson, job_id = self._make_job(self._brief(checkpoint_density="none"))
        deps = self._deps()
        deps.crash_at = "render"
        pipe = self._pipeline(lesson, job_id, deps)
        with self.assertRaises(PipelineCrash):
            asyncio.run(pipe.run())
        payload = pipe._read_stage(sc.JobPhase.review)
        payload["slides"][0]["layout"] = "derivation"
        original_blocks = payload["slides"][0]["blocks"]
        pipe._save_stage(sc.JobPhase.review, payload)
        fake = FakeClassroomLLM()
        job = asyncio.run(self._pipeline(lesson, job_id, self._deps(fake)).run())
        self.assertEqual(job.state, sc.JobState.succeeded, job.last_error)
        revision = store.load_revision(OWNER, WS, lesson, 1)
        self.assertEqual(revision.slides[0].layout, sc.SlideLayout.key_points)
        self.assertEqual([b.model_dump(mode="json") for b in revision.slides[0].blocks], original_blocks)
        self.assertEqual(fake.calls, [])
