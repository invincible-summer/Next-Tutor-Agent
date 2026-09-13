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
from ..core.quiz_verify import RUBRIC_REQUIREMENT, generate_verified_questions
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok, partial_result

VALID_GRADES = ("小学", "初中", "高中", "本科")
VALID_DIFFICULTY = ("easy", "medium", "hard")

_QUIZ_PROMPT = """你是出题专家。为学段「{grade}」的学生，围绕知识点「{topic}」出 {count} 道练习题，难度：{difficulty_zh}。
难度定义（相对于该学段，不是绝对难度）：
- easy 基础：一步直接应用，课本例题级，识别题型套公式即得。
- medium 中等：需要一次转化或综合两个知识点，不能照搬例题；有明确的过程分。
- hard 挑战：多步推理、变式或含易错陷阱。
该学段难度锚点（标定 easy/medium/hard 的参照系，必须遵守）：{anchor}
该学段例题风格：{example_style}
{blueprint}
只输出一个 JSON 对象，不要输出任何其它文字、不要 markdown 代码块。
格式：
{{
  "questions": [
    {{
      "id": 1,
      "type": "multiple_choice",
      "stem": "题干",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "B",
      "explanation": "为什么选 B，讲清思路",
      "knowledge_point": "对应知识点",
      "difficulty": "本题实际难度 easy/medium/hard"
    }}
  ]
}}
要求：
- 题型多样：不要默认只出 multiple_choice。count≥2 时至少包含一道 fill_blank 或 short_answer；count=1 时按知识点特点选题型（计算/推导/步骤/代码实现类优先 fill_blank 或 short_answer，概念辨析类适合 multiple_choice）；学生要求「换一种题型/别的类型」时必须更换题型。
- 题目难度与学段、目标难度匹配，{grade} 学生能看懂。count=1 时严格按目标难度出题，不得暗中降档；count≥2 时套内递进：第 1 题比目标难度低一档起步，逐题加难，最后 1 题必须达到目标难度，每题 difficulty 字段写本题实际难度。
- 题干、选项、答案中的公式和符号也用 LaTeX 语法（$...$ 行内）。
- options 仅在 type 为 multiple_choice 时提供；填空题用 fill_blank，简答用 short_answer，这两类不需要 options，answer 直接写答案文本。
- explanation 必须详细、可复盘：分步讲解。先用一两句点明考查的知识点与解题切入点；再分步给出推导过程（列出所用公式/定理、代入的数据、关键中间结果）；最后给出最终结论并点出学生最容易错的点。禁止只重复答案、禁止一句话带过。解析长度严格 80-200 字，不要超出。
- explanation 字段只写给学生看的讲解，不要写你的思考过程、不要自我质疑、不要修改题目。如果想改题目，就在 stem 里直接写最终版本。
- 所有公式、推导步骤、计算结果必须用 LaTeX 数学语法：行内公式用 $...$，独立公式用 $$...$$。例如 $F=ma$、$\\rho=\\frac{{m}}{{V}}$、$\\sum_{{i=1}}^{{n}}i$。禁止用纯文本写公式（如 F=ma、x^2+y^2=25），必须用 LaTeX。数学环境内不要直接写中文（包括中文下标），必须写中文时用 \\text{{}} 包裹：正确写法 $c_{{\\text{{待测}}}}$，错误写法 $c_{{待测}}$。
- 数字与中英文之间保留一个空格：如「物体质量 5 kg」「$F=10 N$」「$g=10 N/kg$」「$\\rho=1.0\\times10^3 kg/m^3$」。中文与英文/数字之间也要有空格，如「代入 $F=ma$」「$v=10 m/s$」。
- 严格输出可被 json.loads 解析的纯 JSON。""" + RUBRIC_REQUIREMENT

_DIFFICULTY_ZH = {"easy": "基础", "medium": "中等", "hard": "挑战"}

