"""Tests for the Bloom cognitive layer (L1/L2).

Covers: the shared vocabulary module (normalize/guidance — prompt material,
never a gate), the cognitive profile aggregation over the learning ledger
(attempts/correct/weaknesses/legacy compatibility), the ledger's additive
bloom_level field, and the M4 generator's tag pass-through.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase
from app.core import bloom, bloom_profile
from app.core import learning_records as lr
from app.agents.assessment.question import Question
from app.agents.assessment.state import AssessmentGoal, AssessmentContext


class TestBloomVocabulary(unittest.TestCase):

    def test_normalize_aliases(self):
        self.assertEqual(bloom.normalize_level("apply"), "apply")
        self.assertEqual(bloom.normalize_level("应用"), "apply")
        self.assertEqual(bloom.normalize_level("记忆"), "remember")
        self.assertEqual(bloom.normalize_level("auto"), "")
        self.assertEqual(bloom.normalize_level(""), "")
        self.assertEqual(bloom.normalize_level("mystery"), "")
        self.assertEqual(bloom.normalize_level(None), "")

    def test_guidance_block_auto_is_free_not_gated(self):
        block = bloom.guidance_block()
        self.assertIn("自由选择", block)          # LLM decides, no ladder
        self.assertIn("bloom_level", block)        # output tag instruction
        self.assertNotIn("必须", block.split("各层级风格参照")[0].replace("必须检测", ""))

    def test_guidance_block_focus_and_context(self):
        block = bloom.guidance_block(focus="分析",
                                     context_line="导数·应用 3 次对 1 次")
        self.assertIn("分析", block)
        self.assertIn("导数·应用", block)


class TestBloomProfile(StorageSandboxTestCase):

    SID = "sandbox_bloom_student"

    def _seed(self) -> None:
        # 导数：apply 2 对 0（弱）、understand 2 对 2；函数：remember 1 对 1
        rows = [
            ("lr_a", "导数", "apply", "wrong"),
            ("lr_b", "导数", "apply", "wrong"),
            ("lr_c", "导数", "understand", "correct"),
            ("lr_d", "导数", "understand", "correct"),
            ("lr_e", "函数", "remember", "correct"),
        ]
        for rid, kp, lv, verdict in rows:
            lr.record_question(self.SID, "s1", {
                "id": rid, "stem": f"q-{rid}", "answer": "a",
                "knowledge_point": kp, "bloom_level": lv})
            lr.record_verdict(self.SID, "s1", stem=f"q-{rid}", verdict=verdict,
                              concept=kp)

    def test_profile_aggregates_per_concept_level(self):
        self._seed()
        prof = bloom_profile.profile_for(self.SID)
        self.assertEqual(prof["totals"]["tagged"], 5)
        deriv = prof["concepts"]["导数"]["levels"]
        self.assertEqual(deriv["apply"]["attempts"], 2)
        self.assertEqual(deriv["understand"]["correct"], 2.0)
        weak = prof["weaknesses"]
        self.assertEqual(len(weak), 1)                 # only apply@导数 is weak
        self.assertEqual(weak[0]["concept"], "导数")
        self.assertEqual(weak[0]["level"], "apply")

    def test_context_line_and_weakness_lines(self):
        self._seed()
        ctx = bloom_profile.context_line(self.SID, "导数")
        self.assertIn("导数", ctx)
        self.assertIn("应用", ctx)
        lines = bloom_profile.weakness_lines(self.SID)
        self.assertTrue(any("导数" in x for x in lines))

    def test_untagged_legacy_records_counted_but_not_in_levels(self):
        lr.record_question(self.SID, "s2", {
            "id": "old1", "stem": "旧题", "answer": "a", "knowledge_point": "旧概念"})
        lr.record_verdict(self.SID, "s2", stem="旧题", verdict="wrong",
                          concept="旧概念")
        prof = bloom_profile.profile_for(self.SID)
        self.assertEqual(prof["totals"]["records"], 1)
        self.assertEqual(prof["totals"]["tagged"], 0)
        self.assertEqual(prof["concepts"], {})

    def test_empty_student_never_raises(self):
        prof = bloom_profile.profile_for("nobody")
        self.assertEqual(prof["weaknesses"], [])
        self.assertEqual(bloom_profile.context_line("nobody"), "")
        self.assertEqual(bloom_profile.weakness_lines("nobody"), [])


class TestBloomLedgerAndQuestion(unittest.TestCase):

    def test_question_roundtrips_bloom_level(self):
        q = Question.from_quiz_dict({
            "id": 1, "stem": "s", "answer": "a", "type": "short_answer",
            "bloom_level": "分析"})
        self.assertEqual(q.bloom_level, "analyze")
        self.assertEqual(q.to_dict()["bloom_level"], "analyze")
        # legacy dict without the key stays untagged
        q2 = Question.from_quiz_dict({"id": 2, "stem": "s", "answer": "a"})
        self.assertEqual(q2.bloom_level, "")

    def test_goal_bloom_focus_default_compatible(self):
        # AssessmentGoal(**g) 重建：旧会话 dict 无 bloom_focus 不炸
        g = AssessmentGoal(concept="导数", purpose="adaptive").to_dict()
        del g["bloom_focus"]
        restored = AssessmentGoal(**g)
        self.assertEqual(restored.bloom_focus, "")
        goal = AssessmentGoal(concept="导数", bloom_focus="评价")
        self.assertEqual(goal.to_dict()["bloom_focus"], "评价")


class TestGeneratorBloomInjection(unittest.TestCase):

    def test_gen_prompt_contains_bloom_guidance(self):
        from app.agents.assessment import generator
        goal = AssessmentGoal(concept="导数", bloom_focus="应用")
        ctx = AssessmentContext(concept="导数", grade="高中")
        prompt = generator._build_gen_prompt(
            grade="高中", concept="导数", difficulty=3,
            goal=goal, q_type="short_answer", bloom_context="导数·应用 2 次对 0 次")
        self.assertIn("认知层级", prompt)
        self.assertIn("应用", prompt)                  # focus echoed
        self.assertIn("bloom_level", prompt)           # output tag instruction
        self.assertIn("导数·应用", prompt)              # profile context grounded

    def test_constraint_block_includes_guidance_by_default(self):
        from app.agents.assessment import generator
        block = generator._constraint_block(AssessmentGoal(concept="x"))
        self.assertIn("bloom_level", block)


if __name__ == "__main__":
    unittest.main()
