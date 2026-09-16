"""fit_quiz tool: generate similar problems from a reference problem.

Unlike generate_quiz (which generates from a topic), fit_quiz takes an actual
reference problem, reverse-engineers its knowledge points and exam-point
structure, then produces *isomorphic* problems that test the same concepts
from different angles, contexts, and problem structures -- not just data-swap
variants. The prompt encodes a 3-tier variation strategy:
  Tier 1: Context migration (same structure, different real-world scenario)
  Tier 2: Structure mutation (same knowledge, different problem architecture)
  Tier 3: Convergence variation (same answer pattern, different setup)
This is the "拟合 agent" that links with the quiz agent infrastructure.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.llm_async import AsyncLLMClient
from ..core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from ..core.quiz_illustration_policy import IllustrationDisabled, REQUEST_SCHEMA
from ..core.quiz_verify import generate_verified_questions
from ..prompts.registry import get as _prompt
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok, partial_result

VALID_GRADES = ("小学", "初中", "高中", "本科")
VALID_DIFFICULTY = ("easy", "medium", "hard")

_FIT_PROMPT = _prompt("quiz_fit").text

_DIFFICULTY_ZH = {"easy": "基础", "medium": "中等", "hard": "挑战"}

# 自动学段专用拟合 prompt（P1）：省略学段难度锚点，改注自适应难度说明。
_FIT_PROMPT_AUTO = _prompt("quiz_fit_auto").text


class FitQuizTool(Tool):
    name = "fit_quiz"
    description = (
        "从一道参考题目出发，拟合生成同考点的变式题。"
        "当学生上传了一道题/一道例题/一张试卷照片，"
        "或说\"帮我出类似的题\"\"仿照这道题出题\"\"类似练习\"时调用。"
        "参数：reference(参考题目原文,必填) grade(学段,省略=按知识点自动) difficulty(easy/medium/hard) count(1-5)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "illustration_request": REQUEST_SCHEMA,
            "reference": {
                "type": "string",
                "description": "参考题目的完整文本（题干+选项/答案，如有解析一并附上）",
            },
            "grade": {"type": "string", "enum": list(VALID_GRADES), "description": "学生学段（省略=按知识点自适应）"},
            "difficulty": {"type": "string", "enum": list(VALID_DIFFICULTY), "description": "难度"},
            "count": {"type": "integer", "minimum": 1, "maximum": 5, "description": "变式题数量"},
        },
        "required": ["reference"],
    }

    def __init__(self, llm: AsyncLLMClient,
                 grounding_provider: Any | None = None,
                 illustration_policy_provider: Any | None = None) -> None:
        self._llm = llm
        self._illustration_policy_provider = illustration_policy_provider
        # plan.md §6：fit_quiz 的事实源是 reference 本身，不强制重复检索；
        # provider 只用于继承本轮已解析的教材证据（peek 缓存）。
        self._grounding_provider = grounding_provider

    async def run(self, **kwargs: Any):
        reference = str(kwargs.get("reference", "")).strip()
        if not reference:
            return err(self.name, ErrorCode.BAD_ARGS, "reference 不能为空。请提供参考题目文本。")
        from ..agents.teaching_engine.stage_profile import is_auto, normalize_grade
        grade = normalize_grade(kwargs.get("grade") or "")
        if grade and grade not in VALID_GRADES:
            return err(self.name, ErrorCode.BAD_ARGS, f"grade 必须是 {VALID_GRADES} 之一或省略（自动）。")
        difficulty = kwargs.get("difficulty") or "medium"
        if difficulty not in VALID_DIFFICULTY:
            return err(self.name, ErrorCode.BAD_ARGS, f"difficulty 必须是 {VALID_DIFFICULTY} 之一。")
        count = kwargs.get("count") or 3
        try:
            count = max(1, min(5, int(count)))
        except (TypeError, ValueError):
            count = 3

        request = str(kwargs.get("illustration_request") or "auto")
        if request not in {"auto", "none", "required"}:
            return err(self.name, ErrorCode.BAD_ARGS, "illustration_request 无效。")
        provider = self._illustration_policy_provider
        try:
            policy = provider(request) if provider is not None else "off"
            if provider is None and request == "required":
                raise IllustrationDisabled("当前入口不支持题目插图。")
        except IllustrationDisabled as exc:
            return err(self.name, exc.code, str(exc))
        llm = (BudgetedLLM(self._llm, GenerationBudget())
               if policy != "off" else self._llm)

        # plan.md §6：reference 来自普通粘贴 -> grounding_mode="reference"；
        # reference 来自本轮教材预检索/教材题卡（provider 已缓存证据）->
        # 继承 source refs，"reference+textbook"。严禁为统一而重新检索 topic，
        # 额外检索会让无关教材证据污染拟合。
        inherited = None
        if self._grounding_provider is not None:
            peek = getattr(self._grounding_provider, "peek_cached", None)
            if callable(peek):
                try:
                    inherited = peek()
                except Exception:
                    inherited = None
        inherited_usable = bool(inherited is not None and inherited.usable)
        grounding_context = ""
        if inherited_usable:
            from ..core.quiz_grounding import render_grounding_context
            grounding_context = render_grounding_context(inherited)

        def make_prompt() -> str:
            if is_auto(grade):
                base = _FIT_PROMPT_AUTO.format(
                    reference=reference, grade="（学生未指定学段，按知识点本身自适应）",
                    count=count, difficulty=difficulty, difficulty_zh=_DIFFICULTY_ZH[difficulty])
            else:
                from ..agents.teaching_engine.stage_profile import difficulty_anchor
                base = _FIT_PROMPT.format(
                    reference=reference, grade=grade, count=count,
                    difficulty=difficulty, difficulty_zh=_DIFFICULTY_ZH[difficulty],
                    anchor=difficulty_anchor(grade),
                )
            # 布鲁姆层级：变式题在参考题语境中自由选层并带回 bloom_level 标签
            # （流入学习账本/认知档案；工具层不持有学生身份，不做画像注入）。
            from ..core.bloom import guidance_block
            extra = "\n" + guidance_block()
            if grounding_context:
                extra += ("\n\n[命题事实边界]\n"
                          "参考题与下方教材证据共同构成本轮命题事实边界；变式题"
                          "不得引入两者之外的新教材专属事实。\n" + grounding_context)
            return base + extra

        # Same rationale as generate_quiz: structured JSON extraction needs the
        # answer channel — disable thinking so reasoning models don't starve it.
        # Variants then pass the same shared quality gate (structural checks +
        # independent critic re-solve) before reaching the student.
        gen_feedback: dict[str, Any] = {"critic_flags": []}

        def make_prompt_with_feedback() -> str:
            prompt = make_prompt()
            flags = [f for f in (gen_feedback.get("critic_flags") or [])
                     if f.get("reason")]
            if flags:
                lines = "\n".join(
                    "- 未通过审核的变式题（{}）：{}".format(
                        f.get("verdict") or "rejected",
                        str(f.get("reason"))[:160]) for f in flags[:6])
                prompt += ("\n- 上一轮部分变式题未通过独立答案审核而被丢弃，"
                           "本轮变式必须修正这些问题（答案、解析、选项保持"
                           "一致正确，仍须基于参考题做情境迁移而非换数字）：\n"
                           + lines)
            return prompt

        questions, verification = await generate_verified_questions(
            llm, make_prompt=make_prompt_with_feedback, parse=self._parse,
            topic=reference[:60], grade=grade, difficulty=difficulty,
            temperature=0.5, max_tokens=(min(18000, 8000 + 2200 * count) if policy != "off" else 8000),
            raw_preview_chars=3000,
            grounding_context=grounding_context, feedback=gen_feedback,
            illustration_policy=policy)
        inherited_refs: list[dict[str, Any]] = []
        if inherited_usable:
            inherited_refs = [r.to_dict() for r in inherited.source_refs[:6]]
        for q in questions:
            if inherited_usable:
                q["source_refs"] = list(inherited_refs)
                q["grounding_mode"] = "reference+textbook"
                q["grounding_tier"] = inherited.tier
            else:
                q["grounding_mode"] = "reference"
                q["grounding_tier"] = ""
        grounding_meta = {
            "mode": "reference+textbook" if inherited_usable else "reference",
            "tier": inherited.tier if inherited_usable else "",
            "required": bool(inherited.required) if inherited else False,
            "reason": inherited.reason if inherited else "none",
            "source_count": len(inherited_refs)}
        if not questions:
            return partial_result(self.name,
                {"raw": verification.get("raw", ""), "questions": [],
                 "verification": verification,
                 "grounding": grounding_meta},
                "未能生成通过校验的变式题，请重试。")
        questions = questions[:count]
        # Account permission can change while the model is generating.
        if provider is not None:
            try:
                current_policy = provider(request)
            except IllustrationDisabled as exc:
                return err(self.name, exc.code, str(exc))
            if current_policy == "off" and any(q.get("illustration") for q in questions):
                return partial_result(self.name, {"questions": [],
                    "reason": "illustration_disabled", "verification": verification},
                    "插图生成已关闭，请重新生成无图题目。")
        if isinstance(llm, BudgetedLLM):
            verification["generation_calls"] = llm.budget.calls
            verification["illustration_repairs"] = llm.budget.repairs
        verification["illustration_policy"] = policy
        note = "（已通过答案校验）" if verification.get("answer_verified") else ""
        # Deliver surviving validated variants as success; partial is used
        # only when the gate leaves no question to render.
        result_fn = ok
        return result_fn(self.name,
            {"reference": reference[:200], "grade": grade,
             "difficulty": difficulty, "questions": questions,
             "answer_verified": verification.get("answer_verified", False),
             "verification": verification,
             "grounding": grounding_meta},
            f"拟合生成 {len(questions)}/{count} 道变式题{note}。")

    @staticmethod
    def _parse(raw: str) -> list[dict[str, Any]]:
        # 与 GenerateQuizTool._parse 相同：LaTeX 转义容错解析。
        from ..core.json_utils import extract_json_object
        data = extract_json_object(raw)
        if not isinstance(data, dict):
            return []
        qs = data.get("questions", [])
        out: list[dict[str, Any]] = []
        for i, q in enumerate(qs, 1):
            if not isinstance(q, dict) or "stem" not in q or "answer" not in q:
                continue
            q.setdefault("id", i)
            q.setdefault("type", "multiple_choice")
            q.setdefault("explanation", "")
            q.setdefault("knowledge_point", "")
            q.setdefault("difficulty", "medium")
            out.append(q)
        return out
