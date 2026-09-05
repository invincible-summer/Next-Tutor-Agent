"""Constraint-driven question generator (M4 Phase 2).

This is NOT a replacement for the generate_quiz / fit_quiz tools. Those answer
"give me 3 problems on buoyancy" (student-initiated, topic-driven). This
generator answers a different question the Teaching Engine asks internally:

    "I just taught opening direction at difficulty 3; give me ONE question that
     probes vertex identification and forbids calculus -- to close the turn."

That is constraint-driven single-question generation, and it is what turns M3's
advisory next_check ("test this at this difficulty") into an actionable probe.

Design (same shape as the quiz tool, but single-question + constraint-aware):
  - Constraint injection: the prompt encodes WHAT to probe (assesses), HOW HARD
    (difficulty), and what to FORBID (forbidden methods), so the generated
    question is targeted rather than generic.
  - LLM only: question generation is inherently generative, so unlike the
    rule-based evaluator/CAT rules this module calls the LLM.
  - Reuses Question.from_quiz_dict to lift the JSON output, and the quiz tool's
    JSON-extraction pattern. Never raises; failures return None so the
    supervisor simply skips the closing check (M3 behavior).
"""
from __future__ import annotations

import json
import re
from typing import Any

from ...core.config import settings
from ...core.llm_async import AsyncLLMClient
from ...core.quiz_verify import is_well_formed, verify_questions
from .question import Question, QuestionType
from .state import AssessmentContext, AssessmentGoal

# 1..5 internal difficulty -> human label for the prompt
_DIFFICULTY_ZH = {1: "入门", 2: "基础", 3: "中等", 4: "进阶", 5: "挑战"}

_GEN_PROMPT = """你是命题专家。为学段「{grade}」学生，围绕知识点「{concept}」出 1 道检测题，难度：{difficulty_zh}（{difficulty}/5）。
{constraints}
{blueprint}
只输出一个 JSON 对象，不要任何其它文字、不要 markdown 代码块。格式：
{{
  "questions": [
    {{
      "id": 1,
      "type": "{q_type}",
      "stem": "题干",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "B",
      "explanation": "为什么选 B，分步讲解（80-200 字）",
      "knowledge_point": "对应知识点",
      "difficulty": "{difficulty_label}"
    }}
  ]
}}
要求：
 - 题目难度与学段、目标难度匹配，{grade} 学生能看懂。该学段难度锚点（难度标定的参照系，必须遵守）：{anchor}
 - options 仅在 type 为 multiple_choice 时提供；填空题用 fill_blank，简答用 short_answer，这两类不需要 options，answer 直接写答案文本。
 - explanation 分步：先点明考点与切入点，再列公式/数据/中间结果，最后给结论与易错点。禁止只重复答案。
 - 所有公式用 LaTeX（$...$ 行内，$$...$$ 独立）；数学环境内的中文（含中文下标）用 \\text{{}} 包裹，如 $c_{{\\text{{待测}}}}$。数字与中英文间保留空格。
 - 严格输出可被 json.loads 解析的纯 JSON。"""


# 自动学段专用检测题 prompt（P1）：省略学段锚点，难度按知识点本身标定。
_GEN_PROMPT_AUTO = """你是命题专家。围绕知识点「{concept}」出 1 道检测题，难度：{difficulty_zh}（{difficulty}/5，按知识点本身标定）。
{constraints}
{blueprint}
只输出一个 JSON 对象，不要任何其它文字、不要 markdown 代码块。格式：
{{
  "questions": [
    {{
      "id": 1,
      "type": "{q_type}",
      "stem": "题干",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "B",
      "explanation": "为什么选 B，分步讲解（80-200 字）",
      "knowledge_point": "对应知识点",
      "difficulty": "{difficulty_label}"
    }}
  ]
}}
要求：
 - 题目难度与知识点、目标难度匹配。
 - options 仅在 type 为 multiple_choice 时提供；填空题用 fill_blank，简答用 short_answer，这两类不需要 options，answer 直接写答案文本。
 - explanation 分步：先点明考点与切入点，再列公式/数据/中间结果，最后给结论与易错点。禁止只重复答案。
 - 所有公式用 LaTeX（$...$ 行内，$$...$$ 独立）；数学环境内的中文（含中文下标）用 \\text{{}} 包裹，如 $c_{{\\text{{待测}}}}$。数字与中英文间保留空格。
 - 严格输出可被 json.loads 解析的纯 JSON。"""


