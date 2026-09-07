from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents import chat_agent
from app.agents.memory import prompt_memory
from app.core.session import TutorSession


class TestLegacyPromptMemory(unittest.TestCase):
    def test_legacy_path_reads_and_writes_same_bounded_profile(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(prompt_memory, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(prompt_memory, "_POLICY_PATH", Path(tmp) / "policy.json"):
            s = TutorSession(session_id="legacy-chat", student_id="stu")
            chat_agent._legacy_record_prompt_memory(s, "请一步一步讲", [])
            block = chat_agent._legacy_prompt_memory_block(s)
            self.assertIn("分步骤", block)
            self.assertNotIn("一步一步讲", block)


if __name__ == "__main__":
    unittest.main()
