"""Contract tests: 统一 Quiz Grounding（plan.md §3-§8, Phase 1）。

这些测试先于实现编写，编码的是 plan.md 描述的**目标行为**——它们在
当前 main 上必须失败，以证明整改计划命中的是真实缺口：
  1. GenerateQuizTool 没有 grounding 输入层（无 grounding_provider）；
  2. 教材证据没有进入命题蓝图 / 生成 prompt；
  3. strict 模式 NOT_FOUND 时仍会照常出题（假教材题）；
  4. 出题结果不携带可审计的 source_refs provenance。

Phase 2 实现后这些测试必须全绿，并保持回归。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase

# --- Fake LLM：按 prompt 特征区分蓝图/出题/审题三种子调用 ------------------

_BLUEPRINT_JSON = json.dumps({
    "blueprint": [
        {"angle": "概念本质", "bloom": "understand", "q_type": "multiple_choice",
         "trap": "混淆定理成立条件", "idea": "考查 ZX-17 定理右端常数的记忆与理解"},
        {"angle": "应用与迁移", "bloom": "apply", "q_type": "fill_blank",
         "trap": "把右端常数误当系数", "idea": "在新情境中应用 ZX-17 定理"},
    ]
}, ensure_ascii=False)

_QUESTIONS_JSON = json.dumps({
    "questions": [
        {
            "id": 1, "type": "multiple_choice",
            "stem": "根据 ZX-17 定理，其右端常数是多少？",
            "options": {"A": "314159", "B": "271828", "C": "141421", "D": "161803"},
            "answer": "A",
            "explanation": "ZX-17 定理明确规定右端常数为 314159，"
                           "其余选项均为其它数学常数的近似值，属于干扰项。",
            "knowledge_point": "ZX-17 定理",
            "difficulty": "easy",
            "bloom_level": "remember",
            "source_ref_ids": ["src_1"],
        },
        {
            "id": 2, "type": "fill_blank",
            "stem": "在 ZX-17 定理的表述中，右端常数等于______。",
            "answer": "314159",
            "explanation": "教材中 ZX-17 定理的右端常数唯一确定，为 314159；"
                           "填其它数值均不符合定理原文。",
            "knowledge_point": "ZX-17 定理",
            "difficulty": "easy",
            "bloom_level": "remember",
            "source_ref_ids": ["src_1", "src_2"],
        },
    ]
}, ensure_ascii=False)

_CRITIC_OK_JSON = json.dumps({
    "verdicts": [
        {"id": 1, "verdict": "correct", "reason": "与拟定答案一致"},
        {"id": 2, "verdict": "correct", "reason": "与拟定答案一致"},
    ]
}, ensure_ascii=False)


class FakeQuizLLM:
    """按 prompt 开头特征返回蓝图 / 题目 / 审题 JSON；记录全部 prompt。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        prompt = str(messages[0]["content"])
        self.prompts.append(prompt)
        if "命题设计专家" in prompt:
            body = _BLUEPRINT_JSON
        elif "审题员" in prompt:
            body = _CRITIC_OK_JSON
        else:
            body = _QUESTIONS_JSON
        return body, {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}


def _make_ref(**over: Any) -> Any:
    """目标契约 QuizSourceRef（Phase 2 前 ImportError —— 这正是 contract）。"""
    from app.core.quiz_grounding import QuizSourceRef
    kw = {"file_id": "file_zx17", "chunk_id": "file_zx17#0",
          "filename": "zx17讲义.pdf", "source_scope": "session",
          "page": 3, "printed_page": 12,
          "section_path": ["第三章", "3.2"],
          "excerpt": "ZX-17 定理的右端常数为 314159。",
          "context_hash": "hash_zx17"}
    kw.update(over)
    return QuizSourceRef(**kw)


class _StubProvider:
    """duck-typed QuizGroundingProvider，返回预设 bundle。"""

    def __init__(self, bundle):
        self._bundle = bundle
        self.calls: list[dict] = []

    async def resolve(self, *, topic: str, focus: str = "", top_k: int = 6):
        self.calls.append({"topic": topic, "focus": focus, "top_k": top_k})
        return self._bundle


def _bundle(tier: str, required: bool):
    from app.core.quiz_grounding import QuizGroundingBundle
    refs = [] if tier == "not_found" else [_make_ref(), _make_ref(chunk_id="file_zx17#1")]
    return QuizGroundingBundle(
        query="ZX-17 定理", mode="textbook", tier=tier, required=required,
        reason="workspace_material_content_question", source_refs=refs)


