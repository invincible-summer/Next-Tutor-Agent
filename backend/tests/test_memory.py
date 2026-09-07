"""Tests for M6 Memory Intelligence layer.

Covers: schema round-trips, store persistence, classifier rules, episodic
legacy-read helpers, semantic conflict resolution, procedural success-rate
sliding window, JIT retrieval fusion, directive rendering, manager
end-to-end. Uses a temp students/ dir so tests are hermetic.

（旧语义巩固 consolidation 与 episodic 写侧包装已删除——C1/C2/C3：
生产零调用；episodes 文件保留为只读审计，测试经 store 原语播种。）
"""
import os
import sys
import time
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend/ is on sys.path
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.agents.memory.schema import (EpisodicMemory, Importance, MemoryScope,
                                       ProceduralMemory, SemanticFact,
                                       SEMANTIC_CATEGORIES, MIN_TRIALS_FOR_INJECTION)
from app.agents.memory import classifier
from app.agents.memory.classifier import ClassificationResult, classify_turn, classify_events
from app.agents.memory import episodic
from app.agents.memory import semantic
from app.agents.memory import procedural
from app.agents.memory import retrieval
from app.agents.memory import context_builder
from app.agents.memory import store
from app.agents.memory.manager import MemoryService, get_memory_service, is_enabled


class TestSchema(unittest.TestCase):
    """M6.1: schema round-trips + enum validation."""

    def test_episodic_roundtrip(self):
        ep = EpisodicMemory(id="ep_1", summary="completed test", event_type="quiz_graded",
                            concept="calculus", subject="math", score=0.85,
                            emotion="confident", importance=0.8, scope=MemoryScope.CONCEPT)
        d = ep.to_dict()
        self.assertEqual(d["id"], "ep_1")
        self.assertEqual(d["scope"], "concept")
        ep2 = EpisodicMemory.from_dict(d)
        self.assertEqual(ep2.summary, "completed test")
        self.assertEqual(ep2.score, 0.85)
        self.assertEqual(ep2.scope, MemoryScope.CONCEPT)

    def test_semantic_roundtrip(self):
        f = SemanticFact(id="sf_1", fact="prefers examples", category="preference",
                         confidence=0.9, evidence_count=3, scope=MemoryScope.SUBJECT)
        d = f.to_dict()
        f2 = SemanticFact.from_dict(d)
        self.assertEqual(f2.fact, "prefers examples")
        self.assertEqual(f2.category, "preference")
        self.assertEqual(f2.confidence, 0.9)
        self.assertIsNone(f2.superseded_by)

    def test_procedural_roundtrip(self):
        p = ProceduralMemory(strategy="visual_analogy", subject="physics",
                             success_rate=0.82, trials=5)
        d = p.to_dict()
        p2 = ProceduralMemory.from_dict(d)
        self.assertEqual(p2.strategy, "visual_analogy")
        self.assertEqual(p2.trials, 5)

    def test_importance_from_value(self):
        self.assertAlmostEqual(Importance.from_value(0.8), 0.8)
        self.assertAlmostEqual(Importance.from_value(Importance.HIGH), 0.8)
        self.assertAlmostEqual(Importance.from_value("invalid"), Importance.NORMAL.value)
        self.assertAlmostEqual(Importance.from_value(1.5), 1.0)
        self.assertAlmostEqual(Importance.from_value(-0.5), 0.0)

    def test_memory_scope_from_value(self):
        self.assertEqual(MemoryScope.from_value("concept"), MemoryScope.CONCEPT)
        self.assertEqual(MemoryScope.from_value("invalid"), MemoryScope.GLOBAL)
        self.assertEqual(MemoryScope.from_value(None), MemoryScope.GLOBAL)

    def test_semantic_categories_whitelist(self):
        # preference and goal are EXCLUDED (M2 StudentProfile's domain)
        self.assertNotIn("preference", SEMANTIC_CATEGORIES)
        self.assertNotIn("goal", SEMANTIC_CATEGORIES)
        self.assertIn("context", SEMANTIC_CATEGORIES)
        self.assertIn("misconception_pattern", SEMANTIC_CATEGORIES)
        self.assertNotIn("random_category", SEMANTIC_CATEGORIES)

    def test_search_text(self):
        ep = EpisodicMemory(summary="learned calculus", concept="integrals",
                            subject="math")
        self.assertIn("calculus", ep.search_text())
        self.assertIn("integrals", ep.search_text())


