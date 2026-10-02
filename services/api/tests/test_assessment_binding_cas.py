"""R05（update_plan §4）：提交归属服务端解析 + commit compare-and-swap。

- 外区/假区 workspace 404 且零模型调用；CAT 实例题目绑定校验。
- expected_scope_revision 仅作期望断言，不一致 409。
- commit 短事务内重读服务端事实：source 改版/归档/工作区失效/基线判断
  被并发更新/教材撤选 → 旧回包一律 CommitRejected，不发布能力结论。
- 题目 concept_refs 必须与当前 scope 求交（旧题概念不越当前范围）。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_unified_submission import FakeRunner, _learner_output

from app.agents.assessment import manager as am
from app.agents.student_model.evaluation import context as pack_builder
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.service import (
    CommitRejected, LearnerEvaluationService)
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime
from app.core import textbook as tb_store
from app.core.workspace import Workspace, save_workspace

SID = "usr_bind_a"
OTHER = "usr_bind_b"
WS = "ws_bind"


def _chapter(topic: str, key: str, file_id: str) -> dict:
    return {"id": f"custom.{topic}.ch.{key}", "name": f"章{key}",
            "subject": "物理", "level": "本科", "kind": "chapter",
            "metadata": {"file_id": file_id}}


def _concept(topic: str, key: str, name: str, file_ids: list[str]) -> dict:
    return {"id": f"custom.{topic}.c.{key}", "name": name, "subject": "物理",
            "level": "本科", "kind": "concept", "aliases": [],
            "metadata": {"file_ids": list(file_ids)}}


def _part_of(topic: str, member: str, chapter_key: str) -> dict:
    return {"source": f"custom.{topic}.c.{member}",
            "target": f"custom.{topic}.ch.{chapter_key}", "type": "part_of"}


def _mc_task(question_id: str, concept: S.ConceptRef,
             workspace_id: str = "") -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=question_id, question_revision=1,
        q_type=S.QuestionType.MULTIPLE_CHOICE, stem="哪个是并联？",
        options={"A": "同端点", "B": "上下排列"}, answer="A",
        explanation="公共端点判断",
        rubric=[S.FrozenCriterion(id="c1", description="选对", weight=1.0)],
        concept_refs=[concept], workspace_id=workspace_id)


class BindingFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)
        from app.core.library import load_library, save_library
        lib = load_library(SID)
        for fid in ("f1", "f2"):
            lib.add_file("", f"{fid}.pdf", f"{fid} 教材正文", file_id=fid)
        save_library(lib)
        self.group = tb_store.create_group(
            SID, file_ids=["f1", "f2"], title="大学物理",
            subject="物理", level="本科")
        topic = self.group["topic_key"]
        from app.agents.knowledge import store as kg_store
        kg_store.save_custom_graph(SID, topic, {
            "topic": topic, "topic_key": topic,
            "nodes": [_chapter(topic, "mech", "f1"),
                      _concept(topic, "force", "力", ["f1"])],
            "edges": [_part_of(topic, "force", "mech")],
            "contents": [], "coverage": []})
        records = tb_store.load_textbooks(SID)
        for r in records:
            if r["id"] == self.group["id"]:
                r["status"] = "ready"
        tb_store._save(SID, records)
        save_workspace(Workspace(workspace_id=WS, name="力学区",
                                 student_id=SID, selected_file_ids=["f1"]))
        self.topic = topic
        payload = kg_store.load_custom_graph(SID, topic)
        from app.agents.knowledge.scope_primitives import concept_revision
        node = next(n for n in payload["nodes"] if n["id"].endswith(".force"))
        self.concept = S.ConceptRef(
            graph_owner_namespace=SID, textbook_id=self.group["id"],
            file_ids=["f1"], concept_id=node["id"],
            concept_revision=concept_revision(self.group["id"], node),
            display_name="力")
        self.task = _mc_task("q_bind_1", self.concept, workspace_id=WS)
        am.register_task_snapshot(SID, self.task)

    def scope_revision(self) -> str:
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        return get_scope_resolver().resolve(SID, WS).scope_revision


class TestBindingResolution(BindingFixture):
    def test_foreign_or_missing_workspace_404_zero_model_calls(self):
        for wid in ("ws_missing", "ws_bind"):
            with self.assertRaises(am.WorkspaceNotOwned):
                am.resolve_submission_binding(
                    OTHER, question_ref=S.QuestionRef(
                        question_id="q_bind_1", question_revision=1),
                    task=self.task, workspace_id=wid)
        self.assertEqual(self.runner.calls, [])   # 零模型调用

    async def _submit(self, **kw):
        defaults = dict(student_id=SID,
                        question_ref=S.QuestionRef(
                            question_id="q_bind_1", question_revision=1),
                        student_answer="A")
        defaults.update(kw)
        return await am.evaluate_submission(**defaults)

    def test_expected_scope_revision_conflict(self):
        async def run():
            with self.assertRaises(am.ScopeRevisionConflict):
                await self._submit(expected_scope_revision="sr_wrong")
        import asyncio
        asyncio.run(run())

    def test_task_concepts_intersect_scope(self):
        # 题目概念在 scope 内 → allowlist 非空
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        scope = get_scope_resolver().resolve(SID, WS)
        chosen = pack_builder.select_allowlist(scope, self.task, [])
        self.assertEqual([c.key for c in chosen], [self.concept.key])
        # 旧题目的概念不在当前 scope（换了教材）→ 不进白名单
        stale = _mc_task("q_stale", S.ConceptRef(
            graph_owner_namespace="public", textbook_id="tb_other",
            file_ids=["x"], concept_id="other.c9", concept_revision="cr",
            display_name="别的"))
        self.assertEqual(pack_builder.select_allowlist(scope, stale, []), [])

    def test_assessment_binding_question_must_belong(self):
        from app.agents.assessment import adaptive_test as cat
        inst = cat.CatInstance(
            assessment_id="asmt_bind", workspace_id=WS, purpose="adaptive",
            concept_keys=[], concept="力", count_limit=3,
            question_refs=[S.QuestionRef(question_id="q_other",
                                         question_revision=1)],
            created_at=S.utc_now_iso())
        cat.save_instance(SID, inst, change="test")
        with self.assertRaises(am.AssessmentBindingError):
            am.resolve_submission_binding(
                SID, question_ref=S.QuestionRef(question_id="q_bind_1",
                                                question_revision=1),
                task=self.task, assessment_id="asmt_bind")
        with self.assertRaises(am.AssessmentBindingError):
            am.resolve_submission_binding(
                SID, question_ref=S.QuestionRef(question_id="q_bind_1",
                                                question_revision=1),
                task=self.task, assessment_id="asmt_missing")


class TestCommitCas(BindingFixture):
    """提交门：以服务端事实为准（R05）。"""

    def _register_and_claim(self):
        """受理一份作答（inline 关闭），返回 (source_receipt, pack, claimed)。"""
        import asyncio

        async def run():
            return await am.evaluate_submission(
                student_id=SID,
                question_ref=S.QuestionRef(question_id="q_bind_1",
                                           question_revision=1),
                student_answer="A", run_inline=False)
        receipt = asyncio.run(run())
        scheduler = learner_runtime.get_scheduler()
        claimed = scheduler.claim_next(SID, workspace_id=WS)
        self.assertIsNotNone(claimed)
        state = get_journal(SID).state()
        # 深拷贝冻结"旧回包视角"——journal 重放会就地修改 receipt 对象
        source = state.sources[receipt.source_id].receipt.model_copy(
            deep=True)
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        scope = get_scope_resolver().resolve(SID, WS)
        pack = pack_builder.assemble_assessment_pack(
            source=source, task=self.task, scope=scope, state=state,
            task_result=None, scenarios=[], prompt_binding="test")
        return source, pack, claimed

    def _commit(self, source, pack, claimed, **kw):
        service = LearnerEvaluationService(learner_runtime.get_scheduler())
        return service.commit_result(
            SID, job_id=claimed.job.job_id,
            lease_token=claimed.lease_token,
            expected_generation=kw.pop("generation", "gen_missing"),
            source=source, pack=pack, task=self.task,
            interpretation=_learner_output(applicable=False).learner,
            **kw)

    def test_stale_source_revision_rejected(self):
        source, pack, claimed = self._register_and_claim()
        # 在途改版：source_revised → 旧回包（revision=1）不能发布
        get_journal(SID).append([S.OpSourceRevised(
            source_id=source.source_id, source_revision=2,
            canonical_text="A（改）", reason="edit")])
        with self.assertRaises(CommitRejected):
            self._commit(source, pack, claimed)

    def test_archived_source_rejected(self):
        source, pack, claimed = self._register_and_claim()
        get_journal(SID).append([S.OpSourceArchived(
            source_id=source.source_id, reason="session_trashed")])
        with self.assertRaises(CommitRejected):
            self._commit(source, pack, claimed)

    def test_workspace_deleted_rejected(self):
        source, pack, claimed = self._register_and_claim()
        from app.core.workspace import delete_workspace
        delete_workspace(WS)
        with self.assertRaises(CommitRejected):
            self._commit(source, pack, claimed)

    def test_scope_unselected_textbook_rejected(self):
        source, pack, claimed = self._register_and_claim()
        frozen = source.scope_revision
        # 撤选教材：工作区不再选任何卷 → scope revision 变化
        ws = Workspace(workspace_id=WS, name="空区", student_id=SID,
                       selected_file_ids=[])
        save_workspace(ws)
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        get_scope_resolver().invalidate()
        with self.assertRaises(CommitRejected):
            self._commit(source, pack, claimed,
                         expected_scope_revision=frozen)

    def test_base_judgment_cas_rejects_concurrent_update(self):
        source, pack, claimed = self._register_and_claim()
        state = get_journal(SID).state()
        with self.assertRaises(CommitRejected):
            self._commit(source, pack, claimed,
                         generation=state.generation,
                         expected_base_judgment_ids={
                             self.concept.key: "jdg_someone_else"})


if __name__ == "__main__":
    unittest.main()
