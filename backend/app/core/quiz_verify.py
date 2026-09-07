"""Post-generation quiz verification (generator-critic pattern).

Generation is a single LLM call with no guarantee that the answer key is
actually correct.  This module adds the missing quality gate, shared by all
three generation paths (generate_quiz / fit_quiz / M4 constraint generator):

  1. Deterministic structural checks (``is_well_formed``) — MC answer letter
     must be inside options, options non-empty and de-duplicated, stem and
     explanation present.  Ill-formed questions are dropped, not delivered.
  2. LLM critic (``verify_questions``) — one extra ``complete`` call that
     independently re-solves each question and flags wrong answer keys or
     broken questions.  Flagged questions are dropped.

Fail-open philosophy (same as every other guardrail in the system): if the
critic itself errors or returns unparseable output, the surviving questions
are delivered anyway and the incident is recorded in the returned meta —
verification must never take down quiz generation.

Mode is controlled by ``QUIZ_VERIFY_MODE``: ``critic`` (default, both layers),
``basic`` (deterministic only), ``off`` (legacy behavior, no filtering).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Callable

from .config import settings
from .llm_async import AsyncLLMClient

# W3/D04: 量规随出题同一次调用生成（§7.5 成本合并纪律）。这段要求追加到
# 每个出题 prompt 末尾；花括号一律双写（{{}}），因为宿主 prompt 都会再
# .format() 一次，双写后才在 LLM 眼里还原成单括号。
RUBRIC_REQUIREMENT = """
量规（与题目一起生成，用于在看学生作答前冻结判分标准）——每道题对象内额外输出两个字段：
- "rubric_criteria": 2-4 条评分点数组。每条为 {{"id": "c1", "description": "可从学生作答直接观察的判分点（关键步骤/条件/结果）", "weight": 1.0, "critical": true}}。critical=true 表示关键步骤（该步不成立则整题不能算对）；description 写判分点本身（如「正确写出归一化分母」），禁止抄题目答案原文；weight 为该条权重（正数，一般 1.0）。
- "equivalent_solutions": 可接受的等价解法/写法数组（没有则 []）。"""

_CRITIC_PROMPT = """你是严格的审题员。下面是为「{grade}」学生出的 {count} 道练习题（知识点：{topic}），每题附拟定答案。{difficulty_line}
请你逐题**独立求解**——先自己算出/推出正确答案，再核对拟定答案。不要被拟定答案带偏。

只输出一个 JSON 对象，不要任何其它文字、不要 markdown 代码块：
{{
  "verdicts": [
    {{"id": 1, "verdict": "correct", "reason": "一句话说明"}},
    {{"id": 2, "verdict": "incorrect", "correct_answer": "你认为的正确答案", "reason": "错在哪"}},
    {{"id": 3, "verdict": "too_shallow", "reason": "为什么属于降档水题"}}
  ]
}}

verdict 取值与判定：
- "incorrect"：拟定答案本身错误（以你独立求解的结果为准）；题干有知识性错误、条件矛盾或无解；选择题有多个选项都成立，或没有任何选项成立。
- "too_shallow"：题目本身没错，但相对目标难度明显降档——纯记忆复述、定义默写或一步直接套公式，没有任何思维转折点，充当不了 medium/hard 题。（目标难度为 easy 或未给定时不做此判定，一律不判 too_shallow。）
- 其余判 "correct"。拿不准时判 correct（宁可放过，不误杀）。

