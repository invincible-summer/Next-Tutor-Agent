"""Structured answer analysis (W3/D06): rubric-criterion grading in one call.

Replaces the free-text three-level prompt ONLY when the question carries a
frozen rubric (D04) and STRUCTURED_ASSESSMENT_MODE is shadow/active. The
single call folds D06 + the D07/D08 suggestions the plan allows to merge
(updatePlan.md §7.2: "首版将 D06+局部 D07+D08 建议合为一次评估调用"):

    criterion_results   每条量规 met/partial/not_met/not_observed/not_applicable
    first_error         首个有证据支持的实质错误位置 + 此前做对的部分 (F01)
    hypotheses          ≤2 个错因假设（七类枚举，允许多假设）
    uncertainties       尚无法判断的点（不会/拒答/含混 → 不编造）
    observed_capabilities  六维能力观测（§7.3；未观测即 not_observed）
    feedback            strength + next_step（一句区分性追问/局部修正任务）
    continuation        continue/probe/finish 建议（D10 的 LLM 输入）

Deterministic boundaries (the model cannot cross):
  - criterion_id must belong to the frozen rubric's candidate set;
  - every enum is validated against a whitelist; unknown values are dropped;
  - the score is computed HERE from the frozen rubric weights (never taken
    from the model), with not_applicable out of the denominator and a
    critical-not_observed making the overall score indeterminate (§8.6.1);
  - the student's answer is data: the schema has no tool/instruction slot,
    so nothing in the answer can turn into a grading instruction (D06);
  - invalid JSON gets ONE format-only repair attempt, then the analysis
    abstains (returns None) — the caller falls back to the legacy grade and
    no fabricated score is ever produced (§7.5).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ...core.llm_async import AsyncLLMClient
from ...prompts.registry import get as _prompt
from .evaluator import derive_concept_status, verdict_for_score
from .question import Question
from .state import AssessmentContext, AssessmentResult

# §7.3 default capability framework — 概念理解/程序操作/推理解释/迁移应用/
# 延迟保持/自我检查。维度只作观测聚合，不平均成单一"总能力分"。
DIMENSIONS = ("concept", "procedure", "reasoning", "transfer", "retention",
              "self_check")
_CRITERION_RESULTS = ("met", "partial", "not_met", "not_observed",
                      "not_applicable")
_CAPABILITY_RESULTS = ("met", "partial", "not_met", "not_observed")
_HYPOTHESIS_KINDS = ("concept_relation", "process_omission", "arithmetic_unit",
                     "reasoning_condition", "reading_misread", "expression_gap",
                     "other")
_CONTINUATION_ACTIONS = ("continue", "probe", "finish")
_HYPOTHESIS_TO_MISTAKE = {"concept_relation": "concept",
                          "process_omission": "procedure",
                          "arithmetic_unit": "calculation",
                          "reasoning_condition": "reasoning"}

_ANALYZE_PROMPT = _prompt("assessment_analyze").text

_REPAIR_PROMPT = (
    "上一个输出不是合法的本协议 JSON（或字段取值不合法）。只修复格式与枚举值，"
    "不要改变判定内容；无法补齐的字段按协议的空值处理。重新输出完整 JSON 对象，"
    "不要任何其它文字。\n上一个输出：\n{raw}\n\n题目、量规与学生作答同前。"
)


@dataclass
class StructuredAnalysis:
    """Validated structured analysis of one answer (score computed locally)."""

    criterion_results: list[dict[str, str]] = field(default_factory=list)
    first_error: dict[str, str] = field(default_factory=dict)
    hypotheses: list[dict[str, str]] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    observed_capabilities: dict[str, str] = field(default_factory=dict)
    feedback: dict[str, str] = field(default_factory=dict)
    continuation: dict[str, str] = field(default_factory=dict)
    score: float | None = None          # rubric-weighted; None = indeterminate
    verdict: str = ""                   # compat mapping computed locally
    rubric_id: str = ""
    rubric_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_results": [dict(c) for c in self.criterion_results],
            "first_error": dict(self.first_error),
            "hypotheses": [dict(h) for h in self.hypotheses],
            "uncertainties": list(self.uncertainties),
            "observed_capabilities": dict(self.observed_capabilities),
            "feedback": dict(self.feedback),
            "continuation": dict(self.continuation),
            "score": self.score,
            "verdict": self.verdict,
            "rubric_id": self.rubric_id,
            "rubric_version": self.rubric_version,
        }

    def to_result(self, question: Question,
                  ctx: "AssessmentContext | None" = None) -> AssessmentResult:
        """Project the analysis onto the backward-compatible AssessmentResult.

        score=None (indeterminate) maps to the conservative partial verdict —
        W2 semantics: partial never moves legacy BKT, but the tallies, weak
        points and the structured detail all survive.
        """
        verdict = self.verdict or verdict_for_score(
            self.score if self.score is not None else 0.5)
        strength = str(self.feedback.get("strength", "") or "")
        next_step = str(self.feedback.get("next_step", "") or "")
        parts = [p for p in (strength, f"下一步：{next_step}" if next_step else "")
                 if p]
        result = AssessmentResult(
            question_id=question.id,
            concept=question.concept or (ctx.concept if ctx else ""),
            verdict=verdict,
            score=self.score if self.score is not None else 0.5,
            feedback="；".join(parts)[:300],
            difficulty_at=question.difficulty,
        )
        first_err = str(self.first_error.get("description", "") or "")
        if verdict != "correct":
            result.diagnosis_note = (first_err or next_step)[:60]
            result.mistake_type = ""
            if self.hypotheses:
                kind = str(self.hypotheses[0].get("kind", "") or "")
                result.mistake_type = _HYPOTHESIS_TO_MISTAKE.get(kind, "")
        mastery = ctx.current_mastery if ctx else 0.0
        result.concept_status = derive_concept_status(
            result.score, mastery=mastery, mistake_type=result.mistake_type)
        result.structured = self.to_dict()
        return result


def _clip(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def score_from_criteria(criteria: list[dict[str, Any]],
                        results: dict[str, str]) -> float | None:
    """Deterministic rubric-weighted score (never trusts the model's math).

    not_applicable leaves both numerator and denominator (§8.6.1);
    not_observed is excluded as well, but a CRITICAL criterion left
    not_observed makes the whole score indeterminate (None) — the question
    cannot be graded as a whole when its key step was not observable.
    """
    awarded = 0.0
    possible = 0.0
    for c in criteria:
        cid = str(c.get("id") or "")
        r = results.get(cid)
        if r == "not_applicable":
            continue
        if r is None or r == "not_observed":
            if c.get("critical"):
                return None
            continue
        try:
            w = float(c.get("weight", 1.0))
        except (TypeError, ValueError):
            w = 1.0
        if not w > 0:
            w = 1.0
        possible += w
        awarded += w * {"met": 1.0, "partial": 0.5, "not_met": 0.0}.get(r, 0.0)
    if possible <= 0:
        return None
    return round(awarded / possible, 2)


def _validate(raw_obj: dict[str, Any], question: Question,
              ) -> "StructuredAnalysis | None":
    """Strict whitelist validation. Unknown ids/enums are dropped; an empty
    criterion set means the analysis is unusable (None)."""
    rubric = question.rubric or {}
    criteria = rubric.get("criteria") or []
    valid_ids = {str(c.get("id") or "") for c in criteria if c.get("id")}
    if not valid_ids:
        return None

    results: dict[str, str] = {}
    criterion_results: list[dict[str, str]] = []
    for item in (raw_obj.get("criterion_results") or []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("criterion_id") or "").strip()
        r = str(item.get("result") or "").strip()
        if cid not in valid_ids or r not in _CRITERION_RESULTS:
            continue
        if cid in results:
            continue
        results[cid] = r
        criterion_results.append({
            "criterion_id": cid, "result": r,
            "evidence_quote": _clip(item.get("evidence_quote"), 40),
        })
    if not criterion_results:
        return None

    first_error = {
        "description": _clip((raw_obj.get("first_error") or {}).get("description")
                             if isinstance(raw_obj.get("first_error"), dict) else "", 120),
        "preceding_correct": _clip(
            (raw_obj.get("first_error") or {}).get("preceding_correct")
            if isinstance(raw_obj.get("first_error"), dict) else "", 120),
    }
    hypotheses: list[dict[str, str]] = []
    for item in (raw_obj.get("hypotheses") or []):
        if not isinstance(item, dict):
            continue
        statement = _clip(item.get("statement"), 80)
        if not statement:
            continue
        kind = str(item.get("kind") or "").strip()
        hypotheses.append({"kind": kind if kind in _HYPOTHESIS_KINDS else "other",
                           "statement": statement})
        if len(hypotheses) >= 2:
            break
    uncertainties = [_clip(u, 80) for u in (raw_obj.get("uncertainties") or [])
                     if _clip(u, 80)][:3]
    capabilities: dict[str, str] = {}
    raw_caps = raw_obj.get("observed_capabilities")
    if isinstance(raw_caps, dict):
        for dim in DIMENSIONS:
            v = str(raw_caps.get(dim) or "").strip()
            capabilities[dim] = v if v in _CAPABILITY_RESULTS else "not_observed"
    raw_fb = raw_obj.get("feedback") if isinstance(raw_obj.get("feedback"), dict) else {}
    feedback = {"strength": _clip(raw_fb.get("strength"), 80),
                "next_step": _clip(raw_fb.get("next_step"), 120)}
    raw_cont = raw_obj.get("continuation") if isinstance(raw_obj.get("continuation"), dict) else {}
    action = str(raw_cont.get("action") or "").strip()
    continuation = {"action": action if action in _CONTINUATION_ACTIONS else "continue",
                    "reason": _clip(raw_cont.get("reason"), 80)}

    score = score_from_criteria(criteria, results)
    verdict = verdict_for_score(score) if score is not None else "partial"
    try:
        version = int(rubric.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    return StructuredAnalysis(
        criterion_results=criterion_results, first_error=first_error,
        hypotheses=hypotheses, uncertainties=uncertainties,
        observed_capabilities=capabilities, feedback=feedback,
        continuation=continuation, score=score, verdict=verdict,
        rubric_id=str(rubric.get("rubric_id") or question.id or ""),
        rubric_version=version,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _build_prompt(question: Question, student_answer: str,
                  ctx: "AssessmentContext | None") -> str:
    rubric = question.rubric or {}
    lines = [f"- {c.get('id')}：{c.get('description')}"
             + ("（关键步骤）" if c.get("critical") else "")
             for c in (rubric.get("criteria") or [])]
    caps = question.options if question.options else {}
    grade_phrase = (ctx.grade if ctx and ctx.grade else "未指定学段")
    return _ANALYZE_PROMPT.format(
        grade=grade_phrase,
        stem=question.stem[:600],
        q_type=question.q_type,
        correct_answer=str(question.answer or "")[:600],
        explanation=str(question.explanation or "")[:800],
        rubric_lines="\n".join(lines),
        equivalent="；".join(str(e) for e in (rubric.get("equivalent_solutions") or [])) or "无",
        student_answer=str(student_answer or "")[:2000],
        options=json.dumps(caps, ensure_ascii=False) if caps else "（无选项）",
        dimensions="、".join(DIMENSIONS),
        hypothesis_kinds="、".join(_HYPOTHESIS_KINDS),
    )


async def analyze_answer(question: Question, student_answer: str,
                         ctx: "AssessmentContext | None", *,
                         llm: AsyncLLMClient) -> "StructuredAnalysis | None":
    """Run the single structured-analysis call. Never raises; None = abstain."""
    try:
        prompt = _build_prompt(question, student_answer, ctx)
        messages = [{"role": "user", "content": prompt}]
        full, _usage = await llm.complete(
            messages, temperature=0.1, max_tokens=1100, disable_thinking=True)
        obj = _extract_json(full)
        analysis = _validate(obj, question) if obj else None
        if analysis is None and obj is None:
            # One format-only repair attempt (§7.5), then abstain.
            messages = messages + [
                {"role": "assistant", "content": str(full)[:2000]},
                {"role": "user", "content": _REPAIR_PROMPT.format(
                    raw=str(full)[:2000])}]
            full2, _usage2 = await llm.complete(
                messages, temperature=0.1, max_tokens=1100, disable_thinking=True)
            obj2 = _extract_json(full2)
            analysis = _validate(obj2, question) if obj2 else None
        return analysis
    except Exception:
        return None
