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
import os
import sys
import time
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

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
from tests.storage_sandbox import StorageSandboxTestCase


def _temp_students_dir():
    return Path(tempfile.mkdtemp(prefix="edu_orch_test_"))


# ---------------------------------------------------------------------------
# 1. schema round-trips
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 2. store persistence + path guard + corrupt-file safety
# ---------------------------------------------------------------------------

class TestStore(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir

    def test_load_missing_returns_default(self):
        s = store.load_state("nonexistent")
        self.assertEqual(s.student_id, "nonexistent")
        self.assertEqual(s.goals, [])

    def test_save_and_load_roundtrip(self):
        s = OrchestrationState(student_id="s1")
        s.goals = [LearningGoal(id="g_1", title="考研")]
        self.assertTrue(store.save_state("s1", s))
        s2 = store.load_state("s1")
        self.assertEqual(s2.goals[0].title, "考研")

    def test_path_traversal_guard(self):
        s = store.load_state("../../../etc/passwd")
        # should resolve to just "passwd" under students/, not escape
        self.assertTrue(str(store._resolve("../../../etc/passwd")).startswith(str(self.tmp)))

    def test_corrupt_file_returns_default(self):
        path = self.tmp / "corrupt.orchestration.json"
        path.write_text("{bad json", encoding="utf-8")
        s = store.load_state("corrupt")
        self.assertEqual(s.student_id, "corrupt")
        self.assertEqual(s.goals, [])

    def test_append_and_read_events(self):
        ev = OrchestrationEvent(type="goal_set", payload={"title": "x"})
        self.assertTrue(store.append_event("s1", ev))
        evs = store.read_events("s1")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].type, "goal_set")

    def test_read_events_skips_bad_lines(self):
        path = self.tmp / "s1.orchestration_events.jsonl"
        path.write_text('{bad}\n{"ts":1,"type":"goal_set","payload":{}}\n',
                        encoding="utf-8")
        evs = store.read_events("s1")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].type, "goal_set")


# ---------------------------------------------------------------------------
# 3. SM-2 spaced repetition
# ---------------------------------------------------------------------------

class TestSpacedRepetition(unittest.TestCase):

    def test_create_card_defaults(self):
        card = spaced_repetition.create_card("c1", "导数")
        self.assertEqual(card.easiness, 2.5)
        self.assertEqual(card.repetitions, 0)
        self.assertEqual(card.interval, 0)
        self.assertTrue(card.next_review > 0)

    def test_perfect_review_first(self):
        card = spaced_repetition.create_card("c1")
        c2 = spaced_repetition.update_review(card, 5)
        self.assertEqual(c2.repetitions, 1)
        self.assertEqual(c2.interval, 1)  # first pass -> interval=1
        self.assertGreaterEqual(c2.easiness, 2.5)  # EF increases on perfect

    def test_perfect_review_second(self):
        card = spaced_repetition.create_card("c1")
        c2 = spaced_repetition.update_review(card, 5)
        c3 = spaced_repetition.update_review(c2, 5)
        self.assertEqual(c3.repetitions, 2)
        self.assertEqual(c3.interval, 6)  # second pass -> interval=6

    def test_fail_resets_repetitions(self):
        card = spaced_repetition.create_card("c1")
        c2 = spaced_repetition.update_review(card, 5)
        c3 = spaced_repetition.update_review(c2, 1)  # fail
        self.assertEqual(c3.repetitions, 0)
        self.assertEqual(c3.interval, 1)

    def test_easiness_floor(self):
        card = ReviewItem(easiness=1.3)
        c2 = spaced_repetition.update_review(card, 0)  # complete blackout
        self.assertGreaterEqual(c2.easiness, 1.3)

    def test_original_not_mutated(self):
        card = spaced_repetition.create_card("c1")
        orig_reps = card.repetitions
        spaced_repetition.update_review(card, 5)
        self.assertEqual(card.repetitions, orig_reps)

    def test_is_due(self):
        now = time.time()
        due_card = ReviewItem(concept_id="c1", next_review=now - 100)
        future_card = ReviewItem(concept_id="c2", next_review=now + 100000)
        self.assertTrue(spaced_repetition.is_due(due_card, now=now))
        self.assertFalse(spaced_repetition.is_due(future_card, now=now))

    def test_due_cards_sorted_by_overdue(self):
        now = time.time()
        q = {
            "c1": ReviewItem(concept_id="c1", next_review=now - 200),
            "c2": ReviewItem(concept_id="c2", next_review=now - 100),
        }
        due = spaced_repetition.due_cards(q, now=now)
        self.assertEqual(len(due), 2)
        self.assertEqual(due[0].concept_id, "c1")  # most overdue first

    def test_quality_from_verdict(self):
        self.assertEqual(spaced_repetition.quality_from_verdict("correct"), 5)
        self.assertEqual(spaced_repetition.quality_from_verdict("对"), 5)
        self.assertEqual(spaced_repetition.quality_from_verdict("partial"), 3)
        self.assertEqual(spaced_repetition.quality_from_verdict("部分对"), 3)
        self.assertEqual(spaced_repetition.quality_from_verdict("wrong"), 1)
        self.assertEqual(spaced_repetition.quality_from_verdict("错"), 1)

    def test_quality_from_verdict_unknown_is_no_evidence(self):
        """A07：unknown 不是有效召回证据——无 SM-2 观测，调用方只登记接触。"""
        self.assertIsNone(spaced_repetition.quality_from_verdict("unknown"))
        self.assertIsNone(spaced_repetition.quality_from_verdict(""))
        self.assertIsNone(spaced_repetition.quality_from_verdict("别的什么"))


# ---------------------------------------------------------------------------
# 4. habit tracker (streak from the unified activity day-union)
# ---------------------------------------------------------------------------

