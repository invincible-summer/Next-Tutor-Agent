"""M9 orchestration schema: plan structures and back-compat."""
import os
import sys
import time
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.agents.learning_orchestration import (schema, store, spaced_repetition,
    goal_manager, habit_tracker, schedule_engine, learning_planner,
    task_executor, context_builder, manager as orch_manager,
    goal_analyzer, event_emitter, weekly_planner_llm, daily_composer)
from app.agents.learning_orchestration.schema import (DailyTask, DailyTaskStatus,
    GoalType, HabitStats, LearningGoal, Milestone, MilestoneStatus,
    OrchestrationState, PlanConcept, ReviewItem, ScheduleConfig, TaskKind,
    WeeklyPlan, OrchestrationEvent)
from app.agents.learning_orchestration.schema import (
    GapItem, GoalState)
from app.agents.learning_orchestration.manager import (LearningOrchestrationService,
    get_orchestration_service)
from app.agents.learning_orchestration import is_enabled
from tests.support.storage_sandbox import StorageSandboxTestCase
"""Tests for M9 Learning Orchestration Intelligence layer.

Covers all components: schema round-trips, store persistence (path guard +
corrupt-file safety), SM-2 algorithm correctness, TaskCompletionTracker +
habit streak projection (read M6 read-only), goal analyzer (gap analysis +
backward planning), event emitter (M9->M6 event flow), goal/milestone
management, schedule engine, learning planner (topo-sort + reuse), task
executor, context builder rendering, manager end-to-end, supervisor hooks
(don't raise), the toggle/fallback contract, and the single-truth-source
boundary (M9 never directly writes M2/M3/M6 storage; it emits events that
the supervisor forwards into M6's event bus).
"""
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
def _temp_students_dir():
    return Path(tempfile.mkdtemp(prefix="edu_orch_test_"))
if __name__ == "__main__":
    unittest.main()
_DAY1 = time.mktime(time.strptime("2026-07-27", "%Y-%m-%d"))  # a Monday
_DAY2 = _DAY1 + 86400
def _week_plan_state(*, week_start: float, concept_id: str = "c1",
                     name: str = "导数") -> OrchestrationState:
    """A minimal state with a one-concept weekly plan covering week_start."""
    state = OrchestrationState()
    state.goals = [LearningGoal(title="考研数学", subjects=["数学"])]
    state.weekly_plan = [WeeklyPlan(week_start=week_start, concepts=[
        PlanConcept(concept_id=concept_id, name=name, difficulty=3)])]
    return state
