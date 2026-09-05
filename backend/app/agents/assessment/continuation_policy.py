"""CAT continuation policy (W3/D10): evidence-gated continue/probe/finish.

Hard caps ALWAYS win (updatePlan.md D10: "题数/时长/成本上限强制") — the pure
``should_stop`` rules are evaluated first and stay unchanged. Only when they
say "continue" may the structured analysis's suggestion take effect, and only
in STRUCTURED_ASSESSMENT_MODE=active:

  finish  -> sufficient_evidence   (analysis scored >=0.75 with no unmet
                                    critical criterion — coverage looks solid)
             insufficient_evidence (finish suggested but evidence incomplete:
                                    "信息不足时以未定结束，不判失败")
             Both persist in the SAME save as the answer (A03 discipline).
  probe   -> the NEXT question's goal gains targeted sub-abilities taken from
             unmet critical criteria + error hypotheses, so the generator
             probes exactly what is in doubt instead of stepping blindly.
  continue-> default difficulty stepping (unchanged).

off = never consulted. shadow = the decision is computed and persisted on the
session (``continuation_shadow``) for later comparison, but never applied —
the deterministic path is what the student sees. The mapping from analysis to
stop reason is a deterministic function here (never the model's own words).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .adaptive_test import AssessmentSession

ACTION_CONTINUE = "continue"
ACTION_PROBE = "probe"
ACTION_FINISH = "finish"

STOP_SUFFICIENT = "sufficient_evidence"
STOP_INSUFFICIENT = "insufficient_evidence"

# probe 注入下一题约束的最大条数（generator 的 assesses 是"必须检测的子能力"）。
_MAX_PROBE_ITEMS = 2


@dataclass
class ContinuationDecision:
    action: str = ACTION_CONTINUE       # effective action
    stop_reason: str = ""               # "" or a stop reason to persist
    probe_assesses: list[str] = field(default_factory=list)
    reason: str = ""
    source: str = "default"             # hard_rule | llm | default

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "stop_reason": self.stop_reason,
            "probe_assesses": list(self.probe_assesses),
            "reason": self.reason[:80], "source": self.source,
        }


def _finish_reason(session: AssessmentSession,
                   structured: dict[str, Any]) -> str:
    """Deterministically map a finish suggestion onto a stop reason.

    sufficient_evidence requires a solid rubric score AND no unmet critical
    criterion; anything else a finish is suggested for ends undetermined
    (insufficient_evidence), which is a neutral close, never a failure label.
    """
    score = structured.get("score")
    results = {str(c.get("criterion_id") or ""): str(c.get("result") or "")
               for c in (structured.get("criterion_results") or [])
               if isinstance(c, dict)}
    critical_unmet = False
    try:
        last_q = session.questions[-1] if session.questions else None
        for c in ((last_q.rubric or {}).get("criteria") if last_q else []) or []:
            if c.get("critical") and results.get(str(c.get("id") or "")) in (
                    "not_met", "partial"):
                critical_unmet = True
                break
    except Exception:
        critical_unmet = False
    if (isinstance(score, (int, float)) and score >= 0.75
            and not critical_unmet):
        return STOP_SUFFICIENT
    return STOP_INSUFFICIENT


def _probe_assesses(session: AssessmentSession,
                    structured: dict[str, Any]) -> list[str]:
    """Targeted sub-abilities for the next question: the critical criteria the
    student has not met, then (room permitting) the leading error hypothesis."""
    out: list[str] = []
    results = {str(c.get("criterion_id") or ""): str(c.get("result") or "")
               for c in (structured.get("criterion_results") or [])
               if isinstance(c, dict)}
    try:
        last_q = session.questions[-1] if session.questions else None
        for c in ((last_q.rubric or {}).get("criteria") if last_q else []) or []:
            cid = str(c.get("id") or "")
            if (c.get("critical") and results.get(cid) in ("not_met", "partial")
                    and len(out) < _MAX_PROBE_ITEMS):
                desc = str(c.get("description") or "").strip()
                if desc:
                    out.append(desc[:60])
    except Exception:
        out = []
    for h in (structured.get("hypotheses") or []):
        if len(out) >= _MAX_PROBE_ITEMS:
            break
        if isinstance(h, dict):
            statement = str(h.get("statement") or "").strip()
            if statement:
                out.append(statement[:60])
    return out


def decide_continuation(session: AssessmentSession,
                        structured: dict[str, Any] | None, *,
                        hard_stop: str = "") -> ContinuationDecision:
    """Pure decision function. ``hard_stop`` is the existing should_stop()
    verdict — when set it wins outright and the LLM is not consulted."""
    if hard_stop:
        return ContinuationDecision(action=ACTION_FINISH, stop_reason=hard_stop,
                                    reason="hard cap rule", source="hard_rule")
    if not isinstance(structured, dict):
        return ContinuationDecision(source="default")
    cont = structured.get("continuation")
    if not isinstance(cont, dict):
        return ContinuationDecision(source="default")
    action = str(cont.get("action") or "").strip()
    reason = str(cont.get("reason") or "").strip()[:80]
    if action == ACTION_FINISH:
        return ContinuationDecision(
            action=ACTION_FINISH,
            stop_reason=_finish_reason(session, structured),
            reason=reason, source="llm")
    if action == ACTION_PROBE:
        probes = _probe_assesses(session, structured)
        if probes:
            return ContinuationDecision(
                action=ACTION_PROBE, probe_assesses=probes,
                reason=reason, source="llm")
    return ContinuationDecision(reason=reason, source="llm")
