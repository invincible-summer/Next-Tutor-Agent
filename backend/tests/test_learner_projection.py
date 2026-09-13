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


def _judgment(concept: S.ConceptRef, state: S.ConceptState,
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
        states = [S.ConceptState.EMERGING, S.ConceptState.SUPPORTED_IN_SCOPE,
                  S.ConceptState.FRAGILE, S.ConceptState.CONFLICTING,
                  S.ConceptState.NOT_OBSERVED]
        self.commit([_judgment(c, s) for c, s in zip(concepts, states)])
        scope = S.EvaluationScope(
            workspace_id=WS, scope_revision="sr_1", selected_volumes=[],
            allowed_concepts=concepts + [_concept("c9", "未观察")],
            graph_revisions=[])
        views = projections.concept_views(SID, scope)
        by_name = {v.concept_ref.display_name: v for v in views}
        self.assertEqual(by_name["概念1"].state,
                         S.ConceptState.SUPPORTED_IN_SCOPE)
        self.assertEqual(by_name["概念4"].state, S.ConceptState.NOT_OBSERVED)
        # 左连接：完全没碰过的概念也出现
        self.assertIn("未观察", by_name)
        self.assertEqual(by_name["未观察"].state, S.ConceptState.NOT_OBSERVED)
        self.assertEqual(by_name["未观察"].judgment_id, "")
        # 投影无任何数值掌握度
        dumped = str([v.model_dump() for v in views])
        self.assertNotIn("p_known", dumped)
        self.assertNotIn("mastery", dumped)

    def test_latest_judgment_wins(self):
        concept = _concept("c_latest", "最新")
        j1 = _judgment(concept, S.ConceptState.EMERGING, statement="早期")
        self.commit([j1])
        j2 = _judgment(concept, S.ConceptState.SUPPORTED_IN_SCOPE,
                       statement="后期")
        self.commit([j2])
        scope = S.EvaluationScope(
            workspace_id=WS, scope_revision="sr_1", selected_volumes=[],
            allowed_concepts=[concept], graph_revisions=[])
        views = projections.concept_views(SID, scope)
        self.assertEqual(views[0].state, S.ConceptState.SUPPORTED_IN_SCOPE)
        self.assertEqual(views[0].judgment_id, j2.judgment_id)


if __name__ == "__main__":
    unittest.main()