class TestSchema(unittest.TestCase):

    def test_goal_roundtrip(self):
        g = LearningGoal(title="考研数学", goal_type=GoalType.EXAM,
                         subjects=["高数", "线代"], deadline=1700000000.0)
        d = g.to_dict()
        self.assertEqual(d["goal_type"], "exam")
        g2 = LearningGoal.from_dict(d)
        self.assertEqual(g2.title, "考研数学")
        self.assertEqual(g2.goal_type, GoalType.EXAM)

    def test_goaltype_from_value_safe(self):
        self.assertEqual(GoalType.from_value("garbage"), GoalType.ABILITY)
        self.assertEqual(GoalType.from_value(None), GoalType.ABILITY)
        self.assertEqual(GoalType.from_value(GoalType.EXAM), GoalType.EXAM)

    def test_milestone_roundtrip(self):
        m = Milestone(id="ms1", title="高数基础", concept_ids=["c1", "c2"],
                      status=MilestoneStatus.IN_PROGRESS, order=0)
        d = m.to_dict()
        self.assertEqual(d["status"], "in_progress")
        m2 = Milestone.from_dict(d)
        self.assertEqual(m2.concept_ids, ["c1", "c2"])
        self.assertEqual(m2.status, MilestoneStatus.IN_PROGRESS)

    def test_dailytask_roundtrip(self):
        t = DailyTask(id="t1", day="2026-07-29", concept_id="c1",
                      concept_name="导数", kind=TaskKind.REVIEW,
                      status=DailyTaskStatus.COMPLETED, priority=1)
        d = t.to_dict()
        self.assertEqual(d["kind"], "review")
        t2 = DailyTask.from_dict(d)
        self.assertEqual(t2.kind, TaskKind.REVIEW)
        self.assertEqual(t2.status, DailyTaskStatus.COMPLETED)

    def test_reviewitem_roundtrip(self):
        r = ReviewItem(concept_id="c1", easiness=2.8, interval=6,
                       repetitions=2, next_review=1700000000.0, last_quality=5)
        d = r.to_dict()
        self.assertEqual(d["easiness"], 2.8)
        r2 = ReviewItem.from_dict(d)
        self.assertEqual(r2.interval, 6)
        self.assertEqual(r2.repetitions, 2)

    def test_scheduleconfig_roundtrip(self):
        s = ScheduleConfig(daily_minutes=30, available_days=["mon", "wed"],
                           preferred_time="evening",
                           exam_dates={"c1": 1700000000.0})
        d = s.to_dict()
        self.assertEqual(d["daily_minutes"], 30)
        s2 = ScheduleConfig.from_dict(d)
        self.assertEqual(s2.available_days, ["mon", "wed"])
        self.assertEqual(s2.exam_dates["c1"], 1700000000.0)

    def test_habitstats_completion_rate(self):
        h = HabitStats(completed_tasks=3, total_tasks=5)
        self.assertAlmostEqual(h.completion_rate, 0.6, places=3)
        h2 = HabitStats(total_tasks=0)
        self.assertEqual(h2.completion_rate, 0.0)

    def test_orchestration_state_roundtrip(self):
        s = OrchestrationState(student_id="s1")
        s.goals = [LearningGoal(id="g_1", title="test goal"),
                   LearningGoal(id="g_2", title="second goal")]
        s.goal_states = [GoalState(goal_id="g_1", goal_title="test goal")]
        s.milestones = [Milestone(id="ms1", title="m1")]
        s.review_queue = {"c1": ReviewItem(concept_id="c1", easiness=2.5)}
        s.habit = HabitStats(current_streak=3, total_active_days=10)
        d = s.to_dict()
        s2 = OrchestrationState.from_dict(d)
        self.assertEqual(s2.student_id, "s1")
        self.assertEqual([g.id for g in s2.goals], ["g_1", "g_2"])
        self.assertEqual(s2.goals[1].title, "second goal")
        self.assertEqual(s2.goal_states[0].goal_id, "g_1")
        self.assertEqual(len(s2.milestones), 1)
        self.assertEqual(s2.review_queue["c1"].easiness, 2.5)
        self.assertEqual(s2.habit.current_streak, 3)

    def test_legacy_single_goal_state_migrates(self):
        """Old blobs store a scalar `goal`/`goal_state`/`long_term_tasks`;
        from_dict wraps them into the multi-goal form and drops longtasks."""
        legacy = {
            "student_id": "s1",
            "goal": {"title": "旧目标", "subjects": ["数学"]},
            "goal_state": {"goal_title": "旧目标", "supported_ratio": 0.5},
            "long_term_tasks": [{"id": "lt_1", "title": "每天背单词"}],
        }
        s = OrchestrationState.from_dict(legacy)
        self.assertEqual(len(s.goals), 1)
        self.assertEqual(s.goals[0].id, "g_1")
        self.assertEqual(s.goals[0].title, "旧目标")
        self.assertEqual(len(s.goal_states), 1)
        self.assertEqual(s.goal_states[0].goal_id, "g_1")
        self.assertEqual(s.goal_states[0].supported_ratio, 0.5)
        # long_term_tasks have no new home: dropped on load
        d = s.to_dict()
        self.assertNotIn("long_term_tasks", d)
        self.assertNotIn("goal", d)

    def test_goal_cap_on_load(self):
        goals = [{"id": f"g_{i}", "title": f"t{i}"} for i in range(9)]
        s = OrchestrationState.from_dict({"student_id": "s1", "goals": goals})
        self.assertEqual(len(s.goals), schema._MAX_GOALS)

    def test_orchestration_event_roundtrip(self):
        e = OrchestrationEvent(type="goal_set", payload={"title": "x"})
        d = e.to_dict()
        e2 = OrchestrationEvent.from_dict(d)
        self.assertEqual(e2.type, "goal_set")
        self.assertEqual(e2.payload, {"title": "x"})