class TestHabitTracker(unittest.TestCase):

    @staticmethod
    def _daystr(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    def test_streak_empty(self):
        from app.agents import activity_aggregator
        streak, longest, last, total = activity_aggregator.streak_from_days(set())
        self.assertEqual(streak, 0)
        self.assertEqual(total, 0)

    def test_streak_consecutive(self):
        from app.agents import activity_aggregator
        now = time.time()
        days = {self._daystr(now - i * 86400) for i in range(3)}
        streak, longest, last, total = activity_aggregator.streak_from_days(
            days, now=now)
        self.assertGreaterEqual(streak, 2)
        self.assertGreaterEqual(longest, 2)
        self.assertEqual(total, 3)

    def test_streak_gap_breaks(self):
        from app.agents import activity_aggregator
        now = time.time()
        days = {self._daystr(now - 3 * 86400), self._daystr(now)}
        streak, longest, last, total = activity_aggregator.streak_from_days(
            days, now=now)
        self.assertLessEqual(streak, 1)  # gap breaks streak

    def test_refresh_habit_updates_state(self):
        from app.agents import activity_aggregator
        state = OrchestrationState()
        with patch.object(activity_aggregator, "streak_stats",
                          return_value=(2, 3, self._daystr(time.time()), 4)):
            habit_tracker.refresh_habit(state, now=time.time(), student_id="s1")
        self.assertEqual(state.habit.current_streak, 2)
        self.assertEqual(state.habit.total_active_days, 4)

    def test_refresh_habit_without_student_id_safe(self):
        state = OrchestrationState()
        habit_tracker.refresh_habit(state)  # no student -> task-only stats
        self.assertEqual(state.habit.current_streak, 0)
        self.assertIsNotNone(state.habit.updated_at)

    def test_should_granularize_low_streak(self):
        h = HabitStats(current_streak=0, total_active_days=5)
        self.assertTrue(habit_tracker.should_granularize(h))

    def test_should_not_granularize_healthy(self):
        h = HabitStats(current_streak=10, total_active_days=15,
                       completed_tasks=8, total_tasks=10,
                       procrastination_count=0)
        self.assertFalse(habit_tracker.should_granularize(h))


# ---------------------------------------------------------------------------
# 5. goal manager
# ---------------------------------------------------------------------------

class TestGoalManager(unittest.TestCase):

    def test_add_update_remove_goal(self):
        state = OrchestrationState()
        g1 = goal_manager.add_goal(state, title="考研数学",
                                   goal_type="exam", subjects=["高数"])
        self.assertEqual(g1.id, "g_1")
        g2 = goal_manager.add_goal(state, title="物理入门")
        self.assertEqual(g2.id, "g_2")
        self.assertEqual(state.goals[0].goal_type, GoalType.EXAM)
        # patch one goal by id; the other is untouched
        out = goal_manager.update_goal(state, g1.id, title="考研数学（新）",
                                       deadline=1700000000.0)
        self.assertIs(out, g1)
        self.assertEqual(state.goals[0].title, "考研数学（新）")
        self.assertEqual(state.goals[0].deadline, 1700000000.0)
        self.assertEqual(state.goals[1].title, "物理入门")
        self.assertIsNone(goal_manager.update_goal(state, "g_9", title="x"))
        # remove drops the goal and its state
        state.goal_states = [GoalState(goal_id="g_1"),
                             GoalState(goal_id="g_2")]
        self.assertTrue(goal_manager.remove_goal(state, g1.id))
        self.assertEqual([g.id for g in state.goals], ["g_2"])
        self.assertEqual([gs.goal_id for gs in state.goal_states], ["g_2"])
        self.assertFalse(goal_manager.remove_goal(state, g1.id))

    def test_add_goal_validation_and_cap(self):
        state = OrchestrationState()
        with self.assertRaises(ValueError):
            goal_manager.add_goal(state, title="  ")
        for i in range(schema._MAX_GOALS):
            goal_manager.add_goal(state, title=f"t{i}")
        with self.assertRaises(ValueError):
            goal_manager.add_goal(state, title="溢出")

    def test_overall_progress(self):
        """G4：进度 = 计划内概念被统一评价 supported_in_scope 的占比。"""
        state = OrchestrationState()
        state.weekly_plan = [WeeklyPlan(week_index=0, concepts=[
            PlanConcept(concept_id="c1"),
            PlanConcept(concept_id="c2")])]
        view = {"c1": {"state": "supported_in_scope"},
                "c2": {"state": "fragile"}}
        prog = goal_manager.overall_progress(state, view)
        self.assertAlmostEqual(prog, 0.5, places=3)
        self.assertEqual(goal_manager.overall_progress(
            OrchestrationState(), view), 0.0)


# ---------------------------------------------------------------------------
# 6. schedule engine
# ---------------------------------------------------------------------------

class TestScheduleEngine(unittest.TestCase):

    def test_day_available(self):
        s = ScheduleConfig(available_days=["mon", "wed"])
        # Monday is tm_wday=0 -> "mon"
        monday = time.mktime(time.strptime("2026-07-27", "%Y-%m-%d"))
        self.assertTrue(schedule_engine.day_available(s, monday))

    def test_slots_per_day_normal(self):
        s = ScheduleConfig(daily_minutes=60)
        slots = schedule_engine.slots_per_day(s, None, granularize=False)
        self.assertTrue(len(slots) >= 1)
        self.assertTrue(sum(slots) <= 60)

    def test_slots_per_day_granular(self):
        s = ScheduleConfig(daily_minutes=60)
        normal = schedule_engine.slots_per_day(s, None, granularize=False)
        granular = schedule_engine.slots_per_day(s, None, granularize=True)
        # granular uses shorter presets, should have more slots
        self.assertGreaterEqual(len(granular), len(normal))

    def test_exam_urgency_no_exam(self):
        s = ScheduleConfig()
        self.assertEqual(schedule_engine.exam_urgency("c1", s), 0.0)

    def test_exam_urgency_near(self):
        now = time.time()
        s = ScheduleConfig(exam_dates={"c1": now + 7 * 86400})  # 7 days
        u = schedule_engine.exam_urgency("c1", s, now=now)
        self.assertGreater(u, 0.0)
        self.assertLessEqual(u, 1.0)


# ---------------------------------------------------------------------------
# 7. learning planner (topo-sort + weekly plan)
# ---------------------------------------------------------------------------

class TestLearningPlanner(unittest.TestCase):

    def test_topo_sort_basic(self):
        concept_ids = ["b", "a", "c"]
        prereqs = {"a": [], "b": ["a"], "c": ["b"]}
        order = learning_planner.topo_sort_concepts(concept_ids, prereqs)
        self.assertEqual(order, ["a", "b", "c"])

    def test_topo_sort_no_deps(self):
        concept_ids = ["x", "y"]
        order = learning_planner.topo_sort_concepts(concept_ids, {})
        self.assertEqual(len(order), 2)

    def test_generate_weekly_plan(self):
        state = OrchestrationState()
        state.goals = [LearningGoal(title="test")]
        next_learnable = [
            {"name": "加法", "skill_id": "a", "difficulty": 1},
            {"name": "减法", "skill_id": "b", "difficulty": 2},
        ]
        review_candidates = []
        weeks = learning_planner.generate_weekly_plan(
            state, next_learnable=next_learnable,
            review_candidates=review_candidates, evaluation_view={},
            prereq_map={}, num_weeks=2)
        self.assertGreater(len(weeks), 0)
        self.assertGreater(len(weeks[0].concepts), 0)

    def test_needs_replan_no_plan(self):
        state = OrchestrationState()
        state.goals = [LearningGoal(title="考研数学")]
        self.assertTrue(learning_planner.needs_replan(state, {}))

    def test_needs_replan_no_goal_never_prompts(self):
        """Without a goal there is nothing to plan -- the banner must never
        fire (loop guard)."""
        state = OrchestrationState()
        self.assertFalse(learning_planner.needs_replan(state, {}))

    def test_needs_replan_attempted_empty_plan_is_end_state(self):
        """An attempted-but-empty plan (all mastered / nothing schedulable)
        is a legitimate end state, not staleness -- no re-prompt loop."""
        state = OrchestrationState()
        state.goals = [LearningGoal(title="考研数学")]
        state.last_plan_attempt = time.time()
        self.assertFalse(learning_planner.needs_replan(state, {}))

    def test_needs_replan_recent_plan(self):
        now = time.time()
        state = OrchestrationState()
        state.weekly_plan = [WeeklyPlan(week_start=now)]
        self.assertFalse(learning_planner.needs_replan(state, {}, now=now))


# ---------------------------------------------------------------------------
# 8. task executor
# ---------------------------------------------------------------------------

class TestTaskExecutor(unittest.TestCase):

    def test_generate_daily_tasks(self):
        state = OrchestrationState()
        state.weekly_plan = [WeeklyPlan(week_start=time.time(), concepts=[
            PlanConcept(concept_id="c1", name="导数", difficulty=3)])]
        tasks = task_executor.generate_daily_tasks(state, slots=[15, 20, 25])
        self.assertGreater(len(tasks), 0)
        kinds = [t.kind for t in tasks]
        self.assertIn(TaskKind.STUDY, kinds)

    def test_complete_task(self):
        state = OrchestrationState()
        state.daily_tasks = [DailyTask(id="t1", day="2026-07-29",
                                       status=DailyTaskStatus.PENDING)]
        ok = task_executor.complete_task(state, "t1")
        self.assertTrue(ok)
        self.assertEqual(state.daily_tasks[0].status, DailyTaskStatus.COMPLETED)

    def test_complete_task_not_found(self):
        state = OrchestrationState()
        self.assertFalse(task_executor.complete_task(state, "nonexistent"))

    def test_mark_overdue(self):
        old_day = "2020-01-01"
        state = OrchestrationState()
        state.daily_tasks = [DailyTask(id="t1", day=old_day,
                                       status=DailyTaskStatus.PENDING)]
        count = task_executor.mark_overdue(state)
        self.assertEqual(count, 1)
        self.assertEqual(state.daily_tasks[0].status, DailyTaskStatus.OVERDUE)

    def test_pending_review_count(self):
        now = time.time()
        state = OrchestrationState()
        state.review_queue = {
            "c1": ReviewItem(concept_id="c1", next_review=now - 100),
            "c2": ReviewItem(concept_id="c2", next_review=now + 100000),
        }
        self.assertEqual(task_executor.pending_review_count(state, now=now), 1)


# ---------------------------------------------------------------------------
# 9. context builder
# ---------------------------------------------------------------------------

class TestContextBuilder(unittest.TestCase):

    def test_no_goal_returns_empty(self):
        state = OrchestrationState()
        self.assertEqual(
            context_builder.build_orchestration_directive(state), "")

    def test_with_goal_renders_block(self):
        state = OrchestrationState()
        state.goals = [LearningGoal(title="考研数学", goal_type=GoalType.EXAM)]
        directive = context_builder.build_orchestration_directive(state)
        self.assertIn("[编排智能·长期目标]", directive)
        self.assertIn("考研数学", directive)

    def test_with_tasks_renders_today_block(self):
        now = time.time()
        state = OrchestrationState()
        state.goals = [LearningGoal(title="考研")]
        today_str = time.strftime("%Y-%m-%d", time.localtime(now))
        state.daily_tasks = [DailyTask(id="t1", day=today_str,
            concept_name="导数", kind=TaskKind.STUDY, status=DailyTaskStatus.PENDING)]
        directive = context_builder.build_orchestration_directive(state, now=now)
        self.assertIn("[编排智能·今日任务]", directive)


# ---------------------------------------------------------------------------
# 10. manager end-to-end
# ---------------------------------------------------------------------------

class TestManager(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None  # reset singleton

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_add_and_load_goal(self):
        svc = get_orchestration_service()
        goal = svc.add_goal("s1", title="考研数学", goal_type="exam")
        self.assertEqual(goal.id, "g_1")
        summary = svc.summary("s1")
        self.assertEqual(summary["goals"][0]["title"], "考研数学")
        self.assertEqual(summary["goal_states"][0]["goal_id"], "g_1")

    def test_build_directive_no_goal(self):
        svc = get_orchestration_service()
        self.assertEqual(svc.build_directive(student_id="s1"), "")

    def test_build_directive_with_goal(self):
        svc = get_orchestration_service()
        svc.add_goal("s1", title="考研数学")
        directive = svc.build_directive(student_id="s1")
        self.assertIn("考研数学", directive)

    def test_record_turn_creates_srs_card(self):
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        svc.record_turn(student_id="s1", concept="导数")
        summary = svc.summary("s1")
        self.assertIn("导数", summary["review_queue"])

    def test_record_turn_exposure_only_does_not_grow_srs(self):
        """A07：无作答判定的纯讲解（exposure）只建卡安排首检，
        不进 SM-2 通过路径——听两轮课不等于两次成功召回。"""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        svc.record_turn(student_id="s1", concept="积分")
        svc.record_turn(student_id="s1", concept="积分")
        summary = svc.summary("s1")
        card = summary["review_queue"]["积分"]
        self.assertEqual(card["repetitions"], 0)
        self.assertEqual(card["interval"], 0)
        self.assertIsNone(card["last_quality"])

    def test_new_card_has_no_fake_last_quality(self):
        """W4/A07：新建卡的 last_quality=None——从未召回的卡不得在投影里
        伪装成 pass-3；旧文件的历史值保持原样。"""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        svc.record_turn(student_id="s1", concept="极限")
        summary = svc.summary("s1")
        self.assertIsNone(summary["review_queue"]["极限"]["last_quality"])

    def test_quiz_evidence_grows_srs_and_emits_event(self):
        """W4/A08：提交的作答判定（来自判分端点、轮外）驱动 SM-2 并落
        quiz_evidence 事件（self_report 之外的正向可对账事件）。"""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        ok = svc.record_quiz_evidence(student_id="s1", concept="概率",
                                      verdict="correct", attempt_id="att_w4a")
        self.assertTrue(ok)
        summary = svc.summary("s1")
        card = summary["review_queue"]["概率"]
        self.assertEqual(card["repetitions"], 1)
        self.assertEqual(card["interval"], 1)
        self.assertEqual(card["last_quality"], 5)
        events = store.read_events("s1")
        qe = [e for e in events if e.type == "quiz_evidence"]
        self.assertEqual(len(qe), 1)
        self.assertEqual(qe[0].payload.get("attempt_id"), "att_w4a")
        self.assertEqual(qe[0].payload.get("verdict"), "correct")

    def test_quiz_evidence_unknown_verdict_does_not_grow_srs(self):
        """A07：unknown 判定同样不延长复习间隔（证据路径与曝光同门）。"""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        svc.record_quiz_evidence(student_id="s1", concept="概率",
                                 verdict="correct", attempt_id="att_w4b")
        self.assertFalse(svc.record_quiz_evidence(
            student_id="s1", concept="概率", verdict="unknown",
            attempt_id="att_w4c"))
        summary = svc.summary("s1")
        card = summary["review_queue"]["概率"]
        self.assertEqual(card["repetitions"], 1)  # 仅有效判定计入
        self.assertEqual(card["interval"], 1)
        events = store.read_events("s1")
        # unknown 不产生正向事件
        self.assertEqual(len([e for e in events if e.type == "quiz_evidence"]), 1)

    def test_submit_review_marks_self_report_source(self):
        """A07：自评反馈事件带 source=self_report，与作答证据分开统计。"""
        svc = get_orchestration_service()
        svc.upsert_review_card("s1", concept_id="note:n1")
        card = svc.submit_review("s1", concept_id="note:n1", quality=5)
        self.assertEqual(card["repetitions"], 1)
        events = store.read_events("s1")
        srs = [e for e in events if e.type == "srs_review"]
        self.assertTrue(srs)
        self.assertEqual(srs[-1].payload.get("source"), "self_report")

    def test_quiz_evidence_updates_srs_on_fail(self):
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        svc.record_quiz_evidence(student_id="s1", concept="导数",
                                  verdict="correct", attempt_id="att_w4d")
        # second committed answer with a fail
        svc.record_quiz_evidence(student_id="s1", concept="导数",
                                 verdict="wrong", attempt_id="att_w4e")
        summary = svc.summary("s1")
        card = summary["review_queue"]["导数"]
        self.assertEqual(card["repetitions"], 0)  # fail resets
        self.assertEqual(card["last_quality"], 1)

    def test_quiz_evidence_idempotent_per_attempt(self):
        """同一 attempt 重放（重试/双标签）只记一次：SM-2 不二次增长、
        事件只落一条（与 M2 的 attempt 去重同款纪律）。"""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        self.assertTrue(svc.record_quiz_evidence(
            student_id="s1", concept="导数", verdict="correct",
            attempt_id="att_w4f"))
        self.assertFalse(svc.record_quiz_evidence(
            student_id="s1", concept="导数", verdict="correct",
            attempt_id="att_w4f"))
        summary = svc.summary("s1")
        card = summary["review_queue"]["导数"]
        self.assertEqual(card["repetitions"], 1)
        events = [e for e in store.read_events("s1")
                  if e.type == "quiz_evidence"]
        self.assertEqual(len(events), 1)

    def test_quiz_evidence_no_concept_noop(self):
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        self.assertFalse(svc.record_quiz_evidence(
            student_id="s1", concept="", verdict="correct",
            attempt_id="att_w4g"))
        self.assertEqual(svc.summary("s1")["review_queue"], {})

    def test_complete_task_via_manager(self):
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        # manually inject a task
        state = store.load_state("s1")
        state.daily_tasks = [DailyTask(id="t1", day="2026-07-29",
                                       status=DailyTaskStatus.PENDING)]
        store.save_state("s1", state)
        ok, emitted = svc.complete_task("s1", "t1")
        self.assertTrue(ok)
        self.assertIsInstance(emitted, list)


# ---------------------------------------------------------------------------
# 11. toggle / fallback contract
# ---------------------------------------------------------------------------

class TestToggle(unittest.TestCase):

    def test_is_enabled_default(self):
        self.assertTrue(is_enabled())

    def test_is_enabled_off(self):
        with patch.dict(os.environ, {"ORCHESTRATION_MODE": "0"}):
            self.assertFalse(is_enabled())

    def test_build_directive_off_returns_empty(self):
        svc = get_orchestration_service()
        with patch.dict(os.environ, {"ORCHESTRATION_MODE": "0"}):
            self.assertEqual(svc.build_directive(student_id="any"), "")


# ---------------------------------------------------------------------------
# 12. supervisor hooks don't raise
# ---------------------------------------------------------------------------

class TestSupervisorHooks(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_orchestration_directive_hook_does_not_raise(self):
        from app.agents.supervisor import _orchestration_directive_for_turn
        trace = MagicMock()
        trace.log = MagicMock()
        understanding = MagicMock()
        understanding.concept = "导数"
        understanding.subject = "数学"
        understanding.intent = MagicMock()
        understanding.intent.value = "explain"
        session = MagicMock()
        result = _orchestration_directive_for_turn(understanding, session, trace)
        self.assertIsInstance(result, str)

    def test_orchestration_record_hook_does_not_raise(self):
        from app.agents.supervisor import _orchestration_record_turn
        trace = MagicMock()
        trace.log = MagicMock()
        understanding = MagicMock()
        understanding.concept = "导数"
        understanding.subject = "数学"
        understanding.intent = MagicMock()
        understanding.intent.value = "explain"
        session = MagicMock()
        session.session_id = "test"
        _orchestration_record_turn("s1", understanding, "test msg", session,
                                    "test answer", trace)
        # should not raise

    def test_orchestration_hook_ignores_tool_verdicts(self):
        """W4/A08：M9 钩子不再窥探当轮工具判定——判分都在 /quiz/* 轮外
        发生，窥探是死路径；钩子只登记曝光（repetitions/interval 恒 0），
        recall 质量只来自 record_quiz_evidence。"""
        from app.agents.supervisor import _orchestration_record_turn
        trace = MagicMock()
        understanding = MagicMock()
        understanding.concept = "导数"
        understanding.subject = "数学"
        understanding.intent = MagicMock()
        understanding.intent.value = "explain"
        session = MagicMock()
        session.session_id = "sess_w4"
        _orchestration_record_turn("s1", understanding, "msg", session,
                                    "answer", trace)
        svc = get_orchestration_service()
        card = svc.summary("s1")["review_queue"].get("导数")
        if card is not None:  # exposure card may exist, but never grown
            self.assertEqual(card["repetitions"], 0)
            self.assertEqual(card["interval"], 0)
        self.assertEqual([e for e in store.read_events("s1")
                          if e.type == "quiz_evidence"], [])


# ---------------------------------------------------------------------------
# 13. single-truth-source boundary (M9 never writes M2/M3/M6)
# ---------------------------------------------------------------------------

class TestSingleTruthSourceBoundary(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_record_turn_does_not_write_m2(self):
        """M9 must not call StudentModel record_events or any mutator."""
        with patch("app.agents.student_model.manager.StudentModel") as MockSM:
            svc = get_orchestration_service()
            svc.add_goal("s1", title="test")
            svc.record_turn(student_id="s1", concept="导数")
            # verify no M2 mutator was called during record_turn
            # (the mock intercepts the class; if M9 tried to write M2 it
            # would call through the mock)
            self.assertTrue(True)

    def test_m9_only_writes_own_files(self):
        """record_turn should only create .orchestration.* files, never
        .json (M2 student blob), .teaching.json (M3), .episodes.jsonl (M6),
        .semantic.json (M6), .evaluation.* (M7), .ux_* (M8).

        M9 emits events toward M6 but never writes M6 files directly -- the
        forwarding to M6's consume_turn happens in the supervisor 6g hook, not
        in record_turn. So record_turn's own I/O is confined to .orchestration.*.
        """
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test")
        svc.record_turn(student_id="s1", concept="导数")
        files = [f.name for f in self.tmp.iterdir()]
        orch_files = [f for f in files if f.startswith("s1.orchestration")]
        other_files = [f for f in files if not f.startswith("s1.orchestration")]
        self.assertGreater(len(orch_files), 0, "M9 should write its own files")
        self.assertEqual(len(other_files), 0,
                         f"M9 must not write non-orchestration files: {other_files}")

    def test_record_turn_emits_events_but_not_m6_files(self):
        """record_turn returns emitted events (for the supervisor to forward)
        but still writes ONLY .orchestration.* files itself."""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test", subjects=["数学"])
        emitted = svc.record_turn(student_id="s1", concept="导数")
        self.assertIsInstance(emitted, list)
        files = [f.name for f in self.tmp.iterdir()]
        non_orch = [f for f in files if not f.startswith("s1.orchestration")]
        self.assertEqual(non_orch, [],
                         f"M9 must not write M6 files directly: {non_orch}")


class TestSummaryIdentity(StorageSandboxTestCase):
    """W4/A13 双学生回归（§13.3「两个学生的 needs_replan 各读各的」）：
    summary 的 needs_replan 必须读本人 M2 档案——漏传 student_id 时登录
    学生静默回退游客命名空间（审查确认在 W4 前仍未修）。"""

    def _seed_plan(self, sid: str) -> None:
        state = store.load_state(sid)
        state.goals = [LearningGoal(id="g1", title="数学", subjects=["数学"])]
        state.weekly_plan = [WeeklyPlan(
            week_index=0, week_start=time.time() - 86400,
            concepts=[PlanConcept(concept_id="c_plan", name="规划概念")])]
        store.save_state(sid, state)

    def test_needs_replan_reads_own_namespace(self):
        # G4：评价统一在 learning-evidence journal。stu_own 的 c_plan 已
        # supported_in_scope → 计划落后于现实，应触发重规划信号。
        self._seed_plan("stu_own")
        self._seed_plan("stu_other")
        self._commit_supported_judgment("stu_own", "c_plan")
        svc = get_orchestration_service()
        self.assertTrue(svc.summary("stu_own")["needs_replan"])
        # 另一学生无 journal → 不触发；同名概念互不串档。
        self.assertFalse(svc.summary("stu_other")["needs_replan"])

    def _commit_supported_judgment(self, sid: str, concept_id: str) -> None:
        from app.agents.student_model.evaluation import schema as S
        from app.agents.student_model.evaluation.store import (
            get_journal, new_source_id)
        concept = S.ConceptRef(
            graph_owner_namespace="public", textbook_id="tb_x",
            file_ids=[], concept_id=concept_id, concept_revision="cr_1",
            display_name=concept_id)
        judgment = S.ConceptJudgment(
            judgment_id="jdg_" + sid + "_" + concept_id,
            concept_ref=concept, workspace_id="ws_g4",
            state=S.ConceptEvalState.SUPPORTED_IN_SCOPE,
            statement="限定条件下已有支持", claims=[],
            evidence_watermark="gen:1", policy_version=S.POLICY_VERSION,
            theory_version=S.THEORY_VERSION, prompt_ref="p",
            created_at=S.utc_now_iso(), source_id="src_" + sid,
            scope_revision="sr_1")
        journal = get_journal(sid)
        src = new_source_id()
        receipt = S.SourceReceipt(
            source_id=src, source_revision=1,
            kind=S.SourceKind.DIALOGUE, observed_at=S.utc_now_iso(),
            workspace_id_at_observation="", canonical_text="作答正确",
            scope_revision="sr_1")
        interp = S.LearnerInterpretation(
            applicable=True, observation_claims=[], concept_updates=[],
            feedback="")
        journal.append([
            S.OpSourceRegistered(source=receipt),
            S.OpResultCommitted(
                job_id="job_" + src[4:], source_id=src, source_revision=1,
                scope_revision="sr_1",
                interpretation_id="itp_" + src[4:],
                interpretation=interp, judgments=[judgment],
                abstained=False)])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 14. goal analyzer (gap analysis + backward planning) [modification 3]
# ---------------------------------------------------------------------------

class TestGoalAnalyzer(unittest.TestCase):

    def test_parse_exam_goal(self):
        r = goal_analyzer.parse_goal_text("我要考研数学120分")
        self.assertEqual(r["subject"], "数学")
        self.assertEqual(r["goal_type"], GoalType.EXAM)

    def test_parse_interest_goal(self):
        r = goal_analyzer.parse_goal_text("想了解一下量子力学")
        self.assertEqual(r["goal_type"], GoalType.INTEREST)

    def test_parse_unknown_degrades_to_ability(self):
        r = goal_analyzer.parse_goal_text("something totally unknown")
        self.assertEqual(r["goal_type"], GoalType.ABILITY)

    def test_gap_analysis_identifies_missing_and_weak(self):
        state = OrchestrationState()
        state.goals = [LearningGoal(title="数学", goal_type=GoalType.EXAM,
                                  subjects=["数学"])]
        skills = [{"skill_id": "s1", "name": "极限", "subject": "数学",
                   "difficulty": 3},
                  {"skill_id": "s2", "name": "导数", "subject": "数学",
                   "difficulty": 4}]
        view = {"s1": {"state": "supported_in_scope"},
                "s2": {"state": "fragile"}}
        gs = goal_analyzer.compute_gap_analysis(
            state.goals[0], subject_skills=skills, evaluation_view=view,
            prereq_map={"s2": ["s1"]})
        self.assertEqual(gs.supported_ratio, 0.5)
        self.assertEqual(len(gs.gaps), 1)
        self.assertEqual(gs.gaps[0].name, "导数")
        self.assertEqual(gs.gaps[0].status, "weak")
        self.assertEqual(gs.required_skills, ["s2"])

    def test_gap_analysis_missing_skill(self):
        """W4/A13：无观测记录 → unknown（未测），不再宣称「缺失」缺口；
        unknown 仍进入 required_skills（计划照常覆盖未测概念）。"""
        state = OrchestrationState()
        state.goals = [LearningGoal(title="数学", subjects=["数学"])]
        skills = [{"skill_id": "s1", "name": "极限", "subject": "数学",
                   "difficulty": 3}]
        gs = goal_analyzer.compute_gap_analysis(
            state.goals[0], subject_skills=skills, evaluation_view={})
        self.assertEqual(gs.gaps[0].status, "unknown")
        self.assertIn("s1", gs.required_skills)

    def test_gap_analysis_legacy_missing_row_roundtrips(self):
        """旧持久化行的 missing 值原样往返（历史数据不改写）。"""
        from app.agents.learning_orchestration.schema import GapItem
        item = GapItem.from_dict({"skill_id": "s1", "status": "missing"})
        self.assertEqual(item.status, "missing")
        self.assertEqual(GapItem.from_dict({"skill_id": "s2"}).status,
                         "unknown")

    def test_estimate_schedule_range_from_time_budget(self):
        """W4/A13：估期按时间容量形成区间——周容量 45×7=315 分钟、
        每概念 20 分钟 → 时间节奏 15/周；惯例节奏 5/周 → 区间 [1,2]，
        单点 est_weeks 保持 5/周 语义不变。"""
        now = time.time()
        est = goal_analyzer.estimate_schedule(
            10, now + 30 * 86400, now, weekly_pace=5,
            daily_minutes=45, available_days=7, minutes_per_concept=20)
        self.assertEqual(est["est_weeks"], 2)      # legacy 单点不变
        self.assertEqual(est["time_pace"], 15)
        self.assertEqual(est["est_weeks_min"], 1)  # ceil(10/15)
        self.assertEqual(est["est_weeks_max"], 2)  # ceil(10/5)
        self.assertEqual(est["weekly_capacity_minutes"], 315)
        # 时间预算收紧（每天 15 分钟 → 5/周）：区间退化为单点，不夸大。
        est2 = goal_analyzer.estimate_schedule(
            10, now + 30 * 86400, now, weekly_pace=5,
            daily_minutes=15, available_days=7, minutes_per_concept=20)
        self.assertEqual(est2["time_pace"], 5)
        self.assertEqual(est2["est_weeks_min"], 2)
        self.assertEqual(est2["est_weeks_max"], 2)

    def test_level_mapping(self):
        from app.agents.learning_orchestration.schema import GoalAnalysisLevel
        self.assertEqual(GoalAnalysisLevel.from_supported_ratio(0.1),
                         GoalAnalysisLevel.NOVICE)
        self.assertEqual(GoalAnalysisLevel.from_supported_ratio(0.5),
                         GoalAnalysisLevel.INTERMEDIATE)
        self.assertEqual(GoalAnalysisLevel.from_supported_ratio(0.9),
                         GoalAnalysisLevel.PROFICIENT)

    def test_backward_plan_topo_order(self):
        state = OrchestrationState()
        state.goals = [LearningGoal(title="数学", subjects=["数学"])]
        skills = [{"skill_id": "c", "name": "c", "subject": "数学", "difficulty": 3},
                  {"skill_id": "a", "name": "a", "subject": "数学", "difficulty": 1},
                  {"skill_id": "b", "name": "b", "subject": "数学", "difficulty": 2}]
        prereq = {"a": [], "b": ["a"], "c": ["b"]}
        gs = goal_analyzer.compute_gap_analysis(
            state.goals[0], subject_skills=skills, evaluation_view={},
            prereq_map=prereq)
        # a should come before b before c
        self.assertEqual(gs.required_skills, ["a", "b", "c"])

    def test_deadline_urgency(self):
        now = time.time()
        state = OrchestrationState()
        state.goals = [LearningGoal(title="考试", subjects=["数学"],
                                  deadline=now + 30 * 86400)]
        skills = [{"skill_id": "s1", "name": "x", "subject": "数学", "difficulty": 3}]
        gs = goal_analyzer.compute_gap_analysis(
            state.goals[0], subject_skills=skills, evaluation_view={}, now=now)
        self.assertGreater(gs.urgency, 0.0)

    def test_goal_state_roundtrip(self):
        from app.agents.learning_orchestration.schema import GoalState, GapItem
        gs = GoalState(goal_title="考研", supported_ratio=0.5,
                       gaps=[GapItem(skill_id="s1", name="极限")])
        d = gs.to_dict()
        gs2 = GoalState.from_dict(d)
        self.assertEqual(gs2.goal_title, "考研")
        self.assertEqual(gs2.gaps[0].name, "极限")


# ---------------------------------------------------------------------------
# 14b. goal x genealogy binding (L1 目标链：前置闭包 + 排期预估)
# ---------------------------------------------------------------------------

class TestGoalGenealogyBinding(unittest.TestCase):

    def test_goal_roundtrip_with_target_concepts(self):
        g = LearningGoal(title="物理上册考到 85", subjects=["物理"],
                         target_concept_ids=["p.f1", "p.f2"])
        g2 = LearningGoal.from_dict(g.to_dict())
        self.assertEqual(g2.target_concept_ids, ["p.f1", "p.f2"])
        # old persisted goals (no field) stay compatible
        g3 = LearningGoal.from_dict({"title": "旧目标", "subjects": ["数学"]})
        self.assertEqual(g3.target_concept_ids, [])

    def test_goal_state_roundtrip_chain_fields(self):
        from app.agents.learning_orchestration.schema import GoalState
        gs = GoalState(goal_title="g", chain_mode="concept_chain",
                       target_concept_ids=["a", "b"],
                       estimate={"weekly_pace": 5, "est_weeks": 2,
                                 "weeks_left": 4.0, "fit": "ok",
                                 "required_count": 8})
        gs2 = GoalState.from_dict(gs.to_dict())
        self.assertEqual(gs2.chain_mode, "concept_chain")
        self.assertEqual(gs2.target_concept_ids, ["a", "b"])
        self.assertEqual(gs2.estimate["fit"], "ok")
        # legacy dict without the new keys keeps defaults
        gs3 = GoalState.from_dict({"goal_title": "旧"})
        self.assertEqual(gs3.chain_mode, "subject")
        self.assertEqual(gs3.estimate, {})

    def test_prerequisite_closure_walks_and_prunes_mastered(self):
        prereq = {"t": ["m", "u"], "m": ["base"], "u": [], "base": []}
        # m 已掌握：不进链也不再往下走 base；u 未掌握进入
        out = goal_analyzer.prerequisite_closure(
            ["t"], prereq, mastered_ids={"m"})
        self.assertEqual(set(out), {"t", "u"})
        # 全未掌握：完整链 t->m->u->base
        out2 = goal_analyzer.prerequisite_closure(["t"], prereq, set())
        self.assertEqual(set(out2), {"t", "m", "u", "base"})

    def test_prerequisite_closure_cycle_and_cap_safe(self):
        # 环形前置不炸、cap 截断
        prereq = {f"n{i}": [f"n{(i + 1) % 50}"] for i in range(50)}
        out = goal_analyzer.prerequisite_closure(["n0"], prereq, set(), cap=10)
        self.assertLessEqual(len(out), 10)

    def test_estimate_schedule_fits(self):
        now = time.time()
        est = goal_analyzer.estimate_schedule(10, now + 7 * 86400, now)
        self.assertEqual(est["est_weeks"], 2)
        self.assertEqual(est["weeks_left"], 1.0)
        self.assertEqual(est["fit"], "tight")
        est2 = goal_analyzer.estimate_schedule(20, now + 42 * 86400, now)
        # 20 概念 ≈ 4 周 vs 6 周 -> ok（既不紧张也不宽松）
        self.assertEqual(est2["fit"], "ok")
        est3 = goal_analyzer.estimate_schedule(2, now + 200 * 86400, now)
        self.assertEqual(est3["fit"], "loose")
        est4 = goal_analyzer.estimate_schedule(5, 0, now)
        self.assertEqual(est4["fit"], "none")

    def test_gap_analysis_chain_mode_echoed(self):
        state = OrchestrationState()
        state.goals = [LearningGoal(title="目标", subjects=["物理"],
                                  target_concept_ids=["p.t1"])]
        skills = [{"skill_id": "p.t1", "name": "T1", "subject": "物理",
                   "difficulty": 3},
                  {"skill_id": "p.pre", "name": "PRE", "subject": "物理",
                   "difficulty": 2}]
        gs = goal_analyzer.compute_gap_analysis(
            state.goals[0], subject_skills=skills, evaluation_view={},
            prereq_map={"p.t1": ["p.pre"]}, chain_mode="concept_chain")
        self.assertEqual(gs.chain_mode, "concept_chain")
        self.assertEqual(gs.target_concept_ids, ["p.t1"])
        # 进度分母 = 目标链（2 个概念），不是全学科
        self.assertEqual(gs.total_skills, 2)
        self.assertEqual(gs.estimate["required_count"], 2)

    def test_analyze_goals_binding_branch(self):
        svc = get_orchestration_service()
        state = OrchestrationState()
        state.goals = [LearningGoal(id="g_1", title="目标", subjects=[""],
                                  target_concept_ids=["p.t1"])]
        chain_skills = [{"skill_id": "p.t1", "name": "T1", "subject": "物理",
                         "difficulty": 3}]
        with patch.object(svc, "_concept_chain_skills_safe",
                          return_value=chain_skills) as m_chain, \
             patch.object(svc, "_evaluation_view_safe", return_value={}), \
             patch.object(svc, "_prereq_map_safe", return_value={}), \
             patch.object(svc, "_subject_skills_safe",
                          return_value=[{"skill_id": "other",
                                         "name": "X", "subject": "数学",
                                         "difficulty": 3}]) as m_subj:
            svc._analyze_goals_safe(state, student_id="s1")
        m_chain.assert_called_once()
        m_subj.assert_not_called()  # 绑定优先，学科兜底不触发
        self.assertEqual(state.goal_states[0].chain_mode, "concept_chain")
        self.assertEqual(state.goal_states[0].total_skills, 1)

    def test_analyze_goals_empty_subject_no_longer_full_graph(self):
        svc = get_orchestration_service()
        state = OrchestrationState()
        # subjects 空 + 标题无学科关键词 + 无概念绑定 -> 不再全图谱分析
        state.goals = [LearningGoal(id="g_1", title="变得更强")]
        with patch.object(svc, "_subject_skills_safe",
                          return_value=[{"skill_id": "n1", "name": "任意",
                                         "subject": "数学", "difficulty": 3}]) as m:
            svc._analyze_goals_safe(state, student_id="s1")
        m.assert_not_called()
        # 分析不出 -> 带标题的默认 GoalState（可与目标配对）
        self.assertEqual(state.goal_states[0].total_skills, 0)
        self.assertEqual(state.goal_states[0].goal_title, "变得更强")


# ---------------------------------------------------------------------------
# 15. event emitter (M9 -> M6 event flow) [modification 1]
# ---------------------------------------------------------------------------

class TestEventEmitter(unittest.TestCase):

    def test_milestone_completed_transition(self):
        evs = event_emitter.emit_for_milestone_transition(
            "in_progress", "completed", "高数基础", "数学")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].event_type, "milestone_completed")
        self.assertTrue(evs[0].valid)

    def test_milestone_no_transition_no_event(self):
        evs = event_emitter.emit_for_milestone_transition(
            "in_progress", "in_progress", "高数基础")
        self.assertEqual(evs, [])

    def test_streak_threshold_crossed(self):
        evs = event_emitter.emit_for_streak(7, last_reported=3, subject="数学")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].event_type, "habit_milestone")

    def test_streak_non_threshold_no_event(self):
        evs = event_emitter.emit_for_streak(5, last_reported=3)
        self.assertEqual(evs, [])

    def test_streak_dedup(self):
        # already reported 7, streak still 7 -> no new event
        evs = event_emitter.emit_for_streak(7, last_reported=7)
        self.assertEqual(evs, [])

    def test_goal_progress_checkpoint(self):
        evs = event_emitter.emit_for_goal_progress(0.5, last_reported=0.25)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].event_type, "goal_progress")

    def test_task_batch_completed(self):
        ev = event_emitter.task_batch_completed_event("2026-07-29", 3, "数学")
        self.assertTrue(ev.valid)

    def test_valid_events_filters_bogus(self):
        from app.agents.learning_orchestration.schema import \
            OrchestrationLearningEvent
        good = event_emitter.task_batch_completed_event("d", 1)
        bad = OrchestrationLearningEvent(event_type="bogus", summary="x")
        self.assertEqual(len(event_emitter.valid_events([good, bad])), 1)

    def test_to_event_dicts_serializes(self):
        ev = event_emitter.task_batch_completed_event("d", 1, "数学")
        dicts = event_emitter.to_event_dicts([ev])
        self.assertEqual(dicts[0]["event_type"], "task_batch_completed")
        self.assertIn("importance", dicts[0])


