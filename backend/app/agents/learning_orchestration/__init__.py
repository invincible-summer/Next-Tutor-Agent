"""Learning Orchestration Intelligence (module 9): the longitudinal planning layer.

Where M1 answers "what task to run now", M2 "what does this student know",
M3 "how to teach this lesson", M4 "did they learn it", M5 "what knowledge
exists", M6 "what to remember", M7 "is the tutor improving", and M8 "how to
express it", this module answers the longitudinal question:

    "how should the student continue to grow over weeks and months?"

It is a VERTICAL ORCHESTRATION layer sitting above the M1-M4 pipeline: it owns
ONLY long-term plans (goals -> weekly tasks -> daily tasks),
the spaced-repetition review schedule, and study-habit statistics. It reads
M2 mastery, M3 curriculum, M5 knowledge graph, and M6 episodes as READ-ONLY
projections and never writes them back.

Design contract (must hold to protect M1-M8):
  - OBSERVER + EVENT EMITTER: never directly writes M2/M3/M5/M6 storage, but
    EMITS OrchestrationLearningEvents (streak achieved, goal-progress) which
    the supervisor forwards into M6's event bus -- M6 decides whether to
    persist them. See event_emitter.py for the flow.
  - REUSE NOT REBUILD: learning_planner reuses M3's build_learning_path as its
    single-session inference engine, layering schedule/habit/SRS on top.
  - ORTHOGONAL SRS: the SM-2 interval is M9's; the mastery posterior is M2's.
  - GRACEFUL: ORCHESTRATION_MODE (default on). When off, both supervisor hooks
    are no-ops and M1-M8 behavior is byte-identical. Nine layers orthogonal.
  - DETERMINISTIC-FIRST: SM-2, habit stats, schedule, planning are pure
    functions (zero LLM on the per-turn critical path).
"""
from __future__ import annotations

from .schema import (DailyTask, DailyTaskStatus, GapItem, GoalAnalysisLevel,
                     GoalState, HabitStats, LearningGoal,
                     Milestone,
                     MilestoneStatus, OrchestrationLearningEvent,
                     OrchestrationState, PlanConcept,
                     ReviewItem, ScheduleConfig,
                     SubTask, WeekTask, WeeklyPlan, GoalType)
from .manager import (LearningOrchestrationService, get_orchestration_service,
                      is_enabled)

__all__ = [
    "LearningOrchestrationService",
    "get_orchestration_service",
    "is_enabled",
    "DailyTask",
    "DailyTaskStatus",
    "GapItem",
    "GoalAnalysisLevel",
    "GoalState",
    "HabitStats",
    "LearningGoal",
    "Milestone",
    "MilestoneStatus",
    "OrchestrationLearningEvent",
    "OrchestrationState",
    "PlanConcept",
    "ReviewItem",
    "ScheduleConfig",
    "SubTask",
    "WeekTask",
    "WeeklyPlan",
    "GoalType",
]