class TestStore(unittest.TestCase):
    """M6.1: persistence layer."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self._tmpdir = tempfile.mkdtemp(prefix="m6_store_")
        store._STUDENTS_DIR = Path(self._tmpdir)

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir

    def test_append_and_read_episode(self):
        ep = EpisodicMemory(summary="test event", event_type="quiz_graded")
        self.assertTrue(store.append_episode("s1", ep))
        eps = store.read_episodes("s1")
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0].summary, "test event")

    def test_read_episodes_empty(self):
        self.assertEqual(store.read_episodes("nonexistent"), [])

    def test_semantic_save_load(self):
        f = SemanticFact(id="sf_1", fact="goal A", category="goal")
        store.save_semantic_facts("s1", [f])
        loaded = store.load_semantic_facts("s1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].fact, "goal A")

    def test_supersede_excludes_from_active(self):
        f1 = SemanticFact(id="sf_1", fact="old", category="goal")
        f2 = SemanticFact(id="sf_2", fact="new", category="goal")
        store.save_semantic_facts("s1", [f1, f2])
        store.supersede_semantic_fact("s1", "sf_1", "sf_2")
        active = store.load_semantic_facts("s1")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].id, "sf_2")
        all_facts = store.load_all_semantic_facts("s1")
        self.assertEqual(len(all_facts), 2)

    def test_procedural_save_load(self):
        p = ProceduralMemory(strategy="vis", subject="math", success_rate=0.7, trials=3)
        store.save_procedural("s1", [p])
        loaded = store.load_procedural("s1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].strategy, "vis")

    def test_reject_unknown_category(self):
        f = SemanticFact(fact="x", category="bad_category")
        store.add_or_update_semantic_fact("s1", f)
        self.assertEqual(store.load_semantic_facts("s1"), [])

    def test_working_set_ignores_removed_consolidation_key(self):
        # consolidation 状态已随旧语义巩固整簇删除（C2）；旧文件里的
        # "consolidation" 键不阻碍读回（未知键原样保留、semantic 正常解析）。
        raw = {"semantic": [{"id": "sf_1", "fact": "旧事实", "category": "context"}],
               "procedural": [],
               "consolidation": {"events_since_last": 5, "last_ts": 1.0}}
        path = store._resolve("s1", ext=".semantic.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        facts = store.load_semantic_facts("s1")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].fact, "旧事实")


class TestClassifier(unittest.TestCase):
    """M6.2: rule-based classification."""

    def test_quiz_graded_high_score(self):
        results = classify_turn(event_type="quiz_graded", concept="integrals",
                                score=0.9, subject="math")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].memory_type, "episodic")
        self.assertEqual(results[0].emotion, "confident")
        self.assertAlmostEqual(results[0].importance, Importance.HIGH.value)

    def test_quiz_graded_low_score(self):
        results = classify_turn(event_type="quiz_graded", concept="integrals",
                                score=0.2, subject="math")
        self.assertEqual(results[0].emotion, "frustrated")
        self.assertAlmostEqual(results[0].importance, Importance.HIGH.value)

    def test_concept_taught(self):
        results = classify_turn(event_type="concept_taught", concept="derivatives",
                                subject="math", brief="taught chain rule")
        self.assertEqual(results[0].memory_type, "episodic")
        self.assertEqual(results[0].importance, Importance.LOW.value)
        self.assertIn("derivatives", results[0].summary)

    def test_goal_not_classified_by_m6(self):
        """Boundary guard: goals are M2 StudentProfile's domain, not M6's.
        The classifier must NOT produce semantic items for goal statements."""
        results = classify_turn(event_type="goal_set",
                                user_message="我准备高考数学",
                                subject="math")
        semantic_results = [r for r in results if r.memory_type == "semantic"]
        self.assertEqual(len(semantic_results), 0)

    def test_preference_not_classified_by_m6(self):
        """Boundary guard: preferences are M2 StudentProfile.learning_style's
        domain, not M6's. The classifier must NOT produce semantic items."""
        results = classify_turn(event_type="concept_taught",
                                user_message="我喜欢先看例题再总结公式",
                                subject="math")
        semantic_results = [r for r in results if r.memory_type == "semantic"]
        self.assertEqual(len(semantic_results), 0)

    def test_classify_events_batch(self):
        events = [
            {"type": "quiz_graded", "payload": {"concept": "derivatives",
             "correct": True, "subject": "math"}},
            {"type": "concept_taught", "payload": {"concept": "limits",
             "subject": "math"}},
        ]
        results = classify_events(events)
        self.assertTrue(len(results) >= 2)
        self.assertTrue(any(r.event_type == "quiz_graded" for r in results))

    def test_chitchat_no_results(self):
        results = classify_turn(event_type="concept_taught", user_message="hi")
        self.assertEqual(len(results), 1)


