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
import uuid
from typing import Any

from ...core.config import settings
from ...core.llm_async import AsyncLLMClient
from ...core.quiz_verify import freeze_rubric, is_well_formed, verify_questions
from ...prompts.registry import get as _prompt
from .question import Question, QuestionType
from .state import AssessmentContext, AssessmentGoal

# 1..5 internal difficulty -> human label for the prompt
_DIFFICULTY_ZH = {1: "入门", 2: "基础", 3: "中等", 4: "进阶", 5: "挑战"}

# W3/D04: prompt 文本统一入注册表（assessment_generate@1.0.0 / _auto），
# 文本含追加的量规契约；此处薄 re-export 兼容旧引用。
_GEN_PROMPT = _prompt("assessment_generate").text
_GEN_PROMPT_AUTO = _prompt("assessment_generate_auto").text


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
        # W3/D04（承接 W2/A14）：CAT 单题路径此前沿用 LLM 的裸 "id": 1，
        # 跨会话/跨题套不唯一；稳定 id 后在其上冻结量规。
        raw_q["id"] = f"q_{uuid.uuid4().hex[:8]}_1"
        rubric = freeze_rubric(raw_q, raw_q["id"])
        q = Question.from_quiz_dict(raw_q, concept=concept, difficulty=difficulty)
        if not q.stem or not q.answer:
            return None
        q.assesses = list(goal.assesses)
        q.forbidden = list(goal.forbidden)
        # W2/A05: the verification audit rides WITH the question (into the
        # session file) so the evidence gate can weigh it at grading time.
        q.verification = verification
        if rubric is not None:
            q.rubric = rubric
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
