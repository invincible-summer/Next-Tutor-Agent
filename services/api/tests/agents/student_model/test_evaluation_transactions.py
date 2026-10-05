"""Synthetic, isolated regressions for evaluation read/check/append boundaries."""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import evaluator, lifecycle, schema as S
from app.agents.student_model.evaluation import store as st
from app.agents.student_model.evaluation.jobs import JobScheduler
from app.agents.student_model.evaluation.llm import StructuredOutput
from app.agents.student_model.evaluation.service import (
    CommitRejected, LearnerEvaluationService)
from app.agents.student_model.evaluation.worker import EvaluationWorker

SID = "usr_transaction_synthetic"
WS = "ws_transaction_synthetic"


class _Scope:
    def resolve(self, student_id, workspace_id):
        return S.EvaluationScope(workspace_id=workspace_id,
                                 scope_revision="scope_synthetic",
                                 selected_volumes=[], allowed_concepts=[],
                                 graph_revisions=[], unresolved_graph_count=0)


class EvaluationTransactionFixture(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        st.reset_journal_cache()
        self.journal = st.get_journal(SID)
        self.scheduler = JobScheduler()
        self.service = LearnerEvaluationService(self.scheduler)
        self.scope_patch = patch(
            "app.agents.student_model.evaluation.scope.get_scope_resolver",
            return_value=_Scope())
        self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)

    def register(self, suffix="synthetic_one", *, workspace="", kind=S.JobKind.DIALOGUE_EVALUATION):
        source = S.SourceReceipt(
            source_id="src_" + suffix, source_revision=1,
            kind=S.SourceKind.DIALOGUE, observed_at=S.utc_now_iso(),
            canonical_text="Synthetic answer about shared endpoints.",
            workspace_id_at_observation=workspace,
            scope_revision="scope_synthetic" if workspace else "")
        job = S.EvaluationJob(
            job_id="job_" + suffix, kind=kind, source_id=source.source_id,
            source_revision=1, workspace_id=workspace,
            scope_revision=source.scope_revision,
            created_at=S.utc_now_iso(), updated_at=S.utc_now_iso())
        self.journal.register_source(source, job)
        return source.model_copy(deep=True), job

    def pack(self, source, concept=None):
        return S.EvaluationContextPack(
            pack_id="pack_synthetic", source_id=source.source_id,
            prompt_binding="test_synthetic",
            allowlist=([S.PackConceptRefEntry(short_ref="c1", concept=concept)]
                       if concept else []),
            manifest=S.PackManifest(input_hash="synthetic_input",
                                    prompt_ref="test_synthetic"))

    def commit(self, source, claimed, **kwargs):
        return self.service.commit_result(
            SID, job_id=claimed.job.job_id, lease_token=claimed.lease_token,
            expected_generation=claimed.generation, source=source,
            pack=kwargs.pop("pack", self.pack(source)), task=None,
            interpretation=kwargs.pop("interpretation", None), **kwargs)

    def compete(self, first, second, pause_at):
        """Pause after the first check; observe the second executor's lock attempt.

        Events establish ordering; no scheduling sleep is needed. A split
        check/append implementation lets the second enter and fails this gate.
        """
        ready, release = threading.Event(), threading.Event()
        attempted, entered = threading.Event(), threading.Event()
        result = {}
        original_lock = st.file_lock

        def pause():
            ready.set()
            if not release.wait(5):
                raise AssertionError("first executor was not released")

        @contextmanager
        def observed_lock(path, **kwargs):
            second_thread = threading.current_thread().name == "eval-second"
            if second_thread:
                attempted.set()
            with original_lock(path, **kwargs):
                if second_thread:
                    entered.set()
                yield

        def run(key, callback):
            try:
                result[key] = callback()
            except Exception as exc:
                result[key] = exc

        first_thread = threading.Thread(target=run, args=("first", first),
                                        name="eval-first")
        second_thread = threading.Thread(target=run, args=("second", second),
                                         name="eval-second")
        with patch.object(st, "file_lock", observed_lock), pause_at(pause):
            try:
                first_thread.start()
                self.assertTrue(ready.wait(5), f"first never reached checked state: {result}")
                second_thread.start()
                self.assertTrue(attempted.wait(5), "second never tried the journal")
                self.assertFalse(entered.is_set(), "second entered before first append")
            finally:
                release.set()
                first_thread.join(5)
                if second_thread.ident is not None:
                    second_thread.join(5)
                self.assertFalse(first_thread.is_alive())
                self.assertFalse(second_thread.is_alive())
        return result


