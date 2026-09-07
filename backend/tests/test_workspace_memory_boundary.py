from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import workspace, workspace_memory


class TestWorkspaceMemoryBoundary(unittest.TestCase):
    def test_new_session_compacts_once_and_stays_workspace_local(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(workspace, "_WORKSPACES_DIR", Path(tmp) / "workspaces"):
            ws = workspace.Workspace(
                workspace_id="ws1", student_id="stu1", name="课程项目",
                public_memory="知识点：旧内容\n薄弱点：需要复习")
            workspace.save_workspace(ws)

            class LLM:
                calls = 0
                async def complete(self, messages, **kwargs):
                    self.calls += 1
                    return "知识点：压缩后内容\n薄弱点：需要复习", {}

            llm = LLM()
            first = asyncio.run(workspace_memory.compact_workspace_memory_on_new_session(
                "ws1", "chat1", llm=llm))
            second = asyncio.run(workspace_memory.compact_workspace_memory_on_new_session(
                "ws1", "chat1", llm=llm))
            self.assertEqual(first["status"], "compacted")
            self.assertEqual(second["status"], "already_done")
            self.assertEqual(llm.calls, 1)
            restored = workspace.load_workspace("ws1")
            self.assertEqual(restored.public_memory, "知识点：压缩后内容\n薄弱点：需要复习")
            self.assertEqual(restored.memory_boundary_sessions, ["chat1"])


if __name__ == "__main__":
    unittest.main()
