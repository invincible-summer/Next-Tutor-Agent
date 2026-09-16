from __future__ import annotations

import copy
import logging
import time
import uuid
from typing import Any

from ...core.config import settings
from ...core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from ...core.quiz_illustration_policy import IllustrationDisabled, resolve_illustration_policy
from ...core.quiz_verify import freeze_rubric, generate_verified_questions, is_well_formed, verify_questions
from ...prompts.registry import get as _prompt
from .question import Question, QuestionType
from .state import AssessmentContext, AssessmentGoal

logger = logging.getLogger(__name__)
_DIFFICULTY_ZH = {1: "入门", 2: "基础", 3: "中等", 4: "进阶", 5: "挑战"}
_GEN_PROMPT = _prompt("assessment_generate").text
_GEN_PROMPT_AUTO = _prompt("assessment_generate_auto").text
CAT_TEXT_DEADLINE_SECONDS = 27.0


def _difficulty_label(difficulty: int) -> str:
    return "easy" if difficulty <= 2 else "medium" if difficulty == 3 else "hard"


def _constraint_block(goal: AssessmentGoal, *, bloom_context: str = "") -> str:
    from ...core.bloom import guidance_block
    lines = []
    if goal.assesses:
        lines.append("本题必须检测以下子能力：" + "、".join(goal.assesses) + "。")
    if goal.forbidden:
        lines.append("禁止使用以下方法/知识：" + "、".join(goal.forbidden) + "。")
    if not lines:
        lines.append("自由命题，覆盖该知识点的核心考查点。")
    lines.append(guidance_block(focus=goal.bloom_focus, context_line=bloom_context))
    return "\n".join(lines)


def _ctx_grounding_context(ctx: AssessmentContext) -> tuple[str, dict[str, dict[str, Any]]]:
    from ...core.quiz_grounding import QuizGroundingBundle, QuizSourceRef, render_grounding_context
    sources = [source for source in (ctx.grounding_sources or [])
               if isinstance(source, dict)][:6]
    if not sources:
        return "", {}
    refs = [QuizSourceRef(
        file_id=str(source.get("file_id") or ""),
        chunk_id=str(source.get("chunk_id") or ""),
        filename=str(source.get("filename") or ""),
        page=source.get("page"), printed_page=source.get("printed_page"),
        section_path=list(source.get("section_path") or []),
        excerpt=str(source.get("excerpt") or ""),
        context_hash=str(source.get("context_hash") or ""),
        confidence=source.get("confidence"),
    ) for source in sources]
    bundle = QuizGroundingBundle(
        query=ctx.grounding_query, mode=ctx.grounding_mode or "textbook",
        tier=ctx.grounding_tier or "not_found", required=ctx.grounding_required,
        reason="", source_refs=refs)
    return render_grounding_context(bundle), {
        f"src_{index}": dict(source) for index, source in enumerate(sources, 1)}


def _pick_q_type(goal: AssessmentGoal) -> str:
    if goal.q_type:
        return goal.q_type
    return (QuestionType.MULTIPLE_CHOICE
            if goal.purpose in {"check", "diagnose", "adaptive"}
            else QuestionType.SHORT_ANSWER)


def _parse_dict(raw: str) -> dict[str, Any] | None:
    from ...core.json_utils import extract_json_object
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("stem"), str):
        return data
    questions = data.get("questions")
    if isinstance(questions, list):
        return next((item for item in questions if isinstance(item, dict)), None)
    return None


def _parse(raw: str, *, concept: str, difficulty: int) -> Question | None:
    candidate = _parse_dict(raw)
    if candidate is None:
        return None
    question = Question.from_quiz_dict(candidate, concept=concept, difficulty=difficulty)
    return question if question.stem and question.answer else None


async def _revise_question(llm, raw_q: dict[str, Any], *, fixes: list[str],
                           grounding_context: str = "") -> dict[str, Any] | None:
    from ...core.quiz_verify import _revise_dropped
    revised = await _revise_dropped(
        llm, [{**raw_q, "_drop_reason": "\n".join(fixes)}],
        topic=str(raw_q.get("knowledge_point") or ""), grade="",
        difficulty=str(raw_q.get("difficulty") or ""),
        grounding_context=grounding_context, illustration_policy="off",
        max_tokens=3500)
    return revised[0] if revised else None