class TestSchemaBackCompat(unittest.TestCase):

    def test_old_dailytask_dict_loads_with_defaults(self):
        old = {"id": "t1", "day": "2026-07-27", "concept_id": "c1",
               "concept_name": "导数", "kind": "study", "status": "pending",
               "priority": 3, "estimate_minutes": 15, "milestone_id": "",
               "created_at": 1.0, "completed_at": 0.0}
        t = DailyTask.from_dict(old)
        self.assertEqual(t.title, "")
        self.assertEqual(t.phase, "")
        self.assertFalse(t.custom)
        self.assertEqual(t.reason, "")

    def test_new_fields_roundtrip(self):
        t = DailyTask(id="t1", day="d", title="我的标题", phase="sprint",
                      custom=True, reason="为什么")
        t2 = DailyTask.from_dict(t.to_dict())
        self.assertEqual((t2.title, t2.phase, t2.custom, t2.reason),
                         ("我的标题", "sprint", True, "为什么"))

    def test_task_phases_constant(self):
        self.assertEqual(schema.TASK_PHASES,
                         ("foundation", "reinforce", "sprint"))
class TestPlanHierarchySchema(unittest.TestCase):
    """P1: new plan-hierarchy dataclasses round-trip + legacy-default compat."""

    def test_subtask_roundtrip(self):
        from app.agents.learning_orchestration.schema import SubTask
        st = SubTask(id="st_1", title="做 10 道题", source="user",
                     estimate_minutes=20, done=True, done_at=123.0)
        st2 = SubTask.from_dict(st.to_dict())
        self.assertEqual(st2.title, "做 10 道题")
        self.assertEqual(st2.source, "user")
        self.assertTrue(st2.done)

    def test_subtask_legacy_defaults(self):
        from app.agents.learning_orchestration.schema import SubTask
        st = SubTask.from_dict({})
        self.assertEqual(st.source, "auto")
        self.assertFalse(st.done)
        self.assertGreaterEqual(st.estimate_minutes, 1)
        # illegal source falls back to auto
        self.assertEqual(SubTask.from_dict({"source": "hacker"}).source, "auto")

    def test_weektask_effective_done(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        t = WeekTask(id="wt_1", title="task",
                     subtasks=[SubTask(id="a", done=True), SubTask(id="b")])
        self.assertFalse(t.effective_done)
        t.subtasks[1].done = True
        self.assertTrue(t.effective_done)
        # manual toggle wins even with unfinished subtasks
        t2 = WeekTask(id="wt_2", done=True, subtasks=[SubTask(id="c")])
        self.assertTrue(t2.effective_done)
        self.assertTrue(t2.to_dict()["done"])
        # no subtasks + not toggled -> not done
        self.assertFalse(WeekTask(id="wt_3").effective_done)

    def test_weektask_kind_and_caps(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        t = WeekTask.from_dict({"kind": "bogus", "source": "user",
                                "subtasks": [{"id": str(i)} for i in range(20)]})
        self.assertEqual(t.kind, "study")
        self.assertEqual(t.source, "user")
        self.assertEqual(len(t.subtasks), 8)  # _MAX_SUBTASKS

    def test_weeklyplan_tasks_and_origin(self):
        w = WeeklyPlan(week_index=1, focus="浮力周", origin="user")
        d = w.to_dict()
        self.assertEqual(d["origin"], "user")
        self.assertEqual(d["tasks"], [])
        w2 = WeeklyPlan.from_dict(d)
        self.assertEqual(w2.origin, "user")
        # legacy payload without tasks/origin -> auto + empty
        w3 = WeeklyPlan.from_dict({"week_index": 0, "focus": "x"})
        self.assertEqual(w3.origin, "auto")
        self.assertEqual(w3.tasks, [])

    def test_dailytask_source_refs(self):
        t = DailyTask(id="d1", day="2026-08-01", concept_id="c1",
                      week_task_id="wt_0_1", subtask_id="st_1")
        t2 = DailyTask.from_dict(t.to_dict())
        self.assertEqual(t2.week_task_id, "wt_0_1")
        self.assertEqual(t2.subtask_id, "st_1")
        # legacy task without refs
        t3 = DailyTask.from_dict({"id": "d2", "day": "2026-08-01"})
        self.assertEqual(t3.week_task_id, "")
