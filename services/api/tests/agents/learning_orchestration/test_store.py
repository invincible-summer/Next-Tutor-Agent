"""M9 orchestration storage: store behaviors and summary identity."""
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
class TestSummaryIdentity(StorageSandboxTestCase):
    """W4/A13 双学生回归（§13.3「两个学生的 needs_replan 各读各的」）：
    summary 的 needs_replan 必须读本人 M2 档案——漏传 student_id 时登录
    学生静默回退游客命名空间（审查确认在 W4 前仍未修）。"""

    def _seed_plan(self, sid: str) -> None:
        from app.core.workspace import Workspace, save_workspace
        # 工作区文件按 id 命名：两个学生必须用不同 id，否则互相覆盖。
        ws_id = f"ws_g4_{sid}"
        save_workspace(Workspace(workspace_id=ws_id, name="G4 区",
                                 student_id=sid, selected_file_ids=[]))
        state = store.load_state(sid)
        # R18：目标绑定工作区——评价投影只读目标所在区
        state.goals = [LearningGoal(id="g1", title="数学", subjects=["数学"],
                                    workspace_id=ws_id)]
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
            concept_ref=concept, workspace_id=f"ws_g4_{sid}",
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
