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
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from ...core.config import settings
from ...core.llm_async import AsyncLLMClient
from ...core.quiz_verify import freeze_rubric, is_well_formed, verify_questions, generate_verified_questions
from ...core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from ...core.quiz_illustration_policy import resolve_illustration_policy, IllustrationDisabled
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


def _ctx_grounding_context(ctx: AssessmentContext) -> tuple[str, dict[str, dict[str, Any]]]:
    """Render ctx.grounding_sources into the [命题事实边界] block + ref map.

    The generator never retrieves on its own (plan.md §5.3) — it only reads
    the plain-data evidence the API layer projected into the context.  Empty
    sources return ("", {}) and the prompt stays byte-identical to legacy.
    """
    sources = [s for s in (ctx.grounding_sources or []) if isinstance(s, dict)]
    if not sources:
        return "", {}
    from ...core.quiz_grounding import (QuizGroundingBundle, QuizSourceRef,
                                        render_grounding_context)
    refs: list[QuizSourceRef] = []
    for s in sources[:6]:
        refs.append(QuizSourceRef(
            file_id=str(s.get("file_id") or ""),
            chunk_id=str(s.get("chunk_id") or ""),
            filename=str(s.get("filename") or ""),
            page=s.get("page"), printed_page=s.get("printed_page"),
            section_path=list(s.get("section_path") or []),
            excerpt=str(s.get("excerpt") or ""),
            context_hash=str(s.get("context_hash") or ""),
            confidence=s.get("confidence")))
    bundle = QuizGroundingBundle(
        query=ctx.grounding_query, mode=ctx.grounding_mode or "textbook",
        tier=ctx.grounding_tier or "not_found",
        required=ctx.grounding_required, reason="",
        source_refs=refs)
    mapping = {f"src_{i}": dict(s) for i, s in enumerate(sources[:6], 1)}
    return render_grounding_context(bundle), mapping


def _pick_q_type(goal: AssessmentGoal) -> str:
    """Auto-select question type: MC for fast checks/diagnosis, short_answer
    for deeper practice. An explicit goal.q_type always wins."""
    if goal.q_type:
        return goal.q_type
    if goal.purpose in ("check", "diagnose", "adaptive"):
        return QuestionType.MULTIPLE_CHOICE
    return QuestionType.SHORT_ANSWER


def _parse_dict(raw: str) -> "dict[str, Any] | None":
    """Extract the first question JSON dict (pre-lift), or None."""
    from ...core.json_utils import extract_json_object
    data = extract_json_object(raw)
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


async def _revise_question(llm: "AsyncLLMClient", raw_q: dict[str, Any], *,
                           fixes: list[str],
                           grounding_context: str = "") -> "dict[str, Any] | None":
    """按 critic 修复意见回炉一道题（单次修订调用，输出仍是题目 dict）。

    critic 对「答案/解析有误但题目可修复」的题返回 revision_required +
    recommended_revision；直接丢弃会让单题生成连续失败（live 验收：CAT
    连续 3 次全被丢弃 → generation_failed，整个测评中断）。这里带着审核
    意见让模型修订原题；修订结果仍要过同一质量门才被接受。
    """
    NL = chr(10)
    prompt = (
        "你是命题修订专家。下面这道题未通过独立审核，审核意见列出了必须修复的问题。"
        "请输出修复后的完整题目 JSON 对象（与原题相同的 schema：type/stem/options/"
        "answer/explanation/knowledge_point/difficulty/rubric_criteria/"
        "equivalent_solutions；选择题保留 options，非选择题不得有 options）。"
        "保持知识点、题型与难度不变；除修复审核指出的问题外，若审核指出题干"
        "有歧义、条件缺失或符号约定不清，可以重写题干、更换数值或情境"
        "（考查同一知识点即可），不必逐字保留原题。答案、解析、选项与量规"
        "必须同步修正、彼此一致。若原题带 source_ref_ids 字段则原样保留。"
        "只输出该 JSON 对象，不要输出任何其它文字。" + NL + NL
        + "原题：" + NL + json.dumps(raw_q, ensure_ascii=False) + NL + NL
        + "审核修复意见：" + NL + "- " + (NL + "- ").join(fixes))
    if grounding_context:
        prompt += (NL + NL + "[命题事实边界]" + NL
                   + "修订后的题目仍不得引入下方证据之外的新教材专属事实。"
                   + NL + grounding_context)
    try:
        full, _usage = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=1500, disable_thinking=True)
    except Exception:
        return None
    from ...core.json_utils import extract_json_object
    data = extract_json_object(full)
    if isinstance(data, dict) and not str(data.get("stem") or "").strip():
        # 模型常按出题习惯把单题包进 {"questions": [...]} 壳，这里兼容。
        inner = data.get("questions")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            data = inner[0]
    if isinstance(data, dict) and str(data.get("stem") or "").strip()             and str(data.get("answer") or "").strip():
        return data
    logger.info("assessment gen: revision output unparseable (head=%.200s)",
                full or "")
    return None