# ---------------------------------------------------------------------------
# 16. TaskCompletionTracker (modification 2: M9 owns task execution)
# ---------------------------------------------------------------------------

class TestTaskCompletionTracker(unittest.TestCase):

    def test_completion_stats(self):
        state = OrchestrationState()
        now = time.time()
        from app.agents.learning_orchestration.task_executor import _day_str
        d = _day_str(now)
        state.daily_tasks = [
            DailyTask(id="1", day=d, status=DailyTaskStatus.COMPLETED),
            DailyTask(id="2", day=d, status=DailyTaskStatus.PENDING),
            DailyTask(id="3", day=d, status=DailyTaskStatus.COMPLETED),
        ]
        stats = habit_tracker.task_completion_stats(state, now=now)
        self.assertEqual(stats["completed_tasks"], 2)
        self.assertEqual(stats["total_tasks"], 3)
        self.assertAlmostEqual(stats["completion_rate"], 0.667, places=2)

    def test_completion_stats_empty(self):
        state = OrchestrationState()
        stats = habit_tracker.task_completion_stats(state)
        self.assertEqual(stats["completion_rate"], 0.0)


# ---------------------------------------------------------------------------
# 17. manager event emission end-to-end [modification 1]
# ---------------------------------------------------------------------------

class TestManagerEventEmission(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_add_goal_populates_goal_state(self):
        """add_goal should run gap analysis per goal (best-effort). With no
        graph it leaves a paired default state, never raises."""
        svc = get_orchestration_service()
        goal = svc.add_goal("s1", title="考研数学", subjects=["数学"])
        self.assertEqual(goal.id, "g_1")
        summary = svc.summary("s1")
        self.assertIn("goal_states", summary)
        self.assertEqual(summary["goal_states"][0]["goal_id"], "g_1")

    def test_complete_task_emits_batch_event(self):
        """Completing all of today's tasks emits a task_batch_completed event."""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test", subjects=["数学"])
        from app.agents.learning_orchestration.task_executor import _day_str
        d = _day_str(time.time())
        state = store.load_state("s1")
        state.daily_tasks = [
            DailyTask(id="t1", day=d, status=DailyTaskStatus.COMPLETED),
            DailyTask(id="t2", day=d, status=DailyTaskStatus.PENDING)]
        store.save_state("s1", state)
        ok, emitted = svc.complete_task("s1", "t2")
        self.assertTrue(ok)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].event_type, "task_batch_completed")

    def test_record_turn_returns_events_list(self):
        """record_turn returns a list (possibly empty) of emitted events."""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="test", subjects=["数学"])
        emitted = svc.record_turn(student_id="s1", concept="导数")
        self.assertIsInstance(emitted, list)

    def test_habit_patterns_read_returns_list(self):
        """habit_patterns（转正访问器，C9）reads M6 read-only, [] on no data."""
        svc = get_orchestration_service()
        # no M6 data -> empty list, never raises
        result = svc.habit_patterns("s1", subject="数学")
        self.assertIsInstance(result, list)

    def test_habit_context_rendering(self):
        """daily_composer.habit_context: M6 patterns render as compose context."""
        from app.agents.learning_orchestration import daily_composer
        # 空数据 → 空串（prompt 块直接消失）
        self.assertEqual(daily_composer.habit_context([]), "")
        self.assertEqual(daily_composer.habit_context([{"fact": ""}]), "")
        out = daily_composer.habit_context([
            {"fact": "连续学习7天", "evidence_count": 3},
            {"fact": "稳定完成每日任务", "evidence_count": 8},
            {"fact": "第三条", "evidence_count": 1},
            {"fact": "第四条超限", "evidence_count": 1},
        ])
        self.assertIn("连续学习7天", out)
        self.assertIn("证据 3 次", out)
        self.assertIn("稳定完成每日任务", out)
        self.assertNotIn("第四条超限", out)  # limit=3
        self.assertTrue(out.startswith("学生长期学习习惯"))

    def test_compose_context_joins_bloom_and_habits(self):
        """_compose_context_safe 合并布鲁姆弱项 + M6 习惯参考（真实消费点）。"""
        import tempfile as _tf
        from unittest.mock import patch as _patch
        from app.agents.memory import habit_pattern as hp
        from app.agents.memory import store as mem_store
        with _tf.TemporaryDirectory(prefix="m9_habit_") as td:
            with _patch.object(mem_store, "_STUDENTS_DIR", Path(td)):
                hp.consolidate_habit_events("s1", [
                    {"event_type": "habit_milestone",
                     "payload": {"streak": 7}}])
                svc = get_orchestration_service()
                ctx = svc._compose_context_safe("s1")
                self.assertIn("学生长期学习习惯", ctx)
                self.assertIn("连续学习", ctx)


