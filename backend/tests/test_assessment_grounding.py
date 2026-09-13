"""Contract tests: Assessment/CAT grounding（plan.md §5, Phase 1）。

目标行为（当前 main 缺失，测试必须先失败）：
  1. AssessmentContext 携带 additive grounding 字段且 to_dict/from_dict
     round-trip（CAT session 持久化 / 重启恢复不丢证据 scope）；
  2. Question 携带 grounding_mode / grounding_tier / source_refs；
  3. /assessment/start 接受 session_id / textbook_ids / strict_textbook。

Phase 3 实现后全绿并保持回归。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase


class TestAssessmentContextGroundingContract(StorageSandboxTestCase):

    def test_context_has_grounding_fields_with_defaults(self):
        from app.agents.assessment import AssessmentContext
        ctx = AssessmentContext(concept="ZX-17 定理")
        self.assertFalse(ctx.grounding_required)
        self.assertEqual(ctx.grounding_mode, "generic")
        self.assertEqual(ctx.grounding_tier, "not_found")
        self.assertEqual(ctx.grounding_query, "")
        self.assertEqual(ctx.grounding_sources, [])

    def test_context_grounding_roundtrip(self):
        from app.agents.assessment import AssessmentContext
        sources = [{
            "file_id": "file_zx17", "chunk_id": "file_zx17#0",
            "filename": "zx17讲义.pdf", "page": 3, "printed_page": 12,
            "section_path": ["第三章", "3.2"],
            "excerpt": "ZX-17 定理的右端常数为 314159。",
            "context_hash": "hash_zx17", "confidence": 0.82,
        }]
        ctx = AssessmentContext(
            concept="ZX-17 定理", grounding_required=True,
            grounding_mode="textbook", grounding_tier="found",
            grounding_query="ZX-17 定理", grounding_sources=sources)
        d = ctx.to_dict()
        self.assertTrue(d["grounding_required"])
        self.assertEqual(d["grounding_mode"], "textbook")
        self.assertEqual(d["grounding_tier"], "found")
        self.assertEqual(d["grounding_sources"], sources)
        restored = AssessmentContext.from_dict(d)
        self.assertEqual(restored.grounding_mode, "textbook")
        self.assertTrue(restored.grounding_required)
        self.assertEqual(restored.grounding_sources, sources)
        # 旧持久化（无 grounding 字段）必须无损重建。
        legacy = AssessmentContext.from_dict({"concept": "浮力", "grade": "高中"})
        self.assertEqual(legacy.grounding_mode, "generic")
        self.assertEqual(legacy.grounding_sources, [])


class TestAssessmentQuestionGroundingContract(StorageSandboxTestCase):

    def test_question_has_grounding_fields(self):
        from app.agents.assessment.question import Question
        q = Question(id="q1", concept="ZX-17 定理", stem="s", answer="A")
        self.assertEqual(q.grounding_mode, "generic")
        self.assertEqual(q.grounding_tier, "")
        self.assertEqual(q.source_refs, [])

    def test_question_dict_roundtrip_keeps_provenance(self):
        from app.agents.assessment.question import Question
        refs = [{"file_id": "file_zx17", "chunk_id": "file_zx17#0",
                 "filename": "zx17讲义.pdf", "excerpt": "右端常数为 314159。"}]
        q = Question(id="q1", concept="ZX-17", stem="s", answer="A",
                     grounding_mode="textbook", grounding_tier="found",
                     source_refs=refs)
        d = q.to_dict()
        self.assertEqual(d["grounding_mode"], "textbook")
        self.assertEqual(d["source_refs"], refs)
        restored = Question.from_quiz_dict(d)
        self.assertEqual(restored.grounding_mode, "textbook")
        self.assertEqual(restored.grounding_tier, "found")
        self.assertEqual(restored.source_refs, refs)


class TestAssessmentStartRequestContract(StorageSandboxTestCase):

    def test_start_request_accepts_textbook_scope_fields(self):
        from app.api.v1.assessment import StartRequest
        req = StartRequest(concept="ZX-17 定理", session_id="sess_1",
                           textbook_ids=["tb_a"], strict_textbook=True)
        self.assertEqual(req.session_id, "sess_1")
        self.assertEqual(req.textbook_ids, ["tb_a"])
        self.assertTrue(req.strict_textbook)
        # 默认值：不传则不影响旧调用方。
        bare = StartRequest(concept="浮力")
        self.assertEqual(bare.session_id, "")
        self.assertEqual(bare.textbook_ids, [])
        self.assertFalse(bare.strict_textbook)


if __name__ == "__main__":
    unittest.main()