async def _revise_after_critic(
        llm: "AsyncLLMClient", raw_q: dict[str, Any], *,
        bad: list[dict[str, Any]], topic: str, grade: str,
        difficulty_label: str,
        grounding_context: str = "") -> "tuple[dict[str, Any] | None, list]":
    """critic 丢弃后的一次修复回炉，返回 (新题目, kept)。

    只要审核给出了具体修复意见（recommended_revision）就尝试回炉——
    live 验收中 critic 对答案错误的题判 rejected 但同样附带修正指令，
    只认 revision_required 会让单题生成连续失败。修订题必须重新通过
    well-formed + 独立重解，不因“改过”而豁免质量门；复审仍不过 →
    (None, [])。
    """
    fixes = ["（{}）{}".format(b.get("_verdict") or "rejected",
                              str(b.get("_drop_reason") or "").strip())
             for b in bad]
    fixes = [f for f in fixes if not f.endswith("（）")][:4]
    if not fixes:
        return None, []
    revised = await _revise_question(
        llm, raw_q, fixes=fixes, grounding_context=grounding_context)
    if revised is None:
        return None, []
    from ...core.quiz_verify import prepare_illustrations
    safe, _bad = prepare_illustrations([revised], "off")
    if not safe:
        return None, []
    revised = safe[0]
    if not is_well_formed(revised):
        logger.info("assessment gen: revised question not well-formed")
        return None, []
    kept, bad2, critic_ok = await verify_questions(
        llm, [revised], topic=topic, grade=grade,
        difficulty=difficulty_label, grounding_context=grounding_context)
    if not critic_ok or not kept:
        logger.info("assessment gen: revision still dropped (topic=%s, "
                    "reasons=%s)", topic,
                    [str(b.get("_drop_reason") or "")[:160] for b in bad2])
        return None, []
    logger.info("assessment gen: revised after critic (topic=%s, fixes=%d)",
                topic, len(fixes))
    return kept[0], kept


