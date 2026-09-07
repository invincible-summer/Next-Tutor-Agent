"""LLM-driven improvement advisor: generate ImprovementProposals periodically.

This is M7's ONLY component that uses an LLM, and it runs behind a frequency
gate (every ADVISOR_FREQUENCY_GATE traces), mirroring M6's consolidation and
M5.5's dependency reasoner. It reads accumulated metrics + failure patterns and
produces structured ImprovementProposal objects.

Naming: this module is an ADVISOR, not an optimizer. It proposes; it never
applies. A mature agent system does not auto-modify itself -- the safe path to
self-improvement is observe -> diagnose -> propose -> evaluate -> approve ->
deploy. This module owns the "propose" step; "approve" and "deploy" are human.

Safety contract (mirrors M6 consolidation / M5.5 reasoner):
  - Proposals are NEVER auto-applied. They land with status="proposed" and
    require human approval (status transition via the API / store). Applying
    (status=applied) deploys the guidance text into
    teaching_engine/guidance_store — the only path to influence teaching.
  - The advisor output is validated: title/guidance must be non-empty text
    (open-ended, no value domains); legacy targets are checked against
    PROPOSAL_TARGETS. A bad LLM cannot corrupt the store.
  - The LLM call is optional (llm=None defers); it only runs when the
    frequency gate permits and an LLM client is available.
  - Failures degrade to a no-op; never breaks a turn.
"""
from __future__ import annotations

import json
from typing import Any

from . import store, strategy_analyzer
from .schema import (ADVISOR_FREQUENCY_GATE, ImprovementProposal,
                     PROPOSAL_TARGETS)


def should_advise(student_id: str) -> bool:
    """Check the frequency gate: have enough traces accumulated since the last
    advisory run?"""
    try:
        state = store.load_advisor_state(student_id)
        return int(state.get("traces_since_last", 0)) >= ADVISOR_FREQUENCY_GATE
    except Exception:
        return False


def _build_advice_prompt(metrics: dict[str, Any], *,
                         student_id: str) -> str:
    """Build the prompt for the LLM advisor.

    Feeds the accumulated failure distribution + strategy effectiveness so the
    LLM can propose one open-ended teaching-guidance principle. The output is
    free text in a fixed envelope — no value domains, no parameter assignment;
    the deterministic layer never interprets the guidance beyond routing it
    into the teaching engine's rendered fields.
    """
    failure_dist = metrics.get("failure_distribution", {})
    by_mode = metrics.get("by_mode", {})
    total = metrics.get("total", 0)

    lines = [
        "你是 AI 一对一教学系统的教学改进顾问。下面是这位学生最近的教学效果"
        "证据。请综合判断，为这位学生的后续教学提出一条教学指导原则——"
        "教师确认应用后，它会作为软性指导进入后续每一轮教学。\n",
        f"分析的总轮次：{total}",
    ]
    if failure_dist:
        lines.append("失败类型分布：")
        for ft, count in sorted(failure_dist.items(), key=lambda x: -x[1]):
            lines.append(f"  {ft}: {count}")
    if by_mode:
        lines.append("各教学模式的效果：")
        for mode, stats in by_mode.items():
            lines.append(
                f"  {mode}: 次数={stats.get('count',0)}, "
                f"平均增益={stats.get('avg_gain',0):+.2f}, "
                f"成功率={stats.get('success_rate',0):.0%}")
    lines.append("")
    lines.append("要求：指导要针对证据中最突出的问题；表述为可执行的教学做法，"
                 "而不是抽象口号；适合的证据不足时宁可置信度低也不要编造。")
    lines.append("严格按以下 JSON 格式输出（只输出 JSON 对象，不要任何其它文字）：")
    lines.append('{')
    lines.append('  "title": "<一句话标题，概括这条教学指导>",')
    lines.append('  "applicability": "<适用范围：写明情境/学科/概念，如『适用于计算密集型概念的练习环节』；'
                 '通用指导则填空字符串>",')
    lines.append('  "guidance": "<2-3 句话的教学指导原则：教学中具体应该怎么做>",')
    lines.append('  "cautions": ["<执行时需要注意的一点>", "..."],')
    lines.append('  "confidence": <0.0-1.0，证据支持这条指导的强度>')
    lines.append('}')
    return "\n".join(lines)


def _parse_proposal(content: str) -> ImprovementProposal | None:
    """Parse the LLM's JSON response into a validated ImprovementProposal.

    Open-ended guidance format (title/applicability/guidance/cautions) — no
    target whitelist, no value domains. Requires non-empty title + guidance.
    Legacy target-format responses are still accepted so the format transition
    never drops a usable proposal. Returns None on any parse failure so a bad
    LLM response is silently dropped.
    """
    try:
        # extract the first {...} block (LLMs sometimes wrap in markdown)
        text = content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return None
        data = json.loads(text[start:end + 1])
        title = str(data.get("title", "")).strip()
        guidance = str(data.get("guidance", "")).strip()
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        raw_cautions = data.get("cautions")
        if isinstance(raw_cautions, str):
            raw_cautions = [raw_cautions]
        cautions = [str(c).strip() for c in (raw_cautions or []) if str(c).strip()]
        if title and guidance:
            return ImprovementProposal(
                title=title, guidance=guidance,
                applicability=str(data.get("applicability", "")).strip(),
                cautions=cautions[:4],
                change=title,  # one-line mirror for legacy display surfaces
                confidence=confidence, status="proposed",
            )
        # legacy target-format response (pre-guidance advisor output)
        target = str(data.get("target", "")).strip().lower()
        change = str(data.get("change", "")).strip()
        if target in PROPOSAL_TARGETS and change:
            return ImprovementProposal(
                target=target, change=change,
                rationale=str(data.get("rationale", "")).strip(),
                confidence=confidence, status="proposed",
            )
        return None
    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
        return None


async def maybe_advise(student_id: str, llm: Any | None = None,
                       *, force: bool = False) -> ImprovementProposal | None:
    """Run the advisor if the frequency gate permits.

    Returns the generated ImprovementProposal (already persisted) or None when
    the gate is closed, no LLM is available, or parsing failed. Never raises.
    """
    try:
        if not force and not should_advise(student_id):
            return None
        if llm is None:
            return None  # defer until an LLM is available

        traces = store.read_traces(student_id)
        metrics = strategy_analyzer.summarize(traces)
        prompt = _build_advice_prompt(metrics, student_id=student_id)

        content, _ = await llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=400)

        proposal = _parse_proposal(content)
        if proposal is None:
            # reset the gate even on parse failure so we don't retry every turn
            _reset_gate(student_id)
            return None

        # add evidence (the metrics that motivated this proposal)
        proposal.evidence = [
            f"failure_distribution={metrics.get('failure_distribution', {})}",
            f"total_turns={metrics.get('total', 0)}",
        ]
        store.add_proposal(student_id, proposal)
        _reset_gate(student_id)
        return proposal
    except Exception:
        return None


def _reset_gate(student_id: str) -> None:
    """Reset the frequency-gate counter after an advisory attempt."""
    try:
        state = store.load_advisor_state(student_id)
        state["traces_since_last"] = 0
        state["last_ts"] = __import__("time").time()
        store.save_advisor_state(student_id, state)
    except Exception:
        pass
