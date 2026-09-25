"""W3/D08: LLM teaching-decision adapter over the rules-based engine.

The rules path (select_strategy + compose) stays the auditable fallback
(§8.4「原 strategy/difficulty/policy 保留为可审计降级配方」). This adapter adds
ONE bounded LLM call per turn — only when a D08 trigger fires (评估完成/学生新
约束/连续困惑), never otherwise — and its output must pass ``validate_decision``
before it may touch the strategy:

  - a SINGLE primary action (仅一个主要下一步, W3 acceptance);
  - action/target/assistance are whitelisted enums; target must come from the
    candidate concept set (资源 ID 必须属于候选集);
  - explicit student constraints win: allow_followup_assessment=False forbids
    the quiz action (显式学习意愿优先);
  - any validation failure or LLM error returns None -> the rules strategy
    stands unchanged (关闭新模型仍能帮助但不污染状态).

TEACHING_DECISION_MODE=rules|shadow|active (§13.6): rules never calls;
shadow calls + records the comparison in the trace without applying; active
applies the validated decision as a CONSTRAINED adjustment of the composed
strategy (mode/depth/assistance only — never review_first, never new tools).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ...core.llm_async import AsyncLLMClient
from ...prompts.registry import get as _prompt

ACTIONS = ("explain", "practice", "quiz", "review", "clarify", "summarize")
ASSISTANCE_LEVELS = ("full_demo", "key_hints", "independent")

# 显式输出约束即"学生新约束"信号（D08 触发条件之一）。
_CONSTRAINT_FORMATS = ("one_sentence", "concise", "table", "steps")


@dataclass
class TeachingDecision:
    decision_id: str = ""
    action: str = ""
    target: str = ""
    assistance: str = ""
    rationale: str = ""
    expected_observation: str = ""
    stop_condition: str = ""
    source: str = "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id, "action": self.action,
            "target": self.target, "assistance": self.assistance,
            "rationale": self.rationale[:60],
            "expected_observation": self.expected_observation[:80],
            "stop_condition": self.stop_condition[:80],
            "source": self.source,
        }


def should_decide(*, recent_outcome: str = "",
                  constraints: dict[str, Any] | None = None,
                  misconceptions: int = 0, recent_mistakes: int = 0) -> bool:
    """D08 trigger gate: fire on a completed assessment, an explicit new
    student constraint, or consecutive confusion — never otherwise (budget)."""
    if recent_outcome in ("correct", "wrong", "partial"):
        return True
    if isinstance(constraints, dict):
        fmt = str(constraints.get("response_format") or "")
        if fmt in _CONSTRAINT_FORMATS:
            return True
        if constraints.get("allow_followup_assessment") is False:
            return True
    if misconceptions >= 1 or recent_mistakes >= 2:
        return True
    return False


def validate_decision(decision: "TeachingDecision | None",
                      candidates: list[str], *,
                      allow_quiz: bool = True) -> list[str]:
    """Return the list of violations (empty = valid). Pure and total."""
    if decision is None:
        return ["no_decision"]
    errors: list[str] = []
    if decision.action not in ACTIONS:
        errors.append("action_not_whitelisted")
    if decision.assistance not in ASSISTANCE_LEVELS:
        errors.append("assistance_not_whitelisted")
    target = str(decision.target or "").strip()
    if target and candidates and target not in candidates:
        errors.append("target_not_in_candidates")
    if decision.action == "quiz" and not allow_quiz:
        errors.append("quiz_forbidden_by_constraint")
    if len(decision.rationale) > 120:
        errors.append("rationale_too_long")
    return errors


def candidate_concepts(ctx) -> list[str]:
    """The closed candidate set a decision's target may name."""
    out: list[str] = []
    for name in (ctx.concept, ctx.concept_key,
                 *(ctx.unmet_prereq_names or [])):
        name = str(name or "").strip()
        if name and name not in out:
            out.append(name)
    return out


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


async def decide_teaching(ctx, rules_strategy, *, llm: AsyncLLMClient,
                          recent_outcome: str = "",
                          constraints: dict[str, Any] | None = None,
                          ) -> "TeachingDecision | None":
    """One bounded LLM call. Never raises; None on any failure."""
    try:
        prompt = _prompt("teaching_decision").text.format(
            candidates="、".join(candidate_concepts(ctx)) or "（无）",
            misconceptions="、".join((getattr(ctx, "misconceptions", None) or [])[:2]) or "无",
            recent_mistakes="、".join((getattr(ctx, "mistakes", None) or [])[:3]) or "无",
            recent_outcome=recent_outcome or "无近期测评",
            constraint_fmt=str((constraints or {}).get("response_format") or "无"),
            allow_quiz=("否" if (constraints or {}).get(
                "allow_followup_assessment") is False else "是"),
            rules_mode=getattr(getattr(rules_strategy, "mode", None), "value", ""),
            rules_difficulty=str(getattr(rules_strategy, "suggested_quiz_difficulty", "") or ""),
        )
        full, _usage = await llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=500, disable_thinking=True)
        obj = _extract_json(full)
        if not obj:
            return None
        decision = TeachingDecision(
            decision_id="td_" + uuid.uuid4().hex[:12],
            action=str(obj.get("action") or "").strip(),
            target=str(obj.get("target") or "").strip(),
            assistance=str(obj.get("assistance") or "").strip(),
            rationale=str(obj.get("rationale") or "").strip()[:120],
            expected_observation=str(obj.get("expected_observation") or "").strip()[:120],
            stop_condition=str(obj.get("stop_condition") or "").strip()[:120],
        )
        errors = validate_decision(
            decision, candidate_concepts(ctx),
            allow_quiz=(constraints or {}).get("allow_followup_assessment") is not False)
        if errors:
            return None
        return decision
    except Exception:
        return None


# action -> TeachingMode value (constrained remap; the strategy's own
# review_first/next_check structure is never replaced wholesale).
_ACTION_TO_MODE = {
    "explain": "explanation",
    "practice": "practice",
    "quiz": "practice",
    "review": "review",
    "clarify": "remediation",
    "summarize": "review",
}
_ASSISTANCE_HINT = {
    "full_demo": "下一任务先给完整示范，再让学生模仿",
    "key_hints": "下一任务只给关键步骤提示，不直接给答案",
    "independent": "下一任务让学生独立完成，不给提示",
}


def apply_decision(strategy, decision: "TeachingDecision") -> None:
    """Constrained in-place adjustment of the composed rules strategy.

    Only mode/depth/assistance/rationale may move, each within its enum; a
    failed remap leaves the field untouched. This is the entire active-mode
    surface — the LLM cannot invent tools, targets outside the candidate set,
    or a second primary action."""
    from .strategy import TeachingMode
    mode_value = _ACTION_TO_MODE.get(decision.action)
    if mode_value:
        try:
            strategy.mode = TeachingMode(mode_value)
        except ValueError:
            pass
    if decision.assistance == "full_demo":
        strategy.examples_needed = True
    elif decision.assistance == "independent":
        strategy.examples_needed = False
    hint = _ASSISTANCE_HINT.get(decision.assistance)
    if hint:
        strategy.plan_hints = list(strategy.plan_hints or []) + [hint]
    strategy.assistance = decision.assistance
    tag = f"[教学决策 {decision.decision_id}] {decision.rationale[:60]}"
    strategy.rationale = (strategy.rationale + "；" + tag
                          if strategy.rationale else tag)[:400]
    strategy.decision_id = decision.decision_id
