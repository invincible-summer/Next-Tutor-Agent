"""课堂 prompt 注册与 LLM 预算钩子回归（plan.md §6.5/§15.4，D01）。

覆盖：七个 classroom prompt @1.0.0 注册且逐字包含锁定系统指令；
get_llm("classroom") 的模型回退与超时/重试策略；LLMUsageBudget 预留/
结算/耗尽语义；complete() 在真实 HTTP 前预留、按 usage 结算、重试重新
预留、未设钩子时行为不变。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.classroom import limits  # noqa: E402
from app.classroom.llm_budget import LLMUsageBudget  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core import llm_async  # noqa: E402
from app.prompts.classroom import CLASSROOM_PROMPT_IDS, CORE_RULES  # noqa: E402
from app.prompts.registry import active_versions, get  # noqa: E402

# §6.5 锁定系统指令（逐字对照，改 prompt 文本必须升版本）
LOCKED_RULES = """你在编写一节可以实际讲授的课程。只输出所给 JSON schema。
页面用于让学生看，spoken_text 用于让教师讲；不能只把页面文字改写一遍。
使用已提供的 source_id 与 asset_id，不创造来源、URL、图片内容或页码。
证据不足时提出明确缺口，不把网络事实写成教材结论。
每页服务一个主要目标；重要定义说明适用条件，推导说明理由。
讲稿须为自然口语且可直接播放；不得出现待补充、参见某页等未解析占位。
行为只从允许的动作枚举选择，不生成脚本、样式代码或工具调用代码。
不得提前给出正式 checkpoint 的答案；答案进入独立受保护的题目材料。"""


class ClassroomPromptRegistryTests(unittest.TestCase):
    def test_seven_prompts_registered_at_1_0_0(self) -> None:
        for pid in CLASSROOM_PROMPT_IDS:
            self.assertEqual(active_versions().get(pid), "1.0.0",
                             f"{pid} 必须以 1.0.0 active 注册")
            self.assertTrue(get(pid).text.strip())

    def test_locked_core_rules_verbatim_in_every_prompt(self) -> None:
        self.assertEqual(CORE_RULES, LOCKED_RULES)
        for pid in CLASSROOM_PROMPT_IDS:
            self.assertIn(LOCKED_RULES, get(pid).text,
                          f"{pid} 缺少锁定系统指令")

    def test_each_prompt_declares_its_io_contract(self) -> None:
        contracts = {
            "classroom_outline": "page_plan",
            "classroom_search_plan": "queries",
            "classroom_slide": "claims",
            "classroom_review": "issues",
            "classroom_repair": "完整替代页",
            "classroom_explain": "插问回复",
            "classroom_visual_queries": "intents",
        }
        for pid, keyword in contracts.items():
            self.assertIn(keyword, get(pid).text)


class GetLLMClassroomTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(settings, "llm_api_key", "test-key")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_classroom_purpose_falls_back_to_main_model(self) -> None:
        with mock.patch.object(settings, "classroom_model", ""):
            client = llm_async.get_llm("classroom")
            self.assertEqual(client.model, settings.llm_model,
                             "CLASSROOM_MODEL 为空必须回主模型")

    def test_classroom_purpose_uses_override_without_new_credentials(self) -> None:
        with mock.patch.object(settings, "classroom_model", "glm-5.3-air"):
            client = llm_async.get_llm("classroom")
            self.assertEqual(client.model, "glm-5.3-air")
            self.assertEqual(client.base_url, settings.llm_base_url,
                             "凭证/base URL 不另建")

    def test_classroom_client_policy(self) -> None:
        client = llm_async.get_llm("classroom")
        self.assertEqual(client.client.max_retries, 0, "SDK 隐式重试必须关闭")
        self.assertEqual(client._retry_max, 2, "应用层有界重试")
        self.assertEqual(client.client.timeout,
                         float(limits.SINGLE_LLM_TIMEOUT_SECONDS))


class LLMUsageBudgetTests(unittest.TestCase):
    def test_reserve_settle_with_usage(self) -> None:
        budget = LLMUsageBudget(call_budget=3)
        reservation = budget.reserve(
            messages=[{"role": "user", "content": "动量守恒" * 10}],
            max_tokens=500)
        self.assertGreater(reservation["est_in"], 0)
        budget.settle(reservation, {"prompt_tokens": 80,
                                    "completion_tokens": 40})
        self.assertEqual(budget.calls_used, 1)
        self.assertEqual(budget.input_used, 80)
        self.assertEqual(budget.output_used, 40)

    def test_unknown_usage_settles_at_reserved_max(self) -> None:
        budget = LLMUsageBudget(call_budget=3)
        reservation = budget.reserve(messages=[{"role": "user", "content": "hi"}],
                                     max_tokens=600)
        budget.settle(reservation, None)
        self.assertEqual(budget.output_used, 600)

    def test_call_budget_exhausted(self) -> None:
        budget = LLMUsageBudget(call_budget=1)
        r = budget.reserve(messages=[{"role": "user", "content": "x"}],
                           max_tokens=10)
        budget.settle(r, {"prompt_tokens": 5, "completion_tokens": 2})
        with self.assertRaises(ClassroomError) as ctx:
            budget.reserve(messages=[{"role": "user", "content": "x"}],
                           max_tokens=10)
        self.assertEqual(ctx.exception.code, "budget_exceeded")

    def test_token_budgets_and_deadline(self) -> None:
        budget = LLMUsageBudget(call_budget=99, input_tokens=100,
                                output_tokens=100)
        with self.assertRaises(ClassroomError):
            budget.reserve(messages=[{"role": "user", "content": "字" * 400}],
                           max_tokens=10)
        budget = LLMUsageBudget(call_budget=99)
        budget.remaining_seconds = 0
        with self.assertRaises(ClassroomError):
            budget.reserve(messages=[{"role": "user", "content": "x"}],
                           max_tokens=10)
        self.assertTrue(budget.exhausted)


def _fake_response(content: str = "{}", prompt_tokens: int = 10,
                   completion_tokens: int = 5):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=prompt_tokens + completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


class CompleteBudgetHookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(settings, "llm_api_key", "test-key")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, create_fn) -> llm_async.AsyncLLMClient:
        client = llm_async.AsyncLLMClient(sdk_max_retries=0, retry_max=2,
                                          retry_base_delay=0.001)
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))
        return client

    async def test_complete_reserves_before_http_and_settles_usage(self) -> None:
        calls: list[str] = []
        budget = LLMUsageBudget(call_budget=2)

        async def create(**kwargs):
            calls.append("http")
            return _fake_response("ok", prompt_tokens=42,
                                  completion_tokens=17)

        client = self._client(create)
        token = llm_async.set_llm_budget_hook(budget)
        try:
            content, usage = await client.complete(
                [{"role": "user", "content": "hi"}], disable_thinking=True)
        finally:
            llm_async.reset_llm_budget_hook(token)
        self.assertEqual(content, "ok")
        self.assertEqual(calls, ["http"])
        self.assertEqual(budget.calls_used, 1)
        self.assertEqual(budget.input_used, 42)
        self.assertEqual(budget.output_used, 17)

    async def test_budget_exceeded_blocks_http(self) -> None:
        async def create(**kwargs):  # pragma: no cover - must not run
            raise AssertionError("预算耗尽不得发 HTTP")

        client = self._client(create)
        budget = LLMUsageBudget(call_budget=0)
        token = llm_async.set_llm_budget_hook(budget)
        try:
            with self.assertRaises(ClassroomError) as ctx:
                await client.complete([{"role": "user", "content": "hi"}])
            self.assertEqual(ctx.exception.code, "budget_exceeded")
        finally:
            llm_async.reset_llm_budget_hook(token)

    async def test_retry_re_reserves_budget(self) -> None:
        attempts: list[int] = []
        budget = LLMUsageBudget(call_budget=5)

        async def create(**kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                import httpx
                raise llm_async.APIConnectionError(
                    request=httpx.Request("POST", "https://llm.invalid/v1"))
            return _fake_response("ok")

        client = self._client(create)
        token = llm_async.set_llm_budget_hook(budget)
        try:
            content, _ = await client.complete(
                [{"role": "user", "content": "hi"}])
        finally:
            llm_async.reset_llm_budget_hook(token)
        self.assertEqual(content, "ok")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(budget.calls_used, 2,
                         "失败调用按预留扣账 + 重试再预留")

    async def test_without_hook_behavior_unchanged(self) -> None:
        async def create(**kwargs):
            return _fake_response("plain")

        client = self._client(create)
        content, usage = await client.complete(
            [{"role": "user", "content": "hi"}])
        self.assertEqual((content, usage["total_tokens"]), ("plain", 15))


if __name__ == "__main__":
    unittest.main()
