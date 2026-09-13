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
from ..core.quiz_verify import RUBRIC_REQUIREMENT, generate_verified_questions
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok, partial_result

VALID_GRADES = ("小学", "初中", "高中", "本科")
VALID_DIFFICULTY = ("easy", "medium", "hard")

_FIT_PROMPT = """你是一位资深命题专家，擅长"拟合出题"——从一道参考题目出发，生成考察同一知识点体系、但角度和结构各异的变式题。

## 参考题目
{reference}

## 任务
为学段「{grade}」学生，围绕上述参考题目的知识点，拟合生成 {count} 道变式题。难度：{difficulty_zh}。

## 拟合策略（必须遵循，不能只换数据）
你要像命题专家一样先拆解参考题，再按以下三层策略生成变式：

### 第一层：拆题（内部思考，不输出）
识别参考题的：核心知识点、考查能力、解题路径结构、陷阱点。

### 第二层：变式生成
每道变式题必须明确采用以下策略之一（在 knowledge_point 字段末尾标注 [变式:X]）：
- [变式:情境迁移] 同一知识结构，换一个完全不同的生活/工程情境。例如参考题是"木块浮力"，变式可以换成"轮船吃水深度"或"热气球升空"——核心物理规律不变，但学生需要在新情境中识别它。
- [变式:结构反转] 同一知识点，但反转问题结构。例如参考题"已知密度求浮力"，变式可以是"已知浮力求密度"——考查同一公式但反向应用，训练逆向思维。
- [变式:条件增减] 增加或删减一个已知条件，使解题路径改变。例如参考题有3个已知量，变式只给2个，需额外推导——训练学生判断信息充分性。
- [变式:综合嫁接] 将参考题考点与一个相关知识点嫁接，形成小综合。例如浮力+压强、运动学+能量——训练知识迁移能力。
- [变式:陷阱复制] 复制参考题的关键易错点，但换一个新壳子让学生再次踩坑——强化对常见错误的免疫力。

### 约束
- 变式题不能只是简单换数字。必须有结构性的变化。
- 每道变式题的 knowledge_point 要写明它用了哪种变式策略。
- 至少有一道采用[变式:情境迁移]，至少有一道采用[变式:结构反转]或[变式:条件增减]。
- 题目难度与学段匹配，{grade} 学生能看懂。该学段难度锚点：{anchor}
- 题干、选项、解析中所有公式用 LaTeX 语法（$...$ 行内，$$...$$ 独立）；数学环境内的中文（含中文下标）用 \\text{{}} 包裹，如 $c_{{\\text{{待测}}}}$。数字与中英文间保留空格。

## 输出格式
只输出一个 JSON 对象，不要任何其它文字、不要 markdown 代码块：
{{
  "analysis": "一句话拆解：参考题考什么知识点、什么能力",
  "questions": [
    {{
      "id": 1,
      "type": "multiple_choice",
      "stem": "题干",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "B",
      "explanation": "分步详解：知识点 -> 推导过程 -> 结论 -> 易错点（80-200字）",
      "knowledge_point": "知识点名 [变式:情境迁移]",
      "difficulty": "{difficulty}"
    }}
  ]
}}
要求：
- options 仅在 type 为 multiple_choice 时提供；填空题用 fill_blank，简答用 short_answer，这两类不需要 options。
- explanation 分步讲解，禁止元思考泄露、禁止自我质疑，只写给学生看的讲解。
- 严格输出可被 json.loads 解析的纯 JSON。""" + RUBRIC_REQUIREMENT

_DIFFICULTY_ZH = {"easy": "基础", "medium": "中等", "hard": "挑战"}

