"""V1/V2 外部合同一致性（plan.md §30 golden/differential）+ P2-A fallback
结构化观测（plan.md §29）。

golden 断言的是**外部合同**，不是 thinking 文本：
- 两模式最终都产出 done 事件与 answer 增量；
- 工具结果对 session 的持久化（messages / quiz_history）一致；
- 有教材的会话两模式都触发确定性 material grounding 检索。

fallback 断言：
- SUPERVISOR_LEGACY_FALLBACK 未设置（默认关闭）：V2 异常如实 yield error；
- =1：V2 异常可显式回落 V1，作为紧急兼容开关；
- =0：V2 异常如实 yield error，不再静默掩盖回归；
- trace 带结构化字段（exception_type/category/stage/fallback_enabled），
  且不落入 raw 用户消息。
"""
from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase


class _FixedAnswerLLM:
    """稳定文本回答（无 tool call）：V1/V2 都走 direct answer 路径。"""

    def __init__(self):
        self.stream_calls = 0
        self.complete_calls = 0

    async def stream(self, messages, tools=None, temperature=None,
                     max_tokens=None, **kw):
        self.stream_calls += 1
        yield {"kind": "answer", "delta": "ZX-17 定理的右端常数为 314159。"}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"total_tokens": 10}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        self.complete_calls += 1
        return "ZX-17 定理的右端常数为 314159。", {"total_tokens": 10}


def _make_session(sid: str, with_material: bool = False):
    from app.core.session import TutorSession
    session = TutorSession(session_id=sid, grade="本科")
    session.student_id = "student_compat"
    if with_material:
        from app.core.knowledge_store import KnowledgeStore
        session.knowledge = KnowledgeStore()
        session.knowledge.add_file(
            "file_zx", "zx17讲义.txt",
            "第三章 ZX-17 定理\nZX-17 定理的右端常数为 314159。\n" * 10)
        session.pending_material_file_ids = ["file_zx"]
    return session


def _tools(session):
    from app.api.v1.chat import _build_tools
    with unittest.mock.patch("app.core.llm_async.get_llm", lambda: _FixedAnswerLLM()):
        return _build_tools(session, user_message="ZX-17 的右端常数是什么？")


import unittest.mock  # noqa: E402


async def _collect(gen) -> list[dict[str, Any]]:
    out = []
    async for ev in gen:
        out.append(ev)
    return out


class TestV1V2ExternalContract(StorageSandboxTestCase):
    """V1 chat_turn 与 V2 supervisor.run 的外部合同一致性。"""

    def _run(self, mode: str, session, llm, tools):
        from app.agents import chat_agent as ca
        with unittest.mock.patch.dict(os.environ,
                                      {"SUPERVISOR_MODE": mode}):
            return asyncio.run(_collect(ca.run_turn(
                "ZX-17 的右端常数是什么？", session, tools, llm=llm)))

    def test_both_modes_emit_answer_and_done(self):
        for mode, sid in (("v2", "sess_c_v2"), ("legacy", "sess_c_v1")):
            with self.subTest(mode=mode):
                session = _make_session(sid)
                llm = _FixedAnswerLLM()
                tools = _tools(session)
                events = self._run(mode, session, llm, tools)
                kinds = {ev.get("type") for ev in events}
                self.assertIn("done", kinds, f"{mode} 必须产出 done 事件")
                answer_text = "".join(
                    str(ev.get("content") or ev.get("delta") or "")
                    for ev in events if ev.get("type") == "answer")
                self.assertIn("314159", answer_text,
                              f"{mode} 回答必须包含教材事实")

    def test_both_modes_ground_material_when_present(self):
        """有教材 + 内容问题：两模式都触发确定性 material 检索。"""
        for mode, sid in (("v2", "sess_g_v2"), ("legacy", "sess_g_v1")):
            with self.subTest(mode=mode):
                session = _make_session(sid, with_material=True)
                llm = _FixedAnswerLLM()
                tools = _tools(session)
                events = self._run(mode, session, llm, tools)
                tool_events = [ev for ev in events
                               if ev.get("type") in ("tool_result", "tool_start")]
                names = {str(ev.get("tool") or ev.get("name") or "")
                         for ev in tool_events}
                self.assertTrue(
                    any("knowledge" in n for n in names) or
                    any("检索" in str(ev.get("message") or "")
                        for ev in events if ev.get("type") == "progress"),
                    f"{mode} 有教材时必须发生 material 检索（事件面: "
                    f"{[ev.get('type') for ev in events][:12]}）")


