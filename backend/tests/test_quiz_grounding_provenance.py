"""Provenance 验收测试（plan.md §8）：出题结果的 source refs 可审计、
不可伪造、且经 session.quiz_history 持久化/重载不丢。

覆盖：
  1. LLM 返回不存在的 src_999 -> 后端丢弃（strict 模式该题不能标 grounded）；
  2. LLM 伪造完整 file_id dict -> 没有机会直接注入服务端 ref（只认 src_N 短 id）；
  3. tool result -> session.quiz_history -> load_session reload roundtrip，
     source refs / grounding 元数据原样保留（前端 additive 字段合同）。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_quiz_grounding import FakeQuizLLM, _bundle, _StubProvider  # noqa: F401  复用 fake


def _questions_json(source_ref_ids: list[str] | None) -> str:
    q: dict[str, Any] = {
        "id": 1, "type": "fill_blank",
        "stem": "ZX-17 定理的右端常数等于______。",
        "answer": "314159",
        "explanation": "教材中 ZX-17 定理明确规定右端常数为 314159，"
                       "填其它数值均不符合定理原文表述。",
        "knowledge_point": "ZX-17 定理", "difficulty": "easy",
    }
    if source_ref_ids is not None:
        q["source_ref_ids"] = source_ref_ids
    return json.dumps({"questions": [q]}, ensure_ascii=False)


class _RefidQuizLLM(FakeQuizLLM):
    """生成轮返回指定 source_ref_ids 的 fake LLM。"""

    def __init__(self, source_ref_ids: list[str] | None,
                 extra_keys: dict[str, Any] | None = None):
        super().__init__()
        self._ids = source_ref_ids
        self._extra = extra_keys or {}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        prompt = str(messages[0]["content"])
        self.prompts.append(prompt)
        if "命题设计专家" in prompt:
            return _BLUEPRINT_MIN, {}
        if "审题员" in prompt:
            return _CRITIC_OK_MIN, {}
        body = json.loads(_questions_json(self._ids))
        if self._extra:
            body["questions"][0].update(self._extra)
        return json.dumps(body, ensure_ascii=False), {}


_BLUEPRINT_MIN = json.dumps({
    "blueprint": [{"angle": "概念本质", "idea": "考查 ZX-17 右端常数"}]
}, ensure_ascii=False)
_CRITIC_OK_MIN = json.dumps({
    "verdicts": [{"id": 1, "verdict": "correct", "reason": "一致"}]
}, ensure_ascii=False)


class TestQuizProvenanceContract(StorageSandboxTestCase):

    def _run_tool(self, llm, provider):
        from app.tools.quiz import GenerateQuizTool
        tool = GenerateQuizTool(llm, avoid_stems=[], grounding_provider=provider)
        return asyncio.run(tool.run(topic="ZX-17 定理", grade="本科",
                                    difficulty="easy", count=1))

    def test_unknown_ref_id_discarded_non_strict_downgrades_to_generic(self):
        """src_999 等未知 ref id 一律丢弃；非 strict 题降级 generic，不带假 ref。"""
        llm = _RefidQuizLLM(["src_999"])
        result = self._run_tool(llm, _StubProvider(_bundle("found", False)))
        self.assertFalse(result.is_error, result.text)
        questions = result.data.get("questions") or []
        self.assertTrue(questions)
        q = questions[0]
        self.assertEqual(q.get("grounding_mode"), "generic")
        self.assertEqual(q.get("source_refs") or [], [],
                         "未知 ref id 不得产生任何 source ref")

    def test_unknown_ref_id_strict_drops_question(self):
        """strict 教材模式：题目没有任何有效 ref 时直接丢弃，不能 fail-open 成 grounded。"""
        llm = _RefidQuizLLM(["src_999"])
        result = self._run_tool(llm, _StubProvider(_bundle("found", True)))
        self.assertTrue(result.is_error or result.status == "partial")
        questions = result.data.get("questions") or []
        self.assertEqual(
            [q for q in questions if q.get("grounding_mode") == "textbook"], [],
            "strict 模式下无有效 ref 的题必须被丢弃")
        self.assertEqual(
            result.data.get("verification", {}).get("dropped_no_source_ref"), 1)

    def test_forged_file_id_dict_cannot_inject(self):
        """模型伪造完整 file_id dict（而非 src_N 短 id）没有任何注入机会。"""
        llm = _RefidQuizLLM(None, extra_keys={
            # 直接在题目里塞一个伪造的 source_refs —— 后端会用自己的映射
            # 覆盖/剔除该字段，绝不信任模型返回的完整 path/file_id。
            "source_refs": [{
                "file_id": "file_evil", "chunk_id": "file_evil#0",
                "filename": "伪造教材.pdf", "excerpt": "伪造内容"}],
            "source_ref_ids": ["src_1"],
        })
        result = self._run_tool(llm, _StubProvider(_bundle("found", True)))
        self.assertFalse(result.is_error, result.text)
        questions = result.data.get("questions") or []
        self.assertTrue(questions)
        for ref in questions[0].get("source_refs") or []:
            self.assertEqual(ref.get("file_id"), "file_zx17",
                             "伪造的 file_evil 不得出现在 provenance 中")
            self.assertEqual(ref.get("chunk_id"), "file_zx17#0")

    def test_valid_ref_id_maps_to_server_bundle(self):
        llm = _RefidQuizLLM(["src_1"])
        result = self._run_tool(llm, _StubProvider(_bundle("found", True)))
        self.assertFalse(result.is_error, result.text)
        q = result.data["questions"][0]
        self.assertEqual(q["grounding_mode"], "textbook")
        self.assertEqual(q["grounding_tier"], "found")
        refs = q["source_refs"]
        self.assertTrue(refs)
        self.assertEqual(refs[0]["file_id"], "file_zx17")
        self.assertEqual(refs[0]["chunk_id"], "file_zx17#0")
        self.assertEqual(refs[0]["filename"], "zx17讲义.pdf")
        self.assertEqual(refs[0]["printed_page"], 12)

    def test_provenance_survives_session_reload(self):
        """tool result -> session.quiz_history -> save/load roundtrip 不丢。"""
        from app.core.session import load_session, new_session_id, save_session, TutorSession
        llm = _RefidQuizLLM(["src_1"])
        result = self._run_tool(llm, _StubProvider(_bundle("found", True)))
        session = TutorSession(session_id=new_session_id("provenance"))
        session.quiz_history.append(result.data)
        save_session(session)
        reloaded = load_session(session.session_id)
        self.assertIsNotNone(reloaded)
        self.assertTrue(reloaded.quiz_history)
        qh = reloaded.quiz_history[-1]
        self.assertEqual((qh.get("grounding") or {}).get("mode"), "textbook")
        q = (qh.get("questions") or [])[0]
        self.assertEqual(q.get("grounding_mode"), "textbook")
        refs = q.get("source_refs") or []
        self.assertTrue(refs, "reload 后 source refs 不得丢失")
        self.assertEqual(refs[0].get("file_id"), "file_zx17")
        self.assertEqual(refs[0].get("chunk_id"), "file_zx17#0")

    def test_learning_records_and_recent_quiz_keep_refs(self):
        """learning_records / recent quiz 快照保留紧凑 ref（可审计定位）。"""
        from app.core.learning_records import record_question
        from app.core.quiz_recent import record_recent_quiz
        llm = _RefidQuizLLM(["src_1"])
        result = self._run_tool(llm, _StubProvider(_bundle("found", True)))
        q = result.data["questions"][0]
        sid = "student_prov"
        qid = record_question(sid, "sess_prov", q, topic="ZX-17")
        self.assertTrue(qid)
        from app.core.learning_records import list_records
        rec = [r for r in list_records(sid)
               if r.get("record_id") == qid][0]
        self.assertEqual(rec.get("grounding_mode"), "textbook")
        self.assertEqual(rec.get("source_refs")[0]["file_id"], "file_zx17")
        record_recent_quiz("sess_prov", sid, result.data)
        from app.core.quiz_recent import list_recent_questions
        items = list_recent_questions(sid)
        self.assertTrue(items)
        self.assertEqual(items[-1].get("grounding_mode"), "textbook")
        self.assertTrue(items[-1].get("source_refs"))


if __name__ == "__main__":
    unittest.main()
