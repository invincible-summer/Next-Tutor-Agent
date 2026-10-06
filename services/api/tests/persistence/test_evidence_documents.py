"""Evidence/assessment domain SQL cutover contract (ADR-0017).

The learner evidence journal (assessment attempts, jobs, CAT instances,
sources) and the student profile blob route to evidence_documents.
Everything runs through the public journal API — the same surface
assessment manager / evaluation service / projections use.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class EvidenceDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'evidence.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "evidence=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()
        from app.agents.student_model.evaluation import store as journal_store

        journal_store.reset_journal_cache()

    def tearDown(self) -> None:
        from app.persistence import db
        from app.persistence.documents import bridge
        from app.agents.student_model.evaluation import store as journal_store

        journal_store.reset_journal_cache()
        bridge.reset_worker()
        for key, old in self._saved_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        engine = db._engine_cache.pop(self._db_url, None)
        if engine is not None:
            engine.sync_engine.dispose()
        db._session_factory_cache.pop(self._db_url, None)
        super().tearDown()

    def _journal(self):
        from app.agents.student_model.evaluation.store import get_journal

        return get_journal("u1")

    def test_append_state_and_seq_allocation(self) -> None:
        from app.agents.student_model.evaluation import schema as S
        from app.agents.student_model.evaluation.store import get_journal

        journal = get_journal("u1")
        receipt = S.SourceReceipt(
            source_id="src_1", source_revision=1,
            kind=S.SourceKind.DIALOGUE, observed_at="2026-01-01T00:00:00Z",
            canonical_text="canonical text of the source")
        journal.register_source(receipt)
        journal.register_question(S.TaskSnapshot(
            question_id="q1", question_revision=1,
            q_type=S.QuestionType.SHORT_ANSWER,
            stem="1+1=?", answer="2",
            rubric=[S.FrozenCriterion(
                id="c1", description="答对", weight=1.0)]))
        journal.append([S.OpResultCommitted(
            job_id="job_1", source_id="src_1", source_revision=1,
            scope_revision="scope_1",
            task_result=S.TaskResult(
                question_ref=S.QuestionRef(
                    question_id="q1", question_revision=1),
                grading_status=S.GradingStatus.GRADED,
                verdict=S.Verdict.CORRECT, task_score=1.0))])

        state = journal.state()
        self.assertEqual(state.last_seq, 3)
        self.assertEqual(len(state.tasks), 1)
        self.assertIn("q1", state.tasks)

        # A second journal instance (different cache entry) sees the same
        # facts — the row is the cross-process truth.
        from app.agents.student_model.evaluation.store import (
            reset_journal_cache)

        reset_journal_cache()
        fresh = get_journal("u1")
        self.assertEqual(fresh.state().last_seq, 3)

    def test_generation_conflict_and_rewrite(self) -> None:
        from app.agents.student_model.evaluation import schema as S
        from app.agents.student_model.evaluation.store import (
            GenerationConflictError, get_journal)

        journal = get_journal("u1")
        receipt = S.SourceReceipt(
            source_id="src_1", source_revision=1,
            kind=S.SourceKind.DIALOGUE, observed_at="2026-01-01T00:00:00Z",
            canonical_text="canonical text of the source")
        journal.register_source(receipt)
        generation = journal.state().generation

        with self.assertRaises(GenerationConflictError):
            journal.append([S.OpConsumerAck(event_id="e", consumer="c")],
                           expected_generation="wrong-gen")

        # rewrite keeps only the source registration, bumps generation
        def keep(tx: S.JournalTransaction) -> bool:
            return any(isinstance(op, S.OpSourceRegistered)
                       for op in tx.operations)

        new_gen = journal.rewrite(keep, "test-rewrite")
        self.assertNotEqual(new_gen, generation)
        state = journal.state()
        self.assertIn("src_1", state.sources)
        self.assertEqual(state.last_seq, 1)

    def test_profile_blob_roundtrip_and_purge(self) -> None:
        from app.agents.student_model import store as sm_store
        from app.agents.student_model.evaluation.store import (
            purge_journal_files)
        from app.agents.student_model.state import StudentProfile

        sm_store.save_blob("u1", StudentProfile(id="u1", grade="G8"))
        loaded = sm_store.load_blob("u1")
        self.assertEqual(loaded.profile.grade, "G8")

        self.assertTrue(purge_journal_files("u1"))
        from app.agents.student_model.evaluation import sql_store
        self.assertIsNone(sql_store.load_profile_blob("u1"))
        self.assertEqual(sm_store.load_blob("u1").profile.id, "u1")

    def test_isolation_between_students(self) -> None:
        from app.agents.student_model.evaluation import schema as S
        from app.agents.student_model.evaluation.store import get_journal

        for sid in ("u1", "u2"):
            get_journal(sid).register_source(S.SourceReceipt(
                source_id=f"src_{sid}", source_revision=1,
                kind=S.SourceKind.DIALOGUE, observed_at="2026-01-01T00:00:00Z",
                canonical_text=f"source of {sid}"))
        self.assertIn("src_u1", get_journal("u1").state().sources)
        self.assertNotIn("src_u2", get_journal("u1").state().sources)
        self.assertEqual(get_journal("u2").state().last_seq, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
