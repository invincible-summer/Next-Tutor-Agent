"""预检索查询精炼（LLM 分析先行）回归。

 Covers the R10 pre-retrieval query-refinement chain:
   understand -> TaskUnderstanding.search_queries -> executor pre_args
   -> KnowledgeSearchTool._query_variants(focus=...)
 and the fallback contract (empty/junk queries keep the raw-message path).
"""
import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.support.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.agents.state import TaskUnderstanding  # noqa: E402
from app.agents.task_understanding import _clean_search_queries, llm_understand  # noqa: E402
from app.tools.knowledge_search import KnowledgeSearchTool  # noqa: E402


class _UnderstandFakeLLM:
    """complete() returns a canned understand-system JSON payload."""

    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        self.calls.append(messages)
        return self.payload, {"prompt_tokens": 5, "completion_tokens": 3,
                              "total_tokens": 8}


class TestSearchQueryCleaning(unittest.TestCase):
    def test_clean_keeps_bounded_terms(self):
        self.assertEqual(
            _clean_search_queries(["湖畔夜色", " 写作手法 ", "湖畔夜色"]),
            ["湖畔夜色", "写作手法"])

    def test_clean_drops_sentences_and_junk(self):
        # A full spoken sentence is exactly what we must NOT search with.
        self.assertEqual(
            _clean_search_queries(["老师请结合资料帮我讲一下湖畔夜色的写作手法好吗"]),
            [])
        self.assertEqual(_clean_search_queries("湖畔夜色"), [])  # wrong type
        self.assertEqual(_clean_search_queries(None), [])
        self.assertEqual(_clean_search_queries([42, "", "  "]), [])
        self.assertEqual(
            _clean_search_queries(["a", "b", "c", "d"]), ["a", "b", "c"])


class TestLLMUnderstandQueries(unittest.TestCase):
    def test_llm_understand_parses_search_queries(self):
        llm = _UnderstandFakeLLM(
            '{"intent":"explain","subject":"语文","concept":"湖畔夜色",'
            '"goal":"understand","requires_tools":false,'
            '"search_queries":["湖畔夜色","写作手法"]}')
        result = asyncio.run(llm_understand("讲讲《湖畔夜色》的写作手法", llm))
        self.assertIsNotNone(result)
        self.assertEqual(result.search_queries, ["湖畔夜色", "写作手法"])

    def test_llm_understand_tolerates_missing_field(self):
        llm = _UnderstandFakeLLM(
            '{"intent":"explain","concept":"浮力","goal":"understand",'
            '"requires_tools":false}')
        result = asyncio.run(llm_understand("讲一下浮力", llm))
        self.assertIsNotNone(result)
        self.assertEqual(result.search_queries, [])

    def test_state_dict_roundtrip(self):
        u = TaskUnderstanding(search_queries=["牛顿第二定律", "惯性"])
        d = u.to_dict()
        self.assertEqual(d["search_queries"], ["牛顿第二定律", "惯性"])
        restored = TaskUnderstanding.from_dict(d)
        self.assertEqual(restored.search_queries, ["牛顿第二定律", "惯性"])
        # junk in persisted dicts is still bounded
        bounded = TaskUnderstanding.from_dict(
            {"search_queries": ["a", "b", "c", "d", ""]})
        self.assertEqual(bounded.search_queries, ["a", "b", "c"])


