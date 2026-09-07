"""Unit tests for event processing + adaptation rules."""
import os
import unittest
from app.agents.student_model import EventCollector, EventType, StudentModel
from app.agents.student_model.store import _resolve


class _TempStudent:
    """Isolate each test on a throwaway student id; cleans up its files."""
    def __init__(self, test):
        self.test = test
        self.sid = "student_test_" + os.urandom(3).hex()
        self.sm = StudentModel(self.sid).load()
    def cleanup(self):
        for ext in (".json", ".events.jsonl"):
            try:
                _resolve(self.sid, ext).unlink()
            except OSError:
                pass


class TestEventProcessing(unittest.TestCase):
    def setUp(self):
        self.ctx = _TempStudent(self)
    def tearDown(self):
        self.ctx.cleanup()

    def test_concept_taught_introduces(self):
        sm = self.ctx.sm
        sm.note_concept_taught(concept="牛顿第二定律", subject="物理")
        sm.load()
        rec = sm.memory.get("physics.dynamics.newton_second")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state.value, "introduced")
        self.assertIn("物理", sm.profile.subjects)

    def test_quiz_graded_drives_mastery_and_weakness(self):
        sm = self.ctx.sm
        for _ in range(3):
            sm.record_quiz_result(concept="牛顿第二定律", correct=False, subject="物理", note="错")
        sm.load()
        nid = "physics.dynamics.newton_second"
        self.assertLess(sm.mastery.get(nid).p_known, 0.4)
        self.assertIn("牛顿第二定律", sm.profile.weak_points)

    def test_consecutive_correct_becomes_strong(self):
        sm = self.ctx.sm
        sm.note_concept_taught(concept="牛顿第二定律", subject="物理")
        for _ in range(4):
            sm.record_quiz_result(concept="牛顿第二定律", correct=True, subject="物理")
        sm.load()
        nid = "physics.dynamics.newton_second"
        self.assertGreaterEqual(sm.mastery.get(nid).p_known, 0.7)
        self.assertIn(nid, sm.strong_skills())

    def test_goal_recorded(self):
        sm = self.ctx.sm
        col = EventCollector()
        col.goal("高考数学130+")
        sm.record_events(col.drain())
        sm.load()
        self.assertIn("高考数学130+", sm.profile.goals)

    def test_processor_idempotent_on_bad_event(self):
        sm = self.ctx.sm
        # malformed event: missing required fields -> must not raise
        col = EventCollector()
        col.add(EventType.QUIZ_GRADED, {})  # no concept/correct
        sm.record_events(col.drain())
        sm.load()
        # events_processed should still advance (bad event skipped gracefully)
        self.assertGreater(sm.profile.events_processed, 0)


class TestAdaptation(unittest.TestCase):
    def setUp(self):
        self.ctx = _TempStudent(self)
    def tearDown(self):
        self.ctx.cleanup()

    def test_unmet_prereq_triggers_review_first(self):
        sm = self.ctx.sm
        # weaken newton_second so it is the lone weak prereq of buoyancy
        sm.record_quiz_result(concept="牛顿第二定律", correct=False, subject="物理")
        sm.record_quiz_result(concept="牛顿第二定律", correct=False, subject="物理")
        # satisfy the other buoyancy prereqs
        sm.mastery.record_observation("physics.kinematics.velocity", True)
        sm.mastery.record_observation("physics.kinematics.velocity", True)
        sm.mastery.record_observation("physics.mechanics.gravity", True)
        sm.mastery.record_observation("physics.mechanics.gravity", True)
        strat = sm.adapt("浮力", subject="物理", intent="explain")
        names = [n.name for n in strat.review_first]
        self.assertTrue(any("牛顿" in n for n in names), names)
        self.assertEqual(strat.explanation_depth, "basic")

    def test_high_mastery_suggests_deep_and_hard(self):
        sm = self.ctx.sm
        for _ in range(5):
            sm.record_quiz_result(concept="牛顿第二定律", correct=True, subject="物理")
        strat = sm.adapt("牛顿第二定律", subject="物理", intent="explain")
        self.assertEqual(strat.explanation_depth, "deep")
        self.assertEqual(strat.suggested_quiz_difficulty, "hard")


if __name__ == "__main__":
    unittest.main()
