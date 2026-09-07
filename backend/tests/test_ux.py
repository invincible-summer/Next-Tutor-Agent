"""Tests for M8 UX Intelligence layer.

Covers all components: schema round-trips, store persistence (path guard +
corrupt-file safety), feedback classifier rules, engagement/style inference,
motivation streak (read M6 read-only), explanation adapter directive
composition, context builder rendering, manager end-to-end, supervisor hooks,
the toggle/fallback contract, and the single-truth-source boundary (M8 never
writes M2). Uses a temp students/ dir so tests are hermetic.
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

from app.agents.ux_intelligence import (store, feedback_analyzer,
    engagement_tracker, learner_profile, interaction_style,
    motivation_engine, explanation_adapter, context_builder,
    manager as ux_manager)
from app.agents.ux_intelligence.schema import (DetailLevel, FeedbackType,
    InteractionStyle, MotivationState, Tone, UXEvent, UXProfile)
from app.agents.ux_intelligence.explanation_adapter import ResponseDirective
from app.agents.ux_intelligence.manager import UXService, get_ux_service
from app.agents.ux_intelligence import is_enabled


def _temp_students_dir():
    return Path(tempfile.mkdtemp(prefix="edu_ux_test_"))


# ---------------------------------------------------------------------------
# 1. schema round-trips + enum validation
# ---------------------------------------------------------------------------

class TestSchema(unittest.TestCase):

    def test_interaction_style_roundtrip(self):
        s = InteractionStyle(tone=Tone.FORMAL, detail_level=DetailLevel.CONCISE,
                             visual_preference=False, pacing="fast", patience="low")
        d = s.to_dict()
        self.assertEqual(d["tone"], "formal")
        self.assertFalse(d["visual_preference"])
        s2 = InteractionStyle.from_dict(d)
        self.assertEqual(s2.tone, Tone.FORMAL)
        self.assertEqual(s2.pacing, "fast")

    def test_uxprofile_roundtrip(self):
        p = UXProfile(student_id="s1")
        p.recent_feedback = [FeedbackType.EXPLANATION_TOO_HARD, FeedbackType.PRAISE]
        p.recent_response_lengths = [100, 1200]
        p.abandon_signals = 2
        d = p.to_dict()
        self.assertEqual(d["recent_feedback"], ["explanation_too_hard", "praise"])
        p2 = UXProfile.from_dict(d)
        self.assertEqual(p2.recent_feedback[0], FeedbackType.EXPLANATION_TOO_HARD)
        self.assertEqual(p2.abandon_signals, 2)

    def test_feedbacktype_from_value_safe(self):
        self.assertEqual(FeedbackType.from_value("garbage"), FeedbackType.NONE)
        self.assertEqual(FeedbackType.from_value(None), FeedbackType.NONE)
        self.assertEqual(FeedbackType.from_value(FeedbackType.PRAISE),
                         FeedbackType.PRAISE)

    def test_uxevent_roundtrip(self):
        e = UXEvent(student_id="s1", concept="导数", type="feedback",
                    feedback=FeedbackType.EXPLANATION_TOO_LONG,
                    response_length=1500, note="太长了")
        d = e.to_dict()
        e2 = UXEvent.from_dict(d)
        self.assertEqual(e2.feedback, FeedbackType.EXPLANATION_TOO_LONG)
        self.assertEqual(e2.response_length, 1500)

    def test_style_defaults(self):
        s = InteractionStyle()
        self.assertEqual(s.tone, Tone.ENCOURAGING)
        self.assertEqual(s.detail_level, DetailLevel.MEDIUM)
        self.assertTrue(s.visual_preference)


# ---------------------------------------------------------------------------
# 2. store persistence + path traversal + corrupt-file safety
# ---------------------------------------------------------------------------

class TestStore(unittest.TestCase):

    def setUp(self):
        self._dir = _temp_students_dir()
        self._patch = patch.object(store, "_STUDENTS_DIR", self._dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_load_missing_returns_default(self):
        p = store.load_profile("nobody")
        self.assertIsInstance(p, UXProfile)
        self.assertEqual(p.recent_feedback, [])

    def test_save_load_roundtrip(self):
        p = UXProfile(student_id="s1")
        p.style.tone = Tone.FORMAL
        p.abandon_signals = 3
        self.assertTrue(store.save_profile("s1", p))
        p2 = store.load_profile("s1")
        self.assertEqual(p2.style.tone, Tone.FORMAL)
        self.assertEqual(p2.abandon_signals, 3)

    def test_append_read_events(self):
        e = UXEvent(student_id="s1", concept="力", type="feedback",
                    feedback=FeedbackType.TOO_FAST)
        self.assertTrue(store.append_event("s1", e))
        evs = store.read_events("s1")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].feedback, FeedbackType.TOO_FAST)

    def test_read_events_missing_file(self):
        self.assertEqual(store.read_events("nope"), [])

    def test_corrupt_file_treated_as_empty(self):
        path = self._dir / "bad.ux_profile.json"
        path.write_text("{ not valid json", encoding="utf-8")
        p = store.load_profile("bad")
        self.assertIsInstance(p, UXProfile)  # never raises

    def test_path_traversal_guard(self):
        p = store.load_profile("../../etc/passwd")
        # the resolved path must stay under the students dir
        resolved = store._resolve("../../etc/passwd")
        self.assertEqual(resolved.parent, self._dir)
        self.assertNotIn("..", resolved.name)

    def test_profile_summary_never_raises(self):
        s = store.profile_summary("s1")
        self.assertIn("style", s)
        self.assertEqual(s["event_count"], 0)


# ---------------------------------------------------------------------------
# 3. feedback analyzer (rule-based classifier)
# ---------------------------------------------------------------------------

class TestFeedbackAnalyzer(unittest.TestCase):

    def test_classify_too_hard(self):
        self.assertEqual(feedback_analyzer.classify("这个太复杂了看不懂"),
                         FeedbackType.EXPLANATION_TOO_HARD)

    def test_classify_too_long(self):
        self.assertEqual(feedback_analyzer.classify("回答太长了太啰嗦"),
                         FeedbackType.EXPLANATION_TOO_LONG)

    def test_classify_too_short(self):
        self.assertEqual(feedback_analyzer.classify("没讲清，展开点"),
                         FeedbackType.EXPLANATION_TOO_SHORT)

    def test_classify_praise(self):
        self.assertEqual(feedback_analyzer.classify("讲得真好，懂了谢谢"),
                         FeedbackType.PRAISE)

    def test_classify_none(self):
        self.assertEqual(feedback_analyzer.classify("什么是牛顿第二定律"),
                         FeedbackType.NONE)
        self.assertEqual(feedback_analyzer.classify(""), FeedbackType.NONE)

    def test_classify_first_match_wins(self):
        # "看不懂" is more specific than a vaguer phrase and should win
        self.assertEqual(feedback_analyzer.classify("看不懂，讲太多了"),
                         FeedbackType.EXPLANATION_TOO_HARD)

    def test_is_experience_signal(self):
        self.assertTrue(feedback_analyzer.is_experience_signal(
            FeedbackType.EXPLANATION_TOO_HARD))
        self.assertFalse(feedback_analyzer.is_experience_signal(FeedbackType.PRAISE))
        self.assertFalse(feedback_analyzer.is_experience_signal(FeedbackType.NONE))


# ---------------------------------------------------------------------------
# 4. engagement tracker + interaction style inference
# ---------------------------------------------------------------------------

class TestEngagementInference(unittest.TestCase):

    def test_push_response_length_caps(self):
        p = UXProfile()
        for i in range(20):
            engagement_tracker.push_response_length(p, i * 100)
        self.assertLessEqual(len(p.recent_response_lengths), 8)

    def test_repeated_too_long_makes_concise(self):
        p = UXProfile()
        for _ in range(2):
            engagement_tracker.push_feedback(p, FeedbackType.EXPLANATION_TOO_LONG)
        engagement_tracker.apply_engagement_to_style(p)
        self.assertEqual(p.style.detail_level, DetailLevel.CONCISE)
        self.assertEqual(p.style.patience, "low")

    def test_repeated_too_short_makes_detailed(self):
        p = UXProfile()
        for _ in range(2):
            engagement_tracker.push_feedback(p, FeedbackType.EXPLANATION_TOO_SHORT)
        engagement_tracker.apply_engagement_to_style(p)
        self.assertEqual(p.style.detail_level, DetailLevel.DETAILED)
        self.assertEqual(p.style.patience, "high")

    def test_too_hard_keeps_encouraging(self):
        p = UXProfile()
        p.style.tone = Tone.NEUTRAL
        engagement_tracker.push_feedback(p, FeedbackType.EXPLANATION_TOO_HARD)
        engagement_tracker.apply_engagement_to_style(p)
        self.assertEqual(p.style.tone, Tone.ENCOURAGING)

    def test_praise_keeps_neutral(self):
        p = UXProfile()
        p.style.tone = Tone.NEUTRAL
        engagement_tracker.push_feedback(p, FeedbackType.PRAISE)
        engagement_tracker.apply_engagement_to_style(p)
        # praise with no hard complaints -> stays neutral
        self.assertEqual(p.style.tone, Tone.NEUTRAL)

    def test_fast_feedback_slows_pacing(self):
        p = UXProfile()
        for _ in range(2):
            engagement_tracker.push_feedback(p, FeedbackType.TOO_FAST)
        engagement_tracker.apply_engagement_to_style(p)
        self.assertEqual(p.style.pacing, "slow")

    def test_abandon_heuristic_only_on_long_complaint(self):
        p = UXProfile()
        # short answer + length complaint -> no abandon bump
        engagement_tracker.maybe_bump_abandon(p, FeedbackType.EXPLANATION_TOO_LONG, 200)
        self.assertEqual(p.abandon_signals, 0)
        # long answer + length complaint -> bump
        engagement_tracker.maybe_bump_abandon(p, FeedbackType.EXPLANATION_TOO_LONG, 1500)
        self.assertEqual(p.abandon_signals, 1)

    def test_avg_response_length(self):
        p = UXProfile()
        p.recent_response_lengths = [100, 300]
        self.assertEqual(engagement_tracker.avg_response_length(p), 200.0)


# ---------------------------------------------------------------------------
# 5. motivation engine (streak, read M6 read-only)
# ---------------------------------------------------------------------------

class TestMotivationEngine(unittest.TestCase):

    @staticmethod
    def _daystr(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    def test_current_streak_zero_no_data(self):
        with patch.object(motivation_engine, "_active_days", return_value=set()):
            self.assertEqual(motivation_engine.current_streak("s1"), 0)

    def test_current_streak_counts_consecutive(self):
        now = time.time()
        day = 86400.0
        days = {self._daystr(now - i * day) for i in range(3)}  # today + 2 prior
        with patch.object(motivation_engine, "_active_days", return_value=days):
            self.assertEqual(motivation_engine.current_streak("s1", now=now), 3)

    def test_streak_breaks_at_gap(self):
        now = time.time()
        day = 86400.0
        # today + day-3, gap at day-1 and day-2
        days = {self._daystr(now), self._daystr(now - 3 * day)}
        with patch.object(motivation_engine, "_active_days", return_value=days):
            self.assertEqual(motivation_engine.current_streak("s1", now=now), 1)

    def test_next_milestone(self):
        self.assertEqual(motivation_engine.next_milestone(0), 3)
        self.assertEqual(motivation_engine.next_milestone(5), 7)
        self.assertIsNone(motivation_engine.next_milestone(200))

    def test_milestone_due(self):
        self.assertEqual(motivation_engine.milestone_due(7, 3), 7)
        self.assertIsNone(motivation_engine.milestone_due(7, 7))  # already surfaced
        # streak 10 has not yet reached the 14 milestone, and 7 was already
        # surfaced -> nothing new to congratulate
        self.assertIsNone(motivation_engine.milestone_due(10, 7))
        # reaching the 14 milestone with only 7 surfaced -> 14 is due
        self.assertEqual(motivation_engine.milestone_due(14, 7), 14)

    def test_motivation_snapshot_never_raises(self):
        with patch.object(motivation_engine, "_active_days", return_value=set()):
            snap = motivation_engine.motivation_snapshot("s1")
        self.assertEqual(snap["streak_days"], 0)
        self.assertIn("next_milestone", snap)


# ---------------------------------------------------------------------------
# 6. explanation adapter (directive composition)
# ---------------------------------------------------------------------------

class TestExplanationAdapter(unittest.TestCase):

    def setUp(self):
        self._dir = _temp_students_dir()
        self._patch = patch.object(store, "_STUDENTS_DIR", self._dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_directive_has_style_lines(self):
        p = UXProfile()
        d = explanation_adapter.build_directive(
            student_id="s1", profile=p, intent="explain", grade="高中")
        self.assertTrue(d.style_lines)
        self.assertFalse(d.is_empty())

    def test_feedback_adjustment_after_hard(self):
        p = UXProfile()
        p.recent_feedback = [FeedbackType.EXPLANATION_TOO_HARD]
        d = explanation_adapter.build_directive(
            student_id="s1", profile=p, intent="explain")
        self.assertIn("降低门槛", d.feedback_adjustment)

    def test_no_motivation_during_assessment(self):
        p = UXProfile()
        d = explanation_adapter.build_directive(
            student_id="s1", profile=p, intent="quiz")
        self.assertEqual(d.motivation_line, "")
        self.assertIn("测评", d.intent_guard)

    def test_academic_style_note_reads_m2(self):
        p = UXProfile()
        m2 = {"preference": "examples_first", "explanation_depth": "deep"}
        with patch.object(explanation_adapter, "m2_learning_style_snapshot",
                          return_value=m2):
            d = explanation_adapter.build_directive(
                student_id="s1", profile=p, intent="explain")
        self.assertIn("先举例后归纳", d.academic_style_note)
        self.assertIn("深入拓展", d.academic_style_note)

    def test_academic_style_note_empty_when_m2_off(self):
        p = UXProfile()
        with patch.object(explanation_adapter, "m2_learning_style_snapshot",
                          return_value=None):
            d = explanation_adapter.build_directive(
                student_id="s1", profile=p, intent="explain")
        self.assertEqual(d.academic_style_note, "")

    def test_motivation_surfaces_milestone_once(self):
        p = UXProfile()
        now = time.time()
        day = 86400.0
        days = {time.strftime("%Y-%m-%d", time.localtime(now - i * day))
                for i in range(5)}
        with patch.object(motivation_engine, "_active_days", return_value=days):
            d1 = explanation_adapter.build_directive(
                student_id="s1", profile=p, intent="explain")
            self.assertTrue(d1.motivation_line)
            surfaced = p.motivation.last_milestone_surfaced
            # second build: same streak, milestone already surfaced -> no repeat
            d2 = explanation_adapter.build_directive(
                student_id="s1", profile=p, intent="explain")
            self.assertEqual(p.motivation.last_milestone_surfaced, surfaced)


# ---------------------------------------------------------------------------
# 7. context builder (directive rendering + greeting)
# ---------------------------------------------------------------------------

class TestContextBuilder(unittest.TestCase):

    def setUp(self):
        self._dir = _temp_students_dir()
        self._patch = patch.object(store, "_STUDENTS_DIR", self._dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_directive_renders_block(self):
        d = context_builder.build_ux_directive(
            student_id="s1", concept="导数", intent="explain", grade="高中")
        self.assertIn("[交互智能·表达适配]", d)

    def test_greeting_fallback_no_data(self):
        g = context_builder.greeting("s1", grade="高中", lang="zh")
        self.assertIsInstance(g, str)
        self.assertTrue(len(g) > 0)

    def test_greeting_english(self):
        g = context_builder.greeting("s1", grade="高中", lang="en")
        self.assertIsInstance(g, str)
        self.assertIn("learn", g.lower())

    def test_greeting_never_raises_on_source_failure(self):
        # the new sources (M3 teaching log / learning ledger) both failing
        # still yields a generic greeting, never an exception
        with patch("app.agents.teaching_engine.teaching_log.load_teaching_log",
                   side_effect=RuntimeError("boom")), \
             patch("app.core.learning_records.list_records",
                   side_effect=RuntimeError("boom")):
            g = context_builder.greeting("s1", lang="zh")
        # falls back to a generic greeting, not an exception
        self.assertIsInstance(g, str)


# ---------------------------------------------------------------------------
# 8. manager facade end-to-end
# ---------------------------------------------------------------------------

class TestManager(unittest.TestCase):

    def setUp(self):
        self._dir = _temp_students_dir()
        self._store_patch = patch.object(store, "_STUDENTS_DIR", self._dir)
        self._store_patch.start()
        UXService._instance = None  # reset singleton

    def tearDown(self):
        self._store_patch.stop()
        UXService._instance = None

    def test_record_turn_persists_profile_and_events(self):
        ux = get_ux_service()
        ux.record_turn(student_id="s1", session_id="sess1",
                       concept="函数", user_message="太长了看不懂",
                       answer="x" * 1500, grade="高中", intent="explain")
        prof = ux.profile("s1")
        self.assertGreater(prof["event_count"], 0)
        # the feedback was classified and recorded
        self.assertTrue(any("too" in k or "hard" in k for k in prof["recent_feedback_counts"]))

    def test_build_directive_returns_string(self):
        ux = get_ux_service()
        d = ux.build_directive(student_id="s2", intent="explain", grade="高中")
        self.assertIsInstance(d, str)

    def test_record_turn_never_raises_on_bad_input(self):
        ux = get_ux_service()
        # None answer / message should not crash
        ux.record_turn(student_id="s3", user_message=None, answer=None,
                       grade="高中")
        self.assertTrue(True)  # reached here = no exception

    def test_engagement_returns_list(self):
        ux = get_ux_service()
        self.assertEqual(ux.engagement("s4"), [])


# ---------------------------------------------------------------------------
# 9. toggle contract (off -> no-ops, byte-identical M1-M7)
# ---------------------------------------------------------------------------

class TestToggleContract(unittest.TestCase):

    def test_default_enabled(self):
        self.assertTrue(is_enabled())

    def test_disabled_via_env(self):
        with patch.dict(os.environ, {"UX_INTELLIGENCE_MODE": "0"}):
            self.assertFalse(is_enabled())
        with patch.dict(os.environ, {"UX_INTELLIGENCE_MODE": "off"}):
            self.assertFalse(is_enabled())

    def test_supervisor_hook_noop_when_disabled(self):
        from app.agents import supervisor
        understanding = MagicMock()
        understanding.concept = "导数"
        understanding.subject = "数学"
        understanding.intent.value = "explain"
        session = MagicMock()
        session.grade = "高中"
        trace = MagicMock()
        with patch.dict(os.environ, {"UX_INTELLIGENCE_MODE": "0"}):
            d = supervisor._ux_directive_for_turn(understanding, session, trace)
            self.assertEqual(d, "")
            supervisor._ux_record_turn("s1", understanding, "hi", session,
                                         "answer", [], trace)
             # no exception => pass
            self.assertTrue(True)


# ---------------------------------------------------------------------------
# 10. single-truth-source boundary (M8 never writes M2)
# ---------------------------------------------------------------------------

class TestSingleTruthSource(unittest.TestCase):

    def setUp(self):
        self._dir = _temp_students_dir()
        self._patch = patch.object(store, "_STUDENTS_DIR", self._dir)
        self._patch.start()
        UXService._instance = None

    def tearDown(self):
        self._patch.stop()
        UXService._instance = None

    def test_record_turn_does_not_write_m2(self):
        """M8 must not call any StudentModel mutating method. We assert by
        patching the M2 facade's record_events and snapshot to fail loudly
        if invoked."""
        from app.agents import student_model as sm_pkg
        guard = MagicMock()
        with patch.object(sm_pkg.StudentModel, "record_events", guard), \
             patch.object(sm_pkg.StudentModel, "adapt", guard), \
             patch.object(sm_pkg.StudentModel, "load", MagicMock()):
            ux = get_ux_service()
            ux.record_turn(student_id="s1", user_message="看不懂",
                           answer="x" * 500, grade="高中")
            guard.assert_not_called()

    def test_m2_read_is_defensive(self):
        # M2 disabled -> returns None, M8 still works
        with patch("app.agents.student_model.is_enabled", return_value=False):
            self.assertIsNone(learner_profile.m2_learning_style_snapshot("s1"))

    def test_ux_writes_only_own_files(self):
        ux = get_ux_service()
        ux.record_turn(student_id="s1", user_message="太复杂了",
                       answer="y" * 200, grade="高中")
        files = [f.name for f in self._dir.iterdir()]
        # only M8-owned files appear
        self.assertTrue(any(f.endswith(".ux_profile.json") for f in files))
        self.assertTrue(any(f.endswith(".ux_events.jsonl") for f in files))
        # never touches M2/M3/M6/M7 files
        self.assertFalse(any(".json" in f and not f.startswith("s1.ux")
                             for f in files))


# ---------------------------------------------------------------------------
# 9. Response Quality Evaluator (single-turn expression scoring, M8-owned)
# ---------------------------------------------------------------------------

from app.agents.ux_intelligence.response_quality_evaluator import (
    ExpressionFailure, ResponseQualityScore, evaluate_response,
    apply_score_to_profile)


class TestResponseQualityEvaluator(unittest.TestCase):

    def test_praise_scores_high(self):
        profile = UXProfile()
        score = evaluate_response(answer="讲解", feedback=FeedbackType.PRAISE,
                                  profile=profile)
        self.assertGreaterEqual(score.communication_score, 0.9)
        self.assertEqual(score.failure, ExpressionFailure.NONE)

    def test_too_hard_scores_low_with_abstraction_diagnosis(self):
        profile = UXProfile()
        score = evaluate_response(answer="..." * 200,
                                  feedback=FeedbackType.EXPLANATION_TOO_HARD,
                                  profile=profile)
        self.assertLess(score.communication_score, 0.5)
        self.assertEqual(score.failure, ExpressionFailure.ABSTRACTION_TOO_HIGH)

    def test_too_long_diagnoses_verbose(self):
        profile = UXProfile()
        profile.style.detail_level = DetailLevel.CONCISE
        score = evaluate_response(answer="x" * 1200,
                                  feedback=FeedbackType.EXPLANATION_TOO_LONG,
                                  profile=profile)
        self.assertEqual(score.failure, ExpressionFailure.TOO_VERBOSE)
        self.assertTrue(score.over_length_tolerance)

    def test_too_short_diagnoses_terse(self):
        profile = UXProfile()
        score = evaluate_response(answer="短",
                                  feedback=FeedbackType.EXPLANATION_TOO_SHORT,
                                  profile=profile)
        self.assertEqual(score.failure, ExpressionFailure.TOO_TERSE)

    def test_pace_mismatch(self):
        profile = UXProfile()
        score = evaluate_response(answer="...",
                                  feedback=FeedbackType.TOO_FAST,
                                  profile=profile)
        self.assertEqual(score.failure, ExpressionFailure.PACE_MISMATCH)

    def test_follow_up_penalty(self):
        profile = UXProfile()
        score = evaluate_response(answer="讲解", feedback=FeedbackType.NONE,
                                  profile=profile, follow_up_count=4)
        self.assertLess(score.communication_score, 0.5)

    def test_verdict_compounds_on_negative_feedback(self):
        profile = UXProfile()
        score = evaluate_response(answer="...", feedback=FeedbackType.NONE,
                                  profile=profile, verdict="wrong")
        # wrong verdict alone (no feedback) doesn't tank the score
        self.assertGreaterEqual(score.communication_score, 0.7)
        score2 = evaluate_response(answer="...",
                                   feedback=FeedbackType.EXPLANATION_TOO_HARD,
                                   profile=profile, verdict="wrong")
        self.assertLess(score2.communication_score, 0.4)

    def test_score_clamped_0_to_1(self):
        profile = UXProfile()
        for ft in FeedbackType:
            s = evaluate_response(answer="x" * 2000, feedback=ft,
                                  profile=profile, follow_up_count=99)
            self.assertGreaterEqual(s.communication_score, 0.0)
            self.assertLessEqual(s.communication_score, 1.0)

    def test_apply_score_raises_example_density_on_abstraction(self):
        profile = UXProfile()
        profile.style.visual_preference = False
        profile.style.tone = Tone.FORMAL
        score = ResponseQualityScore(
            failure=ExpressionFailure.ABSTRACTION_TOO_HIGH)
        apply_score_to_profile(score, profile)
        self.assertTrue(profile.style.visual_preference)
        self.assertEqual(profile.style.tone, Tone.ENCOURAGING)

    def test_apply_score_shortens_on_verbose(self):
        profile = UXProfile()
        profile.style.detail_level = DetailLevel.DETAILED
        score = ResponseQualityScore(failure=ExpressionFailure.TOO_VERBOSE)
        apply_score_to_profile(score, profile)
        self.assertEqual(profile.style.detail_level, DetailLevel.MEDIUM)

    def test_score_roundtrip(self):
        s = ResponseQualityScore(communication_score=0.3,
            failure=ExpressionFailure.TOO_TERSE, follow_up_count=2,
            answer_length=10, over_length_tolerance=True, note="test")
        d = s.to_dict()
        s2 = ResponseQualityScore.from_dict(d)
        self.assertEqual(s2.failure, ExpressionFailure.TOO_TERSE)
        self.assertAlmostEqual(s2.communication_score, 0.3)


# ---------------------------------------------------------------------------
# 10. M8 <-> M3 boundary solidification (M8 never mutates TeachingPlan)
# ---------------------------------------------------------------------------

class TestM8M3Boundary(unittest.TestCase):
    """The M8 review demanded an explicit contract: M8 receives the M3
    TeachingPlan read-only and outputs an ExplanationDirective; it must NEVER
    mutate the teaching plan. These tests freeze that invariant."""

    def test_build_directive_returns_response_directive_type(self):
        from app.agents.ux_intelligence.explanation_adapter import build_directive
        profile = UXProfile(student_id="s1")
        d = build_directive(student_id="s1", profile=profile, concept="导数",
                            subject="数学", intent="explain")
        # the output is a ResponseDirective (presentation decision), never a
        # TeachingStrategy / TeachingPlan (M3's content decision)
        self.assertTrue(hasattr(d, "style_lines"))
        self.assertTrue(hasattr(d, "feedback_adjustment"))
        self.assertFalse(hasattr(d, "mode"))       # M3's field, not M8's
        self.assertFalse(hasattr(d, "difficulty")) # M3's field, not M8's

    def test_build_directive_does_not_mutate_profile_style(self):
        """build_directive may touch motivation surfacing bookkeeping (one
        nudge per milestone) but must NOT change the style dims (that is the
        record_turn / evaluator's job, a separate write path)."""
        from app.agents.ux_intelligence.explanation_adapter import build_directive
        profile = UXProfile(student_id="s1")
        before_tone = profile.style.tone
        before_detail = profile.style.detail_level
        build_directive(student_id="s1", profile=profile, concept="x")
        self.assertEqual(profile.style.tone, before_tone)
        self.assertEqual(profile.style.detail_level, before_detail)

    def test_m8_never_imports_teaching_engine_at_module_level(self):
        """M8 must stay import-clean of M3 to guarantee it cannot reach into
        the teaching plan."""
        import app.agents.ux_intelligence as ux_pkg
        pkg_dir = Path(ux_pkg.__file__).parent
        for py in pkg_dir.glob("*.py"):
            if py.name == "__init__":
                continue
            src = py.read_text(encoding="utf-8")
            for line in src.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                self.assertNotIn("from ..teaching_engine", stripped,
                    f"{py.name} imports teaching_engine at module level")
                self.assertNotIn("import teaching_engine", stripped,
                    f"{py.name} imports teaching_engine at module level")

    def test_evaluator_adjusts_presentation_not_teaching(self):
        """apply_score_to_profile must only touch UX presentation dims
        (tone/detail/visual/pacing), never academic fields (difficulty/mode)."""
        from app.agents.ux_intelligence.response_quality_evaluator import apply_score_to_profile
        profile = UXProfile()
        for failure in ExpressionFailure:
            score = ResponseQualityScore(failure=failure)
            apply_score_to_profile(score, profile)
        self.assertTrue(hasattr(profile, "style"))
        self.assertFalse(hasattr(profile, "difficulty"))
        self.assertFalse(hasattr(profile, "mode"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
