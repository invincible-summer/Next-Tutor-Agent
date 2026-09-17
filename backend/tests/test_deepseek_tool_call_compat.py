"""DeepSeek V4/V4.1 tool-call compatibility regressions.

These tests pin the 2026-09-17 failure where DeepSeek V4.1 Flash produced a
normal reasoning stream but the student-visible answer became raw DSML.  The
contract is provider-boundary normalization: DSML never reaches answer/history
or TTS, authorized calls become the same internal tool_calls event as native
OpenAI-compatible responses, and DeepSeek reasoning_content is replayed only in
the native assistant/tool protocol message.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

from app.agents.pseudo_tool_guard import PseudoToolGuard
from app.core.llm_async import AsyncLLMClient
from app.core.message_protocol import build_openai_tool_messages
from app.core.tool_call_compat import (
    DeepSeekDSMLStreamParser,
    normalize_tool_call_name,
    take_tool_reasoning,
)
from app.prompts.tutor import skill_cards_preamble


TOOLS = [{
    "type": "function",
    "function": {
        "name": "knowledge_search",
        "description": "检索教材",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
}]

V41_DSML = (
    '<｜DSML｜ calls>'
    '<｜DSML｜ invoke name="agent.skill.knowledge.search_materials@1.0.0">'
    '<｜DSML｜ parameter name="query" string="true">质点 平动物体 可以看成质点的条件 大小形状 影响可以忽略</｜DSML｜ parameter>'
    '<｜DSML｜ parameter name="top_k" string="false">8</｜DSML｜ parameter>'
    '</｜DSML｜ invoke>'
    '<｜DSML｜ invoke name="agent.skill.knowledge.search_materials@1.0.0">'
    '<｜DSML｜ parameter name="query" string="true">质点位置矢量 运动函数 参考系 坐标系</｜DSML｜ parameter>'
    '<｜DSML｜ parameter name="top_k" string="false">6</｜DSML｜ parameter>'
    '</｜DSML｜ invoke>'
    '</｜DSML｜ calls>'
)

V41_DOUBLE_BAR = V41_DSML.replace("<｜DSML｜", "<｜｜DSML｜｜").replace(
    "</｜DSML｜", "</｜｜DSML｜｜")
V4_DSML = (
    '<｜DSML｜tool_calls>'
    '<｜DSML｜invoke name="knowledge_search">'
    '<｜DSML｜parameter name="query" string="true">动量守恒</｜DSML｜parameter>'
    '<｜DSML｜parameter name="top_k" string="false">4</｜DSML｜parameter>'
    '</｜DSML｜invoke>'
    '</｜DSML｜tool_calls>'
)


def _feed_chars(parser: DeepSeekDSMLStreamParser, text: str) -> str:
    return "".join(parser.feed(ch) for ch in text) + parser.flush()


class TestDeepSeekDSMLParser(unittest.TestCase):
    def test_v41_character_split_parses_two_calls_and_hides_markup(self):
        parser = DeepSeekDSMLStreamParser()
        visible = _feed_chars(parser, "先查教材。" + V41_DSML)
        self.assertEqual(visible, "先查教材。")
        self.assertTrue(parser.detected)
        self.assertFalse(parser.malformed)
        self.assertEqual(parser.protocol, "deepseek_dsml_v41")
        self.assertEqual(len(parser.calls), 2)
        self.assertEqual(
            parser.calls[0]["name"],
            "agent.skill.knowledge.search_materials@1.0.0")
        self.assertEqual(parser.calls[0]["args"]["top_k"], 8)
        self.assertIsInstance(parser.calls[0]["args"]["top_k"], int)
        self.assertIn("位置矢量", parser.calls[1]["args"]["query"])
        self.assertNotIn("DSML", visible)

    def test_v41_double_bar_gateway_rendering_is_accepted(self):
        parser = DeepSeekDSMLStreamParser()
        visible = _feed_chars(parser, V41_DOUBLE_BAR)
        self.assertEqual(visible, "")
        self.assertEqual(len(parser.calls), 2)
        self.assertEqual(parser.protocol, "deepseek_dsml_v41")

    def test_v4_old_unspaced_format_is_accepted(self):
        parser = DeepSeekDSMLStreamParser()
        visible = _feed_chars(parser, V4_DSML)
        self.assertEqual(visible, "")
        self.assertEqual(parser.protocol, "deepseek_dsml_v4")
        self.assertEqual(parser.calls[0]["name"], "knowledge_search")
        self.assertEqual(parser.calls[0]["args"], {"query": "动量守恒", "top_k": 4})

    def test_incomplete_dsml_fails_closed(self):
        parser = DeepSeekDSMLStreamParser()
        visible = parser.feed("正常前导<｜DSML｜ calls><｜DSML｜ invoke name=\"knowledge_search\">")
        visible += parser.flush()
        self.assertEqual(visible, "正常前导")
        self.assertTrue(parser.detected)
        self.assertTrue(parser.malformed)
        self.assertEqual(parser.calls, [])

    def test_plain_text_is_unchanged(self):
        parser = DeepSeekDSMLStreamParser()
        text = "质点是在研究中忽略大小和形状后的理想化模型。"
        self.assertEqual(_feed_chars(parser, text), text)
        self.assertFalse(parser.detected)


class TestToolNameNormalization(unittest.TestCase):
    def test_skill_id_resolves_via_registry_only_when_tool_is_authorized(self):
        resolved, source = normalize_tool_call_name(
            "agent.skill.knowledge.search_materials@1.0.0", TOOLS)
        self.assertEqual(resolved, "knowledge_search")
        self.assertEqual(source, "skill_registry")

        unresolved, source = normalize_tool_call_name(
            "agent.skill.knowledge.search_materials@1.0.0", [])
        self.assertEqual(unresolved, "agent.skill.knowledge.search_materials@1.0.0")
        self.assertEqual(source, "unresolved")

    def test_namespace_suffix_cannot_bypass_visible_tool_schema(self):
        resolved, source = normalize_tool_call_name("edu::knowledge_search", TOOLS)
        self.assertEqual((resolved, source), ("knowledge_search", "namespace_suffix"))
        unresolved, source = normalize_tool_call_name("edu::knowledge_search", [])
        self.assertEqual((unresolved, source), ("edu::knowledge_search", "unresolved"))

    def test_skill_card_states_actual_function_name(self):
        card = skill_cards_preamble(["agent.skill.knowledge.search_materials"])
        self.assertIn("执行工具=knowledge_search", card)
        self.assertIn("不是 function 名", card)
        self.assertIn("tools schema", card)


class TestPseudoToolGuardDSML(unittest.TestCase):
    def test_dsml_never_reaches_fallback_answer_stream(self):
        guard = PseudoToolGuard()
        visible = "".join(guard.feed(ch) for ch in "我先查教材。" + V41_DSML)
        visible += guard.flush()
        self.assertEqual(visible, "我先查教材。")
        self.assertTrue(guard.detected)
        self.assertIn("质点", guard.extract_query("fallback"))
        self.assertEqual(
            guard.extract_tool_name(),
            "agent.skill.knowledge.search_materials@1.0.0")
        self.assertNotIn("DSML", visible)


class _AsyncChunkStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _AsyncChunkStream(self.chunks)


def _chunk(*, content=None, reasoning=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=[],
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(usage=None, choices=[choice])


def _fake_llm(chunks) -> AsyncLLMClient:
    # Avoid constructing a real HTTP client: stream() only needs these fields.
    llm = object.__new__(AsyncLLMClient)
    llm.model = "deepseek-flash"
    llm.max_tokens = 4000
    llm.temperature = 0.3
    llm.base_url = "https://api.deepseek.com"
    llm._retry_max = 1
    llm._retry_base_delay = 0.01
    llm._semaphore = asyncio.Semaphore(1)
    completions = _FakeCompletions(chunks)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm


class TestAsyncLLMDeepSeekIntegration(unittest.TestCase):
    def test_raw_dsml_becomes_tool_calls_and_reasoning_is_replayed(self):
        # Split the DSML in awkward boundaries so this exercises the stream
        # parser rather than a whole-string shortcut.
        cuts = [V41_DSML[:17], V41_DSML[17:73], V41_DSML[73:211], V41_DSML[211:]]
        chunks = [
            _chunk(reasoning="我要先检索教材。"),
            _chunk(content="检索后再回答。"),
            *[_chunk(content=part) for part in cuts],
            _chunk(finish_reason="stop"),
        ]
        llm = _fake_llm(chunks)

        async def collect():
            return [event async for event in llm.stream(
                messages=[{"role": "user", "content": "什么是质点"}],
                tools=TOOLS,
            )]

        events = asyncio.run(collect())
        visible = "".join(e.get("delta", "") for e in events
                          if e.get("kind") == "answer")
        self.assertEqual(visible, "检索后再回答。")
        self.assertNotIn("DSML", visible)

        tool_event = next(e for e in events if e.get("kind") == "tool_calls")
        self.assertEqual(tool_event["protocol"], "deepseek_dsml_v41")
        self.assertEqual(len(tool_event["calls"]), 2)
        first = tool_event["calls"][0]
        self.assertEqual(first["name"], "knowledge_search")
        self.assertEqual(first["raw_name"],
                         "agent.skill.knowledge.search_materials@1.0.0")
        self.assertEqual(first["args"]["top_k"], 8)

        done = next(e for e in events if e.get("kind") == "done")
        self.assertEqual(done["finish_reason"], "tool_calls")

        exchange = build_openai_tool_messages(
            "检索后再回答。", call_id=first["id"],
            tool_name=first["name"], args=first["args"],
            result_text="[工具 knowledge_search 完成]")
        self.assertEqual(exchange[0]["reasoning_content"], "我要先检索教材。")
        self.assertEqual(exchange[0]["tool_calls"][0]["function"]["name"],
                         "knowledge_search")
        self.assertEqual(exchange[1]["role"], "tool")
        # The transient value is one-shot and therefore cannot accidentally be
        # persisted/replayed into unrelated future messages.
        self.assertEqual(take_tool_reasoning(first["id"]), "")


if __name__ == "__main__":
    unittest.main()
