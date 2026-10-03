"""M9 manager runtime: events, task writeback, schedule materialization."""
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