题目列表：
{block}"""


def is_well_formed(q: dict[str, Any]) -> bool:
    """Deterministic structural check. A question that fails this is broken
    regardless of what any model thinks — drop it."""
    stem = str(q.get("stem") or "").strip()
    answer = str(q.get("answer") or "").strip()
    if not stem or not answer:
        return False
    if (q.get("type") or "multiple_choice") == "multiple_choice":
        options = q.get("options")
        if not isinstance(options, dict) or len(options) < 2:
            return False
        values = [str(v).strip() for v in options.values()]
        if any(not v for v in values):
            return False
        if len(set(values)) != len(values):
            return False
        keys = {str(k).strip().upper() for k in options}
        if answer.upper() not in keys:
            return False
    if len(str(q.get("explanation") or "").strip()) < 15:
        return False
    return True


def filter_well_formed(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for q in questions:
        (kept if is_well_formed(q) else dropped).append(q)
    return kept, dropped


def _render_for_critic(questions: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for q in questions:
        lines = [f"[{q.get('id')}] 类型: {q.get('type', 'multiple_choice')}",
                 f"题干: {q.get('stem', '')}"]
        options = q.get("options")
        if isinstance(options, dict) and options:
            lines.append("选项: " + "  ".join(f"{k}. {v}" for k, v in options.items()))
        lines.append(f"拟定答案: {q.get('answer', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _parse_verdicts(raw: str) -> dict[int, dict[str, Any]] | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = m.group(0) if m else raw
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(verdicts, list):
        return None
    out: dict[int, dict[str, Any]] = {}
    for v in verdicts:
        if isinstance(v, dict) and "id" in v:
            try:
                out[int(v["id"])] = v
            except (TypeError, ValueError):
                continue
    return out


async def verify_questions(llm: AsyncLLMClient, questions: list[dict[str, Any]],
                           *, topic: str, grade: str, difficulty: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """LLM critic: independently re-solve and flag wrong answers.

    Returns ``(kept, dropped, critic_ok)``.  ``critic_ok=False`` means the
    critic itself failed (error/unparseable) and every question was kept
    unchanged (fail-open).  Per-question missing verdicts are also kept —
    only an explicit ``incorrect``/``too_shallow`` verdict drops a question.

    ``difficulty`` (easy/medium/hard) tells the critic the target level so it
    can flag ``too_shallow`` questions (correct but clearly below target);
    empty or ``easy`` disables that judgment.
    """
    if not questions:
        return [], [], True
    _DIFF_ZH = {"easy": "基础", "medium": "中等", "hard": "挑战"}
    difficulty_line = (f"目标难度：{_DIFF_ZH[difficulty]}（{difficulty}）。"
                       if difficulty in _DIFF_ZH else "")
    prompt = _CRITIC_PROMPT.format(
        grade=grade, count=len(questions), topic=topic,
        difficulty_line=difficulty_line,
        block=_render_for_critic(questions))
    try:
        full, _usage = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=2500, disable_thinking=True)
    except Exception:
        return questions, [], False
    verdicts = _parse_verdicts(full)
    if verdicts is None:
        return questions, [], False
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for q in questions:
        try:
            qid = int(q.get("id", 0))
        except (TypeError, ValueError):
            qid = 0
        verdict = verdicts.get(qid)
        v = ""
        if verdict:
            v = str(verdict.get("verdict", "")).strip().lower()
        if verdict and v in ("incorrect", "too_shallow"):
            dropped.append({**q, "_verdict": v,
                            "_drop_reason": str(verdict.get("reason", ""))[:200],
                            "_critic_answer": str(verdict.get("correct_answer", ""))[:200]})
        else:
            kept.append(q)
    return kept, dropped, True


def question_verified(verification: dict | None) -> bool | None:
    """Content-level verification status for the evidence gate (W2/A05).

    True only when the critic independently re-solved the question and the
    critic itself succeeded. None for basic structural checks, critic-off,
    missing metadata and fail-open critic errors alike — missing must never
    default to trusted ("缺失不默认高置信").
    """
    return True if verification_label(verification) == "content_checked" else None


def verification_label(verification: dict | None) -> str:
    """Four-way content-verification label (W3/A17; audit & projection input).

    content_checked    critic mode, independent re-solve succeeded
    ambiguous          critic mode but the critic itself errored (fail-open —
                       the questions were delivered without real verification)
    structural_valid  basic deterministic checks only (no content check)
    unchecked         off mode / missing metadata / critic never ran

    This refines, never overrides, :func:`question_verified`: only
    content_checked counts as content-verified for the evidence gate.
    """
    if not isinstance(verification, dict):
        return "unchecked"
    mode = str(verification.get("mode") or "")
    critic = str(verification.get("critic") or "")
    if mode == "critic":
        if critic == "ok":
            return "content_checked"
        if critic == "error":
            return "ambiguous"
        return "unchecked"
    if mode == "basic":
        return "structural_valid"
    return "unchecked"


def freeze_rubric(q: dict[str, Any], question_id: str) -> dict[str, Any] | None:
    """Validate + freeze a generation-time rubric onto a question (W3/D04).

    The rubric is produced by the generation call — before any student answer
    exists — so freezing is by construction ("量规在看学生答案前形成并版本化").
    Malformed criteria degrade to None: the question stays usable and the
    structured analyzer (D06) simply falls back to three-level grading. Never
    raises, never rejects a question.
    """
    raw = q.get("rubric_criteria")
    if not isinstance(raw, list) or not raw:
        return None
    criteria: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description") or "").strip()
        if len(desc) < 4:
            continue
        cid = str(item.get("id") or "").strip() or f"c{len(criteria) + 1}"
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        try:
            weight = float(item.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        if not weight > 0:
            weight = 1.0
        criteria.append({"id": cid, "description": desc[:120],
                         "weight": round(weight, 2),
                         "critical": bool(item.get("critical", False))})
        if len(criteria) >= 6:
            break
    if not criteria:
        return None
    eq = q.get("equivalent_solutions")
    eq_list = ([str(e).strip()[:200] for e in eq if str(e).strip()][:3]
               if isinstance(eq, list) else [])
    return {"rubric_id": str(question_id or ""), "version": 1,
            "criteria": criteria, "equivalent_solutions": eq_list,
            "frozen_at": time.time()}


async def generate_verified_questions(
        llm: AsyncLLMClient, *,
        make_prompt: Callable[[], str],
        parse: Callable[[str], list[dict[str, Any]]],
        topic: str, grade: str,
        difficulty: str = "",
        temperature: float, max_tokens: int,
        raw_preview_chars: int = 800
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate → structural filter → critic, with one regeneration retry.

    Shared by generate_quiz and fit_quiz so both tools get identical quality
    semantics.  Returns ``(questions, meta)``; ``meta`` carries the
    verification audit trail (attempts, drops, critic status) for the tool
    result's data payload, Trace, and M10 postconditions. ``difficulty``
    (easy/medium/hard, "" = unspecified) lets the critic flag correct-but-
    too-shallow questions against the target level.
    """
    mode = settings.quiz_verify_mode
    meta: dict[str, Any] = {"mode": mode, "attempts": 0, "critic": "skipped",
                            "dropped_ill_formed": 0, "dropped_by_critic": 0,
                            "dropped_shallow": 0,
                            "critic_flags": [], "raw": ""}
    for attempt in (1, 2):
        meta["attempts"] = attempt
        full, _usage = await llm.complete(
            messages=[{"role": "user", "content": make_prompt()}],
            temperature=temperature, max_tokens=max_tokens, disable_thinking=True)
        raw_questions = parse(full)
        if not raw_questions:
            meta["raw"] = full[:raw_preview_chars]
            continue
        if mode == "off":
            questions = raw_questions
        else:
            questions, ill = filter_well_formed(raw_questions)
            meta["dropped_ill_formed"] += len(ill)
            if mode == "critic" and questions:
                questions, bad, critic_ok = await verify_questions(
                    llm, questions, topic=topic, grade=grade,
                    difficulty=difficulty)
                meta["critic"] = "ok" if critic_ok else "error"
                meta["dropped_by_critic"] += len(bad)
                meta["dropped_shallow"] += sum(
                    1 for b in bad if b.get("_verdict") == "too_shallow")
                meta["critic_flags"] += [
                    {"id": b.get("id"), "verdict": b.get("_verdict", ""),
                     "reason": b.get("_drop_reason", ""),
                     "critic_answer": b.get("_critic_answer", "")} for b in bad]
        if questions:
            # W2/A14: a per-set prefix makes delivered question ids unique
            # across sets — bare 1..N renumbering made every set's "1" the
            # same "question" in the ledger and M2 events.
            set_uid = uuid.uuid4().hex[:8]
            for i, q in enumerate(questions, 1):
                q["id"] = f"q_{set_uid}_{i}"
                # W3/D04: freeze the rubric onto the final stable id (before
                # any student answer can exist); malformed criteria -> absent.
                rubric = freeze_rubric(q, q["id"])
                if rubric is not None:
                    q["rubric"] = rubric
            meta["answer_verified"] = mode != "off" and meta["critic"] != "error"
            return questions, meta
    return [], meta
