"""P6-D 详细召回边界 + bounded prompt memory 测试：

- recall_history：跨会话召回仅在**同工作区**会话间发生；独立对话只检索
  本会话；CROSS_SESSION_MEMORY=off 全关、all 恢复旧行为。
- M6 提示词画像：普通对话与工作区对话统一读取精简画像；详细 transcript
  召回仍只在同一工作区（或 all）发生。
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import context as ctx_mod
from app.core import session as session_mod
from app.core.config import settings
from app.tools.recall_history import RecallHistoryTool
from tests.storage_sandbox import StorageSandboxTestCase


class TestCrossSessionRecallScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            patch.object(ctx_mod, "_TRANSCRIPT_DIR", root / "chat_history"),
            patch.object(session_mod, "_SESSIONS_DIR", root / "chat_history"),
            # RecallHistoryTool 内部落 trace 文件，不 patch 会写进生产 traces/。
            patch.object(settings, "trace_dir", str(root / "traces")),
        ]
        for p in self._patches:
            p.start()
        # 三个会话：s1/s2 同属 ws1，s3 独立（或属其它工作区）
        (root / "chat_history").mkdir(parents=True, exist_ok=True)
        for sid, ws, marker in (("s1", "ws1", "甲"), ("s2", "ws1", "乙"),
                                 ("s3", "", "丙")):
            meta = {"session_id": sid, "student_id": "stu_x",
                    "workspace_id": ws, "title": f"会话{marker}",
                    "updated_at": 1000.0}
            (root / "chat_history" / f"{sid}.session.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            ctx_mod.append_transcript(sid, 1, [
                {"role": "assistant", "content": f"独特的{marker}标记内容 浮力"}])

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def test_workspace_session_recalls_same_workspace_only(self):
        tool = RecallHistoryTool("s1", student_id="stu_x", workspace_id="ws1")
        res = asyncio.run(tool.run(query="浮力"))
        self.assertFalse(res.is_error)
        text = res.text
        self.assertIn("乙标记内容", text)       # 同工作区 s2 可见
        self.assertNotIn("丙标记内容", text)    # 独立会话 s3 不可见

    def test_standalone_session_no_cross_recall(self):
        tool = RecallHistoryTool("s3", student_id="stu_x", workspace_id="")
        res = asyncio.run(tool.run(query="浮力"))
        self.assertFalse(res.is_error)
        self.assertIn("丙标记内容", res.text)   # 本会话
        self.assertNotIn("甲标记内容", res.text)
        self.assertNotIn("乙标记内容", res.text)

    def test_mode_all_restores_old_behavior(self):
        with patch.object(settings, "cross_session_memory", "all"):
            tool = RecallHistoryTool("s3", student_id="stu_x", workspace_id="")
            res = asyncio.run(tool.run(query="浮力"))
            self.assertIn("乙标记内容", res.text)  # 该生其它会话也召回

    def test_mode_off_disables_cross_recall(self):
        with patch.object(settings, "cross_session_memory", "off"):
            tool = RecallHistoryTool("s1", student_id="stu_x", workspace_id="ws1")
            res = asyncio.run(tool.run(query="浮力"))
            self.assertNotIn("乙标记内容", res.text)


class TestMemoryDirectiveGating(StorageSandboxTestCase):
    """精简提示词画像在普通与工作区对话中统一注入。"""

    def _turn(self, workspace_id: str):
        from types import SimpleNamespace
        from app.agents.supervisor import _memory_directive_for_turn
        from app.agents.state import TaskType, TaskUnderstanding
        from app.core.trace import Trace
        u = TaskUnderstanding(intent=TaskType.EXPLAIN, concept="浮力",
                              subject="物理", requires_tools=False)
        session = SimpleNamespace(student_id="stu_x", workspace_id=workspace_id)
        with patch("app.agents.memory.get_memory_service") as gms, \
             patch("app.agents.memory.is_enabled", return_value=True):
            svc = gms.return_value
            svc.build_directive.return_value = "[记忆智能·测试] 过往经验"
            return _memory_directive_for_turn(u, session, Trace())

    def test_standalone_session_keeps_bounded_memory_directive(self):
        self.assertIn("记忆智能", self._turn(""))

    def test_workspace_session_keeps_memory_directive(self):
        self.assertIn("记忆智能", self._turn("ws1"))

    def test_mode_all_keeps_standalone_directive(self):
        with patch.object(settings, "cross_session_memory", "all"):
            self.assertIn("记忆智能", self._turn(""))


if __name__ == "__main__":
    unittest.main()