class TestEpisodic(unittest.TestCase):
    """M6.2: episodic store + dedup."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        store._STUDENTS_DIR = Path(tempfile.mkdtemp(prefix="m6_ep_"))

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir

    def test_recent_episodes_order(self):
        # 写侧包装（append_episode 去重层）已删除（C1，生产零调用）；
        # 播种走 store 原语，读取助手 recent_episodes 保持最新在前。
        for i in range(3):
            store.append_episode("s1", EpisodicMemory(
                summary=f"event {i}", event_type="quiz_graded", concept="x"))
        eps = episodic.recent_episodes("s1")
        self.assertEqual(len(eps), 3)
        self.assertEqual(eps[0].summary, "event 2")

    def test_episodes_for_concept(self):
        ep1 = EpisodicMemory(summary="about calculus", event_type="quiz_graded",
                             concept="calculus")
        ep2 = EpisodicMemory(summary="about physics", event_type="quiz_graded",
                             concept="physics")
        store.append_episode("s1", ep1)
        store.append_episode("s1", ep2)
        matched = episodic.episodes_for_concept("s1", "calculus")
        self.assertEqual(len(matched), 1)


class TestSemanticConflict(unittest.TestCase):
    """M6.5: conflict resolver."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        store._STUDENTS_DIR = Path(tempfile.mkdtemp(prefix="m6_sem_"))

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir

    def test_supporting_evidence_increments(self):
        f = SemanticFact(fact="confuses abstract symbols", category="context",
                         confidence=0.5, scope=MemoryScope.SUBJECT, subject="math")
        result = semantic.add_or_consolidate("s1", f)
        self.assertIsNotNone(result)
        result2 = semantic.add_or_consolidate("s1", f)
        self.assertGreater(result2.evidence_count, 1)
        self.assertGreater(result2.confidence, 0.5)

    def test_contradiction_supersedes(self):
        f1 = SemanticFact(fact="confuses abstract symbols", category="context",
                          scope=MemoryScope.SUBJECT, subject="math")
        semantic.add_or_consolidate("s1", f1)
        f2 = SemanticFact(fact="learns better with visual aids", category="context",
                          scope=MemoryScope.SUBJECT, subject="math")
        result = semantic.add_or_consolidate("s1", f2)
        self.assertIsNotNone(result)
        active = semantic.active_facts("s1")
        self.assertEqual(len(active), 1)
        self.assertIn("visual aids", active[0].fact)
        all_facts = store.load_all_semantic_facts("s1")
        self.assertEqual(len(all_facts), 2)

    def test_different_categories_coexist(self):
        f1 = SemanticFact(fact="math weak", category="context")
        f2 = SemanticFact(fact="confuses derivative with function value",
                          category="misconception_pattern")
        semantic.add_or_consolidate("s1", f1)
        semantic.add_or_consolidate("s1", f2)
        self.assertEqual(len(semantic.active_facts("s1")), 2)

    def test_injectable_filters_low_confidence(self):
        f = SemanticFact(fact="maybe", category="goal", confidence=0.1)
        semantic.add_or_consolidate("s1", f)
        self.assertEqual(semantic.injectable_facts("s1"), [])


