"""Live thinking stream (REASONING_LIVE_MAX_CHARS) regressions.

Contract under test (DESIGN.md §4.3/§5.1):
  - gate -1 (default): provider reasoning_content streams live as thinking
    events (is_delta, summary=False) on both executor paths;
  - gate 0: hidden-CoT legacy behavior, and the mandatory-material-grounding
    thinking clamp is restored;
  - gate >0: total streamed chars capped;
  - supervisor real_summary digest is skipped whenever live thinking streamed;
  - raw CoT still never persists (session thinking stays the public summary).
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.agents.reasoning_live import LiveThinkingGate  # noqa: E402
from app.core.config import settings  # noqa: E402


class TestLiveThinkingGate(unittest.TestCase):
    def test_unlimited_passes_everything(self):
        gate = LiveThinkingGate(-1)
        self.assertTrue(gate.enabled)
        self.assertEqual(gate.take("abc"), "abc")
        self.assertEqual(gate.take("def"), "def")
        self.assertEqual(gate.emitted, 6)

    def test_cap_slices_and_exhausts(self):
        gate = LiveThinkingGate(5)
        self.assertEqual(gate.take("1234567890"), "12345")
        self.assertEqual(gate.take("x"), "")
        self.assertEqual(gate.emitted, 5)

    def test_off_drops_all(self):
        gate = LiveThinkingGate(0)
        self.assertFalse(gate.enabled)
        self.assertEqual(gate.take("abc"), "")

    def test_junk_config_treated_as_unlimited(self):
        self.assertEqual(LiveThinkingGate("bad").max_chars, -1)  # type: ignore[arg-type]


class _StreamFakeLLM:
    """stream() yields thinking deltas then answer then done; kwargs captured."""

    def __init__(self, thinking: str = "", answer: str = "讲解完成。",
                 tool_first_step: dict[str, Any] | None = None):
        self.thinking = thinking
        self.answer = answer
        self.tool_first_step = tool_first_step  # {"name":..., "args":...}
        self.stream_calls: list[dict[str, Any]] = []

    async def stream(self, messages, **kwargs):
        self.stream_calls.append(kwargs)
        if self.tool_first_step and len(self.stream_calls) == 1:
            if self.thinking:
                yield {"kind": "thinking", "delta": self.thinking}
            yield {"kind": "tool_calls", "calls": [
                {"name": self.tool_first_step["name"],
                 "args": self.tool_first_step["args"]}]}
            yield {"kind": "done", "finish_reason": "tool_calls",
                   "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                             "total_tokens": 10}}
            return
        if self.thinking:
            yield {"kind": "thinking", "delta": self.thinking}
        yield {"kind": "answer", "delta": self.answer}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                         "total_tokens": 15}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        return '{"intent":"explain","concept":"浮力","goal":"understand","requires_tools":false}', {
            "prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


class _AnswerTool:
    name = "knowledge_search"
    description = "fake"
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"}}, "required": ["query"]}

    async def run(self, **kwargs):
        from app.core.tool_protocol import ok
        return ok(self.name, data={"query": kwargs.get("query"),
                                   "results": [], "count": 0},
                  text="未找到")

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


def _run_execute(llm, *, plan, tools, session):
    return _run_execute_user(llm, plan=plan, tools=tools, session=session,
                             user_message="讲一下浮力")


def _run_execute_user(llm, *, plan, tools, session, user_message):
    from app.agents.executor import execute
    from app.core.trace import Trace
    events = []

    async def go():
        async for ev in execute([{"role": "user", "content": user_message}],
                                session, tools, plan, llm, Trace()):
            events.append(ev)

    asyncio.run(go())
    return events


def _live_thinking_text(events) -> str:
    return "".join(str(e.get("content") or "") for e in events
                   if e.get("type") == "thinking" and e.get("is_delta")
                   and not e.get("summary"))


class TestExecutorDirectLiveStream(StorageSandboxTestCase):
    THINKING = "先把问题拆解：浮力的定义、方向、产生原因。" * 5  # ~100 chars

    def _direct_turn(self):
        from app.agents.state import TaskPlan
        from app.core.session import TutorSession
        llm = _StreamFakeLLM(thinking=self.THINKING)
        events = _run_execute(llm, plan=TaskPlan(), tools=[],
                              session=TutorSession(grade="初中"))
        return llm, events

    def test_default_streams_full_thinking(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", -1):
            _llm, events = self._direct_turn()
        self.assertEqual(_live_thinking_text(events), self.THINKING)
        for e in events:
            if e.get("type") == "thinking" and not e.get("summary"):
                self.assertTrue(e.get("is_delta"))

    def test_cap_limits_streamed_chars(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", 10):
            _llm, events = self._direct_turn()
        self.assertLessEqual(len(_live_thinking_text(events)), 10)
        self.assertGreater(len(_live_thinking_text(events)), 0)

    def test_zero_restores_hidden_behavior(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", 0):
            _llm, events = self._direct_turn()
        self.assertEqual(_live_thinking_text(events), "")

    def test_done_event_contract_unchanged_for_direct_path(self):
        # The direct (chitchat) path has always shipped thinking="" in done;
        # live streaming must not change that payload contract.
        with mock.patch.object(settings, "reasoning_live_max_chars", -1):
            _llm, events = self._direct_turn()
        done = [e for e in events if e.get("type") == "done"][-1]
        self.assertEqual(done.get("thinking"), "")


class TestGroundingClampGatedByLiveMode(StorageSandboxTestCase):
    """mandatory_material_grounding force-disable only applies when live=0."""

    def _grounded_react_turn(self):
        from app.core.session import TutorSession
        session = TutorSession(grade="初中")
        session.session_id = "sess_live_" + os.urandom(3).hex()
        session.knowledge.add_file("v1", "物理讲义.pdf", "动量守恒定律 内容 " * 30)
        llm = _StreamFakeLLM(
            thinking="先检索资料。",
            tool_first_step={"name": "knowledge_search",
                             "args": {"query": "动量守恒"}})
        events = _run_execute_user(
            llm, plan=None, tools=[_AnswerTool()], session=session,
            user_message="请结合资料讲解动量守恒定律的应用")
        return llm, events

    def test_live_on_keeps_thinking_enabled_on_grounded_turn(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", -1):
            llm, events = self._grounded_react_turn()
        loop_call = llm.stream_calls[0]
        self.assertNotIn("disable_thinking", loop_call)
        self.assertTrue(_live_thinking_text(events))

    def test_live_off_restores_grounded_disable(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", 0):
            llm, events = self._grounded_react_turn()
        loop_call = llm.stream_calls[0]
        self.assertTrue(loop_call.get("disable_thinking"))
        self.assertEqual(_live_thinking_text(events), "")


class TestSupervisorRealSummarySkip(StorageSandboxTestCase):
    THINKING = ("学生问浮力：先确认已学过压强，再用压力差推导 F=ρgV，"
                "举潜艇与气球的例子，最后安排一道浮沉判断题巩固。" * 5)  # >200 chars

    def _run_supervisor(self):
        from app.agents.supervisor import run
        from app.core.session import TutorSession
        session = TutorSession(grade="初中")
        session.session_id = "sess_sum_" + os.urandom(3).hex()
        llm = _StreamFakeLLM(thinking=self.THINKING)
        events = []

        async def go():
            async for ev in run("讲一下浮力", session, tools=[], llm=llm,
                                lang="zh", output_language=None):
                events.append(ev)

        asyncio.run(go())
        return llm, events, session

    def test_real_summary_skipped_when_live_streamed(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", -1), \
                mock.patch.object(settings, "reasoning_summary_level",
                                  "real_summary"):
            llm, events, session = self._run_supervisor()
        reflection = [e for e in events if e.get("type") == "thinking"
                      and e.get("stage") == "reflection"]
        self.assertEqual(reflection, [])
        # live thinking reached the client through the supervisor
        self.assertTrue(_live_thinking_text(events))
        # persisted thinking stays the public summary, never the raw CoT
        persisted = session.messages[-1]
        self.assertNotEqual(persisted.get("thinking"), self.THINKING)
        self.assertNotIn(self.THINKING[:30], persisted.get("thinking") or "")

    def test_real_summary_runs_when_live_off(self):
        with mock.patch.object(settings, "reasoning_live_max_chars", 0), \
                mock.patch.object(settings, "reasoning_summary_level",
                                  "real_summary"):
            llm, events, session = self._run_supervisor()
        reflection = [e for e in events if e.get("type") == "thinking"
                      and e.get("stage") == "reflection"]
        self.assertTrue(reflection, "real_summary digest should run when "
                                    "nothing was streamed live")


if __name__ == "__main__":
    unittest.main()