class TestEvaluationTransactions(EvaluationTransactionFixture):
    def test_claim_next_and_claim_job_serialize_two_facades(self):
        for mode in ("claim_next", "claim_job"):
            with self.subTest(mode=mode):
                source, job = self.register(mode)
                first_journal, second_journal = st.EvidenceJournal(SID), st.EvidenceJournal(SID)
                first_journal.state()
                second_journal.state()
                other = JobScheduler()
                original_lease = self.scheduler._lease

                def pause_at(pause):
                    def lease(*args, **kwargs):
                        pause()
                        return original_lease(*args, **kwargs)
                    return patch.object(self.scheduler, "_lease", lease)

                def journal_for(_sid):
                    return (second_journal if threading.current_thread().name == "eval-second"
                            else first_journal)

                def claim(scheduler):
                    return (scheduler.claim_job(SID, job.job_id) if mode == "claim_job"
                            else scheduler.claim_next(SID))

                with patch("app.agents.student_model.evaluation.jobs.get_journal", journal_for):
                    result = self.compete(lambda: claim(self.scheduler),
                                          lambda: claim(other), pause_at)
                self.assertNotIsInstance(result["first"], Exception)
                self.assertEqual(result["first"].job.job_id, job.job_id)
                self.assertIsNone(result["second"])
                runtime = self.journal.state().jobs[job.job_id]
                self.assertEqual(runtime.job.attempt_count, 1)
                self.assertEqual(runtime.job.lease_token, result["first"].lease_token)

    def test_result_source_check_and_append_share_lock(self):
        source, job = self.register()
        claimed = self.scheduler.claim_job(SID, job.job_id)
        other = st.EvidenceJournal(SID)
        original_build = self.service.build_commit_operations

        def pause_at(pause):
            def build(*args, **kwargs):
                result = original_build(*args, **kwargs)
                pause()
                return result
            return patch.object(self.service, "build_commit_operations", build)

        result = self.compete(
            lambda: self.commit(source, claimed),
            lambda: other.append([S.OpSourceRevised(
                source_id=source.source_id, source_revision=2,
                canonical_text="Synthetic revision", reason="test")]), pause_at)
        self.assertNotIsInstance(result["first"], Exception)
        txs = self.journal._iter_raw_transactions()
        self.assertIsInstance(txs[-2].operations[0], S.OpResultCommitted)
        self.assertIsInstance(txs[-1].operations[0], S.OpSourceRevised)
        self.assertEqual(self.journal.state().sources[source.source_id].current_interpretation_id, "")

    def test_two_results_with_same_base_only_one_commits(self):
        source1, job1 = self.register("first", workspace=WS)
        source2, job2 = self.register("second", workspace=WS)
        claim1 = self.scheduler.claim_job(SID, job1.job_id)
        claim2 = self.scheduler.claim_job(SID, job2.job_id)
        concept = S.ConceptRef(
            graph_owner_namespace="public", textbook_id="synthetic",
            file_ids=["synthetic_file"], concept_id="synthetic.endpoint",
            concept_revision="synthetic_v1", display_name="Synthetic concept")
        interpretation = S.LearnerInterpretation(
            applicable=True, concept_updates=[S.ConceptUpdate(
                concept_ref="c1", proposed_state=S.ConceptEvalState.SUPPORTED_IN_SCOPE,
                statement="Synthetic judgment")])
        original_build = self.service.build_commit_operations

        def pause_at(pause):
            def build(*args, **kwargs):
                result = original_build(*args, **kwargs)
                if threading.current_thread().name == "eval-first":
                    pause()
                return result
            return patch.object(self.service, "build_commit_operations", build)

        # This fixture isolates CAS; validator behavior has its own suites.
        with patch("app.agents.student_model.evaluation.validator.validate_interpretation",
                   return_value=[]):
            result = self.compete(
                lambda: self.commit(source1, claim1, pack=self.pack(source1, concept),
                                    interpretation=interpretation,
                                    expected_base_judgment_ids={concept.key: ""}),
                lambda: self.commit(source2, claim2, pack=self.pack(source2, concept),
                                    interpretation=interpretation,
                                    expected_base_judgment_ids={concept.key: ""}), pause_at)
        self.assertNotIsInstance(result["first"], Exception)
        self.assertIsInstance(result["second"], CommitRejected)
        self.assertEqual(result["second"].issues[0].code, "base_judgment_changed")
        self.assertEqual(len(self.journal.state().judgments), 1)

    def test_superseded_source_does_not_publish_old_result(self):
        source, job = self.register()
        claimed = self.scheduler.claim_job(SID, job.job_id)
        snapshot = self.journal.snapshot()
        st.EvidenceJournal(SID).append([S.OpSourceRevised(
            source_id=source.source_id, source_revision=2,
            canonical_text="Synthetic changed answer", reason="test")])
        self.assertEqual(snapshot.sources[source.source_id].receipt.source_revision, 1)
        before = self.journal.state().last_seq
        with self.assertRaises(CommitRejected) as ctx:
            self.commit(source, claimed)
        self.assertEqual(ctx.exception.issues[0].code, "stale_source")
        self.assertEqual(self.journal.state().last_seq, before)

    def test_lease_expiring_during_validation_rejected_before_append(self):
        source, job = self.register()
        clock = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
        with patch("app.agents.student_model.evaluation.jobs._now", lambda: clock[0]):
            claimed = self.scheduler.claim_job(SID, job.job_id)
            original = self.service.build_commit_operations

            def expire(*args, **kwargs):
                result = original(*args, **kwargs)
                clock[0] += timedelta(seconds=200)
                return result

            before = self.journal.state().last_seq
            with patch.object(self.service, "build_commit_operations", expire):
                with self.assertRaises(CommitRejected) as ctx:
                    self.commit(source, claimed)
            self.assertEqual(ctx.exception.issues[0].code, "stale_lease")
            self.assertEqual(self.journal.state().last_seq, before)

    def test_stale_executor_cannot_fail_cancel_or_prepare_new_lease(self):
        source, job = self.register()
        first = self.scheduler.claim_job(SID, job.job_id)
        self.journal.append([S.OpJobLeased(
            job_id=job.job_id, lease_token="lease_expired",
            lease_expires_at="2020-01-01T00:00:00Z", worker="synthetic")])
        second = self.scheduler.claim_job(SID, job.job_id)
        before = self.journal.state().last_seq
        self.assertEqual(self.scheduler.fail(
            SID, job.job_id, error_code="old_reply", retryable=False,
            lease_token=first.lease_token), S.JobState.RUNNING)
        self.assertFalse(self.scheduler.cancel(SID, job.job_id,
                                              lease_token=first.lease_token))
        with self.assertRaises(CommitRejected):
            self.service.record_job_input(
                SID, job.job_id, input_hash="old_input", prompt_binding="test",
                generation=first.generation, lease_token=first.lease_token)
        with self.assertRaises(CommitRejected):
            self.commit(source, first)
        self.assertEqual(self.journal.state().last_seq, before)
        self.assertEqual(self.journal.state().jobs[job.job_id].job.lease_token,
                         second.lease_token)

    def test_mutate_callback_refreshes_and_preserves_noop_and_generation_guards(self):
        source, job = self.register()
        generation = self.journal.state().generation
        other = st.EvidenceJournal(SID)
        other.append([S.OpSourceRevised(
            source_id=source.source_id, source_revision=2,
            canonical_text="Synthetic revision", reason="test")])
        seen = []

        def build(state):
            seen.append(state.sources[source.source_id].receipt.source_revision)
            self.assertEqual(self.journal.state().generation, generation)
            return [S.OpConsumerAck(event_id="synthetic_ack", consumer="synthetic")]

        tx = self.journal.mutate(build, expected_generation=generation)
        self.assertEqual(seen, [2])
        self.assertEqual(tx.seq, 3)
        self.assertIsNone(self.journal.mutate(lambda state: []))
        self.assertEqual(self.journal.state().last_seq, 3)
        self.journal.rewrite(lambda tx: False, "synthetic deletion")
        with self.assertRaises(st.GenerationConflictError):
            self.journal.mutate(build, expected_generation=generation)

    def test_synthesis_outbox_enqueue_and_ack_cannot_race_another_executor(self):
        self.journal.append([S.OpResultCommitted(
            job_id="job_synthetic", source_id="src_missing", source_revision=1,
            scope_revision="synthetic", abstained=True,
            outbox=[{"event_id": "dirty_synthetic", "consumer": "synthesis",
                     "kind": "concept_dirty", "workspace_id": WS}])])
        worker = EvaluationWorker(scheduler=self.scheduler)
        original = lifecycle.request_resynthesis

        def pause_at(pause):
            def request(*args, **kwargs):
                job_id = original(*args, **kwargs)
                if threading.current_thread().name == "eval-first":
                    pause()
                return job_id
            return patch.object(lifecycle, "request_resynthesis", request)

        result = self.compete(lambda: worker._advance_outbox(SID),
                              lambda: worker._advance_outbox(SID), pause_at)
        self.assertIsNone(result["first"])
        self.assertIsNone(result["second"])
        state = self.journal.state()
        self.assertFalse(state.outbox_unacked)
        self.assertEqual(sum(rt.job.kind == S.JobKind.SYNTHESIS_WORKSPACE
                             for rt in state.jobs.values()), 1)

    def test_teaching_only_outbox_acknowledged(self):
        self.journal.append([S.OpResultCommitted(
            job_id="job_synthetic", source_id="src_missing", source_revision=1,
            scope_revision="synthetic", abstained=True,
            outbox=[{"event_id": "teaching_synthetic", "consumer": "teaching"}])])
        EvaluationWorker(scheduler=self.scheduler)._advance_outbox(SID)
        self.assertFalse(self.journal.state().outbox_unacked)

    def review_job(self, suffix):
        source, original = self.register(suffix, workspace=WS)
        interpretation_id = "itp_synthetic_" + suffix
        self.journal.append([S.OpResultCommitted(
            job_id=original.job_id, source_id=source.source_id,
            source_revision=1, scope_revision="scope_synthetic",
            interpretation_id=interpretation_id,
            interpretation=S.LearnerInterpretation(applicable=False,
                                                   abstain_reason="synthetic"),
            abstained=True)])
        review = S.ReviewRequestRecord(
            review_id="review_" + suffix, source_id=source.source_id,
            interpretation_id=interpretation_id, reason="Synthetic dispute",
            requested_at=S.utc_now_iso(), requested_revision=1)
        job = S.EvaluationJob(
            job_id="job_review_" + suffix, kind=S.JobKind.REVIEW,
            source_id=source.source_id, source_revision=1, workspace_id=WS,
            scope_revision="scope_synthetic", created_at=S.utc_now_iso(),
            updated_at=S.utc_now_iso())
        self.journal.append([S.OpReviewRequested(review=review, job_id=job.job_id),
                             S.OpJobRequested(job=job)])
        return source, review, self.scheduler.claim_job(SID, job.job_id)

    def test_review_decisions_preserve_combined_transaction(self):
        for decision in S.ReviewDecisionKind:
            with self.subTest(decision=decision):
                source, review, claimed = self.review_job(decision.value)
                output = S.ReviewDecisionOutput(
                    decision=decision, reason="Synthetic review",
                    replacement_interpretation=(S.LearnerInterpretation(
                        applicable=False, abstain_reason="synthetic")
                        if decision == S.ReviewDecisionKind.REVISE else None))

                class Runner:
                    async def run_structured(self, **kwargs):
                        # A separate executor can acquire the journal while
                        # the model call awaits; the commit lock is not held.
                        await asyncio.to_thread(st.EvidenceJournal(SID).append, [
                            S.OpConsumerAck(event_id="synthetic_model_call",
                                            consumer="synthetic")])
                        return StructuredOutput(parsed=output, raw="{}")

                with patch.object(evaluator, "_rebuild_pack_for_review",
                                  return_value=self.pack(source)), patch(
                        "app.agents.student_model.evaluation.validator.validate_interpretation",
                        return_value=[]):
                    result = asyncio.run(evaluator.run_review_job(
                        SID, claimed, runner=Runner(), scheduler=self.scheduler))
                self.assertEqual(result, "succeeded")
                state = self.journal.state()
                decided = state.reviews[review.review_id]
                self.assertEqual(decided.decided_kind, decision.value)
                self.assertEqual(decided.status, "active" if decision ==
                                 S.ReviewDecisionKind.INSUFFICIENT_EVIDENCE else "resolved")
                tx = next(tx for tx in reversed(self.journal._iter_raw_transactions())
                          if any(isinstance(op, S.OpReviewResolved) for op in tx.operations))
                if decision == S.ReviewDecisionKind.REVISE:
                    self.assertIsInstance(tx.operations[0], S.OpResultCommitted)
                    self.assertIsInstance(tx.operations[-1], S.OpReviewResolved)
                if decision == S.ReviewDecisionKind.INVALIDATE:
                    self.assertIsInstance(tx.operations[0], S.OpInterpretationRevoked)
                    self.assertIsInstance(tx.operations[-1], S.OpReviewResolved)
                self.assertNotEqual(state.jobs[claimed.job.job_id].job.state,
                                    S.JobState.RUNNING)

    def test_review_archived_during_model_call_is_dismissed(self):
        source, review, claimed = self.review_job("archived_during_call")

        class Runner:
            async def run_structured(self, **kwargs):
                await asyncio.to_thread(st.EvidenceJournal(SID).append, [
                    S.OpSourceArchived(source_id=source.source_id, reason="synthetic")])
                return StructuredOutput(parsed=S.ReviewDecisionOutput(
                    decision=S.ReviewDecisionKind.UPHOLD, reason="Synthetic"), raw="{}")

        self.assertEqual(asyncio.run(evaluator.run_review_job(
            SID, claimed, runner=Runner(), scheduler=self.scheduler)), "cancelled")
        state = self.journal.state()
        self.assertEqual(state.reviews[review.review_id].status, "dismissed")
        self.assertEqual(state.jobs[claimed.job.job_id].job.state, S.JobState.CANCELLED)
        self.assertFalse(any(isinstance(op, S.OpReviewResolved)
                             for tx in self.journal._iter_raw_transactions() for op in tx.operations))

    def test_review_old_lease_cannot_resolve_after_new_executor_claim(self):
        source, review, claimed = self.review_job("lease_replaced")

        class Runner:
            async def run_structured(self, **kwargs):
                st.EvidenceJournal(SID).append([S.OpJobLeased(
                    job_id=claimed.job.job_id, lease_token="lease_new_executor",
                    lease_expires_at="2099-01-01T00:00:00Z", worker="synthetic")])
                return StructuredOutput(parsed=S.ReviewDecisionOutput(
                    decision=S.ReviewDecisionKind.INVALIDATE, reason="Synthetic"), raw="{}")

        with self.assertRaises(CommitRejected) as ctx:
            asyncio.run(evaluator.run_review_job(
                SID, claimed, runner=Runner(), scheduler=self.scheduler))
        self.assertEqual(ctx.exception.issues[0].code, "stale_lease")
        state = self.journal.state()
        self.assertEqual(state.reviews[review.review_id].status, "active")
        self.assertFalse(state.sources[source.source_id].interpretations[
            review.interpretation_id]["revoked"])
        self.assertEqual(state.jobs[claimed.job.job_id].job.lease_token, "lease_new_executor")

    def test_synthesis_allows_metadata_but_retries_changed_evidence(self):
        for evidence_change in (False, True):
            with self.subTest(evidence_change=evidence_change):
                source, job = self.register(
                    "synthesis_" + str(evidence_change), workspace=WS,
                    kind=S.JobKind.SYNTHESIS_WORKSPACE)
                claimed = self.scheduler.claim_job(SID, job.job_id)

                class Runner:
                    async def run_structured(self, **kwargs):
                        op = (S.OpSourceArchived(source_id=source.source_id,
                                                reason="synthetic") if evidence_change
                              else S.OpConsumerAck(event_id="synthetic_metadata",
                                                   consumer="synthetic"))
                        await asyncio.to_thread(st.EvidenceJournal(SID).append, [op])
                        return StructuredOutput(parsed=S.ScopeSynthesisOutput(
                            scope_type=S.ScopeType.WORKSPACE,
                            statement="Synthetic synthesis"), raw="{}")

                result = asyncio.run(evaluator.run_synthesis_job(
                    SID, claimed, scope_type=S.ScopeType.WORKSPACE,
                    runner=Runner(), scheduler=self.scheduler))
                state = self.journal.state()
                if evidence_change:
                    self.assertEqual(result, "failed")
                    self.assertEqual(state.jobs[job.job_id].job.state, S.JobState.RETRY_WAIT)
                    self.assertEqual(state.jobs[job.job_id].last_error_code,
                                     "synthesis_input_changed")
                else:
                    self.assertEqual(result, "succeeded")
                    self.assertEqual(state.jobs[job.job_id].job.state, S.JobState.SUCCEEDED)
                    self.assertEqual(list(state.syntheses.values())[-1].scope_revision,
                                     "scope_synthetic")

    def test_worker_discovers_student_with_only_expired_running_lease(self):
        source, job = self.register(kind=S.JobKind.CLT_REVIEW)
        first = self.scheduler.claim_job(SID, job.job_id)
        self.journal.append([S.OpJobLeased(
            job_id=job.job_id, lease_token=first.lease_token,
            lease_expires_at="2020-01-01T00:00:00Z", worker="synthetic")])

        class Runner:
            async def run_structured(self, **kwargs):
                return StructuredOutput(parsed=S.TeachingDesignReview(), raw="{}")

        worker = EvaluationWorker(scheduler=self.scheduler, runner_provider=Runner)
        self.assertEqual([sid for _, sid in worker._students_with_jobs()], [SID])
        self.assertEqual(asyncio.run(worker.process_pass()), 1)
        self.assertEqual(self.journal.state().jobs[job.job_id].job.state,
                         S.JobState.ABSTAINED)
