"""M9 core data structures (module 9: learning orchestration).

Plain dataclasses with to_dict/from_dict round-trips, mirroring the pattern
used by student_model/state.py (M2), teaching_engine/state.py (M3), and
evaluation/schema.py (M7). No behaviour here beyond serialization; the logic
lives in the sibling modules (goal_manager / spaced_repetition /
habit_tracker / schedule_engine / learning_planner / task_executor).

Scope note: these structures hold ONLY orchestration-level state -- long-term
plans, schedules, habit stats, SRS review cards. Academic truth (mastery,
concept state, teaching log, memory) lives in M2/M3/M6 and is never
duplicated here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_MAX_TASKS_PER_DAY = 20
_MAX_PLAN_CONCEPTS = 30
_MAX_EVENTS_REPLAY = 200
_MAX_GAPS = 40
_MAX_PROJECTION_WEEKS = 52
_MAX_WEEK_TASKS = 12      # per weekly plan
_MAX_SUBTASKS = 8         # per week task
_MAX_GOALS = 4            # per student (multi-goal orchestration)

# stage labels a daily task can carry (LLM composer / user-assigned)
TASK_PHASES = ("foundation", "reinforce", "sprint")

# provenance tag shared by the whole plan hierarchy: anything "user" is
# never touched by any generation/regeneration pipeline.
TASK_SOURCES = ("auto", "user")


class GoalType(str, Enum):
    """Kind of long-term learning goal.

    EXAM     : a fixed-date exam (kaoyan / gaokao / mid-term / finals).
    ABILITY  : an open-ended ability milestone (master linear algebra).
    INTEREST : curiosity-driven (understand quantum mechanics basics).
    """
    EXAM = "exam"
    ABILITY = "ability"
    INTEREST = "interest"

    @classmethod
    def from_value(cls, v: Any) -> "GoalType":
        if isinstance(v, GoalType):
            return v
        try:
            return cls(str(v))
        except (ValueError, TypeError):
            return cls.ABILITY


class DailyTaskStatus(str, Enum):
   PENDING = "pending"
   IN_PROGRESS = "in_progress"
   COMPLETED = "completed"
   SKIPPED = "skipped"
   OVERDUE = "overdue"

   @classmethod
   def from_value(cls, v: Any) -> "DailyTaskStatus":
       if isinstance(v, DailyTaskStatus):
           return v
       try:
           return cls(str(v))
       except (ValueError, TypeError):
           return cls.PENDING

class GoalAnalysisLevel(str, Enum):
    """Coarse current-vs-target proficiency bucket, used by GoalAnalyzer.

    Maps a student's measured mastery of a subject's skill graph into one of
    five coarse levels so the gap analysis can be computed deterministically
    (zero LLM) before planning. The mapping is read-only over M2 mastery.
    """
    NOVICE = "novice"        # <20% of subject skills mastered
    BEGINNER = "beginner"    # 20-40%
    INTERMEDIATE = "intermediate"  # 40-65%
    ADVANCED = "advanced"    # 65-85%
    PROFICIENT = "proficient"  # >85%

    @classmethod
    def from_value(cls, v: Any) -> "GoalAnalysisLevel":
        if isinstance(v, GoalAnalysisLevel):
            return v
        try:
            return cls(str(v))
        except (ValueError, TypeError):
            return cls.NOVICE

    @classmethod
    def from_mastery_ratio(cls, ratio: float) -> "GoalAnalysisLevel":
        """Map a 0..1 mastered-skills ratio to a coarse level."""
        r = max(0.0, min(1.0, float(ratio)))
        if r < 0.20:
            return cls.NOVICE
        if r < 0.40:
            return cls.BEGINNER
        if r < 0.65:
            return cls.INTERMEDIATE
        if r < 0.85:
            return cls.ADVANCED
        return cls.PROFICIENT


class MilestoneStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

    @classmethod
    def from_value(cls, v: Any) -> "MilestoneStatus":
        if isinstance(v, MilestoneStatus):
            return v
        try:
            return cls(str(v))
        except (ValueError, TypeError):
            return cls.NOT_STARTED


class TaskKind(str, Enum):
    """What kind of action a daily task represents.

    Mirrors the M3 TeachingMode family at the task granularity.
    """
    STUDY = "study"
    REVIEW = "review"
    PRACTICE = "practice"
    SUMMARY = "summary"

    @classmethod
    def from_value(cls, v: Any) -> "TaskKind":
        if isinstance(v, TaskKind):
            return v
        try:
            return cls(str(v))
        except (ValueError, TypeError):
            return cls.STUDY


@dataclass
class LearningGoal:
    """A long-term learning goal (top of the plan hierarchy).

    Multiple goals may coexist (state.goals, capped at _MAX_GOALS); the
    weekly planner merges every goal's required skills into one shared plan.

    Distinct from M2 StudentProfile.goals (a free-text list of stated
    intents). This is the structured, scheduled goal that drives milestone +
    weekly planning.

    target_concept_ids: concept-level binding to the knowledge graph. When
    non-empty, gap analysis runs over the goal's prerequisite closure (the
    chain of unmastered concepts the targets actually depend on) instead of
    the whole subject -- multi-subject goals work naturally, and progress is
    measured against the chain, not the entire syllabus. Empty = legacy
    whole-subject analysis (subject-string matching as fallback).
    """
    id: str = ""                # stable id "g_{seq}" (API addressing)
    title: str = ""
    description: str = ""
    goal_type: GoalType = GoalType.ABILITY
    subjects: list[str] = field(default_factory=list)
    target_concept_ids: list[str] = field(default_factory=list)
    deadline: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title,
            "description": self.description,
            "goal_type": self.goal_type.value, "subjects": list(self.subjects),
            "target_concept_ids": list(self.target_concept_ids),
            "deadline": self.deadline, "created_at": self.created_at,
            "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LearningGoal":
        d = d or {}
        return cls(id=str(d.get("id", "")), title=str(d.get("title", "")),
            description=str(d.get("description", "")),
            goal_type=GoalType.from_value(d.get("goal_type")),
            subjects=list(d.get("subjects", []) or []),
            target_concept_ids=list(d.get("target_concept_ids", []) or []),
            deadline=float(d.get("deadline", 0.0)),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())))


@dataclass
class GapItem:
    """One identified knowledge gap between the goal and current mastery.

    Produced by GoalAnalyzer. W4/A13: 'unknown' skills have NO observation
    yet (未测——不宣称缺口，也不等于不会); 'weak' skills are observed below
    target mastery (确认的薄弱). Legacy persisted rows may still read
    'missing' (pre-W4 wording for unknown) and round-trip unchanged. All
    mastery values are read-only projections from M2.
    """
    skill_id: str = ""
    name: str = ""
    subject: str = ""
    difficulty: int = 3
    status: str = "unknown"  # "unknown" | "weak" (legacy rows: "missing")
    current_mastery: float = 0.0
    target_mastery: float = 0.75
    # 拓扑层级（1 = 无未掌握前置，现在就能学；2 = 需先完成某层-1 概念…）。
    # 由 goal_analyzer 按前置链最长路径计算，0 = 未分层（无 prereq 数据）。
    layer: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "name": self.name,
            "subject": self.subject, "difficulty": self.difficulty,
            "status": self.status, "current_mastery": round(self.current_mastery, 3),
            "target_mastery": self.target_mastery, "layer": self.layer}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "GapItem":
        d = d or {}
        return cls(skill_id=str(d.get("skill_id", "")), name=str(d.get("name", "")),
            subject=str(d.get("subject", "")), difficulty=int(d.get("difficulty", 3)),
            status=str(d.get("status", "unknown")),
            current_mastery=float(d.get("current_mastery", 0.0)),
            target_mastery=float(d.get("target_mastery", 0.75)),
            layer=int(d.get("layer", 0)))


@dataclass
class GoalState:
    """The reasoned output of GoalAnalyzer: WHY the plan looks the way it does.

    This is the gap-analysis + backward-planning result that feeds the
    LearningPlanner. It answers "how far is the student from the goal and what
    knowledge must they acquire", read-only over M2 mastery and the M5 skill
    graph. It does NOT own mastery values -- it only references them.

    Distinct from LearningGoal (the intent) and WeeklyPlan (the schedule):
    GoalState is the *reasoning layer* between them.
    """
    goal_id: str = ""            # pairing key into state.goals
    goal_title: str = ""
    goal_type: GoalType = GoalType.ABILITY
    subject: str = ""
    deadline: float = 0.0
    current_level: GoalAnalysisLevel = GoalAnalysisLevel.NOVICE
    target_level: GoalAnalysisLevel = GoalAnalysisLevel.PROFICIENT
    mastered_ratio: float = 0.0
    total_skills: int = 0
    mastered_skills: int = 0
    gaps: list[GapItem] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    recommended_strategy: str = ""
    urgency: float = 0.0  # 0..1 deadline pressure
    analyzed_at: float = field(default_factory=time.time)
    # -- goal x genealogy binding (L1 目标链) --
    # chain_mode: "concept_chain" = gaps/progress are the goal's prerequisite
    # closure (what the TARGET actually needs); "subject" = legacy whole-subject
    # analysis. The UI uses this to label which口径 the numbers describe.
    chain_mode: str = "subject"
    target_concept_ids: list[str] = field(default_factory=list)
    # deterministic schedule estimate (see goal_analyzer.estimate_schedule):
    # {weekly_pace, est_weeks, weeks_left, fit, note}
    estimate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"goal_id": self.goal_id, "goal_title": self.goal_title,
            "goal_type": self.goal_type.value, "subject": self.subject,
            "deadline": self.deadline,
            "current_level": self.current_level.value,
            "target_level": self.target_level.value,
            "mastered_ratio": round(self.mastered_ratio, 3),
            "total_skills": self.total_skills,
            "mastered_skills": self.mastered_skills,
            "gaps": [g.to_dict() for g in self.gaps],
            "required_skills": list(self.required_skills),
            "recommended_strategy": self.recommended_strategy,
            "urgency": round(self.urgency, 3),
            "analyzed_at": self.analyzed_at,
            "chain_mode": self.chain_mode,
            "target_concept_ids": list(self.target_concept_ids),
            "estimate": dict(self.estimate)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "GoalState":
        d = d or {}
        return cls(goal_id=str(d.get("goal_id", "")),
            goal_title=str(d.get("goal_title", "")),
            goal_type=GoalType.from_value(d.get("goal_type")),
            subject=str(d.get("subject", "")),
            deadline=float(d.get("deadline", 0.0)),
            current_level=GoalAnalysisLevel.from_value(d.get("current_level")),
            target_level=GoalAnalysisLevel.from_value(d.get("target_level", "proficient")),
            mastered_ratio=float(d.get("mastered_ratio", 0.0)),
            total_skills=int(d.get("total_skills", 0)),
            mastered_skills=int(d.get("mastered_skills", 0)),
            gaps=[GapItem.from_dict(g) for g in (d.get("gaps") or [])][:_MAX_GAPS],
            required_skills=list(d.get("required_skills") or []),
            recommended_strategy=str(d.get("recommended_strategy", "")),
            urgency=float(d.get("urgency", 0.0)),
            analyzed_at=float(d.get("analyzed_at", time.time())),
            chain_mode=str(d.get("chain_mode", "subject")),
            target_concept_ids=list(d.get("target_concept_ids") or []),
            estimate=dict(d.get("estimate") or {}))


@dataclass
class Milestone:
    """One milestone toward a LearningGoal (e.g. "master calculus basics").

    Groups a set of concepts; status is derived read-only from M2 mastery of
    those concepts. M9 owns the milestone structure, not the mastery values.
    """
    id: str = ""
    title: str = ""
    concept_ids: list[str] = field(default_factory=list)
    status: MilestoneStatus = MilestoneStatus.NOT_STARTED
    order: int = 0
    target_mastery: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title,
            "concept_ids": list(self.concept_ids), "status": self.status.value,
            "order": self.order, "target_mastery": self.target_mastery}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Milestone":
        d = d or {}
        return cls(id=str(d.get("id", "")), title=str(d.get("title", "")),
            concept_ids=list(d.get("concept_ids", []) or []),
            status=MilestoneStatus.from_value(d.get("status")),
            order=int(d.get("order", 0)),
            target_mastery=float(d.get("target_mastery", 0.75)))


@dataclass
class PlanConcept:
    """A concept scheduled into a weekly plan.

    planned_mastery is an M9-owned target for the week; actual mastery lives
    in M2 (read-only). The gap drives re-planning.
    """
    concept_id: str = ""
    name: str = ""
    milestone_id: str = ""
    week_index: int = 0
    difficulty: int = 3
    planned_mastery: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id, "name": self.name,
            "milestone_id": self.milestone_id, "week_index": self.week_index,
            "difficulty": self.difficulty, "planned_mastery": self.planned_mastery}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PlanConcept":
        d = d or {}
        return cls(concept_id=str(d.get("concept_id", "")),
            name=str(d.get("name", "")), milestone_id=str(d.get("milestone_id", "")),
            week_index=int(d.get("week_index", 0)), difficulty=int(d.get("difficulty", 3)),
            planned_mastery=float(d.get("planned_mastery", 0.75)))


@dataclass
class SubTask:
    """One actionable step inside a WeekTask ("做 10 道浮力计算题").

    LLM-recommended (source="auto") or user-added (source="user"); user
    subtasks survive plan regeneration. Completion is tracked here and is
    also written back when a linked DailyTask completes.
    """
    id: str = ""
    title: str = ""
    source: str = "auto"          # auto | user (TASK_SOURCES)
    estimate_minutes: int = 15
    done: bool = False
    done_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "source": self.source,
            "estimate_minutes": self.estimate_minutes, "done": self.done,
            "done_at": self.done_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "SubTask":
        d = d or {}
        src = str(d.get("source", "auto"))
        return cls(id=str(d.get("id", "")), title=str(d.get("title", "")),
            source=src if src in TASK_SOURCES else "auto",
            estimate_minutes=max(1, int(d.get("estimate_minutes", 15))),
            done=bool(d.get("done", False)),
            done_at=float(d.get("done_at", 0.0)))


@dataclass
class WeekTask:
    """One action-level task inside a weekly plan ("学完浮力前两节").

    The week→day materialisation path picks from its unfinished subtasks, so
    what the user sees in the weekly plan is what actually lands in today's
    tasks. `done` is a manual toggle; the effective state (to_dict) is done
    OR every subtask done.
    """
    id: str = ""
    title: str = ""
    concept_ids: list[str] = field(default_factory=list)
    kind: str = "study"           # TaskKind value
    source: str = "auto"          # auto | user — user tasks survive regen
    subtasks: list[SubTask] = field(default_factory=list)
    done: bool = False

    @property
    def effective_done(self) -> bool:
        return self.done or (bool(self.subtasks)
                             and all(st.done for st in self.subtasks))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title,
            "concept_ids": list(self.concept_ids), "kind": self.kind,
            "source": self.source, "done": self.effective_done,
            "subtasks": [s.to_dict() for s in self.subtasks]}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "WeekTask":
        d = d or {}
        src = str(d.get("source", "auto"))
        kind = str(d.get("kind", "study"))
        return cls(id=str(d.get("id", "")), title=str(d.get("title", "")),
            concept_ids=list(d.get("concept_ids", []) or []),
            kind=kind if kind in {k.value for k in TaskKind} else "study",
            source=src if src in TASK_SOURCES else "auto",
            subtasks=[SubTask.from_dict(s) for s in (d.get("subtasks") or [])][:_MAX_SUBTASKS],
            done=bool(d.get("done", False)))


@dataclass
class WeeklyPlan:
    week_index: int = 0
    week_start: float = 0.0
    focus: str = ""
    concepts: list[PlanConcept] = field(default_factory=list)
    tasks: list[WeekTask] = field(default_factory=list)
    origin: str = "auto"          # auto | user — user weeks survive regen

    def to_dict(self) -> dict[str, Any]:
        return {"week_index": self.week_index, "week_start": self.week_start,
            "focus": self.focus, "origin": self.origin,
            "concepts": [c.to_dict() for c in self.concepts],
            "tasks": [t.to_dict() for t in self.tasks]}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "WeeklyPlan":
        d = d or {}
        origin = str(d.get("origin", "auto"))
        return cls(week_index=int(d.get("week_index", 0)),
            week_start=float(d.get("week_start", 0.0)), focus=str(d.get("focus", "")),
            concepts=[PlanConcept.from_dict(c) for c in (d.get("concepts") or [])][:_MAX_PLAN_CONCEPTS],
            tasks=[WeekTask.from_dict(t) for t in (d.get("tasks") or [])][:_MAX_WEEK_TASKS],
            origin=origin if origin in TASK_SOURCES else "auto")


@dataclass
class DailyTask:
    """A single actionable task for a specific day.

    Generated by task_executor from the weekly plan + SRS queue. Completing it
    does NOT set mastery (M2's job). M9 only tracks the task lifecycle.
    """
    id: str = ""
    day: str = ""
    concept_id: str = ""
    concept_name: str = ""
    kind: TaskKind = TaskKind.STUDY
    status: DailyTaskStatus = DailyTaskStatus.PENDING
    priority: int = 3
    estimate_minutes: int = 15
    milestone_id: str = ""
    week_task_id: str = ""    # source WeekTask this task materialised from
    subtask_id: str = ""      # source SubTask (completion writes back to it)
    title: str = ""           # user-facing custom title (optional)
    phase: str = ""           # one of TASK_PHASES ("" = unlabelled)
    custom: bool = False      # True = user-created; pipelines never touch it
    reason: str = ""          # "why today" note (LLM coach / template)
    # W4/A12: launch binding + completion attribution.
    episode_id: str = ""      # active LearningEpisode bound via launch
    session_id: str = ""      # the pre-created chat session of that episode
    completion_source: str = ""   # "" | quiz_evidence | self_report
    evidence_attempt_id: str = ""  # the committed attempt that completed it
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "day": self.day, "concept_id": self.concept_id,
            "concept_name": self.concept_name, "kind": self.kind.value,
            "status": self.status.value, "priority": self.priority,
            "estimate_minutes": self.estimate_minutes, "milestone_id": self.milestone_id,
            "week_task_id": self.week_task_id, "subtask_id": self.subtask_id,
            "title": self.title, "phase": self.phase, "custom": self.custom,
            "reason": self.reason,
            "episode_id": self.episode_id, "session_id": self.session_id,
            "completion_source": self.completion_source,
            "evidence_attempt_id": self.evidence_attempt_id,
            "created_at": self.created_at, "completed_at": self.completed_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "DailyTask":
        d = d or {}
        return cls(id=str(d.get("id", "")), day=str(d.get("day", "")),
            concept_id=str(d.get("concept_id", "")),
            concept_name=str(d.get("concept_name", "")),
            kind=TaskKind.from_value(d.get("kind")),
            status=DailyTaskStatus.from_value(d.get("status")),
            priority=int(d.get("priority", 3)),
            estimate_minutes=int(d.get("estimate_minutes", 15)),
            milestone_id=str(d.get("milestone_id", "")),
            week_task_id=str(d.get("week_task_id", "")),
            subtask_id=str(d.get("subtask_id", "")),
            title=str(d.get("title", "")), phase=str(d.get("phase", "")),
            custom=bool(d.get("custom", False)),
            reason=str(d.get("reason", "")),
            episode_id=str(d.get("episode_id", "")),
            session_id=str(d.get("session_id", "")),
            completion_source=str(d.get("completion_source", "")),
            evidence_attempt_id=str(d.get("evidence_attempt_id", "")),
            created_at=float(d.get("created_at", time.time())),
            completed_at=float(d.get("completed_at", 0.0)))


@dataclass
class ScheduleConfig:
    """The student's time budget and constraints.

    Drives task_executor: sizes daily tasks to fit daily_minutes and
    prioritises concepts whose exam date is nearer.
    """
    daily_minutes: int = 45
    available_days: list[str] = field(default_factory=lambda: [
        "mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    preferred_time: str = ""
    exam_dates: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"daily_minutes": self.daily_minutes,
            "available_days": list(self.available_days),
            "preferred_time": self.preferred_time,
            "exam_dates": {str(k): float(v) for k, v in self.exam_dates.items()}}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ScheduleConfig":
        d = d or {}
        return cls(daily_minutes=int(d.get("daily_minutes", 45)),
            available_days=list(d.get("available_days", []) or []),
            preferred_time=str(d.get("preferred_time", "")),
            exam_dates={str(k): float(v) for k, v in (d.get("exam_dates") or {}).items()})


@dataclass
class HabitStats:
    """Task-completion + consistency statistics (M9-owned execution layer).

    After the M9 review (modification 2), the ownership split is:

      - M9 TaskCompletionTracker (THIS dataclass): task EXECUTION stats --
        completed_tasks / total_tasks / procrastination_count. M9 owns these
        because they are derived from its own daily-task lifecycle.
      - M6 HabitPatternMemory: LONG-TERM behavioural patterns -- streak,
        time-of-day efficiency, weekend completion rate. These live in M6
        semantic memory ("study_habit" category).

    The streak / active-day fields below are READ-ONLY projections from M6
    episodes (computed by habit_tracker but owned by M6). They are carried
    here so schedule_engine can adapt granularity without a second I/O, but the
    AUTHORITATIVE long-term habit pattern lives in M6. M9 never persists these
    as its own truth -- M6 does, during consolidation.
    """
    current_streak: int = 0
    longest_streak: int = 0
    last_active_day: str = ""
    total_active_days: int = 0
    completed_tasks: int = 0
    total_tasks: int = 0
    procrastination_count: int = 0
    updated_at: float = field(default_factory=time.time)

    @property
    def completion_rate(self) -> float:
        return self.completed_tasks / self.total_tasks if self.total_tasks > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"current_streak": self.current_streak,
            "longest_streak": self.longest_streak,
            "last_active_day": self.last_active_day,
            "total_active_days": self.total_active_days,
            "completed_tasks": self.completed_tasks,
            "total_tasks": self.total_tasks,
            "procrastination_count": self.procrastination_count,
            "completion_rate": round(self.completion_rate, 3),
            "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "HabitStats":
        d = d or {}
        return cls(current_streak=int(d.get("current_streak", 0)),
            longest_streak=int(d.get("longest_streak", 0)),
            last_active_day=str(d.get("last_active_day", "")),
            total_active_days=int(d.get("total_active_days", 0)),
            completed_tasks=int(d.get("completed_tasks", 0)),
            total_tasks=int(d.get("total_tasks", 0)),
            procrastination_count=int(d.get("procrastination_count", 0)),
            updated_at=float(d.get("updated_at", time.time())))


@dataclass
class ReviewItem:
    """One SM-2 spaced-repetition card (M9-owned).

    The interval/easiness/repetitions triple is classic SM-2 scheduling
    state. The mastery posterior lives in M2 (read-only) -- orthogonal: SM-2
    decides WHEN to review, M2 records WHETHER the review succeeded.
    """
    concept_id: str = ""
    concept_name: str = ""
    easiness: float = 2.5
    interval: int = 0
    repetitions: int = 0
    next_review: float = 0.0
    # W4/A07: None until a real recall observation exists (legacy files keep
    # their historical value; only new cards default to "no observation").
    last_quality: int | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id, "concept_name": self.concept_name,
            "easiness": round(self.easiness, 3), "interval": self.interval,
            "repetitions": self.repetitions, "next_review": self.next_review,
            "last_quality": self.last_quality, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ReviewItem":
        d = d or {}
        lq = d.get("last_quality", None)
        return cls(concept_id=str(d.get("concept_id", "")),
            concept_name=str(d.get("concept_name", "")),
            easiness=float(d.get("easiness", 2.5)),
            interval=int(d.get("interval", 0)),
            repetitions=int(d.get("repetitions", 0)),
            next_review=float(d.get("next_review", 0.0)),
            last_quality=(int(lq) if lq is not None else None),
            created_at=float(d.get("created_at", time.time())))


@dataclass
class OrchestrationState:
    """Top-level working set for one student (the orchestration.json blob).

    Aggregates goals + per-goal gap analysis + weekly plan + daily tasks +
    schedule config + habit stats + SRS review queue. All purely
    orchestration-level: no mastery, no concept state, no memory records
    (those live in M2/M3/M6).
    """
    student_id: str = "student_default"
    goals: list[LearningGoal] = field(default_factory=list)   # cap _MAX_GOALS
    goal_states: list[GoalState] = field(default_factory=list)  # 1:1 by goal_id
    milestones: list[Milestone] = field(default_factory=list)
    weekly_plan: list[WeeklyPlan] = field(default_factory=list)
    daily_tasks: list[DailyTask] = field(default_factory=list)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    habit: HabitStats = field(default_factory=HabitStats)
    review_queue: dict[str, ReviewItem] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events_processed: int = 0
    # dedup for event emission toward M6 (highest already-reported values)
    last_streak_reported: int = 0
    last_progress_reported: float = -1.0
    # last regenerate attempt (success or empty). Distinguishes "never planned"
    # from "planned but nothing to schedule" so needs_replan cannot loop.
    last_plan_attempt: float = 0.0

    # --- multi-goal read helpers (single-goal legacy call sites) ----------

    @property
    def has_goals(self) -> bool:
        """True when at least one goal carries a title."""
        return any(g.title for g in self.goals)

    @property
    def primary_subject(self) -> str:
        """First non-empty subject across goals (event/prompt labeling)."""
        for g in self.goals:
            if g.subjects:
                return g.subjects[0]
        return ""

    @property
    def goals_label(self) -> str:
        """All goal titles joined for one prompt line ("A"、"B")."""
        return "、".join(g.title for g in self.goals if g.title)

    def goal_state_for(self, goal_id: str) -> GoalState | None:
        return next((gs for gs in self.goal_states
                     if gs.goal_id == goal_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {"student_id": self.student_id,
            "goals": [g.to_dict() for g in self.goals],
            "goal_states": [gs.to_dict() for gs in self.goal_states],
            "milestones": [m.to_dict() for m in self.milestones],
            "weekly_plan": [w.to_dict() for w in self.weekly_plan],
            "daily_tasks": [t.to_dict() for t in self.daily_tasks],
            "schedule": self.schedule.to_dict(), "habit": self.habit.to_dict(),
            "review_queue": {k: v.to_dict() for k, v in self.review_queue.items()},
            "created_at": self.created_at, "updated_at": self.updated_at,
            "events_processed": self.events_processed,
            "last_streak_reported": self.last_streak_reported,
            "last_progress_reported": self.last_progress_reported,
            "last_plan_attempt": self.last_plan_attempt}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "OrchestrationState":
        d = d or {}
        goals = [LearningGoal.from_dict(g)
                 for g in (d.get("goals") or [])][:_MAX_GOALS]
        goal_states = [GoalState.from_dict(gs)
                       for gs in (d.get("goal_states") or [])]
        # legacy single-goal migration: an old blob stores a scalar `goal`
        # (+ `goal_state`); wrap it into the list form with a stable id.
        if not goals:
            legacy = LearningGoal.from_dict(d.get("goal"))
            if legacy.title:
                legacy.id = legacy.id or "g_1"
                goals = [legacy]
                legacy_gs = GoalState.from_dict(d.get("goal_state"))
                legacy_gs.goal_id = legacy_gs.goal_id or legacy.id
                goal_states = [legacy_gs]
        return cls(student_id=str(d.get("student_id", "student_default")),
            goals=goals, goal_states=goal_states,
            milestones=[Milestone.from_dict(m) for m in (d.get("milestones") or [])],
            weekly_plan=[WeeklyPlan.from_dict(w) for w in (d.get("weekly_plan") or [])],
            daily_tasks=[DailyTask.from_dict(t) for t in (d.get("daily_tasks") or [])][:_MAX_TASKS_PER_DAY * 7],
            schedule=ScheduleConfig.from_dict(d.get("schedule")),
            habit=HabitStats.from_dict(d.get("habit")),
            review_queue={k: ReviewItem.from_dict(v) for k, v in (d.get("review_queue") or {}).items()},
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
            events_processed=int(d.get("events_processed", 0)),
            last_streak_reported=int(d.get("last_streak_reported", 0)),
            last_progress_reported=float(d.get("last_progress_reported", -1.0)),
            last_plan_attempt=float(d.get("last_plan_attempt", 0.0)))


@dataclass
class OrchestrationEvent:
    """An immutable record of one orchestration-level action (black-box log).

    Distinct from M2 LearningEvent (academic) and M6 EpisodicMemory
    (narrative): purely about plan/schedule/habit changes.
    """
    ts: float = field(default_factory=time.time)
    type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "type": self.type, "payload": dict(self.payload)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OrchestrationEvent":
        return cls(ts=float(d.get("ts", time.time())), type=str(d.get("type", "")),
            payload=dict(d.get("payload", {}) or {}))


# --- event flowing to M6 (orchestration -> long-term memory) ----------------
# Closed set of event types M9 may emit toward M6's event bus. The supervisor
# forwards these into M6.consume_turn; M6 (not M9) decides whether to persist
# them as episodic/semantic memory. M9 never writes M6 storage directly.
ORCHESTRATION_EVENT_TYPES = frozenset({
    "milestone_completed",   # a milestone reached target mastery
    "habit_milestone",       # streak/consistency achievement (e.g. 7-day streak)
    "goal_progress",         # mastered_ratio crossed a threshold
    "plan_regenerated",      # the weekly plan was rebuilt
    "task_batch_completed",  # all of today's tasks done
})


@dataclass
class OrchestrationLearningEvent:
    """An orchestration-level event emitted toward M6's event bus.

    This realises the M9 -> M6 *event flow* (modification 1): M9 does NOT
    write M6 storage; instead it produces these events, the supervisor forwards
    them to M6.consume_turn, and M6 decides whether to persist them as episodic
    memory (e.g. "completed the calculus milestone", "30-day study streak").
    The event_type is validated against ORCHESTRATION_EVENT_TYPES so a bug in
    M9 cannot inject arbitrary events into M6.

    importance is read by M6's classifier to decide retention priority.
    """
    event_type: str = ""
    summary: str = ""
    subject: str = ""
    importance: float = 0.5
    payload: dict[str, Any] = field(default_factory=dict)

    def to_event_dict(self) -> dict[str, Any]:
        """Render as a plain event dict compatible with M6's consume_turn
        events list (event_type / summary / subject / importance + payload)."""
        out = {"event_type": self.event_type, "summary": self.summary,
               "subject": self.subject, "importance": self.importance}
        out.update(self.payload)
        return out

    @property
    def valid(self) -> bool:
        return self.event_type in ORCHESTRATION_EVENT_TYPES
