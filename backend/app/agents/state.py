"""Supervisor Agent state structures (V2 module 1: orchestrator).

Data types the Supervisor uses to reason about a learning task, plan an
agent workflow, and track progress across turns.

Design notes:
  - Plain dataclasses with to_dict/from_dict, mirroring TutorSession style.
  - TaskType is a str-Enum so it serializes cleanly to JSON.
  - StudentSnapshot is deliberately *lightweight*: it derives only from
    signals V1 already has (grade, uploaded materials, quiz_history,
    compaction summary). It does NOT invent numeric mastery scores like
    physics_level/knowledge_state -- those belong to a future Memory /
    Student Model module (V3). The boundary is surfaced explicitly so later
    work knows where to plug in.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    """What kind of learning task the student's message represents.

    CHITCHAT covers greetings / acks and maps onto the V1 'direct' branch.
    """
    EXPLAIN = "explain"        # 讲解一个知识点 / 我不懂 X
    PRACTICE = "practice"      # 出题 / 练习 / 测一测
    DIAGNOSE = "diagnose"      # 我为什么总做错 / 薄弱点分析
    REVIEW = "review"          # 复习 / 总结
    GENERATE = "generate"      # 生成教案 / 学习计划
    SOLVE = "solve"            # 解一道具体题目
    PLAN = "plan"              # 规划学习路径
    CHITCHAT = "chitchat"      # 问候 / 致谢 / 确认

    @classmethod
    def from_value(cls, v: Any) -> "TaskType | None":
        """Tolerant parse: returns None on unknown values (don't raise)."""
        if v is None:
            return None
        if isinstance(v, TaskType):
            return v
        try:
            return cls(str(v))
        except ValueError:
            return None


@dataclass
class TaskUnderstanding:
    """Structured output of task understanding (LLM or rule-based).

    `source` records which path produced it so the trace can explain why the
    Supervisor chose the resulting plan.
    """
    intent: TaskType = TaskType.CHITCHAT
    subject: str = ""          # 学科: 物理/数学/...
    concept: str = ""          # 核心知识点
    goal: str = ""             # understand / solve_problem / practice / ...
    difficulty: str | None = None
    requires_tools: bool = False
    confidence: float = 1.0    # 0.0-1.0; rule short-circuit = 1.0
    source: str = "rule"       # "rule" | "llm" | "fallback"
    # Explicit output constraints extracted from the student's wording. These
    # are control-plane facts, not a second teaching strategy.
    response_format: str = ""  # one_sentence | concise | table | steps | ""
    allow_followup_assessment: bool = True
    # LLM-refined retrieval terms for the turn (concept/lesson/chapter names).
    # Empty on the rule path -> pre-retrieval falls back to the raw message.
    # Bounded to 3 by task_understanding; each entry is a short search term,
    # never the full sentence (see preresearch R10 contract).
    search_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "subject": self.subject,
            "concept": self.concept,
            "goal": self.goal,
            "difficulty": self.difficulty,
            "requires_tools": self.requires_tools,
            "confidence": self.confidence,
            "source": self.source,
            "response_format": self.response_format,
            "allow_followup_assessment": self.allow_followup_assessment,
            "search_queries": list(self.search_queries),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskUnderstanding":
        return cls(
            intent=TaskType.from_value(d.get("intent")) or TaskType.CHITCHAT,
            subject=d.get("subject", "") or "",
            concept=d.get("concept", "") or "",
            goal=d.get("goal", "") or "",
            difficulty=d.get("difficulty"),
            requires_tools=bool(d.get("requires_tools", False)),
            confidence=float(d.get("confidence", 1.0)),
            source=d.get("source", "rule") or "rule",
            response_format=str(d.get("response_format", "") or ""),
            allow_followup_assessment=bool(d.get("allow_followup_assessment", True)),
            search_queries=[str(q).strip() for q in (d.get("search_queries") or [])
                            if str(q).strip()][:3],
        )


@dataclass
class PlanStep:
    """One step of a Supervisor workflow plan.

    `agent_role` is a *capability* name (not a concrete agent process yet):
    knowledge / teaching / assessment / memory. The router maps it onto the
    actual tool subset the executor may use for this step.
    """
    agent_role: str            # "knowledge" | "teaching" | "assessment" | "memory"
    task: str                  # natural-language instruction fed to executor
    suggested_tools: list[str] = field(default_factory=list)
    # M10 executable capability ids. suggested_tools remains for backwards
    # compatibility while plans migrate from tool names to Skill Contracts.
    skill_ids: list[str] = field(default_factory=list)
    optional: bool = False
    # Structured execution hint for deterministic plan fulfillment. Normal
    # ReAct calls remain model-driven; when auto_invoke is true and the model
    # omits the call, the executor may issue this exact authorized tool call.
    tool_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    auto_invoke: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_role": self.agent_role,
            "task": self.task,
            "suggested_tools": list(self.suggested_tools),
            "skill_ids": list(self.skill_ids),
            "optional": self.optional,
            "tool_args": {name: dict(args) for name, args in self.tool_args.items()},
            "auto_invoke": self.auto_invoke,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlanStep":
        return cls(
            agent_role=d.get("agent_role", "teaching"),
            task=d.get("task", "") or "",
            suggested_tools=list(d.get("suggested_tools", []) or []),
            skill_ids=list(d.get("skill_ids", []) or []),
            optional=bool(d.get("optional", False)),
            tool_args={str(name): dict(args) for name, args
                       in (d.get("tool_args", {}) or {}).items()
                       if isinstance(args, dict)},
            auto_invoke=bool(d.get("auto_invoke", False)),
        )


# Valid capability roles. Keep in sync with router.CAPABILITIES.
VALID_AGENT_ROLES = ("knowledge", "teaching", "assessment", "memory")


@dataclass
class TaskPlan:
    """An ordered workflow plan. Advisory + constraining, not a rigid script:
    the executor (ReAct loop) still decides within the bounds the plan sets
    (visible tool subset + step instruction).
    """
    steps: list[PlanStep] = field(default_factory=list)
    source: str = "rule"       # "rule" | "llm" | "fallback"
    # Explicit output constraints extracted from the student's wording. These
    # are control-plane facts, not a second teaching strategy.
    response_format: str = ""  # one_sentence | concise | table | steps | ""
    allow_followup_assessment: bool = True
    validated: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "source": self.source,
            "validated": self.validated,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TaskPlan | None":
        if not d:
            return None
        return cls(
            steps=[PlanStep.from_dict(s) for s in (d.get("steps") or [])],
            source=d.get("source", "rule") or "rule",
            validated=bool(d.get("validated", True)),
            created_at=float(d.get("created_at", time.time())),
        )

    @property
    def is_empty(self) -> bool:
        return not self.steps


@dataclass
class StudentSnapshot:
    """Lightweight student-state snapshot derived from existing V1 signals.

    V2 carried only grade / materials / quiz count (no numeric mastery). V3
    (Student Model module) adds the richer fields below; when the Student
    Model is enabled, derive_snapshot() fills them from the model; when off,
    they stay empty so behavior degrades to the V2 lightweight view exactly.
    All additions are backward-compatible: from_dict defaults every V3 field.
    """
    grade: str = "本科"
    has_materials: bool = False
    material_count: int = 0
    material_names: list[str] = field(default_factory=list)
    recent_quiz_count: int = 0
    recent_weak_points: list[str] = field(default_factory=list)
    conversation_topic_hint: str | None = None
    # --- V3 Student Model additions (all optional, default empty) ---
    goals: list[str] = field(default_factory=list)
    current_subject: str = ""
    weak_skills: list[str] = field(default_factory=list)        # skill_ids
    strong_skills: list[str] = field(default_factory=list)
    mastery_map: dict[str, float] = field(default_factory=dict)  # skill_id -> p_known
    learning_style: dict[str, str] = field(default_factory=dict)
    recent_mistakes: list[str] = field(default_factory=list)
    unfinished_prereqs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "has_materials": self.has_materials,
            "material_count": self.material_count,
            "material_names": list(self.material_names),
            "recent_quiz_count": self.recent_quiz_count,
            "recent_weak_points": list(self.recent_weak_points),
            "conversation_topic_hint": self.conversation_topic_hint,
            "goals": list(self.goals),
            "current_subject": self.current_subject,
            "weak_skills": list(self.weak_skills),
            "strong_skills": list(self.strong_skills),
            "mastery_map": dict(self.mastery_map),
            "learning_style": dict(self.learning_style),
            "recent_mistakes": list(self.recent_mistakes),
            "unfinished_prereqs": list(self.unfinished_prereqs),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StudentSnapshot":
        return cls(
            grade=d.get("grade", "本科") or "本科",
            has_materials=bool(d.get("has_materials", False)),
            material_count=int(d.get("material_count", 0)),
            material_names=list(d.get("material_names", []) or []),
            recent_quiz_count=int(d.get("recent_quiz_count", 0)),
            recent_weak_points=list(d.get("recent_weak_points", []) or []),
            conversation_topic_hint=d.get("conversation_topic_hint"),
            goals=list(d.get("goals", []) or []),
            current_subject=d.get("current_subject", "") or "",
            weak_skills=list(d.get("weak_skills", []) or []),
            strong_skills=list(d.get("strong_skills", []) or []),
            mastery_map={k: float(v) for k, v in (d.get("mastery_map", {}) or {}).items()},
            learning_style=dict(d.get("learning_style", {}) or {}),
            recent_mistakes=list(d.get("recent_mistakes", []) or []),
            unfinished_prereqs=list(d.get("unfinished_prereqs", []) or []),
        )


@dataclass
class TaskState:
    """Cross-turn Supervisor memory, persisted on the session.

    Holds the current goal + an agent todo list (completed/remaining) so the
    Supervisor does not forget mid-task between turns. `execution_history` is
    capped by the supervisor; older entries live in the transcript.
    """
    current_goal: str = ""
    task_type: str | None = None   # TaskType value (str for JSON)
    plan: dict[str, Any] | None = None  # serialized TaskPlan
    completed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    execution_history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal,
            "task_type": self.task_type,
            "plan": self.plan,
            "completed": list(self.completed),
            "remaining": list(self.remaining),
            "execution_history": list(self.execution_history),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TaskState":
        if not d:
            return cls()
        return cls(
            current_goal=d.get("current_goal", "") or "",
            task_type=d.get("task_type"),
            plan=d.get("plan"),
            completed=list(d.get("completed", []) or []),
            remaining=list(d.get("remaining", []) or []),
            execution_history=list(d.get("execution_history", []) or []),
            updated_at=float(d.get("updated_at", time.time())),
        )

    @property
    def has_goal(self) -> bool:
        return bool(self.current_goal)