class TestLegacyFallbackSwitch(StorageSandboxTestCase):
    """P2-A（plan.md §29）：fallback 默认关闭 + 显式紧急开关 + trace。"""

    def _run_with_broken_v2(self, fallback: str | None):
        from app.agents import chat_agent as ca
        session = _make_session("sess_fback")
        llm = _FixedAnswerLLM()
        tools = _tools(session)

        async def _broken_supervisor(*a, **kw):
            raise RuntimeError("planner boom")
            yield  # pragma: no cover

        with unittest.mock.patch.dict(os.environ, {"SUPERVISOR_MODE": "v2"}), \
             unittest.mock.patch("app.agents.supervisor.run", _broken_supervisor):
            if fallback is None:
                os.environ.pop("SUPERVISOR_LEGACY_FALLBACK", None)
            else:
                os.environ["SUPERVISOR_LEGACY_FALLBACK"] = fallback
            events = asyncio.run(_collect(ca.run_turn(
                "讲个知识点", session, tools, llm=llm)))
        return events

    def test_fallback_default_is_disabled(self):
        events = self._run_with_broken_v2(None)
        kinds = [ev.get("type") for ev in events]
        self.assertIn("error", kinds, "未设置开关时 V2 错误必须显式暴露")
        self.assertNotIn("done", kinds, "默认不得静默切 legacy 掩盖 V2 回归")

    def test_fallback_enabled_returns_legacy_answer(self):
        events = self._run_with_broken_v2("1")
        kinds = {ev.get("type") for ev in events}
        self.assertIn("done", kinds, "显式开启 fallback 时 V1 接管，回答不中断")

    def test_fallback_disabled_yields_error_not_silent(self):
        events = self._run_with_broken_v2("0")
        kinds = [ev.get("type") for ev in events]
        self.assertIn("error", kinds, "fallback 关闭时必须如实 error")
        self.assertNotIn("done", kinds, "不得再切 legacy 掩盖回归")

    def test_trace_has_structured_fields_without_user_message(self):
        captured: dict[str, Any] = {}

        class _Trace:
            def log(self, event, **fields):
                captured["event"] = event
                captured.update(fields)

        from app.agents import chat_agent as ca
        session = _make_session("sess_trace")
        llm = _FixedAnswerLLM()
        tools = _tools(session)

        async def _broken(*a, **kw):
            raise RuntimeError("context stage boom")
            yield  # pragma: no cover

        env = {"SUPERVISOR_MODE": "v2", "SUPERVISOR_LEGACY_FALLBACK": "0"}
        with unittest.mock.patch.dict(os.environ, env), \
             unittest.mock.patch("app.agents.supervisor.run", _broken), \
             unittest.mock.patch("app.agents.chat_agent.Trace", _Trace):
            asyncio.run(_collect(ca.run_turn(
                "这是不该进 trace 的用户消息", session, tools, llm=llm)))
        self.assertEqual(captured.get("event"), "supervisor_fallback_to_legacy")
        self.assertEqual(captured.get("exception_type"), "RuntimeError")
        self.assertIn(captured.get("category"),
                      ("context_error", "unknown"))
        self.assertFalse(captured.get("fallback_enabled"))
        self.assertNotIn("这是不该进 trace 的用户消息",
                         str(captured.get("message", "")),
                         "raw 用户消息不得进入 fallback trace")

    def test_classification_categories(self):
        from app.agents.chat_agent import _classify_supervisor_error

        def _raise_from(module_hint: str):
            try:
                if module_hint == "planner":
                    import app.agents.planner as _m  # noqa: F401
                raise RuntimeError(f"boom in {module_hint}")
            except RuntimeError as e:
                return e

        plain = RuntimeError("plain boom")
        self.assertEqual(_classify_supervisor_error(plain), "unknown")


if __name__ == "__main__":
    unittest.main()