# 自动学段专用 prompt（P1）：省略学段锚点/例题风格，改注自适应难度说明。
_QUIZ_PROMPT_AUTO = """你是出题专家。围绕知识点「{topic}」为{grade}的学生出 {count} 道练习题，难度：{difficulty_zh}。
难度定义（按知识点本身标定，不是绝对难度）：
- easy 基础：一步直接应用，识别题型套公式即得。
- medium 中等：需要一次转化或综合两个知识点，不能照搬例题；有明确的过程分。
- hard 挑战：多步推理、变式或含易错陷阱。
{blueprint}
只输出一个 JSON 对象，不要输出任何其它文字、不要 markdown 代码块。
格式：
{{
  "questions": [
    {{
      "id": 1,
      "type": "multiple_choice",
      "stem": "题干",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "B",
      "explanation": "为什么选 B，讲清思路",
      "knowledge_point": "对应知识点",
      "difficulty": "本题实际难度 easy/medium/hard"
    }}
  ]
}}
要求：
- 题型多样：不要默认只出 multiple_choice。count≥2 时至少包含一道 fill_blank 或 short_answer；count=1 时按知识点特点选题型（计算/推导/步骤/代码实现类优先 fill_blank 或 short_answer，概念辨析类适合 multiple_choice）；学生要求「换一种题型/别的类型」时必须更换题型。
- 题目难度与知识点、目标难度匹配。count=1 时严格按目标难度出题，不得暗中降档；count≥2 时套内递进：第 1 题比目标难度低一档起步，逐题加难，最后 1 题必须达到目标难度，每题 difficulty 字段写本题实际难度。
- 题干、选项、答案中的公式和符号也用 LaTeX 语法（$...$ 行内）。
- options 仅在 type 为 multiple_choice 时提供；填空题用 fill_blank，简答用 short_answer，这两类不需要 options，answer 直接写答案文本。
- explanation 必须详细、可复盘：分步讲解。先用一两句点明考查的知识点与解题切入点；再分步给出推导过程（列出所用公式/定理、代入的数据、关键中间结果）；最后给出最终结论并点出学生最容易错的点。禁止只重复答案、禁止一句话带过。解析长度严格 80-200 字，不要超出。
- explanation 字段只写给学生看的讲解，不要写你的思考过程、不要自我质疑、不要修改题目。如果想改题目，就在 stem 里直接写最终版本。
- 所有公式、推导步骤、计算结果必须用 LaTeX 数学语法：行内公式用 $...$，独立公式用 $$...$$。例如 $F=ma$、$\\rho=\\frac{{m}}{{V}}$、$\\sum_{{i=1}}^{{n}}i$。禁止用纯文本写公式（如 F=ma、x^2+y^2=25），必须用 LaTeX。数学环境内不要直接写中文（包括中文下标），必须写中文时用 \\text{{}} 包裹：正确写法 $c_{{\\text{{待测}}}}$，错误写法 $c_{{待测}}$。
- 数字与中英文之间保留一个空格：如「物体质量 5 kg」「$F=10 N$」「$g=10 N/kg$」「$\\rho=1.0\\times10^3 kg/m^3$」。中文与英文/数字之间也要有空格，如「代入 $F=ma$」「$v=10 m/s$」。
- 严格输出可被 json.loads 解析的纯 JSON。""" + RUBRIC_REQUIREMENT


class GenerateQuizTool(Tool):
    name = "generate_quiz"
    description = (
        "为指定知识点生成分层练习题（含答案与详细解析）。"
        "当学生想要练习、出题、测试、巩固某个知识点时调用。"
        "参数：topic(知识点,必填) grade(学段:小学/初中/高中/本科,省略=按知识点自动) difficulty(easy/medium/hard) count(题目数1-5)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "要出题的知识点，如\"一元二次方程\"、\"牛顿第二定律\""},
            "grade": {"type": "string", "enum": list(VALID_GRADES), "description": "学生学段（省略=按知识点本身自适应标定难度）"},
            "difficulty": {"type": "string", "enum": list(VALID_DIFFICULTY), "description": "难度"},
            "count": {"type": "integer", "minimum": 1, "maximum": 5, "description": "题目数量"},
            "focus": {"type": "string", "description": "可选：本轮讲解的具体侧重点（如\"滴定步骤\"），出题必须与之直接相关"},
        },
        "required": ["topic"],
    }

    def __init__(self, llm: AsyncLLMClient, avoid_stems: list[str] | None = None,
                 grounding_provider: Any | None = None) -> None:
        self._llm = llm
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
        count = kwargs.get("count") or 3
        try:
            count = max(1, min(5, int(count)))
        except (TypeError, ValueError):
            count = 3

        focus = str(kwargs.get("focus", "")).strip()[:60]

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
            self._llm, topic=topic, grade=grade, difficulty=difficulty,
            count=count, focus=focus, avoid_stems=self._avoid_stems,
            grounding_context=grounding_context)

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
        questions, verification = await generate_verified_questions(
            self._llm, make_prompt=make_prompt, parse=self._parse,
            topic=topic, grade=grade, difficulty=difficulty,
            temperature=0.4, max_tokens=5000,
            grounding_context=grounding_context)
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
                    "未能生成通过校验的题目，已返回模型原始输出片段。")
            return partial_result(self.name,
                {"raw": verification.get("raw", ""), "questions": [],
                 "verification": verification,
                 "grounding": bundle.grounding_meta()},
                "未能生成通过校验的题目，已返回模型原始输出片段。")

        def _grounding_meta() -> dict[str, Any]:
            if bundle is not None and grounded:
                return bundle.grounding_meta()
            if bundle is not None:
                return {**bundle.grounding_meta(), "mode": "generic"}
            return {"mode": "generic", "tier": "", "required": False,
                    "reason": resolve_error or "none", "query": topic,
                    "source_count": 0, "omitted_count": 0}

        note = "（已通过答案校验）" if verification.get("answer_verified") else ""
        tier_note = ""
        if grounded and bundle.tier == "partial":
            tier_note = "（部分教材依据）"
        return ok(self.name,
            {"topic": topic, "grade": grade, "difficulty": difficulty,
             "questions": questions,
             "answer_verified": verification.get("answer_verified", False),
             "verification": verification,
             "grounding": _grounding_meta()},
            f"生成 {len(questions)} 道关于「{topic}」的练习题{note}{tier_note}。")

    @staticmethod
    def _parse(raw: str) -> list[dict[str, Any]]:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = m.group(0) if m else raw
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return []
        qs = data.get("questions", []) if isinstance(data, dict) else []
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