# 自动学段专用拟合 prompt（P1）：省略学段难度锚点，改注自适应难度说明。
_FIT_PROMPT_AUTO = """你是一位资深命题专家，擅长"拟合出题"——从一道参考题目出发，生成考察同一知识点体系、但角度和结构各异的变式题。

## 参考题目
{reference}

## 任务
为{grade}的学生，围绕上述参考题目的知识点，拟合生成 {count} 道变式题。难度：{difficulty_zh}。

## 拟合策略（必须遵循，不能只换数据）
你要像命题专家一样先拆解参考题，再按以下三层策略生成变式：

### 第一层：拆题（内部思考，不输出）
识别参考题的：核心知识点、考查能力、解题路径结构、陷阱点。

### 第二层：变式生成
每道变式题必须明确采用以下策略之一（在 knowledge_point 字段末尾标注 [变式:X]）：
- [变式:情境迁移] 同一知识结构，换一个完全不同的生活/工程情境。例如参考题是"木块浮力"，变式可以换成"轮船吃水深度"或"热气球升空"——核心物理规律不变，但学生需要在新情境中识别它。
- [变式:结构反转] 同一知识点，但反转问题结构。例如参考题"已知密度求浮力"，变式可以是"已知浮力求密度"——考查同一公式但反向应用，训练逆向思维。
- [变式:条件增减] 增加或删减一个已知条件，使解题路径改变。例如参考题有3个已知量，变式只给2个，需额外推导——训练学生判断信息充分性。
- [变式:综合嫁接] 将参考题考点与一个相关知识点嫁接，形成小综合。例如浮力+压强、运动学+能量——训练知识迁移能力。
- [变式:陷阱复制] 复制参考题的关键易错点，但换一个新壳子让学生再次踩坑——强化对常见错误的免疫力。

### 约束
- 变式题不能只是简单换数字。必须有结构性的变化。
- 每道变式题的 knowledge_point 要写明它用了哪种变式策略。
- 至少有一道采用[变式:情境迁移]，至少有一道采用[变式:结构反转]或[变式:条件增减]。
- 题目难度按知识点本身标定，与目标难度匹配。
- 题干、选项、解析中所有公式用 LaTeX 语法（$...$ 行内，$$...$$ 独立）；数学环境内的中文（含中文下标）用 \\text{{}} 包裹，如 $c_{{\\text{{待测}}}}$。数字与中英文间保留空格。

## 输出格式
只输出一个 JSON 对象，不要任何其它文字、不要 markdown 代码块：
{{
  "analysis": "一句话拆解：参考题考什么知识点、什么能力",
  "questions": [
    {{
      "id": 1,
      "type": "multiple_choice",
      "stem": "题干",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "B",
      "explanation": "分步详解：知识点 -> 推导过程 -> 结论 -> 易错点（80-200字）",
      "knowledge_point": "知识点名 [变式:情境迁移]",
      "difficulty": "{difficulty}"
    }}
  ]
}}
要求：
- options 仅在 type 为 multiple_choice 时提供；填空题用 fill_blank，简答用 short_answer，这两类不需要 options。
- explanation 分步讲解，禁止元思考泄露、禁止自我质疑，只写给学生看的讲解。
- 严格输出可被 json.loads 解析的纯 JSON。""" + RUBRIC_REQUIREMENT


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
                 grounding_provider: Any | None = None) -> None:
        self._llm = llm
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
        questions, verification = await generate_verified_questions(
            self._llm, make_prompt=make_prompt, parse=self._parse,
            topic=reference[:60], grade=grade, difficulty=difficulty,
            temperature=0.5, max_tokens=8000,
            raw_preview_chars=3000,
            grounding_context=grounding_context)
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
                "未能生成通过校验的变式题，已返回模型原始输出片段。")
        note = "（已通过答案校验）" if verification.get("answer_verified") else ""
        return ok(self.name,
            {"reference": reference[:200], "grade": grade,
             "difficulty": difficulty, "questions": questions,
             "answer_verified": verification.get("answer_verified", False),
             "verification": verification,
             "grounding": grounding_meta},
            f"拟合生成 {len(questions)} 道变式题{note}。")

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
