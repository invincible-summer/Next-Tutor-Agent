"""§11.4/R15：概念主张越出 pack 白名单时降级为弃权提交（live 回归）。

live 复现：无工作区聊天题卡的 P3 解释带概念主张，撞 concept_not_allowed
→ 整个提交 validation_rejected，job failed，连本题反馈都丢失。净化后必须
ABSTAINED（workspace_required / concept_not_allowed）并保留反馈，绝不停在
failed；带合法工作区 scope 时白名单内主张正常发布判断。
"""
from __future__ import annotations

import asyncio
import json
import unittest

from app.agents.assessment import manager as assessment_manager
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.scope import set_scope_resolver
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime
from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_unified_submission import FakeRunner

SID = "usr_sanitize"
WS = "ws_sanitize"


class _FixedScopeResolver:
    """注入固定 scope（1 个可评价概念），绕开教材/图谱装配。"""

    def __init__(self, concepts: list[S.ConceptRef]):
        self.concepts = concepts

    def resolve(self, student_id: str, workspace_id: str) -> S.EvaluationScope:
        if workspace_id != WS:
            from app.agents.student_model.evaluation.scope import ScopeNotFound
            raise ScopeNotFound(workspace_id)
        return S.EvaluationScope(
            workspace_id=workspace_id, scope_revision="sr_san_1",
            selected_volumes=[S.VolumeSelection(
                textbook_id="tb_w", graph_owner_namespace="public",
                topic_key="topic_w", file_ids=["f1"],
                graph_revision="gr_1")],
            allowed_concepts=self.concepts,
            graph_revisions=[S.GraphRevisionInfo(
                graph_owner_namespace="public", textbook_id="tb_w",
                graph_revision="gr_1")],
            unresolved_graph_count=0)


def _claim(concept_ref: str) -> dict:
    return {
        "local_id": "ob1", "concept_ref": concept_ref,
        "statement": "能在本题选对收敛判别条件",
        "stance": "supports", "current_evidence": [
            {"ref": "s1", "start": 0, "end": 1, "quote": "B"}],
        "opportunity_ref": "t1", "warrant": "学生选择了 B",
        "limits": [], "cognitive_processes": ["understand"],
        "knowledge_types": ["conceptual"], "evidence_conditions": [],
        "alternatives": [],
    }


def _update() -> dict:
    return {
        "concept_ref": "c1", "base_judgment_id": "",
        "retain_claim_ids": [], "add_claim_local_ids": ["ob1"],
        "revise_claims": [], "close_claims": [],
        "proposed_state": "supported_in_scope",
        "statement": "在本题条件下能选对判别条件",
    }


def _output(claims: list[dict], updates: list[dict] | None = None
            ) -> S.AssessmentInterpretationOutput:
    return S.AssessmentInterpretationOutput.model_validate({
        "criterion_results": [],
        "learner": {
            "applicable": True, "abstain_reason": None,
            "observation_claims": claims,
            "concept_updates": updates or [],
            "feedback": "本题反馈保留"},
        "continuation": {"action": "continue", "reason": ""},
    })


def _task(question_id: str, *, concept_refs=None) -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=question_id, question_revision=1,
        q_type=S.QuestionType.MULTIPLE_CHOICE,
        stem="条件概率的定义式是哪一个？",
        options={"A": "P(A)", "B": "P(A∩B)/P(A)"},
        answer="B", explanation="以 A 发生为条件",
        rubric=[S.FrozenCriterion(id="c1", description="选对", weight=1.0)],
        concept_refs=concept_refs or [],
        # knowledge_point 名称与 scope 概念 display_name 严格同名，走
        # §7.2 hint 匹配进入白名单（与聊天题卡注册路径一致）
        source_badge="条件概率",
        verification=S.TaskVerification(status="passed"))


def _concept() -> S.ConceptRef:
    return S.ConceptRef(graph_owner_namespace="public", textbook_id="tb_w",
                        file_ids=["f1"], concept_id="phys.c1",
                        concept_revision="cr_1", display_name="条件概率")


class AllowlistSanitizeContractTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()

    def tearDown(self) -> None:
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def _submit(self, task: S.TaskSnapshot, output, *,
                workspace_id: str = "") -> None:
        runner = FakeRunner([output])
        learner_runtime.set_evaluation_runner(runner)
        if workspace_id:
            ws_file = (self.root / "chat_history" / "workspaces"
                       / f"{workspace_id}.json")
            ws_file.write_text(json.dumps({
                "workspace_id": workspace_id, "student_id": SID,
                "name": "净化测试区", "session_ids": ["session_sanitize"],
                "selected_file_ids": [], "updated_at": 0}), encoding="utf-8")
        asyncio.run(assessment_manager.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id=task.question_id,
                                       question_revision=1),
            student_answer="B", source_session_ref="session_sanitize",
            workspace_id=workspace_id,
            run_inline=True, runner=runner))

    def _job(self):
        state = get_journal(SID).state()
        for rt in state.jobs.values():
            if rt.job.kind == S.JobKind.ASSESSMENT_EVALUATION:
                return rt, state
        raise AssertionError("no assessment job in journal")

    def test_no_workspace_claims_become_workspace_required_abstain(self):
        # 聊天无工作区路径：task.concept_refs 为空 → pack 白名单为空
        task = _task("q_san_1", concept_refs=[])
        assessment_manager.register_task_snapshot(SID, task)
        self._submit(task, _output([_claim("c1")]))
        rt, state = self._job()
        self.assertEqual(rt.job.state, S.JobState.ABSTAINED)
        self.assertNotEqual(rt.last_error_code, "validation_rejected")
        # 题目反馈仍然随弃权解释提交（不能整个丢掉）
        src = next(iter(state.sources.values()))
        self.assertTrue(src.current_interpretation_id)

    def test_scoped_workspace_publishes_allowed_and_drops_foreign(self):
        set_scope_resolver(_FixedScopeResolver([_concept()]))
        task = _task("q_san_2", concept_refs=[])
        assessment_manager.register_task_snapshot(SID, task)
        self._submit(task, _output(
            [_claim("c1"), _claim("foreign_key")], [_update()]),
            workspace_id=WS)
        rt, state = self._job()
        self.assertEqual(rt.job.state, S.JobState.SUCCEEDED)
        self.assertNotEqual(rt.last_error_code, "validation_rejected")
        # 白名单内主张 + patch 发布为当前判断
        self.assertTrue(state.concept_current)
        key = next(iter(state.concept_current.values()))
        self.assertEqual(len(state.judgments[key].claims), 1)

    def test_all_foreign_claims_abstain_not_fail(self):
        set_scope_resolver(_FixedScopeResolver([_concept()]))
        task = _task("q_san_3", concept_refs=[])
        assessment_manager.register_task_snapshot(SID, task)
        self._submit(task, _output([_claim("foreign_only")], [_update()]),
                     workspace_id=WS)
        rt, _ = self._job()
        self.assertEqual(rt.job.state, S.JobState.ABSTAINED)
        self.assertNotEqual(rt.last_error_code, "validation_rejected")


if __name__ == "__main__":
    unittest.main()
