"""B07 测评/评价/教学指导动作回归（plan.md §21.3，8 操作）。

覆盖：review_required 审批链；expected_status/expected_revision 绑定
（引用正确性 404/409）；assessment.start 经真实路由核心（fake LLM）；
practice 同题路径（无 LLM）；teaching.apply 核对指导实际生效（FULL-14）；
retry 仅 failed；synthesize 复用排队作业。
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.core import assistant_store as store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


def _gen_json(n: int = 1) -> str:
    questions = []
    for i in range(1, n + 1):
        questions.append({
            "id": i, "type": "multiple_choice", "stem": f"助手测评题{i}",
            "options": {"A": "对", "B": "错"}, "answer": "A",
            "explanation": "解析内容至少十五个字，保证结构校验通过。",
            "knowledge_point": "测试点", "difficulty": "easy",
            "rubric_criteria": [
                {"id": "c1", "description": "选对", "weight": 1.0,
                 "critical": True}],
            "equivalent_solutions": [],
        })
    return json.dumps({"questions": questions}, ensure_ascii=False)


class _GenLLM:
    """出题 LLM 固定回放（蓝图 → 生成 → 审题），与生命周期测试同构。"""

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False, **kw):
        text = "\n".join(str(m.get("content") or "") for m in messages)
        if "任务设计者" in text:
            return json.dumps({"items": [{
                "local_question_id": "q1", "target_concept_refs": ["测试点"],
                "target_claims": ["能识别测试点"],
                "intended_processes": ["understand"],
                "knowledge_types": ["factual"], "q_type": "multiple_choice",
                "difficulty_design": "基础", "task_family": "b07-test",
                "construction_brief": "B07 测试题",
            }]}, ensure_ascii=False), {}
        if "出题审核员" in text:
            return json.dumps({"items": [{
                "question_ref": "1", "answer_check": "valid",
                "grounding_check": "not_required",
                "actual_required_processes": ["understand"],
                "knowledge_types": ["factual"], "alignment": "aligned",
                "opportunity_checks": [], "rubric_issues": [],
                "brief_basis": "", "grounding_refs": [],
                "recommended_revision": "", "proposed_status": "passed"}]},
                ensure_ascii=False), {}
        return _gen_json(1), {}


class _B07Case(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b07_" + _hex()
        self.cid = "astc_b07"
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": self.cid,
            "turn_id": "astt_b07", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, approval_id: str | None = None):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-b07", route_epoch=1,
            approval_id=approval_id, main_loop=None)

    def _preview(self, action_id: str):
        from app.agents.site_assistant import previews
        return previews.build_preview(self.sid, action_id)

    def _approve(self, action_id: str) -> str:
        from app.agents.site_assistant import previews
        preview = self._preview(action_id)
        approval = previews.approve(
            self.sid, action_id, preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        return approval["approval_id"]

    def _seed_proposal(self, title: str = "多举生活实例",
                       status: str = "proposed") -> str:
        from app.agents.evaluation import store as eval_store
        from app.agents.evaluation.schema import ImprovementProposal
        pid = "prop_" + _hex()
        eval_store.save_proposals(self.sid, [ImprovementProposal(
            id=pid, title=title, applicability="物理讲解",
            guidance="讲解新概念时先给一个生活实例再给定义。",
            cautions=["避免过长的引子"], confidence=0.7,
            status=status)])
        return pid


class TeachingFlowTest(_B07Case):
    def test_approve_apply_revoke_with_real_effect(self) -> None:
        from app.agents.evaluation import store as eval_store
        from app.agents.teaching_engine import guidance_store
        pid = self._seed_proposal()
        # 批准（不自动应用）。
        self._add_action("asta_ta", {
            "kind": "domain_write", "operation": "teaching.approve",
            "input": {"proposal_id": pid, "expected_status": "proposed"}})
        preview = self._preview("asta_ta")
        self.assertEqual(preview["approval"], "review_required")
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected):
            self._execute("asta_ta")
        result = self._execute("asta_ta", self._approve("asta_ta"))
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(eval_store.load_proposal(self.sid, pid).status,
                         "approved")
        # 应用：独立确认 + 核对指导真实 active。
        self._add_action("asta_tap", {
            "kind": "domain_write", "operation": "teaching.apply",
            "input": {"proposal_id": pid, "expected_status": "approved"}})
        result = self._execute("asta_tap", self._approve("asta_tap"))
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(result["business_result"]["result_revision"],
                         "applied_active")
        entries = [e for e in guidance_store.load_all(self.sid)
                   if e.source_proposal == pid]
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].active)   # FULL-14：实际生效，非仅状态
        # 撤销：立即停用、审计保留。
        gid = entries[0].id
        self._add_action("asta_tr", {
            "kind": "domain_write", "operation": "teaching.revoke",
            "input": {"guidance_id": gid, "expected_active": True}})
        result = self._execute("asta_tr", self._approve("asta_tr"))
        self.assertEqual(result["action"]["state"], "succeeded")
        entry = next(e for e in guidance_store.load_all(self.sid)
                     if e.id == gid)
        self.assertFalse(entry.active)
        # 重复撤销 → 409（target_changed）。
        self._add_action("asta_tr2", {
            "kind": "domain_write", "operation": "teaching.revoke",
            "input": {"guidance_id": gid, "expected_active": True}})
        with mock.patch.object(
                __import__("app.agents.site_assistant.previews",
                           fromlist=["previews"]),
                "actions_enabled", return_value=True):
            from app.agents.site_assistant import previews
            with self.assertRaises(ActionRejected) as ctx:
                previews.build_preview(self.sid, "asta_tr2")
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_apply_before_approve_rejected(self) -> None:
        pid = self._seed_proposal()   # 仍是 proposed
        self._add_action("asta_tap2", {
            "kind": "domain_write", "operation": "teaching.apply",
            "input": {"proposal_id": pid, "expected_status": "approved"}})
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_tap2")
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_unknown_proposal_404(self) -> None:
        self._add_action("asta_ta3", {
            "kind": "domain_write", "operation": "teaching.approve",
            "input": {"proposal_id": "prop_missing_1",
                      "expected_status": "proposed"}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_ta3")
        self.assertEqual(ctx.exception.code, "entity_not_found")


class AssessmentActionsTest(_B07Case):
    def _seed_question(self):
        from app.agents.student_model.evaluation import schema as S
        from app.agents.assessment.manager import register_task_snapshot
        task = S.TaskSnapshot(
            question_id="q_b07_" + _hex(), question_revision=1,
            q_type=S.QuestionType.MULTIPLE_CHOICE, stem="s",
            options={"A": "1", "B": "2"}, answer="B", explanation="e",
            rubric=[S.FrozenCriterion(id="c1", description="选对",
                                      weight=1.0)])
        register_task_snapshot(self.sid, task)
        return task

    def test_practice_same_mode(self) -> None:
        task = self._seed_question()
        self._add_action("asta_ap", {
            "kind": "domain_write", "operation": "assessment.practice",
            "input": {"question_id": task.question_id,
                      "question_revision": 1, "mode": "same"}})
        preview = self._preview("asta_ap")
        self.assertEqual(preview["approval"], "review_required")
        self.assertIn("同题再练", preview["summary"])
        result = self._execute("asta_ap", self._approve("asta_ap"))
        self.assertEqual(result["action"]["state"], "succeeded")
        from app.agents.student_model.evaluation.store import get_journal
        state = get_journal(self.sid).state()
        # 新实例记录了来源引用（origin_question_ref 指向原题）。
        origins = [getattr(t, "origin_question_ref", None)
                   for revs in state.tasks.values() for t in revs.values()]
        self.assertTrue(any(
            ref is not None
            and getattr(ref, "question_id", "") == task.question_id
            for ref in origins))

    def test_practice_missing_question_404(self) -> None:
        self._add_action("asta_ap2", {
            "kind": "domain_write", "operation": "assessment.practice",
            "input": {"question_id": "q_missing_1",
                      "question_revision": 1, "mode": "same"}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_ap2")
        self.assertEqual(ctx.exception.code, "entity_not_found")

    def test_start_uses_real_route_core(self) -> None:
        from app.core import learner_runtime
        learner_runtime.reset_learner_runtime()
        try:
            self._add_action("asta_as", {
                "kind": "domain_write", "operation": "assessment.start",
                "input": {"workspace_id": "", "concept_keys": ["测试点"],
                          "count": 1}})
            preview = self._preview("asta_as")
            self.assertEqual(preview["approval"], "review_required")
            self.assertTrue(any("不代答" in s for s in preview["side_effects"]))
            with mock.patch(
                    "app.api.v1.assessment.get_llm",
                    return_value=_GenLLM()):
                result = self._execute("asta_as", self._approve("asta_as"))
            self.assertEqual(result["action"]["state"], "succeeded")
            self.assertTrue(result["business_result"]["entity_id"])
        finally:
            learner_runtime.reset_learner_runtime()


class EvaluationActionsTest(_B07Case):
    def test_request_review_gates(self) -> None:
        # 未知来源 / 版本不符 / 解释不存在（引用正确性）。
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_er", {
            "kind": "domain_write", "operation": "evaluation.request_review",
            "input": {"source_id": "src_missing_1",
                      "interpretation_id": "itp_xxxx1",
                      "reason": "这个解释引用的概念不对", "expected_revision":
                      1}})
        with self.assertRaises(ActionRejected) as ctx:
            self._preview("asta_er")
        self.assertEqual(ctx.exception.code, "entity_not_found")

    def test_retry_only_failed(self) -> None:
        from app.core import learner_runtime
        from app.agents.student_model.evaluation import schema as S
        from app.agents.site_assistant.actions import ActionRejected
        learner_runtime.reset_learner_runtime()
        try:
            scheduler = learner_runtime.get_scheduler()
            job = scheduler.enqueue(self.sid, kind=S.JobKind.SYNTHESIS_WORKSPACE)
            self._add_action("asta_ert", {
                "kind": "domain_write", "operation": "evaluation.retry",
                "input": {"job_id": job.job_id}})
            # QUEUED（非 failed）→ 409。
            with self.assertRaises(ActionRejected) as ctx:
                self._preview("asta_ert")
            self.assertEqual(ctx.exception.code, "target_changed")
            # 未知作业 → 404。
            self._add_action("asta_ert2", {
                "kind": "domain_write", "operation": "evaluation.retry",
                "input": {"job_id": "job_missing_1"}})
            with self.assertRaises(ActionRejected) as ctx:
                self._preview("asta_ert2")
            self.assertEqual(ctx.exception.code, "entity_not_found")
        finally:
            learner_runtime.reset_learner_runtime()

    def test_synthesize_enqueues_once(self) -> None:
        from app.core import learner_runtime
        from app.agents.student_model.evaluation.store import get_journal
        learner_runtime.reset_learner_runtime()
        try:
            wid = self._make_workspace()
            self._add_action("asta_es", {
                "kind": "domain_write", "operation":
                    "evaluation.synthesize",
                "input": {"workspace_id": wid}})
            result = self._execute("asta_es", self._approve("asta_es"))
            self.assertEqual(result["action"]["state"], "succeeded")
            job_id = result["business_result"]["entity_id"]
            state = get_journal(self.sid).state()
            self.assertIn(job_id, state.jobs)
            # 重复请求 → 领域复用排队作业（duplicate）。
            self._add_action("asta_es2", {
                "kind": "domain_write", "operation":
                    "evaluation.synthesize",
                "input": {"workspace_id": wid}})
            result = self._execute("asta_es2", self._approve("asta_es2"))
            self.assertEqual(result["action"]["state"], "succeeded")
            self.assertEqual(
                result["business_result"]["related_ids"]["duplicate"],
                "True")
        finally:
            learner_runtime.reset_learner_runtime()

    def _make_workspace(self) -> str:
        from app.core.workspace import Workspace, save_workspace
        return save_workspace(Workspace(name="B07评价区",
                                        student_id=self.sid))


if __name__ == "__main__":
    unittest.main()
