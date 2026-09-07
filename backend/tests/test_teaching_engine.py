"""Unit tests for the Adaptive Teaching Engine (module 3, Phase 1).

Covers: TeachingMode state machine (mastery bands + cross-turn advancement +
misconception/remediation override), teaching_log round-trip + cross-turn
advancement, policy composition (recipes/depth/exercise level/next_check),
and the StudentModel.adapt() delegation producing a strategy with a mode.
"""
import os
import unittest

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.teaching_engine import (TeachingContext, TeachingManager,
                                        TeachingMode, TeachingOutcome,
                                        adapt_from_context,
                                        get_teaching_manager,
                                        load_teaching_log,
                                        previous_mode_for,
                                        record_turn_outcome)
from app.agents.teaching_engine.teaching_log import _resolve


def _ctx(**kw) -> TeachingContext:
    base = dict(concept="积分", task_type="explain", mastery=0.0)
    base.update(kw)
    return TeachingContext(**base)


class TestStrategySelection(unittest.TestCase):
    def test_first_touch_low_mastery_is_introduction(self):
        self.assertEqual(select(_ctx(mastery=0.1, turns_on_concept=0)),
                         TeachingMode.INTRODUCTION)

    def test_progressing_band_is_explanation(self):
        self.assertEqual(select(_ctx(mastery=0.45)), TeachingMode.EXPLANATION)

    def test_solid_band_is_practice(self):
        self.assertEqual(select(_ctx(mastery=0.7)), TeachingMode.PRACTICE)

    def test_strong_band_is_challenge(self):
        self.assertEqual(select(_ctx(mastery=0.9)), TeachingMode.CHALLENGE)

    def test_misconception_forces_remediation_regardless_of_mastery(self):
        # a student who "knows" it (0.85) but has a confirmed wrong idea still
        # gets REMEDIATION -- correcting the root cause beats advancing.
        ctx = _ctx(mastery=0.85, misconceptions=["把积分当函数值"])
        self.assertEqual(select(ctx), TeachingMode.REMEDIATION)

    def test_unmet_prereq_plus_novice_is_remediation(self):
        ctx = _ctx(mastery=0.1, unmet_prereq_names=["极限"])
        self.assertEqual(select(ctx), TeachingMode.REMEDIATION)

    def test_unmet_prereq_with_progressing_does_not_remediate(self):
        # a progressing student (0.45) with an unmet prereq is NOT forced into
        # remediation; they get a quick inline recap (policy's review_first).
        ctx = _ctx(mastery=0.45, unmet_prereq_names=["极限"])
        self.assertEqual(select(ctx), TeachingMode.EXPLANATION)

    def test_review_intent_pins_review_mode(self):
        self.assertEqual(select(_ctx(mastery=0.5, task_type="review")),
                         TeachingMode.REVIEW)

    def test_practice_intent_pins_practice(self):
        self.assertEqual(select(_ctx(mastery=0.5, task_type="practice")),
                         TeachingMode.PRACTICE)

    def test_cross_turn_advancement_on_clean_correct(self):
        # previous EXPLANATION ended CORRECT -> bump up to PRACTICE even if
        # mastery band alone would still say EXPLANATION (0.45).
        ctx = _ctx(mastery=0.45, previous_mode=TeachingMode.EXPLANATION.value,
                   previous_outcome=TeachingOutcome.CORRECT)
        self.assertEqual(select(ctx), TeachingMode.PRACTICE)

    def test_no_advancement_on_wrong_outcome(self):
        ctx = _ctx(mastery=0.45, previous_mode=TeachingMode.EXPLANATION.value,
                   previous_outcome=TeachingOutcome.WRONG)
        self.assertEqual(select(ctx), TeachingMode.EXPLANATION)

    def test_advancement_capped_at_challenge(self):
        ctx = _ctx(mastery=0.9, previous_mode=TeachingMode.CHALLENGE.value,
                   previous_outcome=TeachingOutcome.CORRECT)
        self.assertEqual(select(ctx), TeachingMode.CHALLENGE)


def select(ctx: TeachingContext) -> TeachingMode:
    from app.agents.teaching_engine import select_strategy
    return select_strategy(ctx)