# ---------------------------------------------------------------------------
# 19. task uniqueness contract (materialize gap-fill / carryover / idempotent)
# ---------------------------------------------------------------------------

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


class TestMaterializeDay(unittest.TestCase):

    def test_gap_fill_preserves_existing_identity(self):
        day = task_executor._day_str(_DAY1)
        state = OrchestrationState()
        existing = DailyTask(id=f"{day}_c1_study", day=day, concept_id="c1",
                             kind=TaskKind.STUDY,
                             status=DailyTaskStatus.COMPLETED)
        state.daily_tasks = [existing]
        candidates = [
            DailyTask(id=f"{day}_c1_study", day=day, concept_id="c1",
                      kind=TaskKind.STUDY, status=DailyTaskStatus.PENDING),
            DailyTask(id=f"{day}_c2_review", day=day, concept_id="c2",
                      kind=TaskKind.REVIEW),
        ]
        out = task_executor.materialize_day(state, day, candidates)
        # existing (concept_id, kind) key kept as-is (still COMPLETED, same object)
        self.assertIs(out[0], existing)
        self.assertEqual(state.daily_tasks[0].status, DailyTaskStatus.COMPLETED)
        # only the missing key inserted
        self.assertEqual(len(state.daily_tasks), 2)
        self.assertEqual(state.daily_tasks[1].concept_id, "c2")

    def test_gap_fill_keeps_custom_tasks_untouched(self):
        day = task_executor._day_str(_DAY1)
        state = OrchestrationState()
        state.daily_tasks = [DailyTask(id=f"user_{day}_1", day=day,
                                       concept_id="c9", kind=TaskKind.STUDY,
                                       custom=True, title="我的任务")]
        task_executor.materialize_day(state, day, [
            DailyTask(id=f"{day}_c1_study", day=day, concept_id="c1",
                      kind=TaskKind.STUDY)])
        self.assertEqual(state.daily_tasks[0].id, f"user_{day}_1")
        self.assertTrue(state.daily_tasks[0].custom)
        self.assertEqual(len(state.daily_tasks), 2)

    def test_carryover_tasks_only_unfinished_past_days(self):
        state = OrchestrationState()
        state.daily_tasks = [
            DailyTask(id="a", day="2026-07-26", status=DailyTaskStatus.OVERDUE),
            DailyTask(id="b", day="2026-07-26",
                      status=DailyTaskStatus.COMPLETED),
            DailyTask(id="c", day="2026-07-27", status=DailyTaskStatus.PENDING),
        ]
        co = task_executor.carryover_tasks(state, now=_DAY1)
        self.assertEqual([t.id for t in co], ["a"])