async def _revise_after_critic(llm, raw_q: dict[str, Any], *, bad: list[dict[str, Any]],
                               topic: str, grade: str, difficulty_label: str,
                               grounding_context: str = ""):
    from ...core.quiz_verify import prepare_illustrations
    revised = await _revise_question(
        llm, raw_q, fixes=[str(item.get("_drop_reason") or "") for item in bad],
        grounding_context=grounding_context)
    safe, _ = prepare_illustrations([revised], "off") if revised else ([], [])
    if not safe or not is_well_formed(safe[0]):
        return None, []
    kept, _, critic_ok = await verify_questions(
        llm, safe, topic=topic, grade=grade,
        difficulty=difficulty_label, grounding_context=grounding_context)
    return (kept[0], kept) if critic_ok and kept else (None, [])


def _build_gen_prompt(*, grade: str, concept: str, difficulty: int,
                      goal: AssessmentGoal, q_type: str,
                      bloom_context: str = "", blueprint: str = "") -> str:
    from ..teaching_engine.stage_profile import difficulty_anchor, is_auto
    fields = dict(
        concept=concept, difficulty=difficulty,
        difficulty_zh=_DIFFICULTY_ZH.get(difficulty, "中等"),
        difficulty_label=_difficulty_label(difficulty),
        constraints=_constraint_block(goal, bloom_context=bloom_context),
        blueprint=blueprint, q_type=q_type)
    if is_auto(grade):
        return _GEN_PROMPT_AUTO.format(**fields)
    return _GEN_PROMPT.format(
        **fields, grade=grade or "本科", anchor=difficulty_anchor(grade or "本科"))


def _lift(candidate: dict[str, Any], meta: dict[str, Any], *,
          goal: AssessmentGoal, ctx: AssessmentContext, concept: str,
          difficulty: int, ref_map: dict[str, dict[str, Any]],
          budget: GenerationBudget) -> Question | None:
    raw = copy.deepcopy(candidate)
    if not is_well_formed(raw):
        return None
    if any(len(str(raw.get(key) or "")) > maximum for key, maximum in (
            ("stem", 3600), ("answer", 4000), ("explanation", 6000))):
        return None
    raw_ids = raw.pop("source_ref_ids", [])
    refs = [dict(ref_map[key]) for key in raw_ids
            if isinstance(key, str) and key in ref_map] if isinstance(raw_ids, list) else []
    if ctx.grounding_required and not refs:
        return None
    verification = {**meta, **(raw.get("verification") or {}), **budget.summary()}
    verified = meta.get("critic") == "ok" and verification.get("status") == "passed"
    verification["answer_verified"] = verified
    prefix = "q_" if verified else "q_draft_"
    raw["id"] = prefix + uuid.uuid4().hex[:24]
    result = Question.from_quiz_dict(raw, concept=concept, difficulty=difficulty)
    result.assesses = list(goal.assesses)
    result.forbidden = list(goal.forbidden)
    result.verification = verification
    result.rubric = freeze_rubric(raw, result.id) or {}
    result.knowledge_points = [str(point)[:55] for point in result.knowledge_points[:3]]
    if refs:
        result.grounding_mode = "textbook"
        result.grounding_tier = ctx.grounding_tier or "partial"
        result.source_refs = refs[:6]
        result.verification["grounding_verification"] = "content_checked" if verified else "unavailable"
    return result


def _self_check(concept: str, budget: GenerationBudget) -> Question:
    label = concept[:160]
    return Question(
        id="q_draft_" + uuid.uuid4().hex[:24], concept=concept,
        knowledge_points=[label[:55]], q_type=QuestionType.SHORT_ANSWER,
        difficulty=1,
        stem=("【保底自检草稿：不是已审核测评题，不自动评分】\n\n"
              f"请围绕「{label}」完成以下自检：\n\n"
              "1. 用自己的话写出核心定义，并列出适用条件。\n"
              "2. 给出一个例子，解释它为什么满足上述定义或条件。\n"
              "3. 写出一个容易混淆的情形，并说明需要查证的地方。\n\n"
              "请结合你选定的教材自行核对；本草稿不声称引用了已检索到的教材。"),
        answer="本草稿没有经核验的标准答案，不用于自动判分。",
        explanation="这是生成链路不可用时的本地自检任务，不是模型生成或审核通过的答案。",
        verification={"status": "unreviewed", "answer_verified": False,
                      "critic": "unavailable", "fallback": "self_check",
                      **budget.summary()},
        rubric={"criteria": [{"id": "self_check", "description": "仅供自检，不用于自动评分",
                               "weight": 1.0, "critical": False}]},
    )


