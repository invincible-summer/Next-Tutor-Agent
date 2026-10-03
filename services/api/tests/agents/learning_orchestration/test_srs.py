"""M9 spaced repetition (SM-2) and habit tracking."""
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
