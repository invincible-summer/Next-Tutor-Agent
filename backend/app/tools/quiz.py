"""generate_quiz tool: structured practice questions (evolved to async).

Enforces a strict JSON schema (stem / options / answer / explanation /
knowledge_point / difficulty) so the result is reliably parseable and
grade-appropriate. Uses the async LLM client to generate the questions.
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
VALID_QUESTION_TYPES = ("multiple_choice", "fill_blank", "short_answer")
_QUESTION_TYPE_ZH = {
    "multiple_choice": "选择题（必须提供至少两个不重复选项，answer 必须是选项字母）",
    "fill_blank": "填空题（不得提供 options）",
    "short_answer": "简答题（不得提供 options）",
}
# 两个出题模板共用的「题型多样」要求原文。学生显式指定题型时必须整段
# 替换为硬约束——只追加一条"本轮不适用"在实测中仍会被模型当作次要指令
# 忽略（live 验收：要求 multiple_choice，模型仍输出 short_answer 被过滤
# 成 0 题，题卡消失）。
_TYPE_DIVERSITY_LINE = (
    "- 题型多样：不要默认只出 multiple_choice。count≥2 时至少包含一道 "
    "fill_blank 或 short_answer；count=1 时按知识点特点选题型（计算/推导/"
    "步骤/代码实现类优先 fill_blank 或 short_answer，概念辨析类适合 "
    "multiple_choice）；学生要求「换一种题型/别的类型」时必须更换题型。"
)

_QUIZ_PROMPT = _prompt("quiz_generate").text

_DIFFICULTY_ZH = {"easy": "基础", "medium": "中等", "hard": "挑战"}

# 自动学段专用 prompt（P1）：省略学段锚点/例题风格，改注自适应难度说明。
_QUIZ_PROMPT_AUTO = _prompt("quiz_generate_auto").text


class GenerateQuizTool(Tool):
    name = "generate_quiz"
    description = (
        "为指定知识点生成分层练习题（含答案与详细解析）。"
        "当学生想要练习、出题、测试、巩固某个知识点时调用。"
        "参数：topic(知识点,必填) grade(学段:小学/初中/高中/本科,省略=按知识点自动) difficulty(easy/medium/hard) count(题目数1-5) q_type(可选显式题型)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "illustration_request": REQUEST_SCHEMA,
            "topic": {"type": "string", "description": "要出题的知识点，如\"一元二次方程\"、\"牛顿第二定律\""},
            "grade": {"type": "string", "enum": list(VALID_GRADES), "description": "学生学段（省略=按知识点本身自适应标定难度）"},
            "difficulty": {"type": "string", "enum": list(VALID_DIFFICULTY), "description": "难度"},
            "count": {"type": "integer", "minimum": 1, "maximum": 5, "description": "题目数量"},
            "q_type": {"type": "string", "enum": list(VALID_QUESTION_TYPES), "description": "学生明确指定的题型；未指定时省略"},
            "focus": {"type": "string", "description": "可选：本轮讲解的具体侧重点（如\"滴定步骤\"），出题必须与之直接相关"},
        },
        "required": ["topic"],
    }

    def __init__(self, llm: AsyncLLMClient, avoid_stems: list[str] | None = None,
                 grounding_provider: Any | None = None,
                 illustration_policy_provider: Any | None = None) -> None:
        self._llm = llm
        self._illustration_policy_provider = illustration_policy_provider
        # 本会话已出过的题干（截断），注入 prompt 防止逐轮出同质题。
        self._avoid_stems = [s for s in (avoid_stems or []) if s][:8]
        # 统一 Quiz Grounding 输入层（plan.md §4.3）：服务端闭包绑定的
        # KnowledgeSearchTool 投影，LLM schema 不新增 scope 参数。
        self._grounding_provider = grounding_provider

    async def run(self, **kwargs: Any):
        topic = str(kwargs.get("topic", "")).strip()
        if not topic:
            return err(self.name, ErrorCode.BAD_ARGS, "topic 不能为空。")
        from ..agents.teaching_engine.stage_profile import is_auto, normalize_grade
        grade = normalize_grade(kwargs.get("grade") or "")
        # B5：省略/空 = 自动（按知识点自适应），不强制 enum；非空须为合法学段。
        if grade and grade not in VALID_GRADES:
            return err(self.name, ErrorCode.BAD_ARGS, f"grade 必须是 {VALID_GRADES} 之一或省略（自动）。")
        difficulty = kwargs.get("difficulty") or "medium"
        if difficulty not in VALID_DIFFICULTY:
            return err(self.name, ErrorCode.BAD_ARGS, f"difficulty 必须是 {VALID_DIFFICULTY} 之一。")
        q_type = str(kwargs.get("q_type") or "").strip()
        if q_type and q_type not in VALID_QUESTION_TYPES:
            return err(self.name, ErrorCode.BAD_ARGS,
                       f"q_type 必须是 {VALID_QUESTION_TYPES} 之一或省略。")
        count = kwargs.get("count") or 3
        try:
            count = max(1, min(5, int(count)))
        except (TypeError, ValueError):
            count = 3

        focus = str(kwargs.get("focus", "")).strip()[:60]

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

        # --- 统一 Quiz Grounding（plan.md §4.3）---------------------------
        # 检索先于蓝图：第一轮蓝图决定角度/Bloom/陷阱，若未见教材，蓝图会
        # 先发散到教材外，第二轮再要求"基于教材"已经太晚。
        bundle = None
        resolve_error = ""
        if self._grounding_provider is not None:
            try:
                bundle = await self._grounding_provider.resolve(
                    topic=topic, focus=focus)
            except Exception:
                bundle = None
                resolve_error = "grounding_resolve_error"
        strict = bool(bundle is not None and bundle.required)
        grounded = bool(bundle is not None and bundle.usable)
        if strict and not grounded:
            # 严格教材模式 + NOT_FOUND（或零证据）：不能假装是教材题。
            meta = bundle.grounding_meta() if bundle is not None else {}
            if resolve_error:
                meta["reason"] = resolve_error
                text = (f"教材证据检索暂时不可用，未生成教材题。"
                        "请稍后重试，或让我直接出通用练习题。")
            else:
                text = (f"当前可选资料中未找到与「{topic}」相关的可靠教材证据，"
                        "未生成教材题。请确认教材已上传/解析完成，或换用知识点"
                        "名称重试；也可以让我出通用练习题。")
            return partial_result(self.name,
                {"topic": topic, "grade": grade, "difficulty": difficulty,
                 "questions": [], "grounding": meta,
                 "verification": {"grounding_gate": "not_found"}},
                text)
        grounding_context = ""
        if grounded:
            from ..core.quiz_grounding import render_grounding_context
            grounding_context = render_grounding_context(bundle)
        elif bundle is not None and not bundle.usable and not strict:
            # 非强制但检索未命中：允许 generic 出题（plan.md 原则 5）。
            pass

        # 两轮出题（QUIZ_DESIGN_MODE=two_pass）：先跑命题蓝图设计轮（考查角度/
        # 认知层级/陷阱设计），蓝图注入生成 prompt；蓝图轮失败自动回退单轮。
        from ..core.quiz_design import design_blueprint
        blueprint, design_status = await design_blueprint(
            llm, topic=topic, grade=grade, difficulty=difficulty,
            count=count, focus=focus, avoid_stems=self._avoid_stems,
            grounding_context=grounding_context, illustration_policy=policy)

        def make_prompt() -> str:
            from ..agents.teaching_engine.stage_profile import (
                difficulty_anchor, example_style)
            if is_auto(grade):
                # 自动学段：不注入学段难度锚点/例题风格，改注自适应难度说明。
                base = _QUIZ_PROMPT_AUTO.format(
                    grade="（学生未指定学段，按知识点本身自适应）",
                    topic=topic, count=count,
                    difficulty=difficulty, difficulty_zh=_DIFFICULTY_ZH[difficulty],
                    blueprint=blueprint)
            else:
                base = _QUIZ_PROMPT.format(
                    grade=grade, topic=topic, count=count,
                    difficulty=difficulty, difficulty_zh=_DIFFICULTY_ZH[difficulty],
                    anchor=difficulty_anchor(grade),
                    example_style=example_style(grade),
                    blueprint=blueprint,
                )
            extra = ""
            if focus:
                extra += (f"\n- 本轮讲解的侧重点是「{focus}」，出的题必须直接检测这个侧重点，"
                          "不要只考知识点的泛化常识。")
            if q_type:
                # 硬替换模板里的「题型多样」要求，消除与显式题型约束的
                # 指令冲突（模型会把互相矛盾的 bullets 按主次取舍）。
                forced = (f"- 本套题型已由学生明确指定：每道题的 type 字段必须"
                          f"恰好是 \"{q_type}\"（{_QUESTION_TYPE_ZH[q_type]}）。"
                          "禁止输出任何其它题型——不符合的题目会被系统整题丢弃，"
                          "导致你拿不到任何作答数据。")
                base = base.replace(_TYPE_DIVERSITY_LINE, forced)
            if self._avoid_stems:
                extra += ("\n- 以下题目本会话已经出过，禁止重复或仅换数字"
                          "（换情境、换考查角度、换数据）：\n"
                          + "\n".join(f"  · {s}" for s in self._avoid_stems))
            # 布鲁姆认知层级：LLM 在语境中自由选层（无阶梯规则），题目带回
            # bloom_level 标签 → 经 record_recent_quiz 流入学习账本/认知档案。
            # 工具层按设计不持有学生身份（对话链路冻结），此处不做画像注入；
            # M4 出题路径带完整认知档案 grounding。
            from ..core.bloom import guidance_block
            extra += "\n" + guidance_block()
            if grounding_context:
                # 命题事实边界（plan.md §4.5）：grounding_context 已带
                # <material_excerpt> 数据定界与 src_N 短 ref id。
                extra += ("\n\n[命题事实边界]\n"
                          "本轮要求根据给定教材证据命题。\n"
                          "每题的题干、正确答案与解析中的教材事实必须能由下方证据"
                          "直接支持。\n"
                          "可以重新设计数值/情境以形成练习，但不得引入教材证据之外"
                          "的新定理、新定义或教材专属事实。\n"
                          + grounding_context)
            return base + extra

        # Structured JSON extraction: non-streaming call with thinking disabled.
        # Reasoning models otherwise burn the whole budget on reasoning_content
        # and the answer channel comes back empty (unparseable -> 0 questions).
        # Every generation then passes the shared quality gate (structural
        # checks + independent critic re-solve) before reaching the student.
        type_feedback: dict[str, Any] = {"wrong_types": [], "last_parsed": []}

        def parse_requested_type(raw: str) -> list[dict[str, Any]]:
            parsed = self._parse(raw)
            if not q_type:
                return parsed
            kept = [q for q in parsed if q.get("type") == q_type]
            if not kept and parsed:
                type_feedback["wrong_types"] = sorted({
                    str(q.get("type") or "unknown") for q in parsed})
                type_feedback["last_parsed"] = parsed
            return kept

        gen_feedback: dict[str, Any] = {"critic_flags": []}

        def make_prompt_with_feedback() -> str:
            prompt = make_prompt()
            wrong = type_feedback["wrong_types"]
            if wrong:
                prompt += (f"\n- 上一次输出被系统整题拒绝：题型是"
                           f"「{'、'.join(wrong)}」，不符合学生的显式要求。"
                           f"本次每道题的 type 字段必须恰好是 \"{q_type}\"，"
                           "并按该题型补齐 options/answer 结构。")
            flags = [f for f in (gen_feedback.get("critic_flags") or [])
                     if f.get("reason")]
            if flags:
                lines = "\n".join(
                    "- 未通过审核的题（{}）：{}".format(
                        f.get("verdict") or "rejected",
                        str(f.get("reason"))[:160]) for f in flags[:6])
                prompt += ("\n- 上一轮部分题目未通过独立答案审核而被丢弃，"
                           "本轮命题必须修正这些问题（答案、解析、选项保持"
                           "一致正确）：\n" + lines)
            return prompt

        questions, verification = await generate_verified_questions(
            llm, make_prompt=make_prompt_with_feedback,
            parse=parse_requested_type,
            topic=topic, grade=grade, difficulty=difficulty,
            temperature=0.4, max_tokens=(min(16000, 5000 + 2200 * count) if policy != "off" else 5000),
            grounding_context=grounding_context, feedback=gen_feedback,
            illustration_policy=policy, required_type=q_type)
        if (not questions and q_type and type_feedback["last_parsed"] and policy == "off"):
            # 题卡必须出现（update_plan 验收）：两轮显式题型约束后模型仍
            # 输出其它题型时，交付结构完好的题目并如实标注，不让题卡
            # 凭空消失。此类题按未通过内容审核处理（answer_verified=False）。
            from ..core.quiz_verify import filter_well_formed
            from ..core.quiz_verify import prepare_illustrations
            safe_fallback, _invalid = prepare_illustrations(type_feedback["last_parsed"], "off")
            fallback_kept, _dropped = filter_well_formed(safe_fallback)
            if fallback_kept:
                questions = fallback_kept
                verification["type_contract_fallback"] = (
                    type_feedback["wrong_types"])
                verification["answer_verified"] = False
        verification["design"] = design_status

        # --- Provenance 验证与附加（plan.md §4.5）---------------------------
        # 模型只允许输出已提供的 src_N 短 ref id；后端映射回服务端 bundle，
        # 无效 ref 丢弃，绝不信任模型返回的完整 path/file_id。
        ref_dicts: dict[str, dict[str, Any]] = {}
        if grounded:
            from ..core.quiz_grounding import ref_id as _ref_id
            for i, ref in enumerate(bundle.source_refs[:6], 1):
                ref_dicts[_ref_id(i)] = ref.to_dict()
        grounded_questions: list[dict[str, Any]] = []
        dropped_no_ref = 0
        for q in questions:
            raw_ids = q.pop("source_ref_ids", None)
            refs: list[dict[str, Any]] = []
            if isinstance(raw_ids, list):
                for sid in raw_ids:
                    key = str(sid).strip()
                    if key in ref_dicts:
                        refs.append(ref_dicts[key])
            if grounded:
                if refs:
                    q["source_refs"] = refs
                    q["grounding_mode"] = "textbook"
                    q["grounding_tier"] = bundle.tier
                    grounded_questions.append(q)
                elif strict:
                    # 严格教材模式：一个有效 source ref 都没有的题不能标
                    # grounded（plan.md §4.6 失败语义——source ref 缺失不能
                    # fail-open 成 grounded）。
                    dropped_no_ref += 1
                else:
                    q["grounding_mode"] = "generic"
                    q["grounding_tier"] = ""
                    grounded_questions.append(q)
            else:
                q["grounding_mode"] = "generic"
                q["grounding_tier"] = ""
                grounded_questions.append(q)
        questions = grounded_questions
        if dropped_no_ref:
            verification["dropped_no_source_ref"] = dropped_no_ref
        if grounded:
            # required=true 时 critic 不可用可以 fail-open，但必须留审计标记
            # （plan.md §4.6：grounding_verification="unavailable"）。
            verification["grounding_verification"] = (
                "content_checked" if verification.get("critic") == "ok"
                else "unavailable")
        if not questions:
            if dropped_no_ref or not grounding_context:
                return partial_result(self.name,
                    {"raw": verification.get("raw", ""), "questions": [],
                     "verification": verification,
                     **({"grounding": bundle.grounding_meta()}
                        if bundle is not None else {})},
                    "未能生成通过校验的题目，请重试。")
            return partial_result(self.name,
                {"raw": verification.get("raw", ""), "questions": [],
                 "verification": verification,
                 "grounding": bundle.grounding_meta()},
                "未能生成通过校验的题目，请重试。")

        def _grounding_meta() -> dict[str, Any]:
            if bundle is not None and grounded:
                return bundle.grounding_meta()
            if bundle is not None:
                return {**bundle.grounding_meta(), "mode": "generic"}
            return {"mode": "generic", "tier": "", "required": False,
                    "reason": resolve_error or "none", "query": topic,
                    "source_count": 0, "omitted_count": 0}

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
        tier_note = ""
        if grounded and bundle.tier == "partial":
            tier_note = "（部分教材依据）"
        # A quality gate may legitimately drop some candidates.  Any
        # surviving, fully audited questions are still a successful response;
        # partial is reserved for the no-question outcome handled above.
        result_fn = ok
        return result_fn(self.name,
            {"topic": topic, "grade": grade, "difficulty": difficulty,
             "questions": questions,
             "answer_verified": verification.get("answer_verified", False),
             "verification": verification,
             "grounding": _grounding_meta()},
            f"已生成 {len(questions)}/{count} 道关于「{topic}」的练习题{note}{tier_note}。")

    @staticmethod
    def _parse(raw: str) -> list[dict[str, Any]]:
        # LaTeX 题干里的 \{ \} $ 是非法 JSON 转义：loads_tolerant 修复后
        # 重试，避免整份输出被当作 0 题（chat 出题卡直接消失）。
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