class TestProcedural(unittest.TestCase):
    """M6.4: procedural memory + sliding window."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        store._STUDENTS_DIR = Path(tempfile.mkdtemp(prefix="m6_proc_"))

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir

    def test_record_outcome_updates_rate(self):
        for _ in range(5):
            procedural.record_outcome("s1", "explanation", "math", "correct")
        items = procedural.all_procedural("s1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].trials, 5)
        self.assertGreater(items[0].success_rate, 0.5)

    def test_low_trial_not_injectable(self):
        procedural.record_outcome("s1", "vis", "math", "correct")
        self.assertEqual(procedural.injectable_strategies("s1", "math"), [])

    def test_high_trial_injectable(self):
        for _ in range(MIN_TRIALS_FOR_INJECTION + 1):
            procedural.record_outcome("s1", "vis", "math", "correct")
        good = procedural.injectable_strategies("s1", "math")
        self.assertEqual(len(good), 1)

    def test_active_procedural_write_does_not_mutate_legacy_semantic(self):
        legacy = SemanticFact(id="legacy", fact="旧审计事实", category="context")
        store.save_semantic_facts("s1", [legacy])
        legacy_path = store._resolve("s1", ext=".semantic.json")
        before = legacy_path.read_bytes()
        procedural.record_outcome("s1", "visual", "math", "correct")
        self.assertEqual(legacy_path.read_bytes(), before)
        self.assertTrue(store._resolve("s1", ext=".procedural.json").exists())

    def test_mixed_outcomes(self):
        for _ in range(3):
            procedural.record_outcome("s1", "vis", "math", "correct")
        for _ in range(3):
            procedural.record_outcome("s1", "vis", "math", "wrong")
        items = procedural.all_procedural("s1")
        self.assertGreater(items[0].success_rate, 0.0)
        self.assertLess(items[0].success_rate, 1.0)


class TestRetrieval(unittest.TestCase):
    """M6.3: JIT retrieval fusion."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        store._STUDENTS_DIR = Path(tempfile.mkdtemp(prefix="m6_ret_"))

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir

    def test_empty_returns_empty(self):
        hits = retrieval.retrieve("s1", "calculus")
        self.assertEqual(hits, [])

    def test_retrieves_relevant_episode(self):
        ep = EpisodicMemory(summary="struggled with integral substitution",
                            event_type="quiz_graded", concept="integrals",
                            subject="math")
        store.append_episode("s1", ep)
        hits = retrieval.retrieve("s1", "integral substitution",
                                  concept="integrals", subject="math")
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0].kind, "episodic")
        self.assertIn("integral", hits[0].text.lower())

    def test_irrelevant_not_retrieved(self):
        ep = EpisodicMemory(summary="learned about photosynthesis",
                            event_type="concept_taught", concept="biology")
        store.append_episode("s1", ep)
        hits = retrieval.retrieve("s1", "quantum mechanics")
        self.assertEqual(hits, [])

    def test_procedural_filtered_by_trials(self):
        procedural.record_outcome("s1", "vis", "math", "correct")
        hits = retrieval.retrieve("s1", "visual")
        proc_hits = [h for h in hits if h.kind == "procedural"]
        self.assertEqual(proc_hits, [])


