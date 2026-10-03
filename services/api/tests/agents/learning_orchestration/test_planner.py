"""M9 goal analysis, weekly planning, and daily composition (LLM paths)."""
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

    def test_level_mapping_removed(self):
        """R18：支持占比 → 五档能力的映射必须删除（API/UI/prompt/存储都
        不得再产生能力档位；supported_ratio 只保留覆盖计数语义）。"""
        import app.agents.learning_orchestration.schema as orch_schema
        self.assertFalse(hasattr(orch_schema, "GoalAnalysisLevel"))
        import inspect
        self.assertNotIn("current_level",
                         inspect.signature(
                             orch_schema.GoalState.__init__).parameters)
        gs = orch_schema.GoalState(goal_id="g", supported_ratio=0.9)
        self.assertNotIn("current_level", gs.to_dict())
        self.assertNotIn("target_level", gs.to_dict())

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
