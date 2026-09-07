"""伪工具标签护栏（pseudo_tool_guard）单元契约。

弱模型把工具调用"叙述"成正文时（两种已观测格式：假
`<knowledge_search>` 标签、`<tool_call><function=…><parameter=…>` XML
叙述），标签形成即停止转发、前导正文照常流出、可提取检索词；末尾
附一条 chat_agent 级集成回归（2026-08-31「角动量守恒」泄漏）。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.agents.pseudo_tool_guard import PseudoToolGuard  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


def feed_all(guard: PseudoToolGuard, text: str) -> str:
    out = []
    for i in range(len(text)):
        out.append(guard.feed(text[i]))
    return "".join(out)


XML_NARRATION = (
    "我先检索一下教材。<tool_call>\n<function=knowledge_search>\n"
    "<parameter=keywords>角动量守恒定律 合外力矩为零 固定轴 刚体</parameter>\n"
    '<parameter=content_types>["textbook"]</parameter>\n'
    "<parameter=max_results>5</parameter>\n</function>\n</tool_call>")


class TestPseudoToolGuard(unittest.TestCase):
    def test_plain_text_passes_through(self):
        g = PseudoToolGuard()
        self.assertEqual(feed_all(g, "导数是瞬时变化率。"), "导数是瞬时变化率。")
        self.assertFalse(g.detected)
        self.assertEqual(g.flush(), "")

    def test_tag_detected_and_stripped_from_stream(self):
        g = PseudoToolGuard()
        visible = feed_all(
            g, "我先检索一下教材。\n<knowledge_search>\n  检索关键词：导数 定义\n</knowledge_search>")
        self.assertTrue(g.detected)
        self.assertEqual(visible, "我先检索一下教材。\n")
        # 命中后再喂入的文本一律不再转发
        self.assertEqual(g.feed("后续内容"), "")
        self.assertEqual(g.flush(), "")

    def test_xml_tool_call_format_detected_and_stripped(self):
        # 2026-08-31 回归：deepseek-v4-flash 把调用叙述成 XML 正文，旧版
        # 只匹配 <knowledge_search，整段标记流进了聊天和 TTS。
        g = PseudoToolGuard()
        visible = feed_all(g, XML_NARRATION)
        self.assertTrue(g.detected)
        self.assertEqual(visible, "我先检索一下教材。")
        self.assertEqual(g.feed("后续内容"), "")
        self.assertEqual(g.flush(), "")

    def test_bare_function_tag_detected_mid_text(self):
        g = PseudoToolGuard()
        visible = feed_all(g, "正文提到<function=quiz_generate>生成习题")
        self.assertTrue(g.detected)
        self.assertEqual(visible, "正文提到")

    def test_partial_tool_call_prefix_held(self):
        g = PseudoToolGuard()
        self.assertEqual(feed_all(g, "说明 <tool_c"), "说明 ")
        self.assertFalse(g.detected)
        out = g.feed("all>")
        self.assertTrue(g.detected)
        self.assertEqual(out, "")

    def test_partial_prefix_held_then_released(self):
        g = PseudoToolGuard()
        # 半截标签前缀 "<knowled" 持有不转发，之前的正文照常放行
        self.assertEqual(feed_all(g, "正文 <knowled"), "正文 ")
        self.assertFalse(g.detected)
        # 后续字符 "<knowledg！" 无法成为标签前缀，持有段原样释放
        out = g.feed("g！")
        self.assertEqual(out, "<knowledg！")
        self.assertFalse(g.detected)
        self.assertEqual(g.flush(), "")

    def test_extract_query_from_tag_content(self):
        g = PseudoToolGuard()
        feed_all(g, "<knowledge_search>\n  检索关键词：导数 定义\n</knowledge_search>")
        self.assertEqual(g.extract_query("用户的问题"), "导数 定义")

    def test_extract_query_from_xml_keywords_parameter(self):
        g = PseudoToolGuard()
        feed_all(g, XML_NARRATION)
        self.assertEqual(g.extract_query("用户的问题"),
                         "角动量守恒定律 合外力矩为零 固定轴 刚体")

    def test_extract_query_fallback_to_user_message(self):
        g = PseudoToolGuard()
        feed_all(g, "<knowledge_search>随便写的说明文字很长很长超过一百二十个字符" + "x" * 200)
        self.assertEqual(g.extract_query("导数高中要学点什么"), "导数高中要学点什么")

    def test_unicode_angle_brackets_not_triggered(self):
        g = PseudoToolGuard()
        visible = feed_all(g, "全角标签＜knowledge_search＞不是伪标签")
        self.assertFalse(g.detected)
        self.assertEqual(visible, "全角标签＜knowledge_search＞不是伪标签")


class _NarratingLLM:
    """第一轮把工具调用叙述成 XML 正文（按 7 字符 delta 切开，模拟真实
    流式边界把标签撕成碎片），注入检索后正常作答。"""

    def __init__(self):
        self.contexts: list[list[dict]] = []
        self.calls = 0

    async def stream(self, messages, tools=None, temperature=None):
        self.contexts.append([dict(m) for m in messages])
        self.calls += 1
        if self.calls == 1:
            for i in range(0, len(XML_NARRATION), 7):
                yield {"kind": "answer", "delta": XML_NARRATION[i:i + 7]}
        else:
            yield {"kind": "answer", "delta": "角动量守恒的条件是合外力矩为零。"}
        yield {"kind": "done", "finish_reason": "stop", "usage": {}}


class TestPseudoToolGuardChatIntegration(StorageSandboxTestCase):
    def test_xml_narrated_tool_call_never_reaches_user_or_history(self):
        from app.agents import chat_agent
        from app.core.knowledge_store import KnowledgeStore
        from app.core.session import TutorSession
        from app.tools.knowledge_search import KnowledgeSearchTool

        llm = _NarratingLLM()
        store = KnowledgeStore()
        store.add_file("fid1", "物理讲义.txt",
                       "角动量守恒定律：刚体定轴转动时，合外力矩为零则角动量守恒。")
        session = TutorSession(session_id="s_guard", knowledge=store)

        async def _collect():
            return [ev async for ev in chat_agent.chat_turn(
                "角动量守恒的条件是什么", session,
                [KnowledgeSearchTool(session.knowledge)], llm=llm)]

        with patch.object(chat_agent, "save_session"), \
                patch.object(chat_agent, "_persist_turn"), \
                patch.object(chat_agent, "Trace") as trace_cls:
            trace_cls.return_value.run_id = "t0"
            trace_cls.return_value.summary.return_value = {}
            events = asyncio.run(_collect())

        # 用户可见的 answer 流绝不含标记原文，只含前导正文与真回答
        answers = "".join(e.get("content", "") for e in events
                          if e["type"] == "answer")
        for markup in ("<tool_call", "<function", "<parameter", "</"):
            self.assertNotIn(markup, answers, f"markup leaked: {markup}")
        self.assertIn("我先检索一下教材。", answers)
        self.assertIn("角动量守恒的条件是合外力矩为零。", answers)

        # 命中后执行真实检索（带 auto 标记），检索词来自 keywords 参数；
        # 它出现在首个 answer 之后（叙述发生在流中——R10 预检索在更早处）。
        guard_starts = [e for e in events if e["type"] == "tool_start"
                        and e.get("name") == "knowledge_search"
                        and e.get("args", {}).get("query")
                        == "角动量守恒定律 合外力矩为零 固定轴 刚体"]
        self.assertEqual(len(guard_starts), 1)
        self.assertTrue(guard_starts[0].get("auto"))
        first_answer = next(i for i, e in enumerate(events)
                            if e["type"] == "answer")
        self.assertGreater(events.index(guard_starts[0]), first_answer)
        # 注入的检索结果进入第二轮上下文
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[1])
        self.assertIn("角动量守恒", ctx_text)

        # 落盘的 assistant 消息同样不含标记
        persisted = [m for m in session.messages
                     if m.get("role") == "assistant"]
        self.assertTrue(persisted)
        for m in persisted:
            content = str(m.get("content", ""))
            for markup in ("<tool_call", "<function", "<parameter"):
                self.assertNotIn(markup, content)


if __name__ == "__main__":
    unittest.main()
