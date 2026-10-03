from __future__ import annotations


import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.support.storage_sandbox import StorageSandboxTestCase
from app.agents.memory import prompt_memory
from app.agents.memory.manager import MemoryService
from app.core import trash
from app.core.memory_safety import memory_safe_text
from app.core.workspace_memory import _render_turn_for_memory
from app.agents import chat_agent
from app.core.session import TutorSession
from app.api.v1 import memory as memory_api
from app.api.v1 import trash as trash_api
from app.api.v1.memory import PromptMemoryWindowRequest
from app.api.v1.trash import RestoreRequest


class PromptMemoryFixture(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        prompt_memory.set_policy(default_window=5, max_window=30,
                                 core_char_limit=900, directive_char_limit=1100)


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

# Related memory safety regressions.


class TestMemorySafety(unittest.TestCase):
    def test_ocr_body_removed_but_instruction_kept(self):
        raw = "<ocr_material>秘密扫描正文\n公式很多</ocr_material>\n\n请讲解第二问"
        safe = memory_safe_text(raw)
        self.assertNotIn("秘密扫描正文", safe)
        self.assertIn("请讲解第二问", safe)

    def test_material_excerpt_removed_from_answer(self):
        safe = memory_safe_text("结论如下 <material_excerpt>教材大段原文</material_excerpt> 学生尚未掌握")
        self.assertNotIn("教材大段原文", safe)
        self.assertIn("学生尚未掌握", safe)

    def test_workspace_memory_render_uses_safe_projection(self):
        rendered = _render_turn_for_memory(
            "<ocr_material>不可跨会话的 OCR</ocr_material>\n\n帮我分析",
            "<material_excerpt>不可持久化的教材原文</material_excerpt>需要复习",
        )
        self.assertNotIn("不可跨会话的 OCR", rendered)
        self.assertNotIn("不可持久化的教材原文", rendered)
        self.assertIn("帮我分析", rendered)
        self.assertIn("需要复习", rendered)


# Related legacy prompt memory regressions.


class TestLegacyPromptMemory(StorageSandboxTestCase):
    def test_legacy_path_reads_and_writes_same_bounded_profile(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(prompt_memory, "_STUDENTS_DIR", Path(tmp)), \
                patch.object(prompt_memory, "_POLICY_PATH", Path(tmp) / "policy.json"):
            s = TutorSession(session_id="legacy-chat", student_id="stu")
            chat_agent._legacy_record_prompt_memory(s, "请一步一步讲", [])
            block = chat_agent._legacy_prompt_memory_block(s)
            self.assertIn("分步骤", block)
            self.assertNotIn("一步一步讲", block)


# Related lifecycle contracts regressions.


class TestLifecycleContracts(unittest.TestCase):
    def test_routes_are_registered_once_and_policy_precedes_dynamic_item_route(self):
        from fastapi.routing import APIRoute
        paths = [(r.path, tuple(sorted(getattr(r, "methods", set()))))
                 for r in trash_api.router.routes if isinstance(r, APIRoute)]
        self.assertIn(("/trash/policy", ("GET",)), paths)
        self.assertIn(("/trash/policy", ("PUT",)), paths)
        self.assertIn(("/trash/{item_id}", ("GET",)), paths)
        policy_index = next(i for i, x in enumerate(paths)
                            if x == ("/trash/policy", ("GET",)))
        item_index = next(i for i, x in enumerate(paths)
                          if x == ("/trash/{item_id}", ("GET",)))
        self.assertLess(policy_index, item_index)
        memory_paths = [r.path for r in memory_api.router.routes if isinstance(r, APIRoute)]
        self.assertEqual(memory_paths.count("/memory/prompt-profile"), 1)

    def test_request_bounds_match_product_policy(self):
        self.assertEqual(PromptMemoryWindowRequest(window_size=15).window_size, 15)
        self.assertEqual(RestoreRequest(workspace_ids=[]).workspace_ids, [])
        with self.assertRaises(Exception):
            PromptMemoryWindowRequest(window_size=4)
        with self.assertRaises(Exception):
            RestoreRequest(workspace_ids=["x"] * 101)


if __name__ == "__main__":
    unittest.main()