async def generate_question(goal: AssessmentGoal, ctx: AssessmentContext, *,
                            llm, student_id: str = "",
                            budget: GenerationBudget | None = None,
                            use_blueprint: bool = True) -> Question | None:
    concept = str(goal.concept or ctx.concept or "").strip()
    if not concept:
        return None
    policy = resolve_illustration_policy(student_id, goal.illustration_request)
    if isinstance(llm, BudgetedLLM):
        budget = llm.budget
        llm = llm.llm
    cat_mode = not use_blueprint
    budget = budget or (GenerationBudget(max_calls=settings.assessment_generation_max_calls)
                        if cat_mode else GenerationBudget())
    if cat_mode:
        # CAT is text-first.  The same shared budget is capped here so outer
        # retry loops cannot silently turn a 27s text phase into multiple 27s
        # attempts.  SVG enrichment has its own independent 18s budget.
        budget.deadline = min(budget.deadline,
                              time.monotonic() + CAT_TEXT_DEADLINE_SECONDS)
    difficulty = max(1, min(5, int(goal.difficulty or ctx.base_difficulty or 3)))
    q_type = _pick_q_type(goal)
    if q_type not in {"multiple_choice", "fill_blank", "short_answer"}:
        return None
    best = None
    baseline = None
    try:
        from ..teaching_engine.stage_profile import normalize_grade
        grade = normalize_grade(ctx.grade or "")
        grounding, ref_map = _ctx_grounding_context(ctx)
        if ctx.grounding_required and not ref_map:
            return None
        blueprint = ""
        if use_blueprint:
            from ...core.quiz_design import design_blueprint
            blueprint, _ = await design_blueprint(
                BudgetedLLM(llm, budget),
                topic=concept, grade=grade, difficulty=_difficulty_label(difficulty),
                count=1, focus="、".join(goal.assesses),
                grounding_context=grounding, illustration_policy=policy)
        prompt = _build_gen_prompt(
            grade=grade, concept=concept, difficulty=difficulty,
            goal=goal, q_type=q_type, blueprint=blueprint)
        if goal.avoid_stems:
            prompt += "\n本次已出题干，避免重复：\n" + "\n".join(
                str(stem)[:200] for stem in goal.avoid_stems[-6:])
        if grounding:
            prompt += "\n\n[命题事实边界]\n" + grounding

        async def attempt(phase_policy: str, phase_deadline: float):
            def parse(raw: str):
                candidate = _parse_dict(raw)
                return [candidate] if candidate and candidate.get("type") == q_type else []
            candidates, meta = await generate_verified_questions(
                BudgetedLLM(llm, budget, call_timeout=12 if cat_mode else None,
                            phase_deadline=phase_deadline),
                make_prompt=lambda: prompt, parse=parse,
                topic=concept, grade=grade, difficulty=_difficulty_label(difficulty),
                temperature=0.3, max_tokens=4500 if phase_policy != "off" else 3500,
                grounding_context=grounding, illustration_policy=phase_policy,
                max_attempts=1, repair_max_tokens=4500 if phase_policy != "off" else 3500,
                required_type=q_type)
            for candidate in candidates:
                try:
                    result = _lift(candidate, meta, goal=goal, ctx=ctx, concept=concept,
                                   difficulty=difficulty, ref_map=ref_map, budget=budget)
                    if result is not None:
                        return result
                except (TypeError, ValueError):
                    continue
            return None

        if cat_mode:
            # Never regenerate a second complete question merely to obtain an
            # SVG.  The accepted text question is frozen first; the assessment
            # illustration endpoint enriches that exact question afterwards.
            best = await attempt("off", budget.deadline)
            baseline = best
        else:
            best = await attempt(policy, budget.deadline)
    except IllustrationDisabled:
        raise
    except Exception:
        logger.exception("assessment generation failed")
    current_policy = resolve_illustration_policy(student_id, goal.illustration_request)
    if best is not None and best.illustration is not None and current_policy == "off":
        best = baseline
    if best is None and cat_mode and not ctx.grounding_required:
        best = _self_check(concept, budget)
    if best is not None:
        best.verification.update(budget.summary())
        logger.info("assessment generation result=%s metrics=%s",
                    "draft" if best.id.startswith("q_draft_") else "verified", budget.summary())
    return best