class TestPolicyComposition(unittest.TestCase):
    def test_introduction_recipe_no_formula_piling(self):
        # 小学/初中首次接触：直觉优先、不堆公式（通用配方不变）
        s = adapt_from_context(_ctx(mastery=0.1, turns_on_concept=0, grade="初中"))
        self.assertEqual(s.mode, TeachingMode.INTRODUCTION)
        self.assertEqual(s.depth, "basic")
        self.assertTrue(s.examples_needed)
        self.assertIn("不要堆砌公式", s.avoid)
        self.assertEqual(s.exercise_level, "easy")

    def test_introduction_recipe_grade_calibrated_for_high_school(self):
        # 高中/本科首次接触：直觉切入但定义与推导必须讲透，depth 不再 basic；
        # 练习难度也不再从课本例题档起步（学段地板：无作答证据时 easy->medium）
        s = adapt_from_context(_ctx(mastery=0.1, turns_on_concept=0, grade="高中"))
        self.assertEqual(s.mode, TeachingMode.INTRODUCTION)
        self.assertEqual(s.depth, "adaptive")
        self.assertTrue(any("完整定义" in f for f in s.focus), s.focus)
        self.assertNotIn("不要引入严格定义与边界情形", s.avoid)
        self.assertEqual(s.exercise_level, "medium")
        self.assertEqual(s.next_check.difficulty, 3)

    def test_grade_floor_yields_to_assessed_evidence(self):
        # 学段地板只垫「没有作答证据」的新概念：连错时拨盘全权，仍降回 easy
        from app.agents.teaching_engine.policy import compose
        ctx = _ctx(mastery=0.4, grade="高中")
        strat = compose(ctx, TeachingMode.PRACTICE,
                        recent_outcomes=["wrong", "wrong", "wrong"])
        self.assertEqual(strat.exercise_level, "easy")
        # 小学/初中无地板：新概念仍从 easy 起步
        s_primary = adapt_from_context(_ctx(mastery=0.1, turns_on_concept=0,
                                            grade="小学"))
        self.assertEqual(s_primary.exercise_level, "easy")

    def test_challenge_recipe_deep_and_hard(self):
        s = adapt_from_context(_ctx(mastery=0.9))
        self.assertEqual(s.mode, TeachingMode.CHALLENGE)
        self.assertEqual(s.depth, "deep")
        self.assertEqual(s.exercise_level, "hard")
        self.assertEqual(s.next_check.difficulty, 4)

    def test_remediation_recipe_targets_root_cause(self):
        s = adapt_from_context(_ctx(mastery=0.85,
                                    misconceptions=["混淆积分与函数"]))
        self.assertEqual(s.mode, TeachingMode.REMEDIATION)
        # list membership would need an exact element; check substring instead
        self.assertTrue(any("先定位错在哪一步" in f for f in s.focus), s.focus)
        self.assertTrue(any("不要只给正确答案" in a for a in s.avoid), s.avoid)

    def test_style_depth_override_wins_over_mode_recipe(self):
        ctx = _ctx(mastery=0.1, turns_on_concept=0,
                   learning_style={"explanation_depth": "deep"})
        s = adapt_from_context(ctx)
        # mode is still INTRODUCTION but depth honors the student's preference
        self.assertEqual(s.mode, TeachingMode.INTRODUCTION)
        self.assertEqual(s.depth, "deep")

    def test_legacy_fields_populated_for_backcompat(self):
        # the V3 fields must still be filled so existing callers/tests work
        s = adapt_from_context(_ctx(mastery=0.45, mistakes=["漏单位"]))
        self.assertEqual(s.explanation_depth, s.depth)
        self.assertEqual(s.suggested_quiz_difficulty, s.exercise_level)
        self.assertEqual(s.recent_mistakes, ["漏单位"])
        self.assertTrue(s.plan_hints)