def _difficulty_label(d: int) -> str:
    """Map the 1..5 internal scale to the quiz tool's easy/medium/hard triple so
    the generated question round-trips through Question.from_quiz_dict cleanly."""
    if d <= 2:
        return "easy"
    if d <= 3:
        return "medium"
    return "hard"


def _constraint_block(goal: AssessmentGoal, *, bloom_context: str = "") -> str:
    """Render the assesses/forbidden constraints + Bloom guidance as prompt
    directives. The Bloom block asks the LLM to pick the cognitive level in
    context (free, no ladder) and tag the question with bloom_level."""
    lines = []
    if goal.assesses:
        lines.append("本题必须检测以下子能力：" + "、".join(goal.assesses) + "。")
    if goal.forbidden:
        lines.append("禁止使用以下方法/知识：" + "、".join(goal.forbidden) + "。")
    if not lines:
        lines.append("自由命题，覆盖该知识点的核心考查点。")
    from ...core.bloom import guidance_block
    lines.append(guidance_block(focus=goal.bloom_focus,
                                context_line=bloom_context))
    return "\n".join(lines)


def _pick_q_type(goal: AssessmentGoal) -> str:
    """Auto-select question type: MC for fast checks/diagnosis, short_answer
    for deeper practice. An explicit goal.q_type always wins."""
    if goal.q_type:
        return goal.q_type
    if goal.purpose in ("check", "diagnose"):
        return QuestionType.MULTIPLE_CHOICE
    return QuestionType.SHORT_ANSWER


def _parse_dict(raw: str) -> "dict[str, Any] | None":
    """Extract the first question JSON dict (pre-lift), or None."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = m.group(0) if m else raw
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    qs = data.get("questions", []) if isinstance(data, dict) else []
    if not qs or not isinstance(qs[0], dict):
        return None
    return qs[0]


def _parse(raw: str, *, concept: str, difficulty: int) -> "Question | None":
    """Extract the first question JSON and lift it via Question.from_quiz_dict.
    Mirrors the quiz tool's extraction but returns a single Question."""
    raw_q = _parse_dict(raw)
    if raw_q is None:
        return None
    q = Question.from_quiz_dict(raw_q, concept=concept, difficulty=difficulty)
    if not q.stem or not q.answer:
        return None
    return q


