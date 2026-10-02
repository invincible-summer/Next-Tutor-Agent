"""G3 回归：投影语义（plan §12 / §18.2 test_learner_projection）。

- 五种类别正确投影（经物化判断）；无证据概念由 scope 左连接。
- 最新判断生效（不是最先返回）。
- 概念视图不产生掌握度数值/平均。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import lifecycle, projections
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core.workspace import Workspace, save_workspace

SID = "usr_proj_a"
WS = "ws_proj"


def _concept(cid: str, name: str) -> S.ConceptRef:
    return S.ConceptRef(graph_owner_namespace="public", textbook_id="tb_p",
                        file_ids=["f1"], concept_id=cid,
                        concept_revision="cr_1", display_name=name)


def _judgment(concept: S.ConceptRef, state: S.ConceptEvalState,
              statement: str = "") -> S.ConceptJudgment:
    return S.ConceptJudgment(
        judgment_id="jdg_" + concept.concept_id + "_" + statement[:4],
        concept_ref=concept, workspace_id=WS, state=state,
        statement=statement or state.value, claims=[],
        evidence_watermark="gen:1", policy_version=S.POLICY_VERSION,
        theory_version=S.THEORY_VERSION, prompt_ref="p",
        created_at=S.utc_now_iso(), source_id="src_" + concept.concept_id,
        scope_revision="sr_1")


class ProjectionFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core import learner_runtime
        learner_runtime.reset_learner_runtime()
        save_workspace(Workspace(workspace_id=WS, name="物理", student_id=SID))

    def commit(self, judgments: list[S.ConceptJudgment]) -> None:
        journal = get_journal(SID)
        from app.agents.student_model.evaluation.store import new_source_id
        src_id = new_source_id()
        receipt = S.SourceReceipt(
            source_id=src_id, source_revision=1,
            kind=S.SourceKind.DIALOGUE, observed_at=S.utc_now_iso(),
            workspace_id_at_observation=WS, canonical_text="原文",
            scope_revision="sr_1")
        interp = S.LearnerInterpretation(applicable=True, observation_claims=[
            S.ObservationClaim(local_id="ob1", concept_ref="c1",
                               statement="能做", stance=S.ClaimStance.SUPPORTS,
                               opportunity_ref="t1")],
            concept_updates=[], feedback="")
        journal.append([
            S.OpSourceRegistered(source=receipt),
            S.OpResultCommitted(
                job_id="job_" + src_id[4:], source_id=src_id,
                source_revision=1, scope_revision="sr_1",
                interpretation_id="itp_" + src_id[4:],
                interpretation=interp, judgments=judgments,
                abstained=False)])


class TestConceptViews(ProjectionFixture):
    def test_left_join_and_five_states(self):
        concepts = [_concept(f"c{i}", f"概念{i}") for i in range(5)]
        states = [S.ConceptEvalState.EMERGING, S.ConceptEvalState.SUPPORTED_IN_SCOPE,
                  S.ConceptEvalState.FRAGILE, S.ConceptEvalState.CONFLICTING,
                  S.ConceptEvalState.NOT_OBSERVED]
        self.commit([_judgment(c, s) for c, s in zip(concepts, states)])
        scope = S.EvaluationScope(
            workspace_id=WS, scope_revision="sr_1", selected_volumes=[],
            allowed_concepts=concepts + [_concept("c9", "未观察")],
            graph_revisions=[])
        views = projections.concept_views(SID, scope)
        by_name = {v.concept_ref.display_name: v for v in views}
        self.assertEqual(by_name["概念1"].state,
                         S.ConceptEvalState.SUPPORTED_IN_SCOPE)
        self.assertEqual(by_name["概念4"].state, S.ConceptEvalState.NOT_OBSERVED)
        # 左连接：完全没碰过的概念也出现
        self.assertIn("未观察", by_name)
        self.assertEqual(by_name["未观察"].state, S.ConceptEvalState.NOT_OBSERVED)
        self.assertEqual(by_name["未观察"].judgment_id, "")
        # 投影无任何数值掌握度
        dumped = str([v.model_dump() for v in views])
        self.assertNotIn("p_known", dumped)
        self.assertNotIn("mastery", dumped)

    def test_latest_judgment_wins(self):
        concept = _concept("c_latest", "最新")
        j1 = _judgment(concept, S.ConceptEvalState.EMERGING, statement="早期")
        self.commit([j1])
        j2 = _judgment(concept, S.ConceptEvalState.SUPPORTED_IN_SCOPE,
                       statement="后期")
        self.commit([j2])
        scope = S.EvaluationScope(
            workspace_id=WS, scope_revision="sr_1", selected_volumes=[],
            allowed_concepts=[concept], graph_revisions=[])
        views = projections.concept_views(SID, scope)
        self.assertEqual(views[0].state, S.ConceptEvalState.SUPPORTED_IN_SCOPE)
        self.assertEqual(views[0].judgment_id, j2.judgment_id)


class TestWrongAnswerItems(ProjectionFixture):
    """R13（update_plan §4）：有错题时投影不得触发 NameError；source_id
    必须是稳定 receipt 值；争议未结的来源不进入错题链路。"""

    def _task(self, qid: str, stem: str = "1+1=?") -> S.TaskSnapshot:
        return S.TaskSnapshot(
            question_id=qid, question_revision=1,
            q_type=S.QuestionType.MULTIPLE_CHOICE, stem=stem,
            options={"A": "1", "B": "2"}, answer="B",
            rubric=[S.FrozenCriterion(id="c1", description="答案正确",
                                      weight=1.0, critical=True)],
            task_family="代数", source_badge="加法")

    def _submit(self, qid: str, verdict: str) -> str:
        from app.agents.student_model.evaluation.store import new_source_id
        journal = get_journal(SID)
        journal.register_question(self._task(qid))
        src_id = new_source_id()
        receipt = S.SourceReceipt(
            source_id=src_id, source_revision=1,
            kind=S.SourceKind.ASSESSMENT, observed_at=S.utc_now_iso(),
            workspace_id_at_observation=WS, canonical_text="A",
            task_ref=S.QuestionRef(question_id=qid, question_revision=1))
        task_result = S.TaskResult(
            question_ref=S.QuestionRef(question_id=qid, question_revision=1),
            grading_status=S.GradingStatus.GRADED,
            verdict=S.Verdict(verdict))
        journal.append([
            S.OpSourceRegistered(source=receipt),
            S.OpResultCommitted(
                job_id="job_" + src_id[4:], source_id=src_id,
                source_revision=1, scope_revision="sr_1",
                task_result=task_result)])
        return src_id

    def test_wrong_items_have_stable_source_id(self):
        wrong_src = self._submit("q_wrong", "wrong")
        self._submit("q_correct", "correct")
        items = projections.wrong_answer_items(SID)
        # 修复前：NameError: name 'sid' is not defined（只有错题才进分支）
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_id"], wrong_src)
        self.assertEqual(items[0]["verdict"], "wrong")
        self.assertEqual(items[0]["stem"], "1+1=?")

    def test_partial_counts_and_disputed_excluded(self):
        partial_src = self._submit("q_partial", "partial")
        disputed_src = self._submit("q_wrong2", "wrong")
        journal = get_journal(SID)
        journal.append([S.OpReviewRequested(
            review=S.ReviewRequestRecord(
                review_id="rev_1", source_id=disputed_src,
                interpretation_id="itp_none", reason="判分有误",
                requested_at=S.utc_now_iso(), requested_revision=1),
            job_id="")])
        items = projections.wrong_answer_items(SID)
        ids = [i["source_id"] for i in items]
        self.assertIn(partial_src, ids)
        self.assertNotIn(disputed_src, ids)


if __name__ == "__main__":
    unittest.main()
