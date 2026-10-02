"""Explainable, deterministic candidate selection for education skills.

This first version runs safely in shadow mode.  It does not replace the
Supervisor planner yet; it records what a contract-aware runtime would choose
and which candidates were rejected by hard preconditions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..state import StudentSnapshot, TaskUnderstanding
from .policy import evaluate_preconditions
from .registry import registry
from ..preresearch import is_content_question
from ..material_signals import mentions_title

_VARIANT_RE = re.compile(r"仿照|类似(?:的)?题|变式|拟合|同类型|照着.{0,6}出")
_MATERIAL_RE = re.compile(r"资料|文件|教材|课件|笔记|PDF|PPT|Word|文档|上传|附件", re.I)
_REFERENCE_BODY_RE = re.compile(r"(?:题目|例题|参考题)\s*[:：]|\n.{8,}|[=＋+－\-×*÷/].{3,}")


@dataclass(frozen=True)
class TaskFrame:
    intent: str
    subject: str
    concept: str
    grade: str
    has_materials: bool
    has_history: bool
    has_reference_question: bool
    asks_for_variant: bool
    references_materials: bool
    requires_tools: bool
    confidence: float
    has_textbook: bool = False  # 当前回合可见的教材信号
    material_grounding_required: bool = False

    def policy_context(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "subject": self.subject,
            "concept": self.concept,
            "grade": self.grade,
            "has_materials": self.has_materials,
            "has_history": self.has_history,
            "has_reference_question": self.has_reference_question,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SkillCandidate:
    skill_id: str
    score: float
    allowed: bool
    reason_codes: tuple[str, ...] = ()
    failed_preconditions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "score": round(self.score, 3),
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "failed_preconditions": list(self.failed_preconditions),
        }


@dataclass(frozen=True)
class SkillDecision:
    mode: str  # direct | execute | clarify
    selected_skill_ids: tuple[str, ...] = ()
    candidates: tuple[SkillCandidate, ...] = ()
    clarification_reason: str = ""
    confidence: float = 0.0
    policy_version: str = "skill-policy-1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "selected_skill_ids": list(self.selected_skill_ids),
            "candidates": [c.to_dict() for c in self.candidates],
            "clarification_reason": self.clarification_reason,
            "confidence": round(self.confidence, 3),
            "policy_version": self.policy_version,
        }


def build_task_frame(message: str, understanding: TaskUnderstanding,
                     snapshot: StudentSnapshot, *, has_history: bool = False,
                     has_attachments: bool = False,
                     has_textbook: bool = False,
                     has_visible_materials: bool = False) -> TaskFrame:
    asks_variant = bool(_VARIANT_RE.search(message))
    material_available = bool(snapshot.has_materials or has_textbook or has_visible_materials)
    explicit_body = bool(_REFERENCE_BODY_RE.search(message)) and len(message.strip()) >= 24
    return TaskFrame(
        intent=understanding.intent.value,
        subject=understanding.subject,
        concept=understanding.concept,
        grade=snapshot.grade,
        has_materials=material_available,
        has_history=has_history,
        has_reference_question=has_attachments or (asks_variant and explicit_body),
        asks_for_variant=asks_variant,
        references_materials=(bool(_MATERIAL_RE.search(message)) or has_attachments
                              or mentions_title(message)),
        requires_tools=understanding.requires_tools,
        confidence=understanding.confidence,
        has_textbook=has_textbook,
        material_grounding_required=(
            (has_attachments and not asks_variant)
            or bool(_MATERIAL_RE.search(message))
            or (material_available and mentions_title(message))
            or (material_available and bool(understanding.concept)
                and is_content_question(message))),
    )


def _score(skill_id: str, frame: TaskFrame) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    skill = registry.get(skill_id)
    if frame.intent in skill.intents:
        score += 0.35
        reasons.append(f"intent.{frame.intent}")
    if skill.role == "teaching" and frame.intent in {"explain", "solve", "review", "plan"}:
        score += 0.42
        reasons.append("teaching_goal")
    if skill.tool_name == "knowledge_search":
        if frame.material_grounding_required:
            score += 0.70
            reasons.append("mandatory_material_grounding")
        elif frame.references_materials:
            score += 0.48
            reasons.append("explicit_material_reference")
        elif frame.has_materials and frame.concept:
            score += 0.16
            reasons.append("materials_and_concept_available")
    elif skill.tool_name == "generate_quiz":
        if frame.intent == "practice":
            score += 0.48
            reasons.append("explicit_practice_request")
        elif frame.intent == "diagnose":
            score += 0.30
            reasons.append("diagnostic_probe_needed")
        if frame.asks_for_variant:
            score -= 0.18
    elif skill.tool_name == "fit_quiz":
        if frame.asks_for_variant:
            score += 0.55
            reasons.append("variant_request")
        if frame.has_reference_question:
            score += 0.20
            reasons.append("reference_question_available")
    elif skill.tool_name == "recall_history":
        if frame.intent == "diagnose":
            score += 0.42
            reasons.append("diagnosis_needs_past_evidence")
        elif frame.intent == "review":
            score += 0.20
            reasons.append("review_may_need_history")
    return max(0.0, min(score, 1.0)), reasons


def decide(frame: TaskFrame, *, max_candidates: int = 4) -> SkillDecision:
    if frame.intent == "chitchat":
        return SkillDecision(mode="direct", confidence=1.0)

    candidates: list[SkillCandidate] = []
    for skill in registry.all_active():
        score, reasons = _score(skill.id, frame)
        policy = evaluate_preconditions(skill, frame.policy_context())
        if score <= 0 and not policy.failed:
            continue
        candidates.append(SkillCandidate(
            skill_id=skill.id,
            score=score,
            allowed=policy.allowed,
            reason_codes=tuple(reasons),
            failed_preconditions=policy.failed,
        ))
    candidates.sort(key=lambda c: (c.allowed, c.score), reverse=True)
    candidates = candidates[:max_candidates]

    # A variant request without an actual reference is not safe to guess from.
    if frame.asks_for_variant and not frame.has_reference_question:
        return SkillDecision(
            mode="clarify", candidates=tuple(candidates),
            clarification_reason="missing_reference_question",
            confidence=0.95,
        )

    allowed = [c for c in candidates if c.allowed]
    if not allowed:
        return SkillDecision(mode="direct", candidates=tuple(candidates), confidence=0.5)

    top = allowed[0]
    if top.score < 0.55 and not frame.material_grounding_required:
        return SkillDecision(mode="direct", candidates=tuple(candidates), confidence=top.score)

    # Material grounding is a composite contract: retrieval is mandatory, but
    # it does not replace the teaching skill that must turn evidence into an
    # answer.  Explicit references keep the historical single-tool selection;
    # an implicit workspace textbook question advertises both capabilities so
    # the planner/gate cannot narrow the tool away.
    selected = [top.skill_id]
    knowledge_id = "agent.skill.knowledge.search_materials"
    if frame.material_grounding_required:
        if knowledge_id not in selected:
            selected.insert(0, knowledge_id)
        if not frame.references_materials:
            teaching = next((c for c in allowed
                             if registry.get(c.skill_id).role == "teaching"), None)
            if teaching and teaching.skill_id not in selected:
                selected.append(teaching.skill_id)

    return SkillDecision(
        mode="execute", selected_skill_ids=tuple(selected),
        candidates=tuple(candidates), confidence=max(top.score, 0.70),
    )


@dataclass(frozen=True)
class SkillGateResult:
    """Result of applying hard Skill preconditions to a legacy-compatible plan."""
    plan: Any
    removed_skill_ids: tuple[str, ...] = ()
    clarification_reason: str = ""
    selected_skill_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_source": getattr(self.plan, "source", ""),
            "steps": [s.to_dict() for s in getattr(self.plan, "steps", [])],
            "removed_skill_ids": list(self.removed_skill_ids),
            "clarification_reason": self.clarification_reason,
            "selected_skill_ids": list(self.selected_skill_ids),
        }


def gate_plan(plan: Any, frame: TaskFrame,
              decision: SkillDecision) -> SkillGateResult:
    """Apply deterministic preconditions without inventing a new workflow.

    The existing planner still decides the broad teaching sequence.  Gating only
    removes skills whose declared preconditions fail, narrows ambiguous
    assessment choices to the selected candidate, and turns missing-reference
    variant requests into a minimal clarification step.
    """
    from ..state import PlanStep, TaskPlan

    if decision.mode == "clarify":
        reason = decision.clarification_reason or "missing_required_input"
        task = {
            "missing_reference_question": (
                "先请学生粘贴或上传完整参考题；只提一个简短澄清问题，"
                "在拿到题目之前不要生成或猜测变式题。"
            ),
        }.get(reason, "先向学生确认完成任务所缺少的一项关键信息。")
        gated = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task=task,
            skill_ids=["agent.skill.teaching.direct_explain"],
        )], source="skill_gated_clarify")
        return SkillGateResult(
            plan=gated, clarification_reason=reason,
            selected_skill_ids=decision.selected_skill_ids,
        )

    selected = set(decision.selected_skill_ids)
    selected_roles = {registry.get(sid).role for sid in selected
                      if sid in registry.active_versions()}
    context = frame.policy_context()
    removed: list[str] = []
    steps: list[PlanStep] = []

    for step in plan.steps:
        candidate_ids = list(step.skill_ids)
        if not candidate_ids and step.suggested_tools:
            for tool_name in step.suggested_tools:
                skill = registry.by_tool_name(tool_name)
                if skill is not None:
                    candidate_ids.append(skill.id)
        if not candidate_ids:
            candidate_ids = [skill.id for skill in registry.for_role(step.agent_role)]

        kept_ids: list[str] = []
        for skill_id in candidate_ids:
            try:
                skill = registry.get(skill_id)
            except KeyError:
                removed.append(skill_id)
                continue
            policy = evaluate_preconditions(skill, context)
            if not policy.allowed:
                removed.append(skill_id)
                continue
            # For a role with an explicit selected candidate, narrow ambiguous
            # alternatives (generate vs fit) to that candidate. Other roles in
            # a multi-step plan remain intact.
            if skill.role in selected_roles and selected and skill_id not in selected:
                removed.append(skill_id)
                continue
            # Uploaded materials alone should not force retrieval in gated mode;
            # the user must reference them or the decision must select retrieval.
            if (skill.role == "knowledge" and skill_id not in selected
                    and not frame.material_grounding_required):
                removed.append(skill_id)
                continue
            kept_ids.append(skill_id)

        if candidate_ids and not kept_ids:
            # A pure teaching step has no tool binding and is represented by its
            # advisory Skill. Other denied/optional tool steps are safely dropped.
            if step.agent_role != "teaching":
                continue
        steps.append(PlanStep(
            agent_role=step.agent_role,
            task=step.task,
            suggested_tools=list(step.suggested_tools),
            skill_ids=kept_ids,
            optional=step.optional,
            tool_args={name: dict(args) for name, args in step.tool_args.items()
                       if name in {registry.get(sid).tool_name for sid in kept_ids
                                   if registry.get(sid).tool_name}},
            auto_invoke=step.auto_invoke,
        ))

    # Hard invariant: a material-grounded turn always has an executable
    # knowledge step.  Do not rely on the LLM planner having remembered to
    # mention it; this is the fix for the former "direct_explain only" trace.
    knowledge_id = "agent.skill.knowledge.search_materials"
    if frame.material_grounding_required and knowledge_id not in {
            sid for step in steps for sid in step.skill_ids}:
        steps.insert(0, PlanStep(
            agent_role="knowledge",
            task="先检索当前工作区/会话授权资料，再将证据交给教学步骤。",
            suggested_tools=["knowledge_search"],
            skill_ids=[knowledge_id],
            auto_invoke=True,
        ))

    if not steps:
        steps = [PlanStep(
            agent_role="teaching", task="直接进行低风险讲解，不调用缺少前置条件的工具。",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )]
    gated = TaskPlan(steps=steps, source=f"skill_gated:{plan.source}")
    return SkillGateResult(
        plan=gated, removed_skill_ids=tuple(dict.fromkeys(removed)),
        selected_skill_ids=decision.selected_skill_ids,
    )
