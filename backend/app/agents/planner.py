"""Supervisor planner: turn a TaskUnderstanding into a workflow TaskPlan.

Hybrid planner (three paths, tried in order, always returning a plan):
  1. Rule fast path -- deterministic plans for the common intent types
     (CHITCHAT/EXPLAIN/PRACTICE/DIAGNOSE/REVIEW). Stable, zero tokens.
  2. LLM planner -- for richer intents (GENERATE/SOLVE/PLAN) or when the rule
     plan needs adapting to the student's state, a low-budget LLM call
     proposes steps. validator checks it.
  3. Fallback -- any LLM failure or invalid plan degrades to a safe
     single-step teaching plan (source='fallback').

Plans are advisory + constraining, not rigid scripts: the executor still runs a
ReAct loop, but the plan narrows the visible tool subset per step (via the
router) and injects a step instruction.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from ..core.llm_async import AsyncLLMClient
from ..core.session import TutorSession
from .router import describe_capabilities
from .state import (PlanStep, StudentSnapshot, TaskPlan,
                    TaskType, TaskUnderstanding, VALID_AGENT_ROLES)

# Hard caps so a plan can never run away or loop.
_MAX_PLAN_STEPS = 4
_PLAN_GOAL_CHARS = 80  # cap the goal string stored on TaskState


def _goal_from(u: TaskUnderstanding) -> str:
    """One-line current goal for TaskState, derived from understanding."""
    base = {
        TaskType.EXPLAIN: "理解",
        TaskType.PRACTICE: "练习",
        TaskType.DIAGNOSE: "诊断薄弱点",
        TaskType.REVIEW: "复习总结",
        TaskType.GENERATE: "生成材料",
        TaskType.SOLVE: "解题",
        TaskType.PLAN: "规划学习路径",
    }.get(u.intent, "")
    target = u.concept or u.subject or ""
    goal = f"{base}{target}".strip() if base else (target or "学习")
    return goal[:_PLAN_GOAL_CHARS]


# --- rule fast path ---------------------------------------------------------

def _rule_plan(u: TaskUnderstanding, snap: StudentSnapshot) -> TaskPlan:
    """Deterministic plan keyed on intent. Uses the student snapshot to decide
    whether a knowledge-retrieval step is worth including (e.g. materials
    uploaded + concept known, or a diagnosed weakness to look up)."""
    if u.intent == TaskType.CHITCHAT:
        return TaskPlan(steps=[], source="rule")  # no steps -> executor direct-answer

    if u.goal == "answer_pending":
        # D02: the student answered the pending exercise from the chat box.
        # Grading happens on the quiz-card API, so this turn must NOT
        # generate another quiz -- the executor answers directly, pointing
        # back to the pending question.
        return TaskPlan(steps=[], source="rule")

    if u.intent == TaskType.PRACTICE:
        steps = [PlanStep(
            agent_role="assessment", task="按学生学段与知识点出练习题",
            suggested_tools=["generate_quiz", "fit_quiz"],
            skill_ids=["agent.skill.assessment.generate_practice",
                       "agent.skill.assessment.fit_variants"],
        )]
        return TaskPlan(steps=steps, source="rule")

    if u.intent == TaskType.DIAGNOSE:
        # diagnose may look at past mistakes (memory) then quiz to probe.
        steps = [PlanStep(
            agent_role="memory", task="回顾历史错题与薄弱点",
            suggested_tools=["recall_history"],
            skill_ids=["agent.skill.memory.recall_history"], optional=True,
        )]
        steps.append(PlanStep(
            agent_role="assessment", task="针对薄弱点出一道诊断题",
            suggested_tools=["generate_quiz"],
            skill_ids=["agent.skill.assessment.generate_practice"],
        ))
        return TaskPlan(steps=steps, source="rule")

    if u.intent == TaskType.REVIEW:
        steps: list[PlanStep] = []
        if snap.recent_quiz_count:
            steps.append(PlanStep(
                agent_role="memory", task="回顾已练习内容与错题",
                suggested_tools=["recall_history"],
                skill_ids=["agent.skill.memory.recall_history"], optional=True,
            ))
        steps.append(PlanStep(
            agent_role="teaching", task="梳理知识点体系并总结要点",
            skill_ids=["agent.skill.teaching.direct_explain"],
        ))
        return TaskPlan(steps=steps, source="rule")

    # EXPLAIN and the rarer GENERATE/SOLVE/PLAN: knowledge-first when materials
    # exist and the concept is specific, then teaching.
    if u.intent in (TaskType.EXPLAIN, TaskType.GENERATE, TaskType.SOLVE, TaskType.PLAN):
        steps: list[PlanStep] = []
        if snap.has_materials and u.concept:
            steps.append(PlanStep(
                agent_role="knowledge", task="检索教材中相关知识片段",
                suggested_tools=["knowledge_search"],
                skill_ids=["agent.skill.knowledge.search_materials"], optional=True,
            ))
        steps.append(PlanStep(
            agent_role="teaching", task="讲解知识点或解决问题",
            skill_ids=["agent.skill.teaching.direct_explain"],
        ))
        return TaskPlan(steps=steps, source="rule")

    # default: single teaching step
    return TaskPlan(steps=[PlanStep(
        agent_role="teaching", task="讲解",
        skill_ids=["agent.skill.teaching.direct_explain"],
    )], source="rule")


# --- validator --------------------------------------------------------------

def _validate(plan: TaskPlan) -> bool:
    """Check a plan is well-formed and safe. Mutates `validated` flag."""
    if not plan.steps:
        plan.validated = True  # empty (chitchat) is valid
        return True
    if len(plan.steps) > _MAX_PLAN_STEPS:
        return False
    roles = [s.agent_role for s in plan.steps]
    if any(r not in VALID_AGENT_ROLES for r in roles):
        return False
    # reject obvious loops: same role repeated 3+ times in a row
    run = 1
    for i in range(1, len(roles)):
        if roles[i] == roles[i - 1]:
            run += 1
            if run >= 3:
                return False
        else:
            run = 1
    plan.validated = True
    return True


# --- LLM planner ------------------------------------------------------------

from ..prompts.registry import get as _prompt

# 阶段D：prompt 文本统一由注册表管理（含版本号），此处薄 re-export 兼容。
# 文本中的「4 步」与 _MAX_PLAN_STEPS 保持一致，改上限时同步注册表并 bump 版本。
_PLANNER_SYSTEM = _prompt("planner_system").text


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


def _known_skill(skill_registry, skill_id: str, role: str) -> bool:
    try:
        return skill_registry.get(skill_id).role == role
    except KeyError:
        return False


async def _llm_plan(u: TaskUnderstanding, snap: StudentSnapshot,
                    llm: AsyncLLMClient) -> TaskPlan | None:
    """LLM-proposed plan. Returns a validated plan or None on any failure."""
    caps = describe_capabilities()
    caps_str = "\n".join(
        f"- {c['role']}: " + ", ".join(
            f"{card['id']}（{card['name']}）" for card in c.get("skills", [])
        ) for c in caps
    )
    user_msg = (
        f"任务意图：{u.intent.value}\n"
        f"学科：{u.subject or '未知'}\n"
        f"知识点：{u.concept or '未明确'}\n"
        f"学习目标：{u.goal or '未明确'}\n"
        f"学生学段：{snap.grade}\n"
        f"已上传资料：{'是(' + str(snap.material_count) + '份)' if snap.has_materials else '否'}\n"
        f"近期练习次数：{snap.recent_quiz_count}\n"
        f"近期薄弱点：{', '.join(snap.recent_weak_points) if snap.recent_weak_points else '无'}\n\n"
        f"可用能力：\n{caps_str}"
    )
    try:
        content, _ = await llm.complete(
            [{"role": "system", "content": _PLANNER_SYSTEM},
             {"role": "user", "content": user_msg}],
            temperature=0.2,
            max_tokens=600,
            disable_thinking=True,  # JSON extraction: reasoning would starve the budget
        )
    except Exception:
        return None
    obj = _extract_json(content)
    if not obj or not isinstance(obj.get("steps"), list):
        return None
    steps: list[PlanStep] = []
    for raw in obj["steps"]:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role", "")).strip()
        task = str(raw.get("task", "")).strip()
        if role not in VALID_AGENT_ROLES or not task:
            continue
        skill_ids: list[str] = []
        raw_skill = raw.get("skill_id")
        raw_skills = raw.get("skill_ids")
        if isinstance(raw_skill, str) and raw_skill.strip():
            skill_ids.append(raw_skill.strip())
        if isinstance(raw_skills, list):
            skill_ids.extend(str(v).strip() for v in raw_skills if str(v).strip())
        # Unknown ids are not executable and must never pass into the router.
        from .skill_runtime.registry import registry as skill_registry
        skill_ids = [sid for sid in dict.fromkeys(skill_ids)
                     if _known_skill(skill_registry, sid, role)]
        steps.append(PlanStep(agent_role=role, task=task, skill_ids=skill_ids))
    if not steps:
        return None
    plan = TaskPlan(steps=steps[:_MAX_PLAN_STEPS], source="llm")
    if not _validate(plan):
        return None
    return plan


# --- public entry point -----------------------------------------------------

async def make_plan(u: TaskUnderstanding, snap: StudentSnapshot,
                    session: TutorSession, llm: AsyncLLMClient | None = None,
                    *, use_llm: bool | None = None) -> tuple[TaskPlan, str]:
    """Produce a (plan, goal) for the understanding + student state.

    Rule fast path first. For the richer intents we try the LLM; any failure or
    invalid plan degrades to the rule plan tagged source='fallback'. The goal
    string is derived from the understanding for TaskState persistence.

    `use_llm`: None -> read SUPERVISOR_LLM_PLAN env (default on).
    """
    if use_llm is None:
        use_llm = os.getenv("SUPERVISOR_LLM_PLAN", "1") not in ("0", "false", "False")

    goal = _goal_from(u)
    ruled = _rule_plan(u, snap)

    # chitchat -> empty rule plan, never call LLM
    if u.intent == TaskType.CHITCHAT:
        return ruled, goal

    # only richer intents benefit from the LLM planner
    rich = u.intent in (TaskType.GENERATE, TaskType.SOLVE, TaskType.PLAN, TaskType.DIAGNOSE)
    if not use_llm or llm is None or not rich:
        return ruled, goal

    llm_plan = await _llm_plan(u, snap, llm)
    if llm_plan is not None:
        return llm_plan, goal

    # LLM failed/invalid -> degrade to rule plan, tagged fallback
    fallback = TaskPlan(steps=ruled.steps, source="fallback")
    return fallback, goal
