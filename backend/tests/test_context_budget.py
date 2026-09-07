"""Context budget, complete-turn compaction and public reasoning summary tests."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.storage_sandbox import StorageSandboxTestCase
from app.agents.state import PlanStep, TaskPlan, TaskType, TaskUnderstanding
from app.core.context import compact_history
from app.core.context_budget import build_budget_snapshot


class _CompactLLM:
    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        return "目标=学习浮力；未完成=完成检测题", {}


class TestContextBudget(StorageSandboxTestCase):
    def test_snapshot_counts_messages_tools_and_thresholds(self):
        messages = [{"role": "system", "content": "系统规则"},
                    {"role": "user", "content": "讲解浮力"}]
        tools = [{"type": "function", "function": {
            "name": "generate_quiz", "description": "生成练习"}}]
        snap = build_budget_snapshot(
            messages, tools, stage="test", max_output_tokens=4000)
        self.assertGreater(snap.estimated_input_tokens, 0)
        self.assertGreater(snap.tool_schema_tokens, 0)
        self.assertLess(snap.soft_trigger_tokens, snap.hard_trigger_tokens)

    def test_compaction_keeps_complete_recent_turns(self):
        messages = [{"role": "system", "content": "SYS"},
                    {"role": "user", "content": "PRE"}]
        for i in range(6):
            messages.extend([
                {"role": "user", "content": f"用户{i}"},
                {"role": "assistant", "content": f"回答{i}"},
            ])
        compacted, summary = asyncio.run(compact_history(
            messages, _CompactLLM(), keep_recent=2))
        contents = [m["content"] for m in compacted]
        self.assertTrue(summary)
        self.assertIn("用户4", contents)
        self.assertIn("回答4", contents)
        self.assertIn("用户5", contents)
        self.assertIn("回答5", contents)
        self.assertNotIn("用户3", contents)

    def test_public_summary_is_informative_without_hidden_chain(self):
        from app.agents.reasoning_narrator import build_reasoning_events
        plan = TaskPlan(steps=[
            PlanStep(agent_role="teaching", task="讲解"),
            PlanStep(agent_role="assessment", task="检测"),
        ])
        strategy = SimpleNamespace(mode=SimpleNamespace(value="introduction"))
        events = build_reasoning_events(
            TaskUnderstanding(intent=TaskType.EXPLAIN, concept="浮力"),
            plan, strategy)
        text = "\n".join(event.content for event in events)
        self.assertIn("任务重点", text)
        self.assertIn("结构化检测题", text)
        self.assertGreater(len(text), 70)
        self.assertNotIn("系统提示", text)

    def test_provider_policy_falls_back_when_thinking_control_missing(self):
        from app.core.llm_runtime.capabilities import ProviderCapabilities
        from app.core.llm_runtime.reasoning import resolve_reasoning_policy
        caps = ProviderCapabilities(
            provider_id="test", model="test", context_window=32768,
            max_output_tokens=6000, supports_disable_thinking=False)
        # EXECUTOR_TOOL_THINKING=0（旧行为）：工具步请求 NONE，provider 不支持
        # 关闭思考时回退 provider_default。
        with patch.object(
                __import__("app.core.llm_runtime.reasoning", fromlist=["settings"]).settings,
                "executor_tool_thinking", False):
            policy = resolve_reasoning_policy(
                "executor_tool", has_tools=True, capabilities=caps,
                runtime_mode="adapter")
        self.assertFalse(policy.disable_thinking)
        self.assertEqual(policy.fallback_reason,
                         "disable_thinking_unsupported")
        self.assertEqual(policy.applied_mode, "provider_default")

    def test_tool_steps_keep_thinking_by_default(self):
        # 默认（EXECUTOR_TOOL_THINKING=1）：工具步 LOW，不再强制关闭思考，
        # 由预算守卫与 incomplete-answer recovery 兜底 starving。
        from app.core.llm_runtime.capabilities import ProviderCapabilities
        from app.core.llm_runtime.reasoning import ReasoningMode, resolve_reasoning_policy
        caps = ProviderCapabilities(
            provider_id="test", model="test", context_window=65536,
            max_output_tokens=8000, supports_disable_thinking=True)
        policy = resolve_reasoning_policy(
            "executor_tool", has_tools=True, capabilities=caps,
            runtime_mode="adapter")
        self.assertEqual(policy.requested_mode, ReasoningMode.LOW)
        self.assertFalse(policy.disable_thinking)

    def test_tool_stage_output_cap_defaults_to_6000_and_is_configurable(self):
        # 思考型模型把 reasoning_content 算进同一信封：旧 4000 硬顶常被思考
        # 吃光触发恢复重试（比重试更省的是放大信封）。默认 6000，可用
        # EXECUTOR_TOOL_MAX_OUTPUT_TOKENS 调回。
        from app.core.llm_runtime.capabilities import ProviderCapabilities
        from app.core.llm_runtime.reasoning import resolve_reasoning_policy
        caps = ProviderCapabilities(
            provider_id="test", model="test", context_window=65536,
            max_output_tokens=8000, supports_disable_thinking=True)
        policy = resolve_reasoning_policy(
            "executor_tool", has_tools=True, capabilities=caps,
            runtime_mode="adapter")
        self.assertEqual(policy.max_output_tokens, 6000)
        reasoning_mod = __import__("app.core.llm_runtime.reasoning",
                                   fromlist=["settings"])
        with patch.object(reasoning_mod.settings,
                          "executor_tool_max_output_tokens", 4000):
            policy = resolve_reasoning_policy(
                "executor_tool", has_tools=True, capabilities=caps,
                runtime_mode="adapter")
        self.assertEqual(policy.max_output_tokens, 4000)

    def test_adaptive_summary_is_detailed_for_solve(self):
        from app.agents.reasoning_narrator import build_reasoning_events
        with patch.object(__import__("app.agents.reasoning_narrator", fromlist=["settings"]).settings,
                          "reasoning_summary_level", "adaptive"):
            events = build_reasoning_events(
                TaskUnderstanding(intent=TaskType.SOLVE, concept="牛顿第二定律"),
                TaskPlan(steps=[PlanStep(agent_role="teaching", task="解题")]),
                None)
        self.assertTrue(all(event.level == "detailed" for event in events))
        self.assertIn("条件是否充分", events[-1].content)

    def test_session_learning_card_roundtrip_and_open_loop(self):
        from app.core.session_learning_card import OpenLoop, SessionLearningCard
        card = SessionLearningCard(session_goal="理解浮力",
                                   active_concepts=["浮力"])
        card.upsert_loop(OpenLoop(
            id="assessment:浮力", kind="student_response",
            description="等待学生完成浮力检测题"))
        restored = SessionLearningCard.from_dict(card.to_dict())
        self.assertEqual(restored.session_goal, "理解浮力")
        self.assertEqual(restored.open_loops[0].status, "pending")
        self.assertIn("未完成事项", restored.render())

    def test_quiz_reconciliation_resolves_assessment_loop(self):
        from app.core.session_learning_card import (OpenLoop, SessionLearningCard,
                                                     reconcile_quiz_history)
        card = SessionLearningCard(open_loops=[OpenLoop(
            id="assessment:浮力", kind="student_response",
            description="等待学生完成浮力检测题")])
        history = [{"questions": [{"id": "q1", "stem": "题目",
                                    "result": {"verdict": "correct",
                                               "student_answer": "A"}}]}]
        reconcile_quiz_history(card, history)
        self.assertFalse(card.pending_assessment)
        self.assertEqual(card.open_loops[0].status, "resolved")
        self.assertIn("q1:correct", card.latest_verdicts)

    def test_quiz_tool_projection_gives_bounded_question_digest(self):
        # The model must be able to discuss the quiz it just generated
        # ("解释一下上面那道题"), so the projection carries a bounded digest
        # (truncated stem/answer/explanation) instead of only IDs — while the
        # full payload stays in SSE/quiz_history and out of the model context.
        from app.core.tool_context import project_tool_result
        from app.core.tool_protocol import ok
        long_stem = "这是一段很长的题干" * 50  # > 300 字截断阈值
        result = ok("generate_quiz", data={"questions": [{
            "id": "q1", "stem": long_stem, "answer": "B",
            "knowledge_point": "浮力", "difficulty": "easy",
            "explanation": "完整解析要点",
        }]})
        projection = project_tool_result(result)
        self.assertIn("题目卡已由前端渲染", projection.text)
        self.assertIn("答案:B", projection.text)
        self.assertIn("考点:浮力", projection.text)
        self.assertIn("完整解析要点", projection.text)
        # bounded: the stem is truncated, never the full unbounded body
        self.assertNotIn(long_stem, projection.text)
        self.assertIn("只引导学生先作答", projection.text)
        self.assertGreater(projection.projected_tokens, 0)

    def test_native_tool_shadow_keeps_call_result_pair(self):
        from app.core.message_protocol import build_native_tool_shadow
        shadow = build_native_tool_shadow(
            "准备调用工具", call_id="call_1", tool_name="generate_quiz",
            args={"topic": "浮力"}, result_text="生成 1 道题")
        self.assertTrue(shadow["valid"])
        self.assertEqual(shadow["tool_call_id"], "call_1")
        self.assertEqual(shadow["message_count"], 2)

    def test_runtime_telemetry_aggregates_without_returning_content(self):
        from app.core.context_telemetry import aggregate_runtime_events
        report = aggregate_runtime_events([[{
            "kind": "context_budget", "estimated_input_tokens": 100,
            "tool_schema_tokens": 20, "pressure": "normal",
        }, {"kind": "llm_usage", "prompt_tokens": 100,
             "completion_tokens": 30, "total_tokens": 130},
            {"kind": "tool_context_projection", "original_tokens": 300,
             "projected_tokens": 80},
            {"kind": "reasoning_policy_call", "requested_mode": "none"},
            {"kind": "provider_capabilities", "model": "test"}]])
        self.assertEqual(report["trace_count"], 1)
        self.assertEqual(report["usage"]["avg_prompt_tokens"], 100.0)
        self.assertEqual(report["tool_projection"]["estimated_saved_tokens"], 220.0)
        self.assertEqual(report["reasoning_modes"]["none"], 1)
        self.assertNotIn("content", report)

    def test_generic_tool_projection_preserves_error_outside_projector(self):
        from app.agents.executor import _project_tool_message
        from app.core.tool_protocol import ErrorCode, err
        result = err("knowledge_search", ErrorCode.NOT_FOUND, "没有找到资料")
        text, meta = _project_tool_message(result)
        self.assertIn("恢复建议", text)
        self.assertEqual(meta["tool"], "knowledge_search")

    def test_second_compaction_carries_previous_summary_forward(self):
        class CaptureLLM:
            def __init__(self):
                self.prompt = ""

            async def complete(self, messages, **kwargs):
                self.prompt = "\n".join(str(m.get("content", "")) for m in messages)
                return "合并后的结构化摘要", {}

        llm = CaptureLLM()
        messages = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "PRE"},
            {"role": "system", "content": "[对话压缩摘要（只读，完整历史见 transcript）]\n旧目标=掌握浮力"},
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "最新问题"},
            {"role": "assistant", "content": "最新回答"},
        ]
        compacted, summary = asyncio.run(compact_history(messages, llm, keep_recent=1))
        self.assertEqual(summary, "合并后的结构化摘要")
        self.assertIn("旧目标=掌握浮力", llm.prompt)
        self.assertEqual(compacted[-2]["content"], "最新问题")
        self.assertEqual(compacted[-1]["content"], "最新回答")

    def test_empty_compaction_never_discards_raw_history(self):
        class EmptyLLM:
            async def complete(self, messages, **kwargs):
                return "", {}

        messages = [{"role": "system", "content": "SYS"},
                    {"role": "user", "content": "PRE"}]
        for i in range(4):
            messages.extend([{"role": "user", "content": f"用户{i}"},
                             {"role": "assistant", "content": f"回答{i}"}])
        compacted, summary = asyncio.run(compact_history(
            messages, EmptyLLM(), keep_recent=1))
        self.assertEqual(summary, "")
        self.assertEqual(compacted, messages)

    def test_shadow_reasoning_policy_observes_without_applying_controls(self):
        from app.core.llm_runtime.capabilities import ProviderCapabilities
        from app.core.llm_runtime.reasoning import resolve_reasoning_policy
        caps = ProviderCapabilities(
            provider_id="test", model="test", context_window=32768,
            max_output_tokens=6000, supports_reasoning_effort=True,
            supports_reasoning_budget=True, supports_disable_thinking=True)
        policy = resolve_reasoning_policy(
            "executor_tool", has_tools=True, capabilities=caps,
            runtime_mode="shadow")
        self.assertFalse(policy.controls_applied)
        self.assertFalse(policy.disable_thinking)
        self.assertEqual(policy.reasoning_effort, "")
        self.assertEqual(policy.reasoning_budget_tokens, 0)

    def test_direct_path_recovers_when_reasoning_starves_answer(self):
        from app.agents import executor
        from app.core.session import TutorSession

        class LLM:
            def __init__(self):
                self.calls = 0
                self.disable_seen = []

            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False, **kwargs):
                self.calls += 1
                self.disable_seen.append(disable_thinking)
                if self.calls == 1:
                    yield {"kind": "thinking", "delta": "内部分析" * 20}
                    yield {"kind": "done", "finish_reason": "length", "usage": {}}
                else:
                    yield {"kind": "answer", "delta": "你好，我们可以直接开始学习。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        class TraceStub:
            run_id = "direct_recovery"
            def log(self, *args, **kwargs): pass
            def llm_call(self, *args, **kwargs): pass
            def decision(self, *args, **kwargs): pass
            def summary(self): return {}

        llm = LLM()
        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "你好"}],
                TutorSession(session_id="direct_recovery"), [],
                TaskPlan(steps=[]), llm, TraceStub())]
        with patch.object(executor.settings, "llm_runtime_mode", "adapter"):
            events = asyncio.run(collect())
        self.assertEqual(llm.calls, 2)
        self.assertTrue(llm.disable_seen[-1])
        self.assertIn("直接开始学习", events[-1]["answer"])
        self.assertTrue(any(e.get("type") == "retry" for e in events))

    def test_post_retrieval_direct_path_injects_multimodal_context(self):
        from app.agents import executor
        from app.core import multimodal_context
        from app.core.session import TutorSession

        captured = {}

        class LLM:
            async def stream(self, messages, tools=None, **kwargs):
                captured["messages"] = messages
                yield {"kind": "answer", "delta": "基于教材回答。"}
                yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        class TraceStub:
            run_id = "multimodal_tool_path"

            def log(self, *args, **kwargs):
                pass

            def llm_call(self, *args, **kwargs):
                pass

            def decision(self, *args, **kwargs):
                pass

            def summary(self):
                return {}

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="基于教材讲解",
        )])

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲解教材中的角动量定理"}],
                TutorSession(session_id="multimodal_tool_path"), [], plan,
                LLM(), TraceStub())]

        with patch.object(multimodal_context, "attachment_context_images",
                          return_value=["data:image/png;base64,AAAA"]), \
             patch.object(multimodal_context, "evidence_snapshot_images",
                          return_value=[]), \
             patch.object(multimodal_context, "get_multimodal_llm",
                          return_value=None), \
             patch.object(executor.settings, "skill_runtime_mode", "shadow"):
            events = asyncio.run(collect())

        self.assertFalse(any(event.get("type") == "error" for event in events))
        user_message = next(
            message for message in reversed(captured["messages"])
            if message.get("role") == "user")
        self.assertIsInstance(user_message["content"], list)
        self.assertEqual(user_message["content"][1]["type"], "image_url")

    def test_done_carries_reasoning_from_all_steps(self):
        # real_summary 的材料来源：done.thinking 必须跨 ReAct 步骤累积，
        # 不能只剩最后一步（出题步的推理才是真正的讲解思考）。
        from app.agents import executor
        from app.core.session import TutorSession
        from app.core.tool_protocol import ok

        class _QuizTool:
            name = "generate_quiz"
            parameters = {"type": "object", "properties": {}}

            def to_schema(self):
                return {"name": "generate_quiz", "description": "",
                        "parameters": self.parameters}

            async def run(self, **kwargs):
                return ok("generate_quiz", data={"questions": [{
                    "stem": "s", "answer": "A", "type": "multiple_choice",
                    "options": {"A": "a", "B": "b"},
                    "explanation": "足够长的解析内容，超过十五个字。"}]},
                          text="生成 1 道题")

        class TwoStepLLM:
            def __init__(self):
                self.calls = 0

            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "thinking", "delta": "第一步的讲解推理"}
                    yield {"kind": "answer", "delta": "先讲一半。"}
                    yield {"kind": "tool_calls",
                           "calls": [{"id": "c1", "name": "generate_quiz", "args": {}}]}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}
                else:
                    yield {"kind": "thinking", "delta": "第二步的收尾推理"}
                    yield {"kind": "answer", "delta": "完整答案。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        class TraceStub:
            run_id = "thinking_accum"
            def log(self, *args, **kwargs): pass
            def llm_call(self, *args, **kwargs): pass
            def decision(self, *args, **kwargs): pass
            def summary(self): return {}

        llm = TwoStepLLM()
        plan = TaskPlan(steps=[PlanStep(
            agent_role="assessment", task="出题检测",
            skill_ids=["agent.skill.assessment.generate_practice"])])

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲浮力并调用工具"}],
                TutorSession(session_id="thinking_accum", grade="高中"),
                [_QuizTool()], plan, llm, TraceStub())]
        events = asyncio.run(collect())
        self.assertEqual(llm.calls, 2)
        done = next(e for e in events if e["type"] == "done")
        self.assertIn("第一步的讲解推理", done["thinking"])
        self.assertIn("第二步的收尾推理", done["thinking"])


    def test_recovery_removes_restarted_markdown_sections(self):
        from app.agents.executor import _continuation_suffix
        prefix = "## 一、概念\n已讲完。\n\n## 二、公式\n公式到这里"
        candidate = "抱歉上一轮被截断。\n\n## 二、公式\n重复公式\n\n## 三、含义\n新的内容"
        self.assertEqual(_continuation_suffix(prefix, candidate),
                         "## 三、含义\n新的内容")

    def test_raw_reasoning_is_never_streamed(self):
        # Provider reasoning_content is hidden CoT; only public summaries may
        # reach the browser, regardless of the legacy preview setting.
        from app.agents import executor
        from app.core.session import TutorSession

        class ThinkingLLM:
            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False, **kwargs):
                yield {"kind": "thinking", "delta": "推理" * 500}
                yield {"kind": "thinking", "delta": "继续" * 500}
                yield {"kind": "answer", "delta": "答案。"}
                yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        class TraceStub:
            run_id = "live_prev"
            def log(self, *args, **kwargs): pass
            def llm_call(self, *args, **kwargs): pass
            def decision(self, *args, **kwargs): pass
            def summary(self): return {}

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲浮力"}],
                TutorSession(session_id="live_prev", grade="高中"),
                [], TaskPlan(steps=[]), ThinkingLLM(), TraceStub())]
        with patch.object(executor.settings, "reasoning_live_max_chars", 1200):
            events = asyncio.run(collect())
        live = [e for e in events
                if e["type"] == "thinking" and e.get("stage") == "reasoning"]
        self.assertEqual(live, [])

    def test_live_reasoning_preview_disabled(self):
        from app.agents import executor
        from app.core.session import TutorSession

        class ThinkingLLM:
            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False, **kwargs):
                yield {"kind": "thinking", "delta": "推理" * 100}
                yield {"kind": "answer", "delta": "答案。"}
                yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        class TraceStub:
            run_id = "live_off"
            def log(self, *args, **kwargs): pass
            def llm_call(self, *args, **kwargs): pass
            def decision(self, *args, **kwargs): pass
            def summary(self): return {}

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲浮力"}],
                TutorSession(session_id="live_off", grade="高中"),
                [], TaskPlan(steps=[]), ThinkingLLM(), TraceStub())]
        with patch.object(executor.settings, "reasoning_live_max_chars", -1):
            events = asyncio.run(collect())
        live = [e for e in events
                if e["type"] == "thinking" and e.get("stage") == "reasoning"]
        self.assertEqual(live, [])


if __name__ == "__main__":
    unittest.main()
