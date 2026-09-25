"""G3 回归：评价复核与依赖失效（plan §12.3/§12.4 / §18.2）。

- 新练习保留旧尝试（重练 = 新观察，旧错误不消失）。
- 同源复核替代解释 / 撤销传播到判断与综合。
- 复核本身不新增学习证据。
"""
from __future__ import annotations

import asyncio
import unittest

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import dialogue as dlg
from app.agents.student_model.evaluation import lifecycle, schema as S
from app.agents.student_model.evaluation import service as svc
from app.agents.student_model.evaluation.store import get_journal
from app.core.workspace import Workspace, save_workspace

SID = "usr_rev_a"
WS = "ws_rev"


class ReviewFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core import learner_runtime
        learner_runtime.reset_learner_runtime()
        save_workspace(Workspace(workspace_id=WS, name="物理", student_id=SID))
        from app.core.session import TutorSession, save_session
        self.session = TutorSession(session_id="sess_rev", grade="高中",
                                    student_id=SID, workspace_id=WS)
        save_session(self.session)

    def commit_dialogue_result(self, message_id: str, text: str,
                               claims: list[dict],
                               updates: list[dict]) -> str:
        """直接经 service 提交一份 dialogue 解释（绕过 LLM，等同 P4 输出
        经 validator 后的提交路径）。"""
        src_id = dlg.register_dialogue_source(
            student_id=SID, session=self.session,
            message={"role": "user", "content": text,
                     "message_id": message_id})
        assert src_id is not None
        journal = get_journal(SID)
        state = journal.state()
        receipt = state.sources[src_id].receipt
        from tests.test_unified_submission import _learner_output
        interp = _learner_output(claims).learner
        interp.concept_updates = [S.ConceptUpdate.model_validate(u)
                                  for u in updates]
        scheduler = None
        from app.core import learner_runtime
        scheduler = learner_runtime.get_scheduler()
        claimed = scheduler.claim_next(SID, workspace_id=WS)
        assert claimed is not None
        pack = self._pack(receipt, claims)
        svc.LearnerEvaluationService(scheduler).commit_result(
            SID, job_id=claimed.job.job_id,
            lease_token=claimed.lease_token,
            expected_generation=journal.state().generation,
            source=receipt, pack=pack, task=None, interpretation=interp,
            expected_base_judgments={})
        return src_id

    def _pack(self, receipt: S.SourceReceipt, claims: list[dict]
              ) -> S.EvaluationContextPack:
        concept = S.ConceptRef(
            graph_owner_namespace="public", textbook_id="tb_rev",
            file_ids=["f1"], concept_id="phys.c1",
            concept_revision="cr_1", display_name="并联关系")
        entries = [S.PackConceptRefEntry(short_ref="c1", concept=concept)]
        return S.EvaluationContextPack(
            pack_id="pack_x", source_id=receipt.source_id,
            prompt_binding="dialogue_learner_evaluation@1.0.0",
            allowlist=entries,
            current_student_evidence={"ref": "s1"},
            manifest=S.PackManifest(
                included_refs=["s1", "c1"], omitted_refs=[], truncations=[],
                input_hash="ih_" + "0" * 24, prompt_ref="p"))


def _claim(stance: str = "supports", statement: str = "能用端点说明并联",
           quote: str = "端点") -> dict:
    start = _TEXT.index(quote)
    return {
        "local_id": "ob1", "concept_ref": "c1", "statement": statement,
        "stance": stance,
        "current_evidence": [{"ref": "s1", "start": start,
                              "end": start + len(quote), "quote": quote}],
        "opportunity_ref": "t1", "warrant": "引用学生原句",
        "limits": [], "cognitive_processes": ["understand"],
        "knowledge_types": ["conceptual"], "evidence_conditions": [],
        "alternatives": [],
    }


def _update(state: str = "supported_in_scope",
            statement: str = "在这些条件下能说明并联") -> dict:
    return {"concept_ref": "c1", "base_judgment_id": "",
            "retain_claim_ids": [], "add_claim_local_ids": ["ob1"],
            "revise_claims": [], "close_claims": [],
            "proposed_state": state, "statement": statement,
            "dependencies": []}


_TEXT = "我认为并联看公共端点，这句话里包含端点这个词。"


class TestReviewAndInvalidation(ReviewFixture):
    def test_new_practice_keeps_old_attempt(self):
        """A16：重练是新观察——第二次作答产生第二个 source，第一次仍在。"""
        self.commit_dialogue_result("m_1", _TEXT, [_claim()], [_update()])
        self.commit_dialogue_result("m_2", _TEXT + "再看一次。",
                                    [_claim()], [_update()])
        state = get_journal(SID).state()
        self.assertEqual(len(state.sources), 2)
        # 概念判断链上保留两次的物化（base 链）
        self.assertGreaterEqual(len(state.judgments), 2)

    def test_invalidate_propagates_to_judgments_and_resynthesis(self):
        src = self.commit_dialogue_result("m_1", _TEXT, [_claim()],
                                          [_update()])
        state = get_journal(SID).state()
        interp_id = state.sources[src].current_interpretation_id
        self.assertTrue(interp_id)
        self.assertTrue(state.concept_current)
        affected = lifecycle.invalidate_interpretation(
            SID, interp_id, reason="test_invalidate")
        self.assertTrue(affected)
        state2 = get_journal(SID).state()
        # 撤销后该解释不再 current；受影响判断从当前位退出
        self.assertFalse(state2.sources[src].current_interpretation_id)
        for jid in affected.values():
            self.assertNotIn(jid, state2.judgments)
        # 重综合 job 已排队（§12.5）
        self.assertTrue(any(
            rt.job.kind == S.JobKind.SYNTHESIS_WORKSPACE
            for rt in state2.jobs.values()))

    def test_review_recorded_without_new_evidence(self):
        src = self.commit_dialogue_result("m_1", _TEXT, [_claim()],
                                          [_update()])
        state = get_journal(SID).state()
        before_sources = len(state.sources)
        interp_id = state.sources[src].current_interpretation_id
        journal = get_journal(SID)
        journal.append([S.OpReviewRequested(review=S.ReviewRequestRecord(
            review_id="rev_t1", source_id=src,
            interpretation_id=interp_id, reason="判分没有识别等价解法",
            requested_at=S.utc_now_iso(), requested_revision=1))])
        state2 = get_journal(SID).state()
        self.assertIn("rev_t1", state2.reviews)
        # 复核请求本身不新增来源（§9.7：用户异议不是新的能力证据）
        self.assertEqual(len(state2.sources), before_sources)


if __name__ == "__main__":
    unittest.main()
