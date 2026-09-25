"""G0 协议回归：统一评价 schema 的边界与 envelope 语义（plan §4/§6.4）。

聚焦模型层可静态判定的约束：extra=forbid、字段长度/条数上限、UTC 时间
格式、journal envelope 校验和、op discriminated union、QuestionPublic
白名单（A07）。语义资格（引用/owner/机会）在 validator 回归中覆盖。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.student_model.evaluation import schema as S  # noqa: E402


def _concept(namespace: str = "public", cid: str = "phys.parallel") -> dict:
    return dict(graph_owner_namespace=namespace, textbook_id="tb_demo",
                file_ids=["f1"], concept_id=cid, concept_revision="rev1",
                display_name="并联关系")


def _observation(**over) -> dict:
    base = dict(local_id="ob1", concept_ref="c1", statement="能用公共端点说明并联",
                stance="supports", current_evidence=[],
                opportunity_ref="t1", warrant="引用了学生原句",
                limits=[], cognitive_processes=["understand"],
                knowledge_types=["conceptual"], evidence_conditions=[], alternatives=[])
    base.update(over)
    return base


class FieldBoundTests(unittest.TestCase):
    """§4.3 字段边界：超限拒绝（等义收敛由 repair/重试承担，不允许裁剪）。"""

    def test_statement_over_600_rejected(self):
        with self.assertRaises(Exception):
            S.ObservationClaim.model_validate(
                _observation(statement="长" * 601))

    def test_limits_max_5_items_300_chars(self):
        with self.assertRaises(Exception):
            S.ObservationClaim.model_validate(
                _observation(limits=["x"] * 6))
        with self.assertRaises(Exception):
            S.ObservationClaim.model_validate(
                _observation(limits=["长" * 301]))

    def test_batch_caps_3_concept_updates_8_claims(self):
        updates = [
            dict(concept_ref=f"c{i}", base_judgment_id="", retain_claim_ids=[],
                 add_claim_local_ids=[], revise_claims=[], close_claims=[],
                 proposed_state="emerging", statement="", dependencies=[])
            for i in range(4)
        ]
        with self.assertRaises(Exception):
            S.LearnerInterpretation.model_validate(
                dict(applicable=True, observation_claims=[],
                     concept_updates=updates, feedback=""))
        claims = [_observation(local_id=f"ob{i}") for i in range(9)]
        with self.assertRaises(Exception):
            S.LearnerInterpretation.model_validate(
                dict(applicable=True, observation_claims=claims,
                     concept_updates=[], feedback=""))

    def test_extra_fields_forbidden(self):
        with self.assertRaises(Exception):
            S.ObservationClaim.model_validate(_observation(mastery=0.9))
        with self.assertRaises(Exception):
            S.TaskResult.model_validate(
                dict(question_ref=dict(question_id="q1", question_revision=1),
                     grading_status="graded", raw_grade="[对]"))

    def test_evidence_span_ordering(self):
        with self.assertRaises(Exception):
            S.EvidenceSpan(ref="s1", start=10, end=5, quote="abc")

    def test_utc_iso_enforced(self):
        with self.assertRaises(Exception):
            S.SourceReceipt.model_validate(dict(
                source_id="src_1", source_revision=1, kind="assessment",
                observed_at="2026-09-13 08:00:00", canonical_text="x"))

    def test_frozen_criterion_weight_positive_finite(self):
        with self.assertRaises(Exception):
            S.FrozenCriterion(id="k1", description="d", weight=0)
        with self.assertRaises(Exception):
            S.FrozenCriterion(id="k1", description="d", weight=float("inf"))
        with self.assertRaises(Exception):
            S.FrozenCriterion(id="k1", description="d", weight=float("nan"))

    def test_task_snapshot_rubric_bounds(self):
        base = dict(question_id="q1", question_revision=1,
                    q_type="short_answer", stem="题干", answer="答案",
                    rubric=[dict(id="k1", description="d", weight=1.0)],
                    concept_refs=[_concept()])
        snap = S.TaskSnapshot.model_validate(base)
        assert len(snap.rubric) == 1
        with self.assertRaises(Exception):
            S.TaskSnapshot.model_validate({**base, "rubric": []})


class QuestionPublicTests(unittest.TestCase):
    """A07：答前不下发答案/量规/等价解/审核细节。"""

    def _snapshot(self) -> S.TaskSnapshot:
        return S.TaskSnapshot.model_validate(dict(
            question_id="q1", question_revision=1, q_type="short_answer",
            stem="解释并联", answer="两端接同一节点",
            explanation="因为公共端点", equivalent_solutions=["另一种解"],
            rubric=[dict(id="k1", description="说明端点", weight=2.0,
                         critical=True)],
            concept_refs=[_concept()], task_family="parallel-identify",
            source_badge="public:tb_demo"))

    def test_public_view_whitelist(self):
        pub = self._snapshot().public_view()
        dumped = pub.model_dump()
        for banned in ("answer", "explanation", "rubric", "rubric_hash",
                       "equivalent_solutions", "verification", "novelty",
                       "evidence_opportunities", "grounding_refs"):
            self.assertNotIn(banned, dumped)
        self.assertEqual(pub.stem, "解释并联")
        self.assertEqual(pub.input_spec.kind, "text")
        self.assertTrue(pub.input_spec.requires_explanation)

    def test_mc_input_spec_is_choice(self):
        snap = S.TaskSnapshot.model_validate(dict(
            question_id="q2", question_revision=1, q_type="multiple_choice",
            stem="选", options={"A": "a", "B": "b"}, answer="A",
            rubric=[dict(id="k1", description="选对", weight=1.0)],
            concept_refs=[_concept()]))
        pub = snap.public_view()
        self.assertEqual(pub.input_spec.kind, "choice")
        self.assertFalse(pub.input_spec.requires_explanation)


class JournalEnvelopeTests(unittest.TestCase):
    """§6.4 事务 envelope：checksum、op union、未知 op 拒绝。"""

    def _tx(self, ops: list[dict], **over) -> S.JournalTransaction:
        tx = S.JournalTransaction.model_validate(dict(
            generation="gen_abcd", seq=7, transaction_id="tx_1",
            created_at="2026-09-13T08:00:00Z", operations=ops, **over))
        tx.checksum = tx.resolved_checksum()
        return tx

    def test_checksum_roundtrip_and_tamper_detection(self):
        tx = self._tx([dict(op="consumer_ack", event_id="ev1", consumer="m9")])
        data = tx.model_dump()
        again = S.JournalTransaction.model_validate(data)
        self.assertTrue(again.verify_checksum())
        data["seq"] = 8
        tampered = S.JournalTransaction.model_validate(data)
        self.assertFalse(tampered.verify_checksum())

    def test_source_plus_job_same_transaction(self):
        """§6.4：SourceReceipt + job_requested 同一行提交。"""
        ops = [
            dict(op="source_registered", source=dict(
                source_id="src_1", source_revision=1, kind="dialogue",
                observed_at="2026-09-13T08:00:00Z", canonical_text="我认为…",
                spans=[dict(start=0, end=6, owner="dialogue")],
                source_session_ref="chat_1")),
            dict(op="job_requested", job=dict(
                job_id="job_1", kind="dialogue_evaluation", source_id="src_1",
                workspace_id="ws_1", scope_revision="scope_1",
                priority=1)),
        ]
        tx = self._tx(ops)
        self.assertIsInstance(tx.operations[0], S.OpSourceRegistered)
        self.assertIsInstance(tx.operations[1], S.OpJobRequested)
        self.assertEqual(tx.operations[0].source.kind, S.SourceKind.DIALOGUE)

    def test_unknown_op_rejected(self):
        with self.assertRaises(Exception):
            self._tx([dict(op="mastery_update", p_known=0.9)])

    def test_result_committed_carries_full_result(self):
        interp = dict(applicable=True, observation_claims=[_observation()],
                      concept_updates=[], feedback="ok")
        ops = [dict(op="result_committed", job_id="job_1", source_id="src_1",
                    source_revision=1, scope_revision="scope_1",
                    task_result=dict(
                        question_ref=dict(question_id="q1", question_revision=1),
                        grading_status="graded", verdict="correct",
                        task_score=1.0, criterion_results=[]),
                    interpretation_id="itp_1", interpretation=interp,
                    abstained=False)]
        tx = self._tx(ops)
        op = tx.operations[0]
        self.assertIsInstance(op, S.OpResultCommitted)
        self.assertEqual(op.task_result.verdict, S.Verdict.CORRECT)
        self.assertEqual(op.interpretation.observation_claims[0].stance,
                         S.ClaimStance.SUPPORTS)

    def test_operations_must_be_nonempty(self):
        with self.assertRaises(Exception):
            self._tx([])


class CanonicalTextTests(unittest.TestCase):
    def test_crlf_normalized(self):
        self.assertEqual(S.canonicalize_text("a\r\nb\rc"), "a\nb\nc")

    def test_concept_ref_key_stable_and_scoped(self):
        a = S.ConceptRef.model_validate(_concept())
        b = S.ConceptRef.model_validate(_concept(namespace="usr_x"))
        self.assertEqual(a.key, S.ConceptRef.model_validate(_concept()).key)
        self.assertNotEqual(a.key, b.key)


if __name__ == "__main__":
    unittest.main()