class TestContextBuilder(unittest.TestCase):
    """M6.3: directive rendering."""

    def test_empty_hits_returns_empty(self):
        directive = context_builder.build_and_render([])
        self.assertEqual(directive, "")

    def test_episodic_rendered(self):
        from app.agents.memory.retrieval import MemoryHit
        hit = MemoryHit(kind="episodic", id="ep_1", text="struggled with integrals",
                        score=1.0, concept="integrals")
        directive = context_builder.build_and_render([hit])
        self.assertIn("[记忆智能·过往经验]", directive)
        self.assertIn("integrals", directive)

    def test_all_kinds_rendered(self):
        from app.agents.memory.retrieval import MemoryHit
        hits = [
            MemoryHit(kind="episodic", id="e1", text="ep", score=1.0),
            MemoryHit(kind="procedural", id="p1", text="vis", score=1.0),
            MemoryHit(kind="semantic", id="s1", text="fact", score=1.0),
        ]
        directive = context_builder.build_and_render(hits)
        self.assertIn("过往经验", directive)
        self.assertIn("有效策略", directive)
        self.assertIn("长期事实", directive)

    def test_low_score_filtered(self):
        from app.agents.memory.retrieval import MemoryHit
        hit = MemoryHit(kind="episodic", id="e1", text="low", score=0.001)
        directive = context_builder.build_and_render([hit])
        self.assertEqual(directive, "")


