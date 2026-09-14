from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.memory import prompt_memory
from app.agents.memory.manager import MemoryService
from app.core import trash


class PromptMemoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prompt_memory_")
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(prompt_memory, "_STUDENTS_DIR", self.root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH", self.root / "students" / "policy.json"),
            patch.object(trash, "_TRASH_DIR", self.root / "trash"),
            patch.object(trash, "_GLOBAL_POLICY", self.root / "trash" / "policy.json"),
        ]
        for p in self.patches:
            p.start()
        prompt_memory.set_policy(default_window=5, max_window=30,
                                 core_char_limit=900, directive_char_limit=1100)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class TestPromptMemoryWindow(PromptMemoryFixture):
    def test_recent_contributions_are_global_bounded_and_content_free(self):
        for i in range(7):
            sid = f"chat-{i}"
            prompt_memory.register_session("stu", sid, "ws1" if i % 2 else "")
            prompt_memory.record_contribution(
                "stu", sid, workspace_id="ws1" if i % 2 else "",
                events=[{"event_type": "quiz_wrong"}],
                user_message="请一步一步讲这道绝密微积分题", strategy_outcome="wrong")
        state = prompt_memory.load_state("stu")
        self.assertEqual(len(state["recent_sessions"]), 5)
        self.assertEqual(state["compacted_session_count"], 2)
        directive = prompt_memory.build_directive("stu")
        self.assertIn("分步骤", directive)
        self.assertNotIn("微积分", directive)
        self.assertNotIn("绝密", directive)
        self.assertLessEqual(len(directive), 1100)

    def test_recent_can_be_forgotten_but_compacted_cannot(self):
        for i in range(6):
            sid = f"s{i}"
            prompt_memory.register_session("stu", sid)
            prompt_memory.record_contribution("stu", sid, strategy_outcome="correct")
        self.assertEqual(prompt_memory.forget_session_contribution("stu", "s5"), "forgotten")
        self.assertEqual(prompt_memory.forget_session_contribution("stu", "s0"),
                         "compacted_unavailable")

    def test_compacted_status_is_session_specific_and_purge_unlinks_identity(self):
        for i in range(6):
            sid = f"s{i}"
            prompt_memory.register_session("stu", sid)
            prompt_memory.record_contribution("stu", sid, strategy_outcome="correct")
        self.assertEqual(prompt_memory.session_forget_status("stu", "s0"),
                         "compacted")
        self.assertEqual(prompt_memory.session_forget_status("stu", "unrelated"),
                         "none")
        self.assertEqual(prompt_memory.forget_session_contribution("stu", "s0"),
                         "compacted_unavailable")
        self.assertEqual(prompt_memory.session_forget_status("stu", "s0"),
                         "legacy_unknown")

    def test_legacy_count_is_reported_as_unknown_not_falsely_attributed(self):
        state = prompt_memory.load_state("stu")
        state["compacted_session_count"] = 2
        state["compacted_session_ids"] = []
        prompt_memory.save_state("stu", state)
        self.assertEqual(prompt_memory.session_forget_status("stu", "old-chat"),
                         "legacy_unknown")

    def test_restore_does_not_recreate_forgotten_contribution(self):
        prompt_memory.register_session("stu", "s1")
        prompt_memory.record_contribution("stu", "s1", user_message="请温柔一点")
        self.assertEqual(prompt_memory.forget_session_contribution("stu", "s1"), "forgotten")
        prompt_memory.register_session("stu", "s1")
        view = prompt_memory.public_view("stu")
        item = next(x for x in view["recent_sessions"] if x["session_id"] == "s1")
        self.assertFalse(item["has_contribution"])

    def test_llm_compaction_hard_caps_and_is_generation_guarded(self):
        for i in range(6):
            prompt_memory.register_session("stu", f"s{i}")
            prompt_memory.record_contribution("stu", f"s{i}", strategy_outcome="wrong")

        class LLM:
            async def complete(self, messages, **kwargs):
                return json.dumps({
                    "learning_summary": "总体需要巩固" * 200,
                    "tone_preference": "耐心",
                    "explanation_preference": "分步骤",
                }, ensure_ascii=False), {}

        out = asyncio.run(prompt_memory.maybe_compact_core("stu", LLM()))
        self.assertEqual(out["status"], "compacted")
        state = prompt_memory.load_state("stu")
        self.assertFalse(state["core_needs_llm"])
        self.assertLessEqual(len(json.dumps(state["core_profile"], ensure_ascii=False)), 900)

    def test_manager_does_not_inject_detailed_learning_records(self):
        service = MemoryService()
        service.consume_turn(
            student_id="stu", session_id="s1",
            events=[{"type": "quiz_graded", "payload": {
                "concept": "积分换元法", "correct": False, "note": "具体错题细节"}}],
            user_message="请简洁一点", strategy_outcome="wrong")
        directive = service.build_directive(student_id="stu", concept="积分换元法", subject="数学")
        self.assertIn("简洁", directive)
        self.assertNotIn("积分换元法", directive)
        self.assertNotIn("具体错题细节", directive)