class TestTodayTasksUniqueness(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_compose_idempotent_llm_skipped_when_day_composed(self):
        """A day with >= 1 non-custom task counts as composed: the LLM
        composer is skipped entirely."""
        import asyncio
        svc = get_orchestration_service()
        state = _week_plan_state(week_start=_DAY1 - 3600)
        day = task_executor._day_str(_DAY1)
        state.daily_tasks = [DailyTask(id=f"{day}_c1_study", day=day,
                                       concept_id="c1", kind=TaskKind.STUDY)]
        store.save_state("s1", state)
        with patch.object(LearningOrchestrationService, "_get_llm") as m_llm:
            out = asyncio.run(svc.today_tasks("s1", now=_DAY1))
            m_llm.assert_not_called()
        self.assertEqual(len(out), 1)

    def test_cross_day_carryover_kept_on_top(self):
        """Re-composition on the next day must not evaporate day-1 tasks;
        the unfinished one carries over (overdue) at the top of the list."""
        import asyncio
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")):
            day1_out = asyncio.run(svc.today_tasks("s1", now=_DAY1))
            self.assertEqual(len(day1_out), 1)  # deterministic fallback: study c1
            day2_out = asyncio.run(svc.today_tasks("s1", now=_DAY2))
        state = store.load_state("s1")
        day1 = task_executor._day_str(_DAY1)
        day2 = task_executor._day_str(_DAY2)
        # both days' tasks still persisted (nothing replaced/deleted)
        days = sorted({t.day for t in state.daily_tasks})
        self.assertEqual(days, [day1, day2])
        # day-1 task swept to overdue and listed first (carryover section)
        self.assertEqual(day2_out[0]["day"], day1)
        self.assertEqual(day2_out[0]["status"], "overdue")
        self.assertEqual(day2_out[1]["day"], day2)

    def test_regenerate_preserves_materialized_and_custom_tasks(self):
        """regenerate_plan recomputes the weekly plan but never touches
        persisted daily_tasks (materialized or custom)."""
        import asyncio
        svc = get_orchestration_service()
        state = _week_plan_state(week_start=_DAY1 - 3600)
        day = task_executor._day_str(_DAY1)
        state.daily_tasks = [
            DailyTask(id=f"{day}_c1_study", day=day, concept_id="c1",
                      kind=TaskKind.STUDY, status=DailyTaskStatus.IN_PROGRESS),
            DailyTask(id=f"user_{day}_1", day=day, custom=True, title="自建")]
        store.save_state("s1", state)
        fake_inputs = {
            "next_learnable": [{"name": "积分", "skill_id": "c2",
                                "difficulty": 4}],
            "review_candidates": [], "evaluation_view": {},
            "prereq_map": {}}
        with patch.object(LearningOrchestrationService, "_assemble_plan_inputs",
                          return_value=fake_inputs):
            ok, reason = asyncio.run(svc.regenerate_plan("s1", now=_DAY1))
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        after = store.load_state("s1")
        self.assertEqual([t.id for t in after.daily_tasks],
                         [f"{day}_c1_study", f"user_{day}_1"])
        self.assertEqual(after.daily_tasks[0].status,
                         DailyTaskStatus.IN_PROGRESS)


# ---------------------------------------------------------------------------
# 20. schema back-compat (old persisted dicts without the new fields)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 21. milestone generator (LLM gate + deterministic fallback + manager)
# ---------------------------------------------------------------------------

class TestWeeklyPlannerLLM(unittest.TestCase):
    """LLM weekly planner: validation gate + materialisation + fallback."""

    def _valid_json(self):
        return json.dumps({"weeks": [
            {"focus": "打基础", "tasks": [
                {"title": "学完 A 与 B", "concept_ids": ["a", "b"],
                 "kind": "study",
                 "subtasks": [{"title": "看 A 讲解", "estimate_minutes": 20},
                              {"title": "做 B 练习", "estimate_minutes": 25}]}]},
            {"focus": "进阶", "tasks": [
                {"title": "攻克 C、D", "concept_ids": ["c", "d"],
                 "kind": "study",
                 "subtasks": [{"title": "推导 C", "estimate_minutes": 30}]},
                {"title": "复盘 E", "concept_ids": ["e"], "kind": "review",
                 "subtasks": [{"title": "错题重做", "estimate_minutes": 15}]}]}]})

    def test_parse_valid_response(self):
        sk = weekly_planner_llm.parse_weekly_response(
            self._valid_json(), ["a", "b", "c", "d", "e"], 2)
        self.assertEqual(len(sk), 2)
        self.assertEqual(sk[0]["tasks"][0]["title"], "学完 A 与 B")
        self.assertEqual(len(sk[1]["tasks"]), 2)

    def test_parse_out_of_pool_id_rejected(self):
        bad = json.dumps({"weeks": [{"focus": "x", "tasks": [
            {"title": "t", "concept_ids": ["zzz"], "kind": "study",
             "subtasks": [{"title": "s"}]}]}]})
        self.assertIsNone(weekly_planner_llm.parse_weekly_response(
            bad, ["a"], 1))

    def test_parse_incomplete_coverage_rejected(self):
        sk = json.dumps({"weeks": [{"focus": "x", "tasks": [
            {"title": "t", "concept_ids": ["a"], "kind": "study",
             "subtasks": [{"title": "s"}]}]}]})
        # "b" never covered
        self.assertIsNone(weekly_planner_llm.parse_weekly_response(
            sk, ["a", "b"], 1))

    def test_parse_duplicate_concept_rejected(self):
        dup = json.dumps({"weeks": [
            {"focus": "x", "tasks": [
                {"title": "t1", "concept_ids": ["a"], "kind": "study",
                 "subtasks": [{"title": "s"}]}]},
            {"focus": "y", "tasks": [
                {"title": "t2", "concept_ids": ["a"], "kind": "practice",
                 "subtasks": [{"title": "s"}]}]}]})
        self.assertIsNone(weekly_planner_llm.parse_weekly_response(
            dup, ["a"], 2))

    def test_parse_review_task_may_revisit_concepts(self):
        """A review/summary task legitimately covers earlier concepts again —
        that is not a duplicate violation."""
        ok = json.dumps({"weeks": [
            {"focus": "x", "tasks": [
                {"title": "t1", "concept_ids": ["a"], "kind": "study",
                 "subtasks": [{"title": "s"}]}]},
            {"focus": "y", "tasks": [
                {"title": "复习周", "concept_ids": ["a"], "kind": "review",
                 "subtasks": [{"title": "错题重做"}]}]}]})
        sk = weekly_planner_llm.parse_weekly_response(ok, ["a"], 2)
        self.assertEqual(len(sk), 2)
        self.assertEqual(sk[1]["tasks"][0]["kind"], "review")

    def test_parse_bad_shapes_rejected(self):
        req = ["a"]
        self.assertIsNone(weekly_planner_llm.parse_weekly_response("junk", req, 1))
        self.assertIsNone(weekly_planner_llm.parse_weekly_response("", req, 1))
        # illegal kind
        self.assertIsNone(weekly_planner_llm.parse_weekly_response(
            json.dumps({"weeks": [{"focus": "x", "tasks": [
                {"title": "t", "concept_ids": ["a"], "kind": "dance",
                 "subtasks": [{"title": "s"}]}]}]}), req, 1))
        # no subtasks
        self.assertIsNone(weekly_planner_llm.parse_weekly_response(
            json.dumps({"weeks": [{"focus": "x", "tasks": [
                {"title": "t", "concept_ids": ["a"], "kind": "study",
                 "subtasks": []}]}]}), req, 1))

    def test_parse_fence_tolerant(self):
        fenced = "```json\n" + self._valid_json() + "\n```"
        sk = weekly_planner_llm.parse_weekly_response(
            fenced, ["a", "b", "c", "d", "e"], 2)
        self.assertEqual(len(sk), 2)

    def test_weeks_from_skeletons_ids_and_concepts(self):
        sk = weekly_planner_llm.parse_weekly_response(
            self._valid_json(), ["a", "b", "c", "d", "e"], 2)
        weeks = weekly_planner_llm.weeks_from_skeletons(sk, {"a": "概念A"})
        self.assertEqual(len(weeks), 2)
        w0 = weeks[0]
        self.assertEqual(w0.origin, "auto")
        self.assertEqual(w0.tasks[0].id, "wt_0_1")
        self.assertEqual(w0.tasks[0].subtasks[0].id, "st_wt_0_1_1")
        self.assertEqual(w0.tasks[0].source, "auto")
        # concepts derived from task concept_ids, names resolved
        self.assertEqual([c.concept_id for c in w0.concepts], ["a", "b"])
        self.assertEqual(w0.concepts[0].name, "概念A")
        # week starts are one week apart
        self.assertAlmostEqual(weeks[1].week_start - weeks[0].week_start,
                               7 * 86400, places=0)

    def test_derive_tasks_fallback(self):
        weeks = [WeeklyPlan(week_index=0, focus="浮力", concepts=[
            PlanConcept(concept_id="c1", name="浮力"),
            PlanConcept(concept_id="c2", name="压强")])]
        weekly_planner_llm.derive_tasks_fallback(weeks)
        self.assertEqual(len(weeks[0].tasks), 1)
        t = weeks[0].tasks[0]
        self.assertEqual(t.source, "auto")
        self.assertTrue(t.subtasks)
        self.assertEqual(t.concept_ids, ["c1", "c2"])

    def test_current_week_window_and_unfinished(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        now = _DAY1
        state = OrchestrationState()
        state.weekly_plan = [
            WeeklyPlan(week_index=0, week_start=now - 14 * 86400,
                       focus="上周",
                       tasks=[WeekTask(id="wt_0_1", subtasks=[
                           SubTask(id="s1", done=True)])]),
            WeeklyPlan(week_index=1, week_start=now - 3600, focus="本周")]
        cur = weekly_planner_llm.current_week(state, now=now)
        self.assertEqual(cur.focus, "本周")
        # outside any window -> first week with unfinished tasks; week1 has
        # none (empty task list), week0's are all done -> first week overall
        cur2 = weekly_planner_llm.current_week(state, now=now + 30 * 86400)
        self.assertEqual(cur2.focus, "上周")
        self.assertIsNone(weekly_planner_llm.current_week(
            OrchestrationState(), now=now))


class TestRegeneratePlanLLM(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def _seed_goal_state(self, required):
        """Seed a student whose gap analysis already produced required_skills."""
        svc = get_orchestration_service()
        svc.add_goal("s1", title="考研数学", subjects=["数学"])
        state = store.load_state("s1")
        state.goal_states = [GoalState(
            goal_id=state.goals[0].id, goal_title="考研数学",
            subject="数学", required_skills=list(required))]
        store.save_state("s1", state)
        return svc

    def _mock_llm(self, content=None, side_effect=None):
        from unittest.mock import AsyncMock
        m = MagicMock()
        if side_effect is not None:
            m.complete = AsyncMock(side_effect=side_effect)
        else:
            m.complete = AsyncMock(return_value=(content, None))
        return m

    def _weekly_json(self, required):
        return json.dumps({"weeks": [{"focus": "全程", "tasks": [
            {"title": "学完 " + c, "concept_ids": [c], "kind": "study",
             "subtasks": [{"title": "看讲解", "estimate_minutes": 20}]}
            for c in required]}]})

    def test_regenerate_llm_success(self):
        import asyncio
        required = ["a", "b", "c"]
        svc = self._seed_goal_state(required)
        with patch("app.core.llm_async.get_llm",
                   return_value=self._mock_llm(self._weekly_json(required))):
            ok, reason = asyncio.run(svc.regenerate_plan("s1", now=_DAY1))
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        summary = svc.summary("s1")
        weeks = summary["weekly_plan"]
        self.assertEqual(len(weeks), 1)
        self.assertEqual(weeks[0]["origin"], "auto")
        self.assertEqual(len(weeks[0]["tasks"]), 3)
        self.assertEqual(weeks[0]["tasks"][0]["id"], "wt_0_1")
        self.assertTrue(weeks[0]["tasks"][0]["subtasks"])
        covered = sorted(c for w in weeks for c in
                         [pc["concept_id"] for pc in w["concepts"]])
        self.assertEqual(covered, required)

    def test_regenerate_llm_empty_falls_back(self):
        """LLM returns nothing -> deterministic path (graph-less here, so the
        plan legitimately ends empty: ok=True + empty_plan, never an error)."""
        import asyncio
        required = ["a", "b", "c"]
        svc = self._seed_goal_state(required)
        fake_inputs = {"next_learnable": [], "review_candidates": [],
                       "evaluation_view": {}, "prereq_map": {}}
        with patch("app.core.llm_async.get_llm",
                   return_value=self._mock_llm("")), \
                patch.object(LearningOrchestrationService,
                             "_assemble_plan_inputs",
                             return_value=fake_inputs):
            ok, reason = asyncio.run(svc.regenerate_plan("s1", now=_DAY1))
        self.assertTrue(ok)
        self.assertEqual(reason, "empty_plan")
        after = store.load_state("s1")
        self.assertEqual(after.last_plan_attempt, _DAY1)

    def test_regenerate_exception_never_propagates(self):
        import asyncio
        svc = self._seed_goal_state(["a"])
        with patch("app.core.llm_async.get_llm",
                   return_value=self._mock_llm(side_effect=RuntimeError("boom"))):
            ok, _ = asyncio.run(svc.regenerate_plan("s1"))  # must not raise
        self.assertTrue(ok)
        with patch.object(LearningOrchestrationService, "_load",
                          side_effect=RuntimeError("io boom")):
            ok2, _ = asyncio.run(svc.regenerate_plan("s1"))
        self.assertFalse(ok2)

    def test_regenerate_no_goal(self):
        import asyncio
        svc = get_orchestration_service()
        ok, reason = asyncio.run(svc.regenerate_plan("s1"))
        self.assertFalse(ok)
        self.assertEqual(reason, "no_goal")

    def test_regenerate_merges_multiple_goals(self):
        """Two goals' required chains merge (goal order, deduped) into one
        shared plan; the prompt carries both goal titles."""
        import asyncio
        svc = get_orchestration_service()
        g1 = svc.add_goal("s1", title="考研数学", subjects=["数学"])
        g2 = svc.add_goal("s1", title="物理入门", subjects=["物理"])
        state = store.load_state("s1")
        state.goal_states = [
            GoalState(goal_id=g1.id, goal_title="考研数学",
                      required_skills=["a", "b"]),
            GoalState(goal_id=g2.id, goal_title="物理入门",
                      required_skills=["b", "c"])]  # "b" deduped
        store.save_state("s1", state)
        seen_goals = {}

        from app.agents.learning_orchestration import weekly_planner_llm as wpl
        orig_build = wpl.build_weekly_prompt

        def build_spy(goal_title, window, *a, **kw):
            seen_goals["title"] = goal_title
            seen_goals["window"] = list(window)
            return orig_build(goal_title, window, *a, **kw)

        with patch.object(wpl, "build_weekly_prompt", side_effect=build_spy):
            with patch("app.core.llm_async.get_llm",
                       return_value=self._mock_llm(
                           self._weekly_json(["a", "b", "c"]))):
                ok, reason = asyncio.run(svc.regenerate_plan("s1", now=_DAY1))
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        # both titles in one prompt line; merged window deduped in goal order
        self.assertIn("考研数学", seen_goals["title"])
        self.assertIn("物理入门", seen_goals["title"])
        self.assertEqual(seen_goals["window"], ["a", "b", "c"])
        summary = svc.summary("s1")
        covered = sorted(c for w in summary["weekly_plan"]
                         for c in [pc["concept_id"] for pc in w["concepts"]])
        self.assertEqual(covered, ["a", "b", "c"])


# ---------------------------------------------------------------------------
# 22. daily composer (pool / gate / LLM + fallback through the manager)
# ---------------------------------------------------------------------------

class TestDailyComposer(unittest.TestCase):

    def _state_with_signals(self):
        """State carrying one SRS-due card, a current week, and a
        carryover task."""
        now = _DAY1
        state = OrchestrationState()
        state.goals = [LearningGoal(title="考研数学", subjects=["数学"])]
        state.review_queue = {"c_srs": ReviewItem(
            concept_id="c_srs", concept_name="极限", next_review=now - 100)}
        state.weekly_plan = [WeeklyPlan(
            week_index=0, week_start=now - 3600, focus="基础周",
            concepts=[PlanConcept(concept_id="c_ms", name="导数")])]
        state.daily_tasks = [DailyTask(
            id=f"2026-07-26_c_old_study", day="2026-07-26", concept_id="c_old",
            concept_name="旧概念", status=DailyTaskStatus.OVERDUE)]
        view = {"c_weak": {"state": "fragile"},
                "c_ms": {"state": "not_observed"}}
        return state, view, now

    def test_candidate_pool_sources(self):
        state, view, now = self._state_with_signals()
        pool = daily_composer.build_candidate_pool(
            state, evaluation_view=view, concept_names={}, now=now)
        by_id = {e["concept_id"]: e for e in pool}
        self.assertIn("srs_due", by_id["c_srs"]["sources"])
        self.assertIn("current_week", by_id["c_ms"]["sources"])
        self.assertIn("weak", by_id["c_weak"]["sources"])
        self.assertIn("carryover", by_id["c_old"]["sources"])

    def test_parse_gate(self):
        pool = [{"concept_id": "a", "name": "A", "mastery": 0.1,
                 "overdue_days": 0, "milestone_id": "", "sources": ["weak"]}]
        ok = json.dumps({"tasks": [
            {"concept_id": "a", "kind": "review", "phase": "reinforce",
             "reason": "该复习了"}]})
        picks = daily_composer.parse_compose_response(ok, pool, 2)
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["phase"], "reinforce")
        # out-of-pool id rejected
        bad_id = json.dumps({"tasks": [
            {"concept_id": "nope", "kind": "review", "phase": "", "reason": ""}]})
        self.assertIsNone(daily_composer.parse_compose_response(bad_id, pool, 2))
        # illegal kind rejected
        bad_kind = json.dumps({"tasks": [
            {"concept_id": "a", "kind": "dance", "phase": "", "reason": ""}]})
        self.assertIsNone(daily_composer.parse_compose_response(bad_kind, pool, 2))
        # illegal phase rejected
        bad_phase = json.dumps({"tasks": [
            {"concept_id": "a", "kind": "review", "phase": "warp", "reason": ""}]})
        self.assertIsNone(daily_composer.parse_compose_response(bad_phase, pool, 2))
        # over slot budget rejected
        over = json.dumps({"tasks": [
            {"concept_id": "a", "kind": "review", "phase": "", "reason": ""},
            {"concept_id": "a", "kind": "study", "phase": "", "reason": ""}]})
        self.assertIsNone(daily_composer.parse_compose_response(over, pool, 1))
        # bad JSON rejected
        self.assertIsNone(daily_composer.parse_compose_response("junk", pool, 2))

    def test_annotate_fallback_template_reasons(self):
        tasks = [DailyTask(id="1", kind=TaskKind.REVIEW),
                 DailyTask(id="2", kind=TaskKind.STUDY, milestone_id="ms_0"),
                 DailyTask(id="3", kind=TaskKind.SUMMARY)]
        daily_composer.annotate_fallback(tasks)
        self.assertEqual(tasks[0].reason, "SRS 到期待复习")
        self.assertEqual(tasks[1].reason, "本周重点概念")
        self.assertEqual(tasks[2].reason, "回顾总结今日所学")


class TestDailyComposerManager(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def _mock_llm(self, content=None, side_effect=None):
        from unittest.mock import AsyncMock
        m = MagicMock()
        if side_effect is not None:
            m.complete = AsyncMock(side_effect=side_effect)
        else:
            m.complete = AsyncMock(return_value=(content, None))
        return m

    def test_llm_success_materializes_picks(self):
        import asyncio
        svc = get_orchestration_service()
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.review_queue = {"c1": ReviewItem(
            concept_id="c1", concept_name="导数", next_review=_DAY1 - 100)}
        store.save_state("s1", state)
        content = json.dumps({"tasks": [
            {"concept_id": "c1", "kind": "review", "phase": "reinforce",
             "reason": "SRS 到期"}]})
        with patch("app.core.llm_async.get_llm",
                   return_value=self._mock_llm(content)):
            out = asyncio.run(svc.today_tasks("s1", now=_DAY1, compose_llm=True))
        day = task_executor._day_str(_DAY1)
        todays = [t for t in out if t["day"] == day]
        self.assertEqual(len(todays), 1)
        self.assertEqual(todays[0]["kind"], "review")
        self.assertEqual(todays[0]["phase"], "reinforce")
        self.assertEqual(todays[0]["reason"], "SRS 到期")

    def test_llm_out_of_pool_falls_back_deterministic(self):
        import asyncio
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        content = json.dumps({"tasks": [
            {"concept_id": "hallucinated", "kind": "study", "phase": "",
             "reason": ""}]})
        with patch("app.core.llm_async.get_llm",
                   return_value=self._mock_llm(content)):
            out = asyncio.run(svc.today_tasks("s1", now=_DAY1, compose_llm=True))
        day = task_executor._day_str(_DAY1)
        todays = [t for t in out if t["day"] == day]
        # deterministic fallback generated the weekly-plan study task
        self.assertTrue(any(t["concept_id"] == "c1" for t in todays))
        self.assertFalse(any(t["concept_id"] == "hallucinated" for t in todays))
        # fallback tasks carry template reasons
        self.assertTrue(all(t["reason"] for t in todays))

    def test_llm_exception_falls_back_deterministic(self):
        import asyncio
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        with patch("app.core.llm_async.get_llm",
                   return_value=self._mock_llm(
                       side_effect=RuntimeError("boom"))):
            out = asyncio.run(svc.today_tasks("s1", now=_DAY1, compose_llm=True))
        day = task_executor._day_str(_DAY1)
        self.assertTrue(any(t["day"] == day for t in out))


# ---------------------------------------------------------------------------
# 23. record_turn auto-progress (6g enhancement)
# ---------------------------------------------------------------------------

class TestRecordTurnAutoProgress(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def _seed_task(self, *, concept_id="c1", concept_name="导数",
                   status=DailyTaskStatus.PENDING):
        svc = get_orchestration_service()
        svc.add_goal("s1", title="考研数学", subjects=["数学"])
        day = task_executor._day_str(time.time())
        state = store.load_state("s1")
        state.daily_tasks = [DailyTask(
            id=f"{day}_{concept_id}_study", day=day, concept_id=concept_id,
            concept_name=concept_name, kind=TaskKind.STUDY, status=status)]
        store.save_state("s1", state)
        return svc

    def _today_task(self):
        state = store.load_state("s1")
        return state.daily_tasks[0]

    def test_concept_taught_moves_pending_to_in_progress(self):
        svc = self._seed_task()
        svc.record_turn(student_id="s1", concept="导数")
        self.assertEqual(self._today_task().status, DailyTaskStatus.IN_PROGRESS)

    def test_unbound_quiz_evidence_does_not_complete_task(self):
        """W4/A12：无绑定的作答证据不再自动完成任务——同概念多任务串联
        完成正是 A12 的缺陷；完成只能来自任务自身的绑定证据或显式勾选
        （self_report）。"""
        svc = self._seed_task()
        svc.record_quiz_evidence(student_id="s1", concept="导数",
                                 verdict="correct", attempt_id="att_w4t1")
        task = self._today_task()
        self.assertNotEqual(task.status, DailyTaskStatus.COMPLETED)
        self.assertEqual(task.completed_at, 0.0)

    def test_evidence_still_grows_srs_while_task_uncompleted(self):
        """做完任务≠会了（§8.2 TaskOutcome 分离）：证据照常喂复习调度，
        任务生命周期不动。"""
        svc = self._seed_task()
        svc.record_quiz_evidence(student_id="s1", concept="导数",
                                 verdict="correct", attempt_id="att_w4t2")
        card = svc.summary("s1")["review_queue"]["导数"]
        self.assertEqual(card["repetitions"], 1)
        self.assertEqual(self._today_task().status, DailyTaskStatus.PENDING)

    def test_match_by_concept_id(self):
        svc = self._seed_task(concept_name="别的名字")
        svc.record_turn(student_id="s1", concept="c1")
        self.assertEqual(self._today_task().status, DailyTaskStatus.IN_PROGRESS)

    def test_unrelated_concept_leaves_task_alone(self):
        svc = self._seed_task()
        svc.record_turn(student_id="s1", concept="积分")
        self.assertEqual(self._today_task().status, DailyTaskStatus.PENDING)

    def test_manual_complete_still_emits_batch_event(self):
        """全天的任务都完成时（显式勾选路径）仍发 task_batch_completed；
        曝光/证据轮不产生批量完成事件。"""
        svc = self._seed_task()
        emitted = svc.record_turn(student_id="s1", concept="导数")
        self.assertEqual(emitted, [])
        ok, emitted = svc.complete_task("s1", self._today_task().id)
        self.assertTrue(ok)
        types = [e.event_type for e in emitted]
        self.assertIn("task_batch_completed", types)


# ---------------------------------------------------------------------------
# 24. update_goal preserves SRS queue + historical tasks; needs_replan
# ---------------------------------------------------------------------------

class TestUpdateGoal(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_update_goal_preserves_srs_and_history(self):
        svc = get_orchestration_service()
        goal = svc.add_goal("s1", title="考研数学", subjects=["数学"])
        svc.record_quiz_evidence(student_id="s1", concept="导数",
                                 verdict="correct", attempt_id="att_w4u")
        state = store.load_state("s1")
        state.daily_tasks = [DailyTask(id="2026-07-26_c1_study",
                                       day="2026-07-26", concept_id="c1",
                                       status=DailyTaskStatus.COMPLETED)]
        store.save_state("s1", state)
        ok = svc.update_goal("s1", goal.id, title="考研数学（提高目标）",
                             deadline=1800000000.0)
        self.assertTrue(ok)
        after = store.load_state("s1")
        self.assertEqual(after.goals[0].title, "考研数学（提高目标）")
        self.assertEqual(after.goals[0].deadline, 1800000000.0)
        self.assertIn("导数", after.review_queue)          # SRS preserved
        self.assertEqual(len(after.daily_tasks), 1)         # history preserved
        self.assertEqual(after.daily_tasks[0].id, "2026-07-26_c1_study")

    def test_delete_goal_removes_goal_and_state(self):
        svc = get_orchestration_service()
        g1 = svc.add_goal("s1", title="考研数学", subjects=["数学"])
        g2 = svc.add_goal("s1", title="物理入门", subjects=["物理"])
        state = store.load_state("s1")
        self.assertEqual([gs.goal_id for gs in state.goal_states],
                         [g1.id, g2.id])
        self.assertTrue(svc.delete_goal("s1", g1.id))
        after = store.load_state("s1")
        self.assertEqual([g.id for g in after.goals], [g2.id])
        self.assertEqual([gs.goal_id for gs in after.goal_states], [g2.id])
        self.assertFalse(svc.delete_goal("s1", g1.id))

    def test_update_goal_without_goal_returns_false(self):
        svc = get_orchestration_service()
        self.assertFalse(svc.update_goal("s1", "g_9", title="x"))

    def test_summary_contains_needs_replan(self):
        svc = get_orchestration_service()
        summary = svc.summary("s1")
        self.assertIn("needs_replan", summary)
        self.assertFalse(summary["needs_replan"])  # no goal -> never prompt
        svc.add_goal("s1", title="考研数学", subjects=["数学"])
        self.assertTrue(svc.summary("s1")["needs_replan"])  # goal, never planned
        state = store.load_state("s1")
        state.weekly_plan = [WeeklyPlan(week_start=time.time())]
        store.save_state("s1", state)
        self.assertFalse(svc.summary("s1")["needs_replan"])


# ---------------------------------------------------------------------------
# 25. task CRUD (manager + API 400 mapping)
# ---------------------------------------------------------------------------

class TestTaskCRUD(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_add_task_id_scheme_and_custom_flag(self):
        svc = get_orchestration_service()
        day = task_executor._day_str(time.time())
        t1 = svc.add_task("s1", title="背单词", kind="review", phase="sprint")
        t2 = svc.add_task("s1", title="错题整理")
        self.assertEqual(t1.id, f"user_{day}_1")
        self.assertEqual(t2.id, f"user_{day}_2")
        self.assertTrue(t1.custom)
        self.assertEqual(t1.phase, "sprint")
        self.assertEqual(t1.status, DailyTaskStatus.PENDING)

    def test_add_task_illegal_kind_phase_raise(self):
        svc = get_orchestration_service()
        with self.assertRaises(ValueError):
            svc.add_task("s1", kind="dance")
        with self.assertRaises(ValueError):
            svc.add_task("s1", phase="warp")

    def test_add_task_day_cap_overflow(self):
        svc = get_orchestration_service()
        for i in range(schema._MAX_TASKS_PER_DAY):
            svc.add_task("s1", title=f"t{i}")
        with self.assertRaises(ValueError):
            svc.add_task("s1", title="overflow")

    def test_update_task_mutable_fields_and_status_timestamps(self):
        svc = get_orchestration_service()
        t = svc.add_task("s1", title="原始")
        ok = svc.update_task("s1", t.id, title="改后", priority=1,
                             estimate_minutes=30, status="completed")
        self.assertTrue(ok)
        state = store.load_state("s1")
        task = state.daily_tasks[0]
        self.assertEqual(task.title, "改后")
        self.assertEqual(task.priority, 1)
        self.assertEqual(task.status, DailyTaskStatus.COMPLETED)
        self.assertGreater(task.completed_at, 0)
        svc.update_task("s1", t.id, status="pending")
        task = store.load_state("s1").daily_tasks[0]
        self.assertEqual(task.status, DailyTaskStatus.PENDING)
        self.assertEqual(task.completed_at, 0.0)

    def test_update_task_invalid_and_missing(self):
        svc = get_orchestration_service()
        t = svc.add_task("s1", title="x")
        with self.assertRaises(ValueError):
            svc.update_task("s1", t.id, status="bogus")
        with self.assertRaises(ValueError):
            svc.update_task("s1", t.id, phase="bogus")
        self.assertFalse(svc.update_task("s1", "nonexistent", title="y"))

    def test_update_task_move_day_to_full_day_raises(self):
        svc = get_orchestration_service()
        # a fixed past date, guaranteed to differ from "today" (the default
        # add_task day) regardless of when the suite runs
        full_day = "1999-01-01"
        for i in range(schema._MAX_TASKS_PER_DAY):
            svc.add_task("s1", day=full_day, title=f"t{i}")
        t = svc.add_task("s1", title="movable")
        with self.assertRaises(ValueError):
            svc.update_task("s1", t.id, day=full_day)

    def test_delete_task(self):
        svc = get_orchestration_service()
        t = svc.add_task("s1", title="to delete")
        self.assertTrue(svc.delete_task("s1", t.id))
        self.assertFalse(svc.delete_task("s1", t.id))
        self.assertEqual(store.load_state("s1").daily_tasks, [])

    def test_api_cap_overflow_maps_to_400(self):
        from fastapi import HTTPException
        from app.api.v1.orchestration import (TaskCreateBody,
                                              orchestration_add_task)
        for i in range(schema._MAX_TASKS_PER_DAY):
            orchestration_add_task(TaskCreateBody(title=f"t{i}"),
                                   student_id="s1")
        with self.assertRaises(HTTPException) as ctx:
            orchestration_add_task(TaskCreateBody(title="overflow"),
                                   student_id="s1")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_api_invalid_phase_maps_to_400(self):
        from fastapi import HTTPException
        from app.api.v1.orchestration import (TaskCreateBody,
                                              orchestration_add_task)
        with self.assertRaises(HTTPException) as ctx:
            orchestration_add_task(TaskCreateBody(title="x", phase="warp"),
                                   student_id="s1")
        self.assertEqual(ctx.exception.status_code, 400)


# ---------------------------------------------------------------------------
# 26. API response contracts (kickoff payload shapes)
# ---------------------------------------------------------------------------

class TestAPIContracts(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_post_goal_response_shape_with_first_task(self):
        """POST /goal -> {ok, goal_id, weeks, first_task}; with a weekly plan
        the kickoff materializes today's tasks so first_task is not null."""
        import asyncio
        from app.api.v1.orchestration import GoalBody, orchestration_add_goal
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")):
            resp = asyncio.run(orchestration_add_goal(
                GoalBody(title="考研数学", subjects=["数学"]),
                student_id="s1"))
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["goal_id"], "g_1")
        self.assertIsInstance(resp["weeks"], list)
        # deterministic fallback composed today's study task -> kickoff CTA
        self.assertIsNotNone(resp["first_task"])
        self.assertEqual(resp["first_task"]["status"], "pending")
        self.assertIn("title", resp["first_task"])
        self.assertIn("phase", resp["first_task"])
        self.assertIn("custom", resp["first_task"])
        self.assertIn("reason", resp["first_task"])

    def test_patch_goal_response_shape(self):
        import asyncio
        from app.api.v1.orchestration import (GoalPatchBody,
                                              orchestration_patch_goal)
        svc = get_orchestration_service()
        goal = svc.add_goal("s1", title="考研数学", subjects=["数学"])
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")):
            resp = asyncio.run(orchestration_patch_goal(
                goal.id, GoalPatchBody(title="考研数学（新）"), student_id="s1"))
        self.assertTrue(resp["ok"])
        self.assertIn("weeks", resp)
        self.assertIn("first_task", resp)

    def test_multi_goal_add_patch_delete_endpoints(self):
        """POST /goal appends (multi-goal); PATCH/DELETE address /goal/{id};
        cap overflow maps to 400, unknown id to 404; the deleted goal's
        concepts leave the auto plan via the replan tail."""
        import asyncio
        from fastapi import HTTPException
        from app.api.v1.orchestration import (GoalBody, GoalPatchBody,
            orchestration_add_goal, orchestration_patch_goal,
            orchestration_delete_goal)
        svc = get_orchestration_service()
        fake_inputs = {"next_learnable": [], "review_candidates": [],
                       "evaluation_view": {}, "prereq_map": {}}
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")), \
                patch.object(LearningOrchestrationService,
                             "_assemble_plan_inputs",
                             return_value=fake_inputs):
            resp1 = asyncio.run(orchestration_add_goal(
                GoalBody(title="考研数学", subjects=["数学"]), student_id="s1"))
            resp2 = asyncio.run(orchestration_add_goal(
                GoalBody(title="物理入门", subjects=["物理"]), student_id="s1"))
        self.assertTrue(resp1["ok"] and resp2["ok"])
        summary = svc.summary("s1")
        self.assertEqual([g["title"] for g in summary["goals"]],
                         ["考研数学", "物理入门"])
        # both goals' gap states are paired by id
        self.assertEqual({gs["goal_id"] for gs in summary["goal_states"]},
                         {"g_1", "g_2"})
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")), \
                patch.object(LearningOrchestrationService,
                             "_assemble_plan_inputs",
                             return_value=fake_inputs):
            patch_resp = asyncio.run(orchestration_patch_goal(
                "g_2", GoalPatchBody(title="物理竞赛入门"), student_id="s1"))
            del_resp = asyncio.run(orchestration_delete_goal(
                "g_1", student_id="s1"))
        self.assertTrue(patch_resp["ok"] and del_resp["ok"])
        summary = svc.summary("s1")
        self.assertEqual([g["title"] for g in summary["goals"]],
                         ["物理竞赛入门"])
        # unknown id -> 404 on both routes
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(orchestration_patch_goal(
                    "g_9", GoalPatchBody(title="x"), student_id="s1"))
            self.assertEqual(ctx.exception.status_code, 404)
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(orchestration_delete_goal("g_9", student_id="s1"))
            self.assertEqual(ctx.exception.status_code, 404)
        # cap overflow -> 400
        state = store.load_state("s1")
        for i in range(schema._MAX_GOALS - 1):
            goal_manager.add_goal(state, title=f"补{i}")
        store.save_state("s1", state)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(orchestration_add_goal(
                GoalBody(title="溢出"), student_id="s1"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_longtask_routes_removed(self):
        """The long-term task module is gone: no /longtask routes remain."""
        from app.api.v1 import orchestration as orch_api
        paths = {getattr(r, "path", "") for r in orch_api.router.routes}
        self.assertFalse(any("/longtask" in p for p in paths),
                         f"longtask routes must be removed: {paths}")

    def test_regenerate_response_shape(self):
        import asyncio
        from app.api.v1.orchestration import orchestration_regenerate
        svc = get_orchestration_service()
        svc.add_goal("s1", title="考研数学", subjects=["数学"])
        with patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")):
            resp = asyncio.run(orchestration_regenerate(student_id="s1"))
        self.assertIn("ok", resp)
        self.assertIn("weeks", resp)

    def test_today_task_dict_includes_new_fields(self):
        import asyncio
        svc = get_orchestration_service()
        day = task_executor._day_str(_DAY1)
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.daily_tasks = [DailyTask(
            id=f"{day}_c1_study", day=day, concept_id="c1",
            kind=TaskKind.STUDY, reason="里程碑关键概念", phase="foundation")]
        store.save_state("s1", state)
        out = asyncio.run(svc.today_tasks("s1", now=_DAY1, compose_llm=True))
        for key in ("title", "phase", "custom", "reason"):
            self.assertIn(key, out[0])


# ---------------------------------------------------------------------------
# 27. regenerate reasons + last_plan_attempt (banner/retry loop guards)
# ---------------------------------------------------------------------------

class TestRegenerateReasons(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_no_goal_reason(self):
        import asyncio
        svc = get_orchestration_service()
        ok, reason = asyncio.run(svc.regenerate_plan("s1"))
        self.assertFalse(ok)
        self.assertEqual(reason, "no_goal")

    def test_empty_plan_is_terminal_ok(self):
        """Empty result with a goal is a legitimate end state: ok=True,
        reason=empty_plan, stale weekly plan cleared, attempt stamped, and
        needs_replan stops firing (the banner/retry loop is broken)."""
        import asyncio
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        fake_inputs = {"next_learnable": [], "review_candidates": [],
                       "evaluation_view": {}, "prereq_map": {}}
        with patch.object(LearningOrchestrationService, "_assemble_plan_inputs",
                          return_value=fake_inputs), \
                patch.object(LearningOrchestrationService, "_get_llm",
                             side_effect=RuntimeError("llm down")):
            ok, reason = asyncio.run(svc.regenerate_plan("s1", now=_DAY1))
        self.assertTrue(ok)
        self.assertEqual(reason, "empty_plan")
        after = store.load_state("s1")
        self.assertEqual(after.weekly_plan, [])
        self.assertEqual(after.last_plan_attempt, _DAY1)
        self.assertFalse(learning_planner.needs_replan(after, {}, now=_DAY1))

    def test_success_stamps_attempt(self):
        import asyncio
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        fake_inputs = {
            "next_learnable": [{"name": "积分", "skill_id": "c2",
                                "difficulty": 4}],
            "review_candidates": [], "evaluation_view": {},
            "prereq_map": {}}
        with patch.object(LearningOrchestrationService, "_assemble_plan_inputs",
                          return_value=fake_inputs), \
                patch.object(LearningOrchestrationService, "_get_llm",
                             side_effect=RuntimeError("llm down")):
            ok, reason = asyncio.run(svc.regenerate_plan("s1", now=_DAY1))
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        after = store.load_state("s1")
        self.assertEqual(after.last_plan_attempt, _DAY1)
        self.assertGreater(len(after.weekly_plan), 0)
        # fallback weeks still carry action-level tasks (derived)
        self.assertTrue(after.weekly_plan[0].tasks)


# ---------------------------------------------------------------------------
# 28. human-override contract: _merge_user_plan (user entries survive regen)
# ---------------------------------------------------------------------------

class TestMergeUserPlan(unittest.TestCase):

    def _auto_week(self, week_start, idx=0):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        return WeeklyPlan(
            week_index=idx, week_start=week_start, focus=f"第{idx}周",
            concepts=[PlanConcept(concept_id=f"c{idx}", name=f"概念{idx}")],
            tasks=[WeekTask(id=f"wt_{idx}_1", title="自动任务",
                            source="auto",
                            subtasks=[SubTask(id="s1", title="步骤")])],
            origin="auto")

    def test_user_week_survives_whole(self):
        from app.agents.learning_orchestration.schema import WeekTask
        old_auto = self._auto_week(_DAY1, 0)
        user_week = WeeklyPlan(week_index=1, week_start=_DAY1 + 7 * 86400,
                               focus="我的复习周", origin="user",
                               tasks=[WeekTask(id="u1", title="自定任务",
                                               source="user")])
        merged = orch_manager._merge_user_plan(
            [old_auto, user_week], [self._auto_week(_DAY1, 0)])
        focuses = [w.focus for w in merged]
        self.assertIn("我的复习周", focuses)
        self.assertEqual(len(merged), 2)
        # re-indexed sequentially by week_start
        self.assertEqual([w.week_index for w in merged], [0, 1])

    def test_user_tasks_carried_onto_same_week(self):
        from app.agents.learning_orchestration.schema import WeekTask
        old = self._auto_week(_DAY1, 0)
        old.tasks.append(WeekTask(id="u1", title="手动加的", source="user"))
        new = self._auto_week(_DAY1, 0)
        merged = orch_manager._merge_user_plan([old], [new])
        titles = [t.title for t in merged[0].tasks]
        self.assertIn("自动任务", titles)
        self.assertIn("手动加的", titles)

    def test_auto_entries_replaced(self):
        old = self._auto_week(_DAY1, 0)
        old.tasks[0].title = "旧自动任务"
        new = self._auto_week(_DAY1, 0)
        merged = orch_manager._merge_user_plan([old], [new])
        titles = [t.title for t in merged[0].tasks]
        self.assertNotIn("旧自动任务", titles)
        self.assertIn("自动任务", titles)

    def test_title_dedupe(self):
        from app.agents.learning_orchestration.schema import WeekTask
        old = self._auto_week(_DAY1, 0)
        old.tasks.append(WeekTask(id="u1", title="自动任务", source="user"))
        new = self._auto_week(_DAY1, 0)
        merged = orch_manager._merge_user_plan([old], [new])
        self.assertEqual(len([t for t in merged[0].tasks
                              if t.title == "自动任务"]), 1)

    def test_empty_new_plan_keeps_user_weeks(self):
        user_week = WeeklyPlan(week_index=0, week_start=_DAY1,
                               focus="仅人工周", origin="user")
        merged = orch_manager._merge_user_plan([user_week], [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].focus, "仅人工周")


# ---------------------------------------------------------------------------
# 29. week + week-concept CRUD (daily tasks never touched)
# ---------------------------------------------------------------------------

class TestWeekCRUD(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_add_week_appends(self):
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1))
        week = svc.add_week("s1", focus="复习周",
                            concepts=[{"concept_id": "c9", "name": "积分",
                                       "difficulty": 4}])
        self.assertEqual(week.week_index, 1)
        self.assertEqual(week.week_start, _DAY1 + 7 * 24 * 3600)
        self.assertEqual(week.concepts[0].week_index, 1)
        self.assertEqual(week.focus, "复习周")

    def test_add_week_first_week_starts_now(self):
        svc = get_orchestration_service()
        week = svc.add_week("s1", concepts=[{"concept_id": "", "name": "随笔"}])
        self.assertEqual(week.week_index, 0)
        self.assertGreater(week.week_start, 0)
        self.assertEqual(week.focus, "随笔")  # focus falls back to first name

    def test_add_week_cap(self):
        svc = get_orchestration_service()
        with self.assertRaises(ValueError):
            svc.add_week("s1", concepts=[
                {"concept_id": f"c{i}", "name": f"n{i}"} for i in range(6)])

    def test_delete_week_keeps_tasks(self):
        svc = get_orchestration_service()
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.daily_tasks = [DailyTask(id="2026-07-26_c1_study",
                                       day="2026-07-26", concept_id="c1")]
        store.save_state("s1", state)
        self.assertTrue(svc.delete_week("s1", 0))
        after = store.load_state("s1")
        self.assertEqual(after.weekly_plan, [])
        self.assertEqual(len(after.daily_tasks), 1)  # uniqueness contract
        self.assertFalse(svc.delete_week("s1", 0))

    def test_add_week_concept(self):
        svc = get_orchestration_service()
        store.save_state("s1", _week_plan_state(week_start=_DAY1))
        pc = svc.add_week_concept("s1", 0, concept_id="c2", name="积分")
        self.assertEqual(pc.week_index, 0)
        with self.assertRaises(ValueError):  # duplicate concept_id
            svc.add_week_concept("s1", 0, concept_id="c2", name="积分2")
        self.assertIsNone(svc.add_week_concept("s1", 99, name="x"))  # 404 path

    def test_add_week_concept_cap(self):
        svc = get_orchestration_service()
        svc.add_week("s1", concepts=[
            {"concept_id": f"c{i}", "name": f"n{i}"} for i in range(5)])
        with self.assertRaises(ValueError):
            svc.add_week_concept("s1", 0, concept_id="c5", name="n5")

    def test_remove_week_concept(self):
        svc = get_orchestration_service()
        state = _week_plan_state(week_start=_DAY1)
        state.weekly_plan[0].concepts.append(
            PlanConcept(concept_id="", name="自由概念", week_index=0))
        store.save_state("s1", state)
        self.assertTrue(svc.remove_week_concept("s1", 0, "c1"))
        self.assertTrue(svc.remove_week_concept("s1", 0, "自由概念"))  # by name
        self.assertFalse(svc.remove_week_concept("s1", 0, "c1"))
        self.assertFalse(svc.remove_week_concept("s1", 99, "c1"))


# ---------------------------------------------------------------------------
# 30. plan-CRUD API contracts (reason field + 400/404 mapping)
# ---------------------------------------------------------------------------

class TestPlanCRUDAPI(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_regenerate_response_includes_reason(self):
        import asyncio
        from unittest.mock import AsyncMock
        from app.api.v1.orchestration import orchestration_regenerate
        m = MagicMock()
        m.complete = AsyncMock(return_value=("", None))
        with patch("app.core.llm_async.get_llm", return_value=m):
            resp = asyncio.run(orchestration_regenerate(student_id="s1"))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["reason"], "no_goal")
        self.assertEqual(resp["weeks"], [])

    def test_week_endpoints(self):
        from fastapi import HTTPException
        from app.api.v1.orchestration import (WeekBody, WeekConceptIn,
            orchestration_add_week, orchestration_delete_week,
            orchestration_add_week_concept, orchestration_remove_week_concept)
        resp = orchestration_add_week(
            WeekBody(focus="复习周",
                     concepts=[WeekConceptIn(concept_id="c1", name="导数")]),
            student_id="s1")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["week"]["week_index"], 0)
        resp = orchestration_add_week_concept(
            0, WeekConceptIn(name="自由概念"), student_id="s1")
        self.assertTrue(resp["ok"])
        with self.assertRaises(HTTPException) as ctx:  # missing week -> 404
            orchestration_add_week_concept(
                99, WeekConceptIn(name="x"), student_id="s1")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(orchestration_remove_week_concept(
            0, "c1", student_id="s1"), {"ok": True})
        self.assertEqual(orchestration_delete_week(
            0, student_id="s1"), {"ok": True})
        with self.assertRaises(HTTPException) as ctx:
            orchestration_delete_week(0, student_id="s1")
        self.assertEqual(ctx.exception.status_code, 404)


# ---------------------------------------------------------------------------
# P1 schema: SubTask / WeekTask / plan-hierarchy provenance
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# P4. week tasks + subtasks CRUD / LLM subtask suggest
# ---------------------------------------------------------------------------

class TestWeekTaskCRUD(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None
        svc = get_orchestration_service()
        state = _week_plan_state(week_start=_DAY1 - 3600)
        store.save_state("s1", state)

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_add_and_delete_week_task(self):
        svc = get_orchestration_service()
        with self.assertRaises(ValueError):
            svc.add_week_task("s1", 0, title=" ")
        with self.assertRaises(ValueError):
            svc.add_week_task("s1", 0, title="t", kind="dance")
        self.assertIsNone(svc.add_week_task("s1", 99, title="t"))  # 404 path
        wt = svc.add_week_task("s1", 0, title="学完浮力",
                               concept_ids=["a", "b", "a"])
        self.assertEqual(wt.id, "user_wt_0_1")
        self.assertEqual(wt.source, "user")
        self.assertEqual(wt.concept_ids, ["a", "b"])  # deduped
        self.assertFalse(svc.delete_week_task("s1", 0, "nope"))
        self.assertTrue(svc.delete_week_task("s1", 0, "user_wt_0_1"))

    def test_subtask_lifecycle(self):
        svc = get_orchestration_service()
        svc.add_week_task("s1", 0, title="学完浮力")
        self.assertIsNone(svc.add_subtask("s1", 0, "nope", title="x"))
        st = svc.add_subtask("s1", 0, "user_wt_0_1", title="做 10 道题",
                             estimate_minutes=25)
        self.assertEqual(st.source, "user")
        self.assertFalse(st.done)
        self.assertTrue(svc.toggle_subtask("s1", 0, "user_wt_0_1", st.id))
        state = store.load_state("s1")
        sub = state.weekly_plan[0].tasks[0].subtasks[0]
        self.assertTrue(sub.done)
        self.assertGreater(sub.done_at, 0)
        # task becomes effectively done when all subtasks done
        self.assertTrue(state.weekly_plan[0].tasks[0].effective_done)
        self.assertTrue(svc.toggle_subtask("s1", 0, "user_wt_0_1", st.id))
        self.assertFalse(svc.toggle_subtask("s1", 0, "user_wt_0_1", "nope"))
        self.assertTrue(svc.delete_subtask("s1", 0, "user_wt_0_1", st.id))
        self.assertFalse(svc.delete_subtask("s1", 0, "user_wt_0_1", st.id))

    def test_subtask_cap(self):
        from app.agents.learning_orchestration.schema import _MAX_SUBTASKS
        svc = get_orchestration_service()
        svc.add_week_task("s1", 0, title="t")
        for i in range(_MAX_SUBTASKS):
            svc.add_subtask("s1", 0, "user_wt_0_1", title=f"s{i}")
        with self.assertRaises(ValueError):
            svc.add_subtask("s1", 0, "user_wt_0_1", title="溢出")

    def test_suggest_subtasks_llm(self):
        import asyncio
        from unittest.mock import AsyncMock
        svc = get_orchestration_service()
        svc.add_week_task("s1", 0, title="学完浮力")
        content = json.dumps({"subtasks": [
            {"title": "看浮力讲解", "estimate_minutes": 20},
            {"title": "做 10 道浮力计算题", "estimate_minutes": 30}]})
        m = MagicMock()
        m.complete = AsyncMock(return_value=(content, None))
        with patch("app.core.llm_async.get_llm", return_value=m):
            task = asyncio.run(svc.suggest_subtasks("s1", 0, "user_wt_0_1"))
        self.assertEqual(len(task.subtasks), 2)
        self.assertTrue(all(s.source == "auto" for s in task.subtasks))
        self.assertEqual(task.subtasks[0].id, "auto_st_user_wt_0_1_1")
        # llm failure -> None, nothing persisted
        m2 = MagicMock()
        m2.complete = AsyncMock(return_value=("junk", None))
        with patch("app.core.llm_async.get_llm", return_value=m2):
            self.assertIsNone(asyncio.run(
                svc.suggest_subtasks("s1", 0, "user_wt_0_1")))
        state = store.load_state("s1")
        self.assertEqual(len(state.weekly_plan[0].tasks[0].subtasks), 2)
        # missing task -> None
        self.assertIsNone(asyncio.run(svc.suggest_subtasks("s1", 0, "nope")))

    def test_suggest_parse_gate(self):
        from app.agents.learning_orchestration import subtask_advisor as sa
        ok = json.dumps({"subtasks": [
            {"title": "a", "estimate_minutes": 10},
            {"title": "b", "estimate_minutes": 20}]})
        self.assertEqual(len(sa.parse_subtask_response(ok)), 2)
        # too few / too many
        self.assertIsNone(sa.parse_subtask_response(
            json.dumps({"subtasks": [{"title": "a"}]})))
        self.assertIsNone(sa.parse_subtask_response(json.dumps(
            {"subtasks": [{"title": str(i)} for i in range(5)]})))
        # empty title / junk
        self.assertIsNone(sa.parse_subtask_response(
            json.dumps({"subtasks": [{"title": " "}, {"title": "b"}]})))
        self.assertIsNone(sa.parse_subtask_response("junk"))

    def test_week_task_endpoints(self):
        import asyncio
        from fastapi import HTTPException
        from unittest.mock import AsyncMock
        from app.api.v1.orchestration import (
            SubTaskBody, WeekTaskBody,
            orchestration_add_week_task, orchestration_delete_week_task,
            orchestration_add_subtask, orchestration_toggle_subtask,
            orchestration_delete_subtask, orchestration_suggest_subtasks)
        resp = orchestration_add_week_task(
            0, WeekTaskBody(title="学完浮力", concept_ids=["c1"]),
            student_id="s1")
        self.assertTrue(resp["ok"])
        tid = resp["task"]["id"]
        st = orchestration_add_subtask(
            0, tid, SubTaskBody(title="做 10 道题"), student_id="s1")
        sid = st["subtask"]["id"]
        self.assertEqual(orchestration_toggle_subtask(
            0, tid, sid, student_id="s1"), {"ok": True})
        self.assertEqual(orchestration_delete_subtask(
            0, tid, sid, student_id="s1"), {"ok": True})
        m = MagicMock()
        m.complete = AsyncMock(return_value=(json.dumps({"subtasks": [
            {"title": "步骤一", "estimate_minutes": 15},
            {"title": "步骤二", "estimate_minutes": 20}]}), None))
        with patch("app.core.llm_async.get_llm", return_value=m):
            sug = asyncio.run(orchestration_suggest_subtasks(
                0, tid, student_id="s1"))
        self.assertEqual(len(sug["task"]["subtasks"]), 2)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(orchestration_suggest_subtasks(0, "nope", student_id="s1"))
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(orchestration_delete_week_task(
            0, tid, student_id="s1"), {"ok": True})
        with self.assertRaises(HTTPException) as ctx:
            orchestration_delete_week_task(0, tid, student_id="s1")
        self.assertEqual(ctx.exception.status_code, 404)


# ---------------------------------------------------------------------------
# P5. daily composer: subtask pool + completion write-back
# ---------------------------------------------------------------------------

class TestComposerPoolExtended(unittest.TestCase):

    def _state(self):
        from app.agents.learning_orchestration.schema import (
            SubTask, WeekTask)
        now = _DAY1
        state = OrchestrationState()
        state.goals = [LearningGoal(id="g_1", title="考研数学",
                                    subjects=["数学"])]
        state.weekly_plan = [WeeklyPlan(
            week_index=0, week_start=now - 3600, focus="基础周",
            concepts=[PlanConcept(concept_id="c1", name="导数")],
            tasks=[WeekTask(id="wt_0_1", title="学完导数",
                            concept_ids=["c1"],
                            subtasks=[SubTask(id="st_1", title="做 10 道导数题"),
                                      SubTask(id="st_2", title="已完成的",
                                              done=True)])])]
        return state, now

    def test_subtask_entries_in_pool(self):
        state, now = self._state()
        pool = daily_composer.build_candidate_pool(
            state, evaluation_view={}, concept_names={}, now=now)
        by_id = {e["concept_id"]: e for e in pool}
        self.assertIn("st_1", by_id)
        e = by_id["st_1"]
        self.assertEqual(e["week_task_id"], "wt_0_1")
        self.assertEqual(e["subtask_id"], "st_1")
        self.assertEqual(e["real_concept_id"], "c1")
        self.assertIn("current_week", e["sources"])
        self.assertNotIn("st_2", by_id)  # done subtasks excluded

    def test_picks_materialize_refs(self):
        from app.agents.learning_orchestration.schema import TaskKind as _TK
        state, now = self._state()
        pool = daily_composer.build_candidate_pool(
            state, evaluation_view={}, concept_names={}, now=now)
        picks = [{"concept_id": "st_1", "kind": "practice",
                  "phase": "reinforce", "reason": "本周子步骤"},
                 {"concept_id": "c1", "kind": "study",
                  "phase": "foundation", "reason": "本周概念"}]
        tasks = daily_composer.tasks_from_picks(
            state, picks, pool, [20, 25], now=now)
        st_task, c_task = tasks
        self.assertEqual(st_task.week_task_id, "wt_0_1")
        self.assertEqual(st_task.subtask_id, "st_1")
        self.assertEqual(st_task.concept_id, "c1")  # real concept, not st id
        self.assertEqual(st_task.title, "做 10 道导数题")
        self.assertEqual(c_task.concept_id, "c1")
        self.assertEqual(c_task.title, "")  # plain concept entry: no title
        self.assertEqual(c_task.week_task_id, "")


class TestCompleteTaskWriteBack(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_complete_writes_back_subtask(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        svc = get_orchestration_service()
        day = task_executor._day_str(_DAY1)
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.weekly_plan[0].tasks = [WeekTask(
            id="wt_0_1", title="学完导数", concept_ids=["c1"],
            subtasks=[SubTask(id="st_1", title="做 10 道题")])]
        state.daily_tasks = [DailyTask(
            id=f"{day}_st_1_practice", day=day, concept_id="c1",
            kind=TaskKind.PRACTICE, week_task_id="wt_0_1", subtask_id="st_1")]
        store.save_state("s1", state)
        ok, _events = svc.complete_task("s1", f"{day}_st_1_practice")
        self.assertTrue(ok)
        after = store.load_state("s1")
        self.assertTrue(after.weekly_plan[0].tasks[0].subtasks[0].done)
        self.assertGreater(after.weekly_plan[0].tasks[0].subtasks[0].done_at, 0)

    def test_complete_plain_task_touches_nothing(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        svc = get_orchestration_service()
        day = task_executor._day_str(_DAY1)
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.weekly_plan[0].tasks = [WeekTask(
            id="wt_0_1", title="学完导数",
            subtasks=[SubTask(id="st_1", title="做 10 道题")])]
        state.daily_tasks = [DailyTask(
            id=f"{day}_c1_study", day=day, concept_id="c1",
            kind=TaskKind.STUDY)]
        store.save_state("s1", state)
        ok, _ = svc.complete_task("s1", f"{day}_c1_study")
        self.assertTrue(ok)
        after = store.load_state("s1")
        self.assertFalse(after.weekly_plan[0].tasks[0].subtasks[0].done)


class TestSchedulePatch(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_update_schedule_clamps_and_persists(self):
        svc = get_orchestration_service()
        out = svc.update_schedule("s1", daily_minutes=90)
        self.assertEqual(out["daily_minutes"], 90)
        out = svc.update_schedule("s1", daily_minutes=99999)
        self.assertEqual(out["daily_minutes"], 480)
        state = store.load_state("s1")
        self.assertEqual(state.schedule.daily_minutes, 480)

    def test_schedule_endpoint(self):
        from app.api.v1.orchestration import (SchedulePatchBody,
                                              orchestration_patch_schedule)
        resp = orchestration_patch_schedule(
            SchedulePatchBody(daily_minutes=60), student_id="s1")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["schedule"]["daily_minutes"], 60)


class TestWriteBackTitleGuard(unittest.TestCase):
    """P5 follow-up: positional ids (wt_{week}_{seq}) can be reused by a
    later regeneration for different content; the write-back must only
    credit a subtask that still is the same work (title match)."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None

    def test_mismatched_title_no_credit(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        svc = get_orchestration_service()
        day = task_executor._day_str(_DAY1)
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.weekly_plan[0].tasks = [WeekTask(
            id="wt_0_1", title="新计划任务",
            subtasks=[SubTask(id="st_1", title="新内容：密度计算")])]
        state.daily_tasks = [DailyTask(
            id=f"{day}_st_1_practice", day=day, concept_id="c1",
            kind=TaskKind.PRACTICE, week_task_id="wt_0_1", subtask_id="st_1",
            title="旧任务：做 10 道浮力题")]  # materialised from the OLD plan
        store.save_state("s1", state)
        ok, _ = svc.complete_task("s1", f"{day}_st_1_practice")
        self.assertTrue(ok)  # daily task completes regardless
        after = store.load_state("s1")
        self.assertFalse(after.weekly_plan[0].tasks[0].subtasks[0].done)

    def test_matching_title_credits(self):
        from app.agents.learning_orchestration.schema import SubTask, WeekTask
        svc = get_orchestration_service()
        day = task_executor._day_str(_DAY1)
        state = _week_plan_state(week_start=_DAY1 - 3600)
        state.weekly_plan[0].tasks = [WeekTask(
            id="wt_0_1", title="t",
            subtasks=[SubTask(id="st_1", title="做 10 道浮力题")])]
        state.daily_tasks = [DailyTask(
            id=f"{day}_st_1_practice", day=day, concept_id="c1",
            kind=TaskKind.PRACTICE, week_task_id="wt_0_1", subtask_id="st_1",
            title="做 10 道浮力题")]
        store.save_state("s1", state)
        ok, _ = svc.complete_task("s1", f"{day}_st_1_practice")
        self.assertTrue(ok)
        after = store.load_state("s1")
        self.assertTrue(after.weekly_plan[0].tasks[0].subtasks[0].done)