async def generate_question(goal: AssessmentGoal, ctx: AssessmentContext,
                            *, llm: "AsyncLLMClient",
                            student_id: str = "") -> "Question | None":
    """Generate one constraint-driven question. Returns None on any failure.

    The difficulty comes from the AssessmentContext (which the supervisor
    assembles from teaching_engine's difficulty engine), so the generated
    question lands in the zone of proximal development. The Bloom cognitive
    level is decided by the generating LLM itself, grounded in the student's
    cognitive-profile snapshot (student_id -> bloom_profile.context_line);
    no data / no LLM tag simply leaves the question untagged. Never raises.
    """
    concept = goal.concept or ctx.concept
    if not concept:
        return None
    difficulty = max(1, min(5, int(goal.difficulty or ctx.base_difficulty or 3)))
    q_type = _pick_q_type(goal)
    from ..teaching_engine.stage_profile import is_auto, normalize_grade
    grade = normalize_grade(ctx.grade or "")
    bloom_context = ""
    if student_id:
        try:
            from ...core.bloom_profile import context_line as bloom_ctx
            bloom_context = bloom_ctx(student_id, concept)
        except Exception:
            bloom_context = ""
    # 两轮出题（QUIZ_DESIGN_MODE=two_pass）：先跑蓝图设计轮（单题的设计要点：
    # 深层考点、陷阱、如何体现约束），失败自动回退单轮。focus 用约束子能力。
    from ...core.quiz_design import design_blueprint
    blueprint, _design_status = await design_blueprint(
        llm, topic=concept, grade=grade,
        difficulty=_difficulty_label(difficulty), count=1,
        focus="、".join(goal.assesses) if goal.assesses else "")
    prompt = _build_gen_prompt(grade=grade, concept=concept, difficulty=difficulty,
                               goal=goal, q_type=q_type,
                               bloom_context=bloom_context, blueprint=blueprint)
    try:
        # Non-streaming call with thinking disabled (same hardening as the
        # quiz tools, DESIGN §21.1): a reasoning model can otherwise burn the
        # whole budget on reasoning_content and return an empty answer.
        full, _usage = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=1500, disable_thinking=True)
        raw_q = _parse_dict(full)
        if raw_q is None:
            return None
        # Shared quality gate: structural check + independent critic re-solve.
        # A failed/dropped question returns None so the supervisor simply
        # skips the closing check instead of quizzing with a broken key.
        verification: dict[str, Any] = {
            "mode": settings.quiz_verify_mode, "critic": "skipped",
            "answer_verified": False}
        if settings.quiz_verify_mode != "off" and not is_well_formed(raw_q):
            return None
        if settings.quiz_verify_mode == "critic":
            kept, _bad, critic_ok = await verify_questions(
                llm, [raw_q], topic=concept, grade=grade,
                difficulty=_difficulty_label(difficulty))
            verification["critic"] = "ok" if critic_ok else "error"
            if critic_ok and not kept:
                return None
        verification["answer_verified"] = (
            settings.quiz_verify_mode == "critic"
            and verification["critic"] == "ok")
        q = Question.from_quiz_dict(raw_q, concept=concept, difficulty=difficulty)
        if not q.stem or not q.answer:
            return None
        q.assesses = list(goal.assesses)
        q.forbidden = list(goal.forbidden)
        # W2/A05: the verification audit rides WITH the question (into the
        # session file) so the evidence gate can weigh it at grading time.
        q.verification = verification
        return q
    except Exception:
        return None


def _build_gen_prompt(*, grade: str, concept: str, difficulty: int,
                      goal: "AssessmentGoal", q_type: str,
                      bloom_context: str = "", blueprint: str = "") -> str:
    """Render the single-question gen prompt, auto-aware (P1).

    ``grade`` already normalized ("") = auto; the prompt then drops the
    stage anchor line and frames difficulty relative to the concept itself.
    ``blueprint`` is the round-1 design block from core.quiz_design ("" when
    the design pass is off or fell back).
    """
    from ..teaching_engine.stage_profile import is_auto
    if is_auto(grade):
        return _GEN_PROMPT_AUTO.format(
            concept=concept, difficulty=difficulty,
            difficulty_zh=_DIFFICULTY_ZH.get(difficulty, "中等"),
            difficulty_label=_difficulty_label(difficulty),
            constraints=_constraint_block(goal, bloom_context=bloom_context),
            blueprint=blueprint,
            q_type=q_type)
    from ..teaching_engine.stage_profile import difficulty_anchor
    return _GEN_PROMPT.format(
        grade=grade or "本科", concept=concept,
        difficulty=difficulty, difficulty_zh=_DIFFICULTY_ZH.get(difficulty, "中等"),
        difficulty_label=_difficulty_label(difficulty),
        constraints=_constraint_block(goal, bloom_context=bloom_context),
        blueprint=blueprint,
        q_type=q_type,
        anchor=difficulty_anchor(grade or "本科"),
    )