class TestManager(unittest.TestCase):
    """End-to-end manager facade tests."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="m6_mgr_"))
        store._STUDENTS_DIR = self._tmp_dir
        from app.agents.memory import prompt_memory
        self._orig_prompt_dir = prompt_memory._STUDENTS_DIR
        self._orig_prompt_policy = prompt_memory._POLICY_PATH
        prompt_memory._STUDENTS_DIR = self._tmp_dir
        prompt_memory._POLICY_PATH = self._tmp_dir / "prompt_memory_policy.json"

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        from app.agents.memory import prompt_memory
        prompt_memory._STUDENTS_DIR = self._orig_prompt_dir
        prompt_memory._POLICY_PATH = self._orig_prompt_policy

    def test_consume_turn_writes_prompt_contribution_not_episodic(self):
        ms = get_memory_service()
        events = [{"type": "quiz_graded", "payload": {"concept": "derivatives",
                  "correct": True, "subject": "math", "note": "good"}}]
        stats = ms.consume_turn(student_id="s1", session_id="chat-1", events=events)
        self.assertEqual(stats["episodic_added"], 0)
        self.assertEqual(ms.episodic_count("s1"), 0)
        self.assertEqual(stats["prompt_memory"]["status"], "updated")

    def test_consume_turn_with_procedural(self):
        ms = get_memory_service()
        events = [{"type": "quiz_graded", "payload": {"concept": "x", "correct": True}}]
        stats = ms.consume_turn(student_id="s1", events=events,
                                strategy_mode="explanation", strategy_outcome="correct",
                                subject="math")
        self.assertGreater(stats["procedural_updated"], 0)

    def test_build_directive_empty_no_memory(self):
        ms = get_memory_service()
        directive = ms.build_directive(student_id="s1", concept="x", subject="y")
        self.assertEqual(directive, "")

    def test_build_directive_after_consume(self):
        ms = get_memory_service()
        events = [{"type": "quiz_graded", "payload": {"concept": "integrals",
                  "correct": False, "subject": "math", "note": "substitution failed"}}]
        ms.consume_turn(student_id="s1", session_id="chat-1", events=events)
        directive = ms.build_directive(student_id="s1", concept="integrals",
                                       subject="math")
        self.assertIn("提示词记忆", directive)
        self.assertNotIn("integrals", directive)

    def test_disabled_flag(self):
        with patch.dict(os.environ, {"MEMORY_INTELLIGENCE_MODE": "0"}):
            self.assertFalse(is_enabled())
        self.assertTrue(is_enabled())


class TestSupervisorIntegration(unittest.TestCase):
    """Verify the supervisor hooks exist and are guarded."""

    def test_hooks_exist(self):
        from app.agents.supervisor import (_memory_directive_for_turn,
                                           _memory_consolidate_turn)
        self.assertTrue(callable(_memory_directive_for_turn))
        self.assertTrue(callable(_memory_consolidate_turn))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# HabitPatternMemory (modification 2b: M6 owns long-term behaviour patterns)
# ---------------------------------------------------------------------------

class TestHabitPattern(unittest.TestCase):
    """M6 HabitPatternMemory: deterministic derivation + consolidation."""

    def setUp(self):
        self._orig_dir = store._STUDENTS_DIR
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="m6_habit_"))
        store._STUDENTS_DIR = self._tmp_dir
        from app.agents.memory import prompt_memory
        self._orig_prompt_dir = prompt_memory._STUDENTS_DIR
        self._orig_prompt_policy = prompt_memory._POLICY_PATH
        prompt_memory._STUDENTS_DIR = self._tmp_dir
        prompt_memory._POLICY_PATH = self._tmp_dir / "prompt_memory_policy.json"

    def tearDown(self):
        store._STUDENTS_DIR = self._orig_dir
        from app.agents.memory import prompt_memory
        prompt_memory._STUDENTS_DIR = self._orig_prompt_dir
        prompt_memory._POLICY_PATH = self._orig_prompt_policy

    def test_derive_weekly_streak_fact(self):
        from app.agents.memory import habit_pattern as hp
        events = [{"event_type": "habit_milestone", "payload": {"streak": 7}}]
        facts = hp.derive_habit_facts(events)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].category, "study_habit")
        self.assertIn("持续学习", facts[0].fact)

    def test_derive_monthly_streak_fact(self):
        from app.agents.memory import habit_pattern as hp
        events = [{"event_type": "habit_milestone", "payload": {"streak": 30}}]
        facts = hp.derive_habit_facts(events)
        self.assertEqual(len(facts), 1)
        self.assertIn("长期学习毅力", facts[0].fact)

    def test_derive_batch_completion_fact(self):
        from app.agents.memory import habit_pattern as hp
        events = [{"event_type": "task_batch_completed"}] * 5
        facts = hp.derive_habit_facts(events)
        self.assertTrue(any("稳定完成每日" in f.fact for f in facts))

    def test_consolidate_accumulates_confidence_without_writing_legacy_semantic(self):
        """Repeated events update the bounded active habit aggregate only."""
        from app.agents.memory import habit_pattern as hp
        events = [{"event_type": "habit_milestone", "payload": {"streak": 7}}]
        hp.consolidate_habit_events("s1", events)
        hp.consolidate_habit_events("s1", events)
        habit_facts = hp.read_habit_patterns("s1")
        self.assertGreaterEqual(len(habit_facts), 1)
        self.assertGreaterEqual(habit_facts[0]["evidence_count"], 2)
        self.assertTrue(store._resolve("s1", ext=".habit_patterns.json").exists())
        self.assertFalse(store._resolve("s1", ext=".semantic.json").exists())

    def test_read_habit_patterns(self):
        from app.agents.memory import habit_pattern as hp
        hp.consolidate_habit_events("s1",
            [{"event_type": "habit_milestone", "payload": {"streak": 7}}])
        patterns = hp.read_habit_patterns("s1")
        self.assertIsInstance(patterns, list)
        self.assertTrue(len(patterns) >= 1)

    def test_consume_turn_folds_orchestration_events(self):
        """consume_turn should fold M9 orchestration events into study_habit
        facts (the M9->M6 integration point)."""
        ms = get_memory_service()
        events = [{"event_type": "habit_milestone", "summary": "连续学习7天",
                   "subject": "数学", "payload": {"streak": 7}}]
        stats = ms.consume_turn(student_id="s1", events=events, subject="数学")
        self.assertGreaterEqual(stats.get("habit_facts_updated", 0), 1)
