"""回归测试：Supervisor 6d/6e/6f/6g 写回钩子不得静默失败。

历史 bug：四个写回钩子（M6 记忆固化 / M7 评估 / M8 UX / M9 编排）里曾有
`from .student_model.store import sid`（store.py 没有 sid 符号），
ImportError 被外层 try/except 吞掉，每轮只在 trace 里留下
memory_consolidate_hook_error / evaluation_hook_error /
ux_record_hook_error / orchestration_record_hook_error，钩子静默不写。

本测试用 fake-LLM 跑一个完整 supervisor turn（fake 模式同
test_supervisor_integration.py），断言 trace 事件中不出现上述四个
hook_error，且 M6 episodic 确实 append 了。存储全量隔离见
tests/storage_sandbox.py（含 prompt_memory 与会话/转写/trace 目录——
历史上漏掉的正是它们）。
"""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.memory import store as mem_store


class FakeLLM:
    """Minimal async LLM stand-in（与 test_supervisor_integration 同款）。

    stream() 吐一段答案后 done；complete() 返回空串，让 task-understanding
    走规则回退（能从「讲一下浮力」抽出 concept=浮力）。"""
    def __init__(self, answer: str = "讲解完成。"):
        self.answer = answer

    async def stream(self, messages, tools=None, temperature=None, max_tokens=None):
        yield {"kind": "answer", "delta": self.answer}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        return "", {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


class TestSupervisorWritebackHooks(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.sid = "hook_reg_" + os.urandom(3).hex()

    def test_writeback_hooks_run_without_error(self):
        from app.agents.supervisor import run
        from app.core.session import TutorSession
        from app.core.trace import trace_dir_path

        session = TutorSession(grade="高中")
        session.session_id = "sess_hook_" + os.urandom(3).hex()
        events = []

        async def go():
            async for ev in run("讲一下浮力", session, tools=[], llm=FakeLLM(),
                                lang="zh", output_language=None,
                                student_id=self.sid):
                events.append(ev)
        asyncio.run(go())

        done = next((e for e in events if e.get("type") == "done"), None)
        self.assertIsNotNone(done, "turn 必须产出 done 事件")

        # trace 事件中不得出现四个写回钩子的 hook_error（历史 bug 的唯一痕迹）
        trace_file = trace_dir_path() / f"trace_{done['trace_id']}.jsonl"
        kinds = [json.loads(line).get("kind")
                 for line in trace_file.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        for bad in ("memory_consolidate_hook_error", "evaluation_hook_error",
                    "ux_record_hook_error", "orchestration_record_hook_error"):
            self.assertNotIn(bad, kinds, f"{bad} 出现说明钩子静默失败: {kinds}")

        # M6 旧 episodic/semantic 文件已收敛为兼容只读；新 turn 不再写详细事件。
        ep_path = mem_store._resolve(self.sid, ext=".episodes.jsonl")
        self.assertFalse(ep_path.exists())

        # M7/M8 的 append-only 黑盒也应各有一条（佐证钩子真的跑了写路径）
        students = self.root / "students"
        for ext in (".eval_traces.jsonl", ".ux_events.jsonl"):
            p = students / f"{self.sid}{ext}"
            self.assertTrue(p.exists() and p.read_text(encoding="utf-8").strip(),
                            f"{ext} 应有内容")


class ThinkingLLM:
    """stream() 先吐 >=200 字真实推理再回答；complete() 返回提炼摘要。"""
    async def stream(self, messages, tools=None, temperature=None, max_tokens=None):
        yield {"kind": "thinking", "delta": "先分析浮力的受力条件与排开液体的体积，" * 15}
        yield {"kind": "answer", "delta": "讲解完成。"}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        return "真实推理提炼：从受力分析入手建立直觉。", \
            {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


class TestRealSummaryReflection(StorageSandboxTestCase):
    """REASONING_SUMMARY_LEVEL=real_summary：真实推理经二次提炼进入 thinking 通道。

    live thinking 流（REASONING_LIVE_MAX_CHARS=-1 默认开）已把真实推理直播给
    前端时，real_summary 会被跳过（见 test_reasoning_live_stream）；本套测试
    钉在 live=0 的隐藏 CoT 模式下验证二次提炼通道本身。"""

    def _run_turn(self, level: str):
        from app.agents.supervisor import run
        from app.core.config import settings
        from app.core.session import TutorSession

        session = TutorSession(grade="高中")
        session.session_id = "sess_rs_" + os.urandom(3).hex()
        events = []

        async def go():
            async for ev in run("讲一下浮力", session, tools=[], llm=ThinkingLLM(),
                                lang="zh", output_language=None,
                                student_id="rs_" + os.urandom(3).hex()):
                events.append(ev)
        with patch.object(settings, "reasoning_summary_level", level), \
                patch.object(settings, "reasoning_live_max_chars", 0):
            asyncio.run(go())
        return events

    def test_real_summary_adds_reflection_stage(self):
        events = self._run_turn("real_summary")
        reflections = [e for e in events
                       if e.get("type") == "thinking" and e.get("stage") == "reflection"]
        self.assertEqual(len(reflections), 1)
        self.assertIn("真实推理提炼", reflections[0]["content"])
        done = next(e for e in events if e.get("type") == "done")
        self.assertIn("真实推理提炼", done.get("thinking", ""))

    def test_adaptive_level_has_no_reflection(self):
        events = self._run_turn("adaptive")
        reflections = [e for e in events
                       if e.get("type") == "thinking" and e.get("stage") == "reflection"]
        self.assertEqual(reflections, [])


if __name__ == "__main__":
    unittest.main()
