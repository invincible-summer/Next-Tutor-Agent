"""Tests for the L1 unified activity aggregator.

Covers: the five-ledger day union, the legacy-episodes compatibility fallback,
streak math over day strings, per-day classified counts (answers / teachings /
reviews), and the never-raises contract. All storage roots are sandboxed via
StorageSandboxTestCase (the aggregator reads each ledger through its owning
store module, so the sandbox redirects apply automatically).
"""
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase
from app.agents import activity_aggregator
from app.agents.learning_orchestration.schema import OrchestrationEvent
from app.agents.memory.schema import EpisodicMemory
from app.agents.memory import store as memory_store
from app.agents.teaching_engine import teaching_log
from app.core import learning_records as lr


def _d(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class TestActivityAggregator(StorageSandboxTestCase):

    SID = "sandbox_agg_student"

    def _seed_each_source(self, now: float) -> None:
        """One activity day per live ledger: today (records + teaching) /
        -2d / -3d / -4d."""
        # 1. learning records: asked + graded today (graded = an answer)
        rid = lr.record_question(
            self.SID, "sess1",
            {"id": "q1", "stem": "1+1=?", "answer": "2", "type": "short_answer",
             "concept": "算术"}, source_kind="chat")
        lr.record_verdict(self.SID, "sess1", stem="1+1=?", verdict="correct",
                          student_answer="2", concept="算术")
        self.assertTrue(rid)
        # 2. teaching log: a turn today (record_turn_outcome stamps now)
        teaching_log.record_turn_outcome(
            self.SID, "math.arithmetic.basic", mode="explanation",
            outcome="good")
        # 3. orchestration events: two days ago
        from app.agents.learning_orchestration import store as orch_store
        self.assertTrue(orch_store.append_event(
            self.SID, OrchestrationEvent(ts=now - 2 * 86400,
                                         type="task_batch_completed")))
        # 4. ux events: three days ago
        from app.agents.ux_intelligence import store as ux_store
        from app.agents.ux_intelligence.schema import UXEvent
        self.assertTrue(ux_store.append_event(
            self.SID, UXEvent(ts=now - 3 * 86400, student_id=self.SID,
                              type="feedback")))
        # 5. eval traces: four days ago
        from app.agents.evaluation import store as eval_store
        from app.agents.evaluation.schema import TurnTrace
        self.assertTrue(eval_store.append_trace(
            self.SID, TurnTrace(ts=now - 4 * 86400)))

    def test_empty_student_is_none_source(self):
        snap = activity_aggregator.activity_snapshot("nobody_here")
        self.assertEqual(snap["source"], "none")
        self.assertEqual(snap["streak_days"], 0)
        self.assertEqual(activity_aggregator.active_days("nobody_here"), set())

    def test_union_across_all_five_ledgers(self):
        now = time.time()
        self._seed_each_source(now)
        expected = {_d(now), _d(now - 2 * 86400), _d(now - 3 * 86400),
                    _d(now - 4 * 86400)}
        self.assertEqual(activity_aggregator.active_days(self.SID), expected)
        snap = activity_aggregator.activity_snapshot(self.SID, now=now)
        self.assertEqual(snap["source"], "aggregated")
        self.assertEqual(snap["active_days"], 4)
        # day -1 has no activity -> current streak is just today
        self.assertEqual(snap["streak_days"], 1)
        # the -2..-4d run is the longest consecutive stretch
        self.assertEqual(snap["longest_streak"], 3)

    def test_legacy_episodes_fallback(self):
        now = time.time()
        self.assertTrue(memory_store.append_episode(
            self.SID, EpisodicMemory(ts=now, summary="legacy day",
                                     event_type="concept_taught")))
        snap = activity_aggregator.activity_snapshot(self.SID, now=now)
        self.assertEqual(snap["source"], "legacy_episodes")
        self.assertEqual(snap["active_days"], 1)
        # the plain day-union API falls back identically
        self.assertEqual(activity_aggregator.active_days(self.SID), {_d(now)})

    def test_live_source_beats_legacy(self):
        now = time.time()
        self.assertTrue(memory_store.append_episode(
            self.SID, EpisodicMemory(ts=now - 10 * 86400, summary="old")))
        lr.record_question(
            self.SID, "s", {"id": "q2", "stem": "2+2=?", "answer": "4"},
            source_kind="chat")
        snap = activity_aggregator.activity_snapshot(self.SID, now=now)
        # live ledger present -> legacy row ignored entirely
        self.assertEqual(snap["source"], "aggregated")

    def test_daily_counts_classification(self):
        now = time.time()
        # graded answer today + ungraded question today (not an answer yet)
        lr.record_question(
            self.SID, "s", {"id": "g1", "stem": "graded", "answer": "a"},
            source_kind="chat")
        lr.record_verdict(self.SID, "s", stem="graded", verdict="correct")
        lr.record_question(
            self.SID, "s", {"id": "u1", "stem": "ungraded", "answer": "a"},
            source_kind="chat")
        teaching_log.record_turn_outcome(
            self.SID, "c1", mode="explanation", outcome="good")
        from app.agents.learning_orchestration import store as orch_store
        orch_store.append_event(
            self.SID, OrchestrationEvent(ts=now, type="task_batch_completed"))
        rows = activity_aggregator.daily_counts(self.SID, days=3, now=now)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["date"], _d(now))
        self.assertEqual(rows[-1]["answers"], 1)      # only the graded one
        self.assertEqual(rows[-1]["teachings"], 1)
        self.assertEqual(rows[-1]["reviews"], 1)

    def test_daily_counts_clamps_bad_window(self):
        rows = activity_aggregator.daily_counts(self.SID, days=0)
        self.assertEqual(len(rows), 1)  # clamped to >=1

    def test_never_raises_on_garbage_store_files(self):
        # corrupt ledgers must not break the aggregator
        students = self.root / "students"
        students.mkdir(parents=True, exist_ok=True)
        (students / f"{self.SID}.learning_records.json").write_text(
            "{not json", encoding="utf-8")
        snap = activity_aggregator.activity_snapshot(self.SID)
        self.assertIsInstance(snap["source"], str)
        self.assertIsInstance(
            activity_aggregator.daily_counts(self.SID), list)

    def test_streak_from_days_pure_math(self):
        now = time.time()
        # today + yesterday + a 3-day-older isolated pair
        days = {_d(now), _d(now - 86400), _d(now - 5 * 86400),
                _d(now - 6 * 86400)}
        current, longest, last, total = activity_aggregator.streak_from_days(
            days, now=now)
        self.assertEqual(current, 2)
        self.assertEqual(longest, 2)
        self.assertEqual(total, 4)
        self.assertEqual(last, _d(now))
