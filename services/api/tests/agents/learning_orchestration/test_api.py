"""M9 orchestration ASGI route contracts and CRUD endpoints."""
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
class TestAPIContracts(StorageSandboxTestCase):

    def setUp(self):
        super().setUp()
        orch_manager._SERVICE = None

    def tearDown(self):
        orch_manager._SERVICE = None
        super().tearDown()

    def test_post_goal_response_shape_with_first_task(self):
        """POST /goal -> {ok, goal_id, weeks, first_task}; with a weekly plan
        the kickoff materializes today's tasks so first_task is not null."""
        import asyncio
        from unittest.mock import AsyncMock
        from app.api.v1.orchestration import GoalBody, orchestration_add_goal
        store.save_state("s1", _week_plan_state(week_start=_DAY1 - 3600))
        with patch("time.time", return_value=_DAY1), \
                patch.object(LearningOrchestrationService, "regenerate_plan",
                             new_callable=AsyncMock, return_value=(True, "")) as replan, \
                patch.object(LearningOrchestrationService, "_get_llm",
                          side_effect=RuntimeError("llm down")):
            resp = asyncio.run(orchestration_add_goal(
                GoalBody(title="考研数学", subjects=["数学"]),
                student_id="s1"))
        # Plan generation has its own suite; this route test supplies a plan
        # and exercises real task materialization and the kickoff response.
        replan.assert_awaited_once_with("s1")
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
class TestRegenerateReasons(unittest.TestCase):

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self.tmp = _temp_students_dir()
        store._STUDENTS_DIR = self.tmp
        orch_manager._SERVICE = None

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        orch_manager._SERVICE = None


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
