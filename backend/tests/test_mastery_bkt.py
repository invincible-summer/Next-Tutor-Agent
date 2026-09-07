"""Unit tests for the BKT mastery engine (math correctness)."""
import unittest
from app.agents.student_model.mastery import BKTParams, Mastery, MasteryTracker


class TestBKT(unittest.TestCase):
    def test_repeated_correct_monotonic_increase(self):
        t = MasteryTracker()
        seq = [t.record_observation("s", True) for _ in range(6)]
        for a, b in zip(seq, seq[1:]):
            self.assertGreaterEqual(b, a - 1e-9)
        self.assertGreater(seq[-1], 0.85)

    def test_repeated_incorrect_decreases(self):
        t = MasteryTracker()
        seq = [t.record_observation("s", False) for _ in range(5)]
        for a, b in zip(seq, seq[1:]):
            self.assertLessEqual(b, a + 1e-9)
        self.assertLess(seq[-1], seq[0])

    def test_clamp_never_exits_unit_interval(self):
        t = MasteryTracker()
        for _ in range(100):
            t.record_observation("s", True)
        for _ in range(100):
            t.record_observation("s", False)
        p = t.get("s").p_known
        self.assertGreater(p, 0.0)
        self.assertLess(p, 1.0)

    def test_weighted_blend_formula(self):
        t = MasteryTracker()
        p = t.record_performance("s", 0.9, weight=0.3)
        self.assertAlmostEqual(p, 0.7 * 0.1 + 0.3 * 0.9, places=5)

    def test_round_trip(self):
        t = MasteryTracker()
        t.record_observation("a", True)
        t.record_observation("a", False, note="oops")
        t.record_observation("b", True)
        d = t.to_dict()
        t2 = MasteryTracker(d)
        self.assertEqual(t2.to_dict(), d)

    def test_reset(self):
        t = MasteryTracker()
        for _ in range(5):
            t.record_observation("s", True)
        t.reset("s")
        self.assertEqual(t.get("s").p_known, 0.1)


if __name__ == "__main__":
    unittest.main()