async def generate_question(goal: AssessmentGoal, ctx: AssessmentContext,
                            *, llm: "AsyncLLMClient",
                            student_id: str = "",
                            budget: GenerationBudget | None = None,
                            use_blueprint: bool = True) -> "Question | None":
    """Generate one constraint-driven question. Returns None on any failure.

    The difficulty comes from the AssessmentContext (which the supervisor
    assembles from teaching_engine's difficulty engine), so the generated
    question lands in the zone of proximal development. The Bloom cognitive
    level is decided by the generating LLM itself (G4：布鲁姆数值档案已删，
    认知层级由 P1 蓝图约束与 LLM 判定); no LLM tag simply leaves the
    question untagged. Never raises.
    """
    concept = goal.concept or ctx.concept
    if not concept:
        logger.warning("assessment gen failed: no concept (goal=%s ctx=%s)",
                       goal.concept, ctx.concept)
        return None
    # Required+disabled propagates as a structured control-plane conflict.
    policy = resolve_illustration_policy(student_id, goal.illustration_request)
    if policy != "off" and not isinstance(llm, BudgetedLLM):
        llm = BudgetedLLM(llm, budget or GenerationBudget())
    difficulty = max(1, min(5, int(goal.difficulty or ctx.base_difficulty or 3)))
    q_type = _pick_q_type(goal)
    from ..teaching_engine.stage_profile import is_auto, normalize_grade
    grade = normalize_grade(ctx.grade or "")
    bloom_context = ""
    # 统一 Quiz Grounding（plan.md §5.3）：ctx grounding -> render ->
    # blueprint -> generation -> critic。检索发生在 API 层 helper，本函数
    # 只读 ctx.grounding_sources。
    grounding_context, ref_map = _ctx_grounding_context(ctx)
    strict_textbook = bool(ctx.grounding_required and ref_map)
    # Chat/工具出题保留 two-pass 设计。CAT 的单题首屏走 fast path：蓝图
    # 是质量增强而非题卡合同，跳过它可少一次串行 LLM 请求；生成仍经过
    # 同一 SVG 规范化和 critic 质量门。
    blueprint = ""
    if use_blueprint:
        from ...core.quiz_design import design_blueprint
        blueprint, _design_status = await design_blueprint(
            llm, topic=concept, grade=grade,
            difficulty=_difficulty_label(difficulty), count=1,
            focus="、".join(goal.assesses) if goal.assesses else "",
            grounding_context=grounding_context, illustration_policy=policy)
    prompt = _build_gen_prompt(grade=grade, concept=concept, difficulty=difficulty,
                               goal=goal, q_type=q_type,
                               bloom_context=bloom_context, blueprint=blueprint)
    avoid_stems = [str(s).strip() for s in (goal.avoid_stems or [])
                   if str(s).strip()]
    if avoid_stems:
        nl = chr(10)
        shown = nl + nl.join("- " + s[:120] for s in avoid_stems[:6])
        prompt += (nl + nl + "以下题目本次测评已经出过，禁止重复或仅换数字"
                   "（必须换情境、换考查角度、换数据）：" + nl + shown)
    if grounding_context:
        prompt += ("\n\n[命题事实边界]\n"
                   "本题的题干、正确答案与解析中的教材事实必须能由下方证据直接支持；"
                   "可以重新设计数值/情境，但不得引入证据之外的新教材专属事实。\n"
                   + grounding_context)
    try:
        def parse_candidate(raw: str) -> list[dict[str, Any]]:
            candidate = _parse_dict(raw)
            return [candidate] if candidate is not None and candidate.get("type") == q_type else []

        questions, verification = await generate_verified_questions(
            llm, make_prompt=lambda: prompt, parse=parse_candidate,
            topic=concept, grade=grade, difficulty=_difficulty_label(difficulty),
            temperature=0.4, max_tokens=4500 if policy != "off" else 1500,
            grounding_context=grounding_context, illustration_policy=policy,
            max_attempts=1, repair_max_tokens=4500 if policy != "off" else 1500,
            required_type=q_type)
        current = resolve_illustration_policy(student_id, goal.illustration_request)
        if not questions:
            return None
        raw_q = questions[0]
        # Preserve the per-question audit in the frozen snapshot.
        verification.update(raw_q.get("verification") or {})
        verification["illustration_policy"] = policy
        if current == "off" and raw_q.get("illustration"):
            return None
        if isinstance(llm, BudgetedLLM):
            verification["generation_calls"] = llm.budget.calls
            verification["illustration_repairs"] = llm.budget.repairs
        # Provenance 映射（plan.md §5.3/§5.4）：只认 ctx.grounding_sources
        # 对应的 src_N 短 id，模型返回的其它 ref 一律丢弃。
        raw_ids = raw_q.pop("source_ref_ids", None)
        refs: list[dict[str, Any]] = []
        if isinstance(raw_ids, list):
            for sid in raw_ids:
                key = str(sid).strip()
                if key in ref_map:
                    refs.append(dict(ref_map[key]))
        if strict_textbook and not refs:
            # strict 教材测评：没有有效 source ref 的题不能标 grounded，
            # 也不能 fail-open 成教材题（plan.md §4.6 失败语义）。
            logger.warning("assessment gen failed: strict grounding without "
                           "valid source refs (concept=%s)", concept)
            return None
        # W3/D04（承接 W2/A14）：CAT 单题路径此前沿用 LLM 的裸 "id": 1，
        # 跨会话/跨题套不唯一；稳定 id 后在其上冻结量规。
        raw_q["id"] = f"q_{uuid.uuid4().hex[:8]}_1"
        rubric = freeze_rubric(raw_q, raw_q["id"])
        q = Question.from_quiz_dict(raw_q, concept=concept, difficulty=difficulty)
        if not q.stem or not q.answer:
            logger.warning("assessment gen failed: empty stem/answer after "
                           "lift (concept=%s)", concept)
            return None
        q.assesses = list(goal.assesses)
        q.forbidden = list(goal.forbidden)
        # W2/A05: the verification audit rides WITH the question (into the
        # session file) so the evidence gate can weigh it at grading time.
        q.verification = verification
        if rubric is not None:
            q.rubric = rubric
        # Grounded provenance（plan.md §5.4）：单一 Question 合同携带。
        if refs:
            q.grounding_mode = "textbook"
            q.grounding_tier = ctx.grounding_tier or "found"
            q.source_refs = refs
            q.verification["grounding_verification"] = (
                "content_checked" if verification.get("critic") == "ok"
                else "unavailable")
        return q
    except IllustrationDisabled:
        raise
    except Exception:
        logger.exception("assessment gen failed: unexpected error "
                         "(concept=%s)", concept)
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
