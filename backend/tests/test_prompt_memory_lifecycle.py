from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.memory import prompt_memory
from app.agents.memory.manager import MemoryService
from app.core import learning_records, quiz_recent, trash
from app.core.error_notebook import collect_error_notebook


class PromptMemoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prompt_memory_")
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(prompt_memory, "_STUDENTS_DIR", self.root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH", self.root / "students" / "policy.json"),
            patch.object(quiz_recent, "_STUDENTS_DIR", self.root / "students"),
            patch.object(learning_records, "_STUDENTS_DIR", self.root / "students"),
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
                    "current_level": "基础阶段",
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


class TestLearningRecordSourceLifecycle(PromptMemoryFixture):
    def test_deleted_chat_keeps_full_attempt_but_disables_jump(self):
        quiz_recent.record_recent_quiz("chat1", "stu", {
            "topic": "力学", "questions": [{
                "stem": "物体为何保持匀速？", "type": "short_answer",
                "difficulty": "medium", "knowledge_point": "惯性",
                "answer": "合外力为零", "explanation": "牛顿第一定律",
            }]})
        from app.core.learning_records import record_question, record_verdict
        record_question("stu", "chat1", {
            "stem": "物体为何保持匀速？", "type": "short_answer",
            "difficulty": "medium", "knowledge_point": "惯性",
            "answer": "合外力为零", "explanation": "牛顿第一定律",
        })
        record_verdict("stu", "chat1", stem="物体为何保持匀速？",
                       verdict="wrong", student_answer="因为没有速度")
        quiz_recent.record_recent_verdict(
            "chat1", "stu", stem="物体为何保持匀速？",
            verdict="wrong", student_answer="因为没有速度")
        self.assertEqual(quiz_recent.mark_session_source_deleted("stu", "chat1"), 1)
        from app.core.learning_records import mark_source_deleted, mark_source_active
        self.assertGreaterEqual(mark_source_deleted("stu", "chat1"), 1)
        items = collect_error_notebook("stu")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["source_status"], "deleted")
        self.assertEqual(item["session_id"], "")
        self.assertEqual(item["source_message"], "来源对话已删除，无法查看")
        self.assertEqual(item["student_answer"], "因为没有速度")
        self.assertEqual(item["correct_answer"], "合外力为零")
        self.assertEqual(item["knowledge_point"], "惯性")
        self.assertEqual(quiz_recent.mark_session_source_active("stu", "chat1"), 1)
        self.assertGreaterEqual(mark_source_active("stu", "chat1"), 1)
        self.assertEqual(collect_error_notebook("stu")[0]["session_id"], "chat1")


if __name__ == "__main__":
    unittest.main()

class TestPermanentSourceDetachment(PromptMemoryFixture):
    def test_permanent_detach_clears_internal_chat_id_but_keeps_outcome(self):
        from app.core.learning_records import (detach_source_session, list_records,
                                                record_question, record_verdict)
        record_question("stu", "chat1", {"id": "q1", "stem": "题目",
                                           "answer": "A", "knowledge_point": "函数"})
        record_verdict("stu", "chat1", stem="题目", verdict="wrong",
                       student_answer="B")
        self.assertEqual(detach_source_session("stu", "chat1"), 1)
        row = list_records("stu")[0]
        self.assertEqual(row["session_id"], "")
        self.assertEqual(row["source_status"], "deleted")
        self.assertEqual(row["student_answer"], "B")
        self.assertEqual(row["correct_answer"], "A")


class TestIndependentLearningLedger(PromptMemoryFixture):
    def test_ledger_survives_source_deletion_and_is_not_capped_at_recent_ui_limit(self):
        from app.core.learning_records import list_records, mark_source_deleted
        from app.core.learning_records import record_question, record_verdict
        for i in range(105):
            sid = f"chat-{i}"
            record_question("stu", sid, {"id": f"q-{i}", "stem": f"题目 {i}",
                                           "answer": "正确答案", "knowledge_point": "知识点"})
            record_verdict("stu", sid, stem=f"题目 {i}", verdict="wrong",
                           student_answer="学生答案")
        self.assertEqual(len(list_records("stu")), 105)
        self.assertEqual(mark_source_deleted("stu", "chat-104"), 1)
        row = next(x for x in list_records("stu") if x["session_id"] == "chat-104")
        self.assertEqual(row["source_status"], "deleted")
        self.assertEqual(row["student_answer"], "学生答案")

class TestAssessmentLedger(PromptMemoryFixture):
    def test_assessment_records_use_durable_ledger_identity(self):
        from app.core.learning_records import record_question, record_verdict, list_records
        record_question("stu", "assessment:stu", {"id": "cat-q", "stem": "CAT 题", "answer": "A", "knowledge_point": "函数"}, topic="诊断", source_kind="assessment")
        record_verdict("stu", "assessment:stu", stem="CAT 题", verdict="wrong", student_answer="B", score=0.0, concept="函数", source_kind="assessment")
        rows = list_records("stu")
        self.assertEqual(rows[0]["session_id"], "assessment:stu")
        self.assertEqual(rows[0]["knowledge_point"], "函数")
        self.assertEqual(rows[0]["score"], 0.0)