class TestQuizGroundingModuleContract(StorageSandboxTestCase):
    """plan §4.1: core/quiz_grounding.py 数据投影层。"""

    def test_module_exports_contract_types(self):
        from app.core.quiz_grounding import (KnowledgeSearchQuizGroundingProvider,
                                             QuizGroundingBundle, QuizSourceRef,
                                             build_quiz_query)
        self.assertTrue(callable(build_quiz_query))
        self.assertEqual(build_quiz_query("ZX-17 定理", "右端常数"),
                         "ZX-17 定理 右端常数")
        self.assertEqual(build_quiz_query("ZX-17 定理", ""), "ZX-17 定理")

    def test_bundle_usable_semantics(self):
        from app.core.quiz_grounding import QuizGroundingBundle
        found = QuizGroundingBundle(query="q", mode="textbook", tier="found",
                                    required=True, reason="r",
                                    source_refs=[_make_ref()])
        self.assertTrue(found.usable)
        partial = QuizGroundingBundle(query="q", mode="textbook", tier="partial",
                                      required=True, reason="r",
                                      source_refs=[_make_ref()])
        self.assertTrue(partial.usable)
        not_found = QuizGroundingBundle(query="q", mode="textbook",
                                        tier="not_found", required=True,
                                        reason="r", source_refs=[])
        self.assertFalse(not_found.usable)
        # found 语义但零 ref：不可用（不能凭空声称 grounded）
        empty_found = QuizGroundingBundle(query="q", mode="textbook", tier="found",
                                          required=True, reason="r", source_refs=[])
        self.assertFalse(empty_found.usable)


class TestGenerateQuizGroundingContract(StorageSandboxTestCase):
    """plan §4.3: GenerateQuizTool 的 grounding 输入层与 provenance 输出。"""

    def _tool(self, provider):
        from app.tools.quiz import GenerateQuizTool
        return GenerateQuizTool(FakeQuizLLM(), avoid_stems=[],
                                grounding_provider=provider)

    def test_tool_accepts_grounding_provider_kwarg(self):
        tool = self._tool(_StubProvider(_bundle("found", True)))
        self.assertIsNotNone(tool)

    def test_strict_not_found_does_not_generate_textbook_quiz(self):
        tool = self._tool(_StubProvider(_bundle("not_found", True)))
        result = asyncio.run(tool.run(topic="ZX-17 定理", grade="本科",
                                      difficulty="easy", count=2))
        # 严格教材模式 + NOT_FOUND：不允许出任何带教材依据的题。
        grounded = [q for q in result.data.get("questions", [])
                    if q.get("grounding_mode") == "textbook"]
        self.assertEqual(grounded, [],
                         "strict NOT_FOUND 不得产出任何教材 grounded 题")
        self.assertFalse(
            result.data.get("grounding", {}).get("mode") == "textbook"
            and result.data.get("grounding", {}).get("tier") == "found")
        # 明确的失败语义（error/partial），上层能据此告知用户。
        self.assertTrue(result.is_error or result.status == "partial",
                        "strict NOT_FOUND 必须返回明确的失败语义")

    def test_found_grounded_questions_carry_source_refs(self):
        provider = _StubProvider(_bundle("found", True))
        tool = self._tool(provider)
        result = asyncio.run(tool.run(topic="ZX-17 定理", grade="本科",
                                      difficulty="easy", count=2))
        self.assertFalse(result.is_error, result.text)
        questions = result.data.get("questions", [])
        self.assertTrue(questions)
        grounding = result.data.get("grounding") or {}
        self.assertEqual(grounding.get("mode"), "textbook")
        self.assertEqual(grounding.get("tier"), "found")
        for q in questions:
            self.assertEqual(q.get("grounding_mode"), "textbook")
            self.assertEqual(q.get("grounding_tier"), "found")
            refs = q.get("source_refs") or []
            self.assertTrue(refs, "strict 教材题必须至少携带一个 source ref")
            for ref in refs:
                self.assertEqual(ref.get("file_id"), "file_zx17")
                self.assertIn("chunk_id", ref)

    def test_blueprint_prompt_contains_grounding_context(self):
        """plan §4.4/两轮命题：蓝图轮必须见到教材证据，不能只在终轮注入。"""
        llm = FakeQuizLLM()
        from app.tools.quiz import GenerateQuizTool
        tool = GenerateQuizTool(llm, avoid_stems=[],
                                grounding_provider=_StubProvider(_bundle("found", True)))
        asyncio.run(tool.run(topic="ZX-17 定理", grade="本科",
                             difficulty="easy", count=2))
        blueprint_prompts = [p for p in llm.prompts if "命题设计专家" in p]
        self.assertTrue(blueprint_prompts, "必须执行蓝图轮")
        self.assertIn("ZX-17", blueprint_prompts[0])
        self.assertIn("命题依据", blueprint_prompts[0],
                      "蓝图 prompt 必须包含教材命题依据块")

    def test_generation_prompt_contains_grounding_block(self):
        llm = FakeQuizLLM()
        from app.tools.quiz import GenerateQuizTool
        tool = GenerateQuizTool(llm, avoid_stems=[],
                                grounding_provider=_StubProvider(_bundle("found", True)))
        asyncio.run(tool.run(topic="ZX-17 定理", grade="本科",
                             difficulty="easy", count=2))
        gen_prompts = [p for p in llm.prompts
                       if "出题专家" in p and "审题员" not in p]
        self.assertTrue(gen_prompts)
        self.assertIn("命题事实边界", gen_prompts[0])
        self.assertIn("source_ref_ids", gen_prompts[0])
        self.assertIn("material_excerpt", gen_prompts[0])

    def test_non_strict_without_provider_stays_generic(self):
        """plan 原则 5：无 provider / 非强制时保持 generic 出题，不强制教材化。"""
        from app.tools.quiz import GenerateQuizTool
        tool = GenerateQuizTool(FakeQuizLLM(), avoid_stems=[])
        result = asyncio.run(tool.run(topic="牛顿第二定律", grade="高中",
                                      difficulty="medium", count=2))
        self.assertFalse(result.is_error, result.text)
        grounding = result.data.get("grounding") or {}
        self.assertEqual(grounding.get("mode", "generic"), "generic")
        for q in result.data.get("questions", []):
            self.assertNotEqual(q.get("grounding_mode"), "textbook")

    def test_optional_not_found_falls_back_to_generic(self):
        provider = _StubProvider(_bundle("not_found", False))
        tool = self._tool(provider)
        result = asyncio.run(tool.run(topic="牛顿第二定律", grade="高中",
                                      difficulty="medium", count=2))
        self.assertFalse(result.is_error, result.text)
        grounding = result.data.get("grounding") or {}
        self.assertEqual(grounding.get("mode"), "generic")
        self.assertEqual(grounding.get("tier"), "not_found")