class TestQueryVariantsFocus(unittest.TestCase):
    RAW = "老师请你帮我讲一下《湖畔夜色》的写作手法好吗"

    def test_focus_terms_take_variant_slots(self):
        variants = KnowledgeSearchTool._query_variants(
            self.RAW, focus=["湖畔夜色", "写作手法"])
        self.assertEqual(variants[:2], ["湖畔夜色", "写作手法"])
        # the raw spoken sentence must never be a variant once focus exists
        self.assertNotIn(self.RAW, variants)
        self.assertLessEqual(len(variants), 3)

    def test_focus_fills_remaining_slot_with_compact_core(self):
        variants = KnowledgeSearchTool._query_variants(
            "请结合这份资料给我解释一下牛顿第二定律的物理意义",
            focus=["牛顿第二定律"])
        self.assertEqual(variants[0], "牛顿第二定律")
        self.assertEqual(len(variants), 3)  # focus + compact + question core
        self.assertNotIn(
            "请结合这份资料给我解释一下牛顿第二定律的物理意义", variants)

    def test_focus_alias_expansion_applies(self):
        variants = KnowledgeSearchTool._query_variants(
            "牛顿第二定律是什么", focus=["牛顿第二定律", "x", "y"])
        # all 3 slots already taken by focus -> no room for alias expansion
        self.assertEqual(variants, ["牛顿第二定律", "x", "y"])
        variants2 = KnowledgeSearchTool._query_variants(
            "牛顿第二定律是什么", focus=["牛顿第二定律"])
        self.assertTrue(any("Newton" in v for v in variants2))

    def test_without_focus_keeps_legacy_raw_first_variant(self):
        variants = KnowledgeSearchTool._query_variants(self.RAW)
        self.assertEqual(variants[0], self.RAW)


class _CapturingKS:
    """Fake knowledge_search tool capturing run() kwargs."""

    name = "knowledge_search"
    description = "fake"
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"}}, "required": ["query"]}

    def __init__(self):
        self.captured: dict[str, Any] = {}

    async def run(self, **kwargs):
        self.captured = dict(kwargs)
        from app.core.tool_protocol import ok
        return ok(self.name,
                  data={"query": kwargs.get("query"), "results": [
                      {"file_id": "v1", "chunk_id": "v1#0", "index": 0,
                       "text": "课文原文片段", "source": "课本.pdf",
                       "evidence_excerpt": "课文原文片段"}],
                      "count": 1},
                  text="从课程资料中筛选出 1 条可靠证据（过滤 0 条）")

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


class TestExecutorPreRetrievalFocus(StorageSandboxTestCase):
    """The executor pre-retrieval must search with the refined term, not the
    raw spoken sentence, and forward the remaining terms as focus_queries."""

    def _run_turn(self, search_queries):
        from app.agents.executor import execute
        from app.agents.state import TaskPlan
        from app.core.session import TutorSession
        from app.core.trace import Trace

        class _DirectFakeLLM:
            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False,
                             reasoning_effort="", reasoning_budget_tokens=0):
                yield {"kind": "answer", "delta": "讲解完成。"}
                yield {"kind": "done", "finish_reason": "stop",
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                 "total_tokens": 15}}

        session = TutorSession(grade="高中")
        session.session_id = "sess_prerefocus_" + __import__("os").urandom(3).hex()
        session.knowledge.add_file("v1", "语文课本.pdf",
                                   "第一章 湖畔夜色\n课文原文 " * 30)
        user_msg = "老师请你帮我讲一下《湖畔夜色》的写作手法好吗"
        messages = [{"role": "user", "content": user_msg}]
        ks = _CapturingKS()
        events = []

        async def go():
            async for ev in execute(messages, session, [ks], TaskPlan(),
                                    _DirectFakeLLM(), Trace(),
                                    search_queries=search_queries):
                events.append(ev)

        asyncio.run(go())
        return ks, events

    def test_pre_retrieval_uses_llm_focus_query(self):
        ks, events = self._run_turn(["湖畔夜色", "写作手法"])
        self.assertTrue(ks.captured, "pre-retrieval should have fired")
        self.assertEqual(ks.captured.get("query"), "湖畔夜色")
        self.assertEqual(ks.captured.get("focus_queries"), ["湖畔夜色", "写作手法"])
        self.assertEqual(ks.captured.get("top_k"), 6)
        tool_starts = [e for e in events if e.get("type") == "tool_start"]
        self.assertTrue(tool_starts and tool_starts[0].get("auto"))

    def test_pre_retrieval_falls_back_to_raw_message_without_focus(self):
        ks, events = self._run_turn([])
        self.assertTrue(ks.captured)
        self.assertIn("湖畔夜色", ks.captured.get("query", ""))
        self.assertNotIn("focus_queries", ks.captured)


if __name__ == "__main__":
    unittest.main()
