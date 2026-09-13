"""G2 回归：P2 逐题审核（plan §18.2 test_question_audit / A06）。

批审核漏一题=该题 unreviewed、错误答案拒绝、整套通过不能替代单题、
嵌套 answer key 不出现在 QuestionPublic（后者在 schema 测试中）。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from app.core.config import settings


class QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False, **kw):
        self.calls.append(messages)
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item, {"total_tokens": 1}


def _q(qid, answer="B"):
    return {"id": qid, "type": "multiple_choice", "stem": "题干",
            "options": {"A": "甲", "B": "乙", "C": "丙"},
            "answer": answer,
            "explanation": "足够长的解析内容，超过十五个字。",
            "knowledge_point": "浮力"}


def _audit_item(qid: int, status: str, **over) -> dict:
    item = {"question_ref": str(qid), "answer_check": "valid",
            "grounding_check": "not_required",
            "actual_required_processes": ["understand"],
            "knowledge_types": ["conceptual"], "alignment": "aligned",
            "opportunity_checks": [], "rubric_issues": [],
            "brief_basis": "", "grounding_refs": [],
            "recommended_revision": "", "proposed_status": status}
    item.update(over)
    return item


def _audits(*items: dict) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


def _audit(qid: int, status: str, **over) -> str:
    return _audits(_audit_item(qid, status, **over))


class TestPerQuestionAudit(unittest.TestCase):
    def test_missing_verdict_is_unreviewed_not_passed(self):
        """A06：批审核漏一题 → 该题 unreviewed（仍交付但明确标记），
        整套 answer_verified=False。"""
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([_audit(1, "passed")])       # 题二缺席
        kept, dropped, ok = asyncio.run(verify_questions(
            llm, [_q(1), _q(2)], topic="浮力", grade="高中"))
        self.assertTrue(ok)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 0)
        statuses = {str(q["id"]): q["verification"]["status"] for q in kept}
        self.assertEqual(statuses["1"], "passed")
        self.assertEqual(statuses["2"], "unreviewed")

    def test_rejected_answer_drops_question(self):
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([_audits(_audit_item(1, "passed"),
                                _audit_item(2, "rejected"))])
        kept, dropped, ok = asyncio.run(verify_questions(
            llm, [_q(1), _q(2, answer="错")], topic="浮力", grade="高中"))
        self.assertTrue(ok)
        self.assertEqual([q["id"] for q in kept], [1])
        self.assertEqual(dropped[0]["_verdict"], "rejected")

    def test_critic_error_marks_all_unreviewed(self):
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([RuntimeError("boom")])
        kept, dropped, ok = asyncio.run(verify_questions(
            llm, [_q(1)], topic="浮力", grade="高中"))
        self.assertFalse(ok)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["verification"]["status"], "unreviewed")
        self.assertEqual(dropped, [])

    def test_system_uses_p2_and_schema(self):
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([_audit(1, "passed")])
        asyncio.run(verify_questions(llm, [_q(1)], topic="浮力", grade="高中"))
        system = llm.calls[0][0]["content"]
        self.assertIn("出题审核员", system)             # P2 角色文本
        self.assertIn("不得用“整套通过”替代单题结论", system)
        user = llm.calls[0][1]["content"]
        self.assertIn("浮力", user)

    def test_generate_verified_set_level_answer_verified_requires_all(self):
        """A06：整套通过不能替代单题——unreviewed 拉低整套 answer_verified。"""
        from app.core.quiz_verify import generate_verified_questions
        gen = json.dumps({"questions": [_q(1), _q(2)]}, ensure_ascii=False)
        llm = QueueLLM([gen, _audit(1, "passed")])   # 生成 + critic（漏题二）
        with mock.patch.object(settings, "quiz_verify_mode", "critic"):
            questions, meta = asyncio.run(generate_verified_questions(
                llm, make_prompt=lambda: "p",
                parse=lambda raw: json.loads(raw)["questions"],
                topic="浮力", grade="高中",
                temperature=0.4, max_tokens=1000))
        self.assertEqual(len(questions), 2)
        self.assertFalse(meta["answer_verified"])
        self.assertEqual(meta["unreviewed_count"], 1)

    def test_all_passed_set_is_verified(self):
        from app.core.quiz_verify import generate_verified_questions
        gen = json.dumps({"questions": [_q(1)]}, ensure_ascii=False)
        llm = QueueLLM([gen, _audit(1, "passed")])
        with mock.patch.object(settings, "quiz_verify_mode", "critic"):
            questions, meta = asyncio.run(generate_verified_questions(
                llm, make_prompt=lambda: "p",
                parse=lambda raw: json.loads(raw)["questions"],
                topic="浮力", grade="高中",
                temperature=0.4, max_tokens=1000))
        self.assertTrue(meta["answer_verified"])
        self.assertEqual(meta["unreviewed_count"], 0)


if __name__ == "__main__":
    unittest.main()