class TestGroundedCriticContract(StorageSandboxTestCase):
    """plan §4.6: verify_questions 的 grounding_context + unsupported verdict。"""

    def test_critic_prompt_gains_unsupported_verdict(self):
        from app.core.quiz_verify import verify_questions
        llm = FakeQuizLLM()
        questions = [{
            "id": 1, "type": "multiple_choice",
            "stem": "ZX-17 定理右端常数是多少？",
            "options": {"A": "314159", "B": "1"}, "answer": "A",
            "explanation": "教材写明右端常数为 314159。"}]
        asyncio.run(verify_questions(
            llm, questions, topic="ZX-17", grade="本科", difficulty="easy",
            grounding_context="ZX-17 定理的右端常数为 314159。"))
        self.assertTrue(llm.prompts)
        critic_prompt = llm.prompts[-1]
        self.assertIn("unsupported", critic_prompt)

    def test_unsupported_verdict_drops_question(self):
        from app.core.quiz_verify import verify_questions

        class _UnsupportedLLM(FakeQuizLLM):
            async def complete(self, messages, temperature=None,
                               max_tokens=None, disable_thinking=False):
                prompt = str(messages[0]["content"])
                self.prompts.append(prompt)
                if "审题员" in prompt:
                    return json.dumps({"verdicts": [
                        {"id": 1, "verdict": "unsupported",
                         "reason": "证据未支持该结论"}]}, ensure_ascii=False), {}
                return _CRITIC_OK_JSON, {}

        llm = _UnsupportedLLM()
        questions = [{
            "id": 1, "type": "multiple_choice",
            "stem": "ZX-17 定理右端常数是多少？",
            "options": {"A": "314159", "B": "1"}, "answer": "A",
            "explanation": "教材写明右端常数为 314159。"}]
        kept, dropped, critic_ok = asyncio.run(verify_questions(
            llm, questions, topic="ZX-17", grade="本科", difficulty="easy",
            grounding_context="ZX-17 定理的右端常数为 314159。"))
        self.assertTrue(critic_ok)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["_verdict"], "unsupported")


class TestQuizDesignGroundingContext(StorageSandboxTestCase):
    """plan §4.4: design_blueprint 的 additive grounding_context。"""

    def test_design_blueprint_accepts_grounding_context(self):
        from app.core.quiz_design import _build_prompt
        prompt = _build_prompt(topic="ZX-17", grade="本科", difficulty="easy",
                               count=2, focus="", avoid_stems=[],
                               grounding_context="ZX-17 定理的右端常数为 314159。")
        self.assertIn("命题依据", prompt)
        self.assertIn("314159", prompt)
        self.assertIn("material_excerpt", prompt)
        # 空 grounding_context 时与旧行为等价（无新增块）。
        legacy = _build_prompt(topic="ZX-17", grade="本科", difficulty="easy",
                               count=2, focus="", avoid_stems=[],
                               grounding_context="")
        self.assertNotIn("命题依据", legacy)


if __name__ == "__main__":
    unittest.main()