class TestTeachingLog(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.sid = "student_te_log_" + os.urandom(3).hex()

    def test_record_and_read_back(self):
        record_turn_outcome(self.sid, "math.calc.integral",
                            mode="introduction", outcome="engaged", note="讲直觉")
        log = load_teaching_log(self.sid)
        entries = log.get("math.calc.integral")
        self.assertIsNotNone(entries)
        self.assertEqual(entries[0].mode, "introduction")
        self.assertEqual(entries[0].outcome, "engaged")
        self.assertEqual(entries[0].note, "讲直觉")

    def test_previous_mode_for_returns_last(self):
        record_turn_outcome(self.sid, "c1", mode="introduction", outcome="engaged")
        record_turn_outcome(self.sid, "c1", mode="explanation", outcome="correct")
        mode, outcome, turns = previous_mode_for(self.sid, "c1")
        self.assertEqual(mode, "explanation")
        self.assertEqual(outcome, TeachingOutcome.CORRECT)
        self.assertEqual(turns, 2)

    def test_previous_mode_for_unknown_concept(self):
        mode, outcome, turns = previous_mode_for(self.sid, "never_seen")
        self.assertEqual(mode, "")
        self.assertEqual(outcome, TeachingOutcome.UNKNOWN)
        self.assertEqual(turns, 0)

    def test_record_turn_trims_to_cap(self):
        # write well past the per-concept cap; only the tail must survive
        for i in range(20):
            record_turn_outcome(self.sid, "c2", mode="explanation",
                                outcome="correct", note=str(i))
        log = load_teaching_log(self.sid)
        self.assertLessEqual(len(log["c2"]), 6)
        # the last kept note should be the most recent
        self.assertEqual(log["c2"][-1].note, "19")

    def test_corrupt_file_treated_as_empty(self):
        path = _resolve(self.sid, ".teaching.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")
        log = load_teaching_log(self.sid)
        self.assertEqual(log, {})


class TestCrossTurnEndToEnd(StorageSandboxTestCase):
    """Simulate three turns on one concept and watch the mode advance."""
    def setUp(self):
        super().setUp()
        self.sid = "student_e2e_te_" + os.urandom(3).hex()
        self.tm = get_teaching_manager()

    def _do_turn(self, concept_key, mastery, outcome):
        pm, po, turns = previous_mode_for(self.sid, concept_key)
        ctx = TeachingContext(concept=concept_key, mastery=mastery,
                              previous_mode=pm, previous_outcome=po,
                              turns_on_concept=turns)
        strat = self.tm.adapt(ctx)
        self.tm.record_turn(self.sid, concept_key, mode=strat.mode, outcome=outcome)
        return strat.mode

    def test_advancement_introduction_to_practice(self):
        ck = "math.calc.integral"
        m1 = self._do_turn(ck, mastery=0.1, outcome=TeachingOutcome.ENGAGED)
        self.assertEqual(m1, TeachingMode.INTRODUCTION)
        m2 = self._do_turn(ck, mastery=0.45, outcome=TeachingOutcome.CORRECT)
        # previous INTRODUCTION + CORRECT -> advance to EXPLANATION
        self.assertEqual(m2, TeachingMode.EXPLANATION)
        m3 = self._do_turn(ck, mastery=0.45, outcome=TeachingOutcome.CORRECT)
        # previous EXPLANATION + CORRECT -> advance to PRACTICE
        self.assertEqual(m3, TeachingMode.PRACTICE)


class TestStudentModelDelegation(StorageSandboxTestCase):
    """StudentModel.adapt() must now go through the engine and return a
    strategy carrying a mode (the M3 contract)."""
    def setUp(self):
        super().setUp()
        from app.agents.student_model import StudentModel
        self.sid = "student_sm_del_" + os.urandom(3).hex()
        self.sm = StudentModel(self.sid).load()

    def test_adapt_returns_strategy_with_mode(self):
        self.sm.record_quiz_result(concept="牛顿第二定律", correct=True, subject="物理")
        self.sm.record_quiz_result(concept="牛顿第二定律", correct=True, subject="物理")
        self.sm.record_quiz_result(concept="牛顿第二定律", correct=True, subject="物理")
        self.sm.record_quiz_result(concept="牛顿第二定律", correct=True, subject="物理")
        strat = self.sm.adapt("牛顿第二定律", subject="物理", intent="explain")
        self.assertTrue(hasattr(strat, "mode"))
        self.assertEqual(strat.mode, TeachingMode.CHALLENGE)
        self.assertEqual(strat.depth, "deep")


if __name__ == "__main__":
    unittest.main()


# ===========================================================================
# Phase 2 tests: misconception diagnosis + correction recipes
# ===========================================================================

class TestMisconceptionDiagnosis(unittest.TestCase):
    def test_concept_confusion_keywords(self):
        from app.agents.teaching_engine.misconception import diagnose, MistakeType
        self.assertEqual(diagnose("把积分当成函数值"), MistakeType.CONCEPT)
        self.assertEqual(diagnose("误认为质量等于重量"), MistakeType.CONCEPT)

    def test_calculation_keywords(self):
        from app.agents.teaching_engine.misconception import diagnose, MistakeType
        self.assertEqual(diagnose("正负号算错了"), MistakeType.CALCULATION)
        self.assertEqual(diagnose("约分错了"), MistakeType.CALCULATION)

    def test_procedure_keywords(self):
        from app.agents.teaching_engine.misconception import diagnose, MistakeType
        self.assertEqual(diagnose("漏了受力分析这一步"), MistakeType.PROCEDURE)
        self.assertEqual(diagnose("跳步了"), MistakeType.PROCEDURE)

    def test_reasoning_keywords(self):
        from app.agents.teaching_engine.misconception import diagnose, MistakeType
        self.assertEqual(diagnose("运动方向判断错"), MistakeType.REASONING)

    def test_unclassifiable_returns_none(self):
        from app.agents.teaching_engine.misconception import diagnose
        self.assertIsNone(diagnose("这道题挺有意思"))
        self.assertIsNone(diagnose(""))

    def test_concept_wins_over_calculation_on_ambiguous_note(self):
        # "把加速度当成速度，又算错符号" has both concept + calc signals;
        # concept is the more fundamental error, so it should win (rules order).
        from app.agents.teaching_engine.misconception import diagnose, MistakeType
        self.assertEqual(diagnose("把加速度当成速度，又算错符号"), MistakeType.CONCEPT)


class TestMisconceptionRecipeFolding(unittest.TestCase):
    def test_concept_recipe_folds_into_focus_avoid(self):
        from app.agents.teaching_engine import adapt_from_context, TeachingContext
        ctx = TeachingContext(concept="积分", mastery=0.85,
                              misconceptions=["把积分当函数值"],
                              mistake_types=["concept"])
        s = adapt_from_context(ctx)
        self.assertEqual(s.mode.value, "remediation")
        # mode recipe + correction recipe both present
        self.assertTrue(any("重建正确的概念模型" in f for f in s.focus), s.focus)
        self.assertTrue(any("不要直接堆公式" in a for a in s.avoid), s.avoid)

    def test_no_mistake_types_leaves_mode_recipe_intact(self):
        from app.agents.teaching_engine import adapt_from_context, TeachingContext
        ctx = TeachingContext(concept="积分", mastery=0.45)
        s = adapt_from_context(ctx)
        # EXPLANATION mode recipe, no correction overlay
        self.assertEqual(s.mode.value, "explanation")
        self.assertFalse(any("重建正确的概念模型" in f for f in s.focus))


class TestMisconceptionPersistenceLoop(StorageSandboxTestCase):
    """End-to-end: a wrong quiz with a concept-confusion note gets diagnosed,
    persisted to ConceptRecord.mistake_types, and surfaces in the next strategy."""
    def setUp(self):
        super().setUp()
        from app.agents.student_model import StudentModel
        self.sid = "student_misc_e2e_" + os.urandom(3).hex()
        self.sm = StudentModel(self.sid).load()

    def test_diagnosis_persisted_and_surfaces_in_strategy(self):
        self.sm.record_quiz_result(concept="牛顿第二定律", correct=False,
                                   subject="物理", note="把加速度当成速度")
        self.sm.load()
        rec = self.sm.memory.get("physics.dynamics.newton_second")
        self.assertEqual(rec.mistake_types, ["concept"])
        strat = self.sm.adapt("牛顿第二定律", subject="物理", intent="explain")
        self.assertEqual(strat.mode.value, "remediation")
        self.assertTrue(any("概念模型" in f for f in strat.focus))


# ===========================================================================
# Phase 3 tests: dynamic difficulty
# ===========================================================================

class TestDifficulty(unittest.TestCase):
    def test_seed_from_mastery_bands(self):
        from app.agents.teaching_engine.difficulty import seed_from_mastery
        self.assertEqual(seed_from_mastery(0.1), 1)
        self.assertEqual(seed_from_mastery(0.4), 2)
        self.assertEqual(seed_from_mastery(0.7), 3)
        self.assertEqual(seed_from_mastery(0.9), 4)

    def test_no_history_returns_seed(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        self.assertEqual(compute_difficulty(0.4, []), 2)

    def test_high_accuracy_steps_up(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        from app.agents.teaching_engine import TeachingOutcome
        # mastery 0.4 -> seed 2; 4/5 correct (>=80%) -> step up to 3
        outs = ["correct", "correct", "correct", "correct", "wrong"]
        self.assertEqual(compute_difficulty(0.4, outs), 3)

    def test_low_accuracy_steps_down(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        # mastery 0.7 -> seed 3; 1/5 correct (<=40%) -> step down to 2
        outs = ["wrong", "wrong", "wrong", "wrong", "correct"]
        self.assertEqual(compute_difficulty(0.7, outs), 2)

    def test_clamped_at_max(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        # mastery 0.9 -> seed 4; all correct -> step up, clamped to 5
        self.assertEqual(compute_difficulty(0.9, ["correct"] * 5), 5)

    def test_clamped_at_min(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        # mastery 0.1 -> seed 1; all wrong -> step down, clamped to 1
        self.assertEqual(compute_difficulty(0.1, ["wrong"] * 5), 1)

    def test_engaged_outcomes_filtered(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        # engaged turns carry no difficulty signal -> treated as no history
        self.assertEqual(compute_difficulty(0.4, ["engaged", "engaged"]), 2)

    def test_partial_counts_as_half(self):
        from app.agents.teaching_engine.difficulty import compute_difficulty
        # mastery 0.4 -> seed 2; 3 partial + 1 correct + 1 wrong = (1.5+1+0)/5 = 0.5 -> no step
        outs = ["partial", "partial", "partial", "correct", "wrong"]
        self.assertEqual(compute_difficulty(0.4, outs), 2)

    def test_difficulty_to_level_mapping(self):
        from app.agents.teaching_engine.difficulty import difficulty_to_level
        self.assertEqual(difficulty_to_level(1), "easy")
        self.assertEqual(difficulty_to_level(2), "easy")
        self.assertEqual(difficulty_to_level(3), "medium")
        self.assertEqual(difficulty_to_level(4), "hard")
        self.assertEqual(difficulty_to_level(5), "hard")


class TestDifficultyEndToEnd(StorageSandboxTestCase):
    """Several correct turns should raise the suggested difficulty over time."""
    def setUp(self):
        super().setUp()
        from app.agents.student_model import StudentModel
        self.sid = "student_diff_e2e_" + os.urandom(3).hex()
        self.sm = StudentModel(self.sid).load()

    def test_difficulty_rises_after_streak(self):
        from app.agents.teaching_engine import TeachingOutcome
        # seed a known mastery so the band is stable
        for _ in range(3):
            self.sm.record_quiz_result(concept="牛顿第二定律", correct=True,
                                       subject="物理")
        self.sm.load()
        # record several correct teaching turns in the log
        from app.agents.teaching_engine import get_teaching_manager
        tm = get_teaching_manager()
        ck = "physics.dynamics.newton_second"
        for _ in range(4):
            tm.record_turn(self.sid, ck, mode="practice", outcome=TeachingOutcome.CORRECT)
        strat = self.sm.adapt("牛顿第二定律", subject="物理", intent="explain")
        # with a strong correct streak, difficulty should be at least 4
        self.assertGreaterEqual(strat.next_check.difficulty, 4,
                                f"got {strat.next_check.difficulty}")


class TestConceptKeyNormalization(StorageSandboxTestCase):
    """P2: the difficulty dial must read the SAME normalized key the write
    side uses (graph node id), not the raw per-turn concept string."""

    def setUp(self):
        super().setUp()
        self.sid = "student_cknorm_" + os.urandom(3).hex()
        self.tm = get_teaching_manager()

    def test_adapt_reads_history_via_concept_key_not_raw_concept(self):
        ck = "math.geometry_advanced.tangent"
        for _ in range(4):
            self.tm.record_turn(self.sid, ck, mode="practice",
                                outcome=TeachingOutcome.CORRECT)
        # Raw concept differs from the log key — this is exactly the
        # fragmentation the supervisor hit ("切线放缩" vs node id).
        # grade=小学 to isolate the dial from the 高中/本科 grade floor.
        ctx = TeachingContext(concept="切线放缩", concept_key=ck, mastery=0.5,
                              grade="小学")
        strat = self.tm.adapt(ctx, student_id=self.sid)
        # 4/4 correct streak on mastery band 2 -> stepped up to 3.
        self.assertGreaterEqual(strat.next_check.difficulty, 3,
                                f"got {strat.next_check.difficulty}")
        # And without the normalized key the dial stays blind (seed only).
        ctx_blind = TeachingContext(concept="切线放缩", mastery=0.5,
                                    grade="小学")
        strat_blind = self.tm.adapt(ctx_blind, student_id=self.sid)
        self.assertLess(strat_blind.next_check.difficulty,
                        strat.next_check.difficulty)

    def test_record_quiz_attempt_writes_assessed_outcome_to_teaching_log(self):
        from app.core.quiz_attempts import record_quiz_attempt
        record_quiz_attempt("", stem="求 ∫x dx", verdict="correct",
                            student_answer="x^2/2+C", concept="积分",
                            student_id=self.sid, correct=True)
        log = load_teaching_log(self.sid)
        # concept may normalize to a graph node id (积分 -> math.calculus.integral)
        # or fall back to the raw name; either way exactly one key is written.
        self.assertEqual(len(log), 1, f"log keys: {list(log.keys())}")
        entries = next(iter(log.values()))
        self.assertEqual(entries[-1].mode, "practice")
        self.assertEqual(entries[-1].outcome, "correct")


# ===========================================================================
# Phase 3 tests: curriculum / learning path
# ===========================================================================

class TestCurriculum(unittest.TestCase):
    def test_next_learnable_sorted_by_difficulty(self):
        from app.agents.teaching_engine import build_learning_path
        lp = build_learning_path(
            current_name="导数",
            next_learnable=[{"name": "积分", "skill_id": "i", "difficulty": 5},
                            {"name": "微元法", "skill_id": "m", "difficulty": 3}])
        self.assertEqual([n.name for n in lp.next_nodes], ["微元法", "积分"])

    def test_stale_middling_skill_becomes_review(self):
        import time
        from app.agents.teaching_engine import build_learning_path
        old_ts = time.time() - 5 * 24 * 3600  # 5 days stale
        lp = build_learning_path(
            review_candidates=[{"name": "函数单调性", "skill_id": "m",
                                "mastery": 0.45, "last_review": old_ts}])
        self.assertEqual(len(lp.review_nodes), 1)
        self.assertEqual(lp.review_nodes[0].name, "函数单调性")

    def test_mastered_skill_not_in_review(self):
        import time
        from app.agents.teaching_engine import build_learning_path
        old_ts = time.time() - 5 * 24 * 3600
        lp = build_learning_path(
            review_candidates=[{"name": "已掌握", "skill_id": "x",
                                "mastery": 0.9, "last_review": old_ts}])
        self.assertEqual(lp.review_nodes, [])

    def test_fresh_middling_skill_not_in_review(self):
        import time
        from app.agents.teaching_engine import build_learning_path
        fresh = time.time() - 3600  # 1 hour ago, not stale
        lp = build_learning_path(
            review_candidates=[{"name": "刚学", "skill_id": "x",
                                "mastery": 0.5, "last_review": fresh}])
        self.assertEqual(lp.review_nodes, [])

    def test_empty_inputs_returns_path_with_rationale(self):
        from app.agents.teaching_engine import build_learning_path
        lp = build_learning_path()
        self.assertEqual(lp.next_nodes, [])
        self.assertEqual(lp.review_nodes, [])
        self.assertTrue(lp.rationale)

    def test_rationale_mentions_next_and_review(self):
        import time
        from app.agents.teaching_engine import build_learning_path
        old_ts = time.time() - 4 * 24 * 3600
        lp = build_learning_path(
            next_learnable=[{"name": "积分", "skill_id": "i", "difficulty": 5}],
            review_candidates=[{"name": "导数", "skill_id": "d",
                                "mastery": 0.4, "last_review": old_ts}])
        self.assertIn("积分", lp.rationale)
        self.assertIn("导数", lp.rationale)
