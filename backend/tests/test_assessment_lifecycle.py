"""M4 CAT 生命周期回归（journal 版，plan §11.5 / A10）。

固定的持久化契约：独立 assessment_id、停止结论与作答同批落盘、next 不叠
题/终态不进新题、abandon 幂等、刷新恢复（GET /active）、报告不覆盖、
进程重启（journal 缓存清空）后状态一致。全部经 StorageSandbox 落盘。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.agents.student_model.evaluation import store as st  # noqa: E402
from app.core import learner_runtime  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from tests.test_unified_submission import FakeRunner, _learner_output  # noqa: E402


def _gen_json(n: int = 1) -> str:
    questions = []
    for i in range(1, n + 1):
        questions.append({
            "id": i, "type": "multiple_choice", "stem": f"生命周期题{i}",
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
    """出题 LLM：蓝图轮（P1）→ 生成 → P2 审题，全部固定回放。"""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False, **kw):
        text = "\n".join(str(m.get("content") or "") for m in messages)
        if "任务设计者" in text:
            self.calls += 1
            return json.dumps({"items": [{
                "local_question_id": "q1", "target_concept_refs": ["测试点"],
                "target_claims": ["能识别测试点"],
                "intended_processes": ["understand"],
                "knowledge_types": ["factual"], "q_type": "multiple_choice",
                "difficulty_design": "基础", "task_family": "life-test",
                "construction_brief": "生命周期测试题",
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


class CatLifecycleTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()      # 沙箱已设 AUTH_MODE=1 并负责恢复
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)
        id_store.create_user("life@example.com", "Life",
                             hash_password("pw123456"), user_id="usr_life")
        self.token = create_token("usr_life")
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _start(self) -> dict:
        with patch("app.api.v1.assessment.get_llm", return_value=_GenLLM()):
            r = self.client.post("/api/v1/assessment/start", json={
                "workspace_id": "", "concept_keys": ["测试点"],
                "goal": {"purpose": "adaptive", "target_claims": []},
                "count": 3}, headers=self._headers())
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _answer(self, assessment_id: str, question: dict, answer: str):
        self.runner.outputs = [_learner_output(applicable=False)]
        return self._answer_raw(assessment_id, question, answer)

    def _answer_raw(self, assessment_id: str, question: dict, answer: str):
        return self.client.post("/api/v1/assessment/answer", json={
            "assessment_id": assessment_id,
            "question_id": question["question_id"],
            "question_revision": question["question_revision"],
            "student_answer": answer}, headers=self._headers())

    def test_start_assigns_fresh_assessment_id_each_run(self):
        a1 = self._start()
        a2 = self._start()
        self.assertNotEqual(a1["assessment_id"], a2["assessment_id"])
        # 旧实例被 supersede（user_stopped），同区只留一个 active
        active = self.client.get("/api/v1/assessment/active",
                                 headers=self._headers()).json()
        self.assertEqual(active["assessment_id"], a2["assessment_id"])

    def test_answer_then_next_new_question_and_stop_on_max(self):
        start = self._start()
        aid = start["assessment_id"]
        q1 = start["question"]
        r1 = self._answer(aid, q1, "A")
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["task_result"]["verdict"], "correct")
        with patch("app.api.v1.assessment.get_llm", return_value=_GenLLM()):
            r2 = self.client.post("/api/v1/assessment/next", json={
                "assessment_id": aid}, headers=self._headers())
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIsNotNone(r2.json()["question"])
        q2 = r2.json()["question"]
        self.assertNotEqual(q2["question_id"], q1["question_id"])
        # 未作答时 next 重发相同题，不叠题
        with patch("app.api.v1.assessment.get_llm", return_value=_GenLLM()):
            r3 = self.client.post("/api/v1/assessment/next", json={
                "assessment_id": aid}, headers=self._headers())
        self.assertEqual(r3.json()["question"]["question_id"],
                         q2["question_id"])
        # 答满 count=3 后到达 max_questions → completed + 报告
        self._answer(aid, q2, "B")
        with patch("app.api.v1.assessment.get_llm", return_value=_GenLLM()):
            r4 = self.client.post("/api/v1/assessment/next", json={
                "assessment_id": aid}, headers=self._headers())
        q3 = r4.json()["question"]
        self.assertIsNotNone(q3)
        r5 = self._answer(aid, q3, "A")
        self.assertIn("summary", r5.json(), r5.text)
        self.assertEqual(r5.json()["summary"]["status"], "completed")
        self.assertEqual(r5.json()["summary"]["stop_code"] or
                         r5.json().get("stop_reason"), "max_questions")

    def test_terminal_replay_same_answer_is_duplicate(self):
        start = self._start()
        aid = start["assessment_id"]
        q1 = start["question"]
        self._answer(aid, q1, "A")
        r = self._answer(aid, q1, "A")
        # 同题同答案重放：重复提交语义（不产生第二份观察）
        self.assertEqual(r.status_code, 200)
        state = st.get_journal("usr_life").state()
        count = sum(1 for s in state.sources.values()
                    if s.receipt.assessment_id == aid)
        self.assertEqual(count, 1)

    def test_abandon_idempotent_and_report_persists(self):
        start = self._start()
        aid = start["assessment_id"]
        q1 = start["question"]
        self._answer(aid, q1, "A")
        for _ in range(2):     # 幂等
            r = self.client.post("/api/v1/assessment/abandon", json={
                "assessment_id": aid}, headers=self._headers())
            self.assertEqual(r.status_code, 200)
        report = self.client.get("/api/v1/assessment/report", params={
            "assessment_id": aid}, headers=self._headers()).json()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["summary"]["status"], "abandoned")
        self.assertEqual(report["summary"]["asked"], 1)
        # 新测评不覆盖旧报告
        start2 = self._start()
        report2 = self.client.get("/api/v1/assessment/report", params={
            "assessment_id": aid}, headers=self._headers()).json()
        self.assertEqual(report2["summary"]["asked"], 1)
        self.assertNotEqual(start2["assessment_id"], aid)

    def test_restart_restores_active_question(self):
        start = self._start()
        aid = start["assessment_id"]
        # 模拟进程重启：journal/scope/runner 缓存全部失效
        st.reset_journal_cache()
        learner_runtime.reset_learner_runtime()
        learner_runtime.set_evaluation_runner(self.runner)
        active = self.client.get("/api/v1/assessment/active",
                                 headers=self._headers()).json()
        self.assertEqual(active["status"], "ok")
        self.assertEqual(active["assessment_id"], aid)
        self.assertEqual(active["question"]["question_id"],
                         start["question"]["question_id"])

    def test_question_public_has_no_answer(self):
        start = self._start()
        q = start["question"]
        dumped = json.dumps(q, ensure_ascii=False)
        self.assertNotIn("\"answer\"", dumped)
        self.assertNotIn("rubric", dumped)
        self.assertIn("question_id", dumped)

    def test_next_not_blocked_after_failed_evaluation(self):
        """语义评价硬失败（schema_invalid，终态）不得让 next 永远 409。

        409 evaluation_pending 只表示"评价仍在途"（queued/running/
        retry_wait）；终态 failed 时按 §10.3 评价层是 unavailable，
        CAT 应继续出题而不是卡死在“评价仍在进行”。"""
        start = self._start()
        aid = start["assessment_id"]
        q1 = start["question"]
        # 两次 run_structured 都返回错误码 → 语义 job 终态 failed
        self.runner.outputs = ["schema_invalid", "schema_invalid"]
        r1 = self._answer_raw(aid, q1, "A")
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["task_result"]["verdict"], "correct")
        # §10.3：硬故障 → unavailable，不冒充 pending
        self.assertEqual(r1.json()["evaluation"]["status"], "unavailable")
        state = st.get_journal("usr_life").state()
        from app.agents.student_model.evaluation.schema import JobState
        failed_jobs = [rt for rt in state.jobs.values()
                       if rt.job.state == JobState.FAILED]
        self.assertTrue(failed_jobs, "语义评价作业应已失败")
        with patch("app.api.v1.assessment.get_llm", return_value=_GenLLM()):
            r2 = self.client.post("/api/v1/assessment/next", json={
                "assessment_id": aid}, headers=self._headers())
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIsNotNone(r2.json()["question"],
                             "评价失败后 next 必须照常出下一题")
        # 报告：MC 局部判分不受语义失败影响（§11.5 分清 pending 与局部结果）
        rep = self.client.get("/api/v1/assessment/report", params={
            "assessment_id": aid}, headers=self._headers()).json()
        self.assertEqual(rep["summary"]["graded"], 1, rep)
        self.assertEqual(rep["summary"]["counts"]["correct"], 1, rep)
        self.assertEqual(rep["summary"]["items"][0]["evaluation_status"],
                         "unavailable", rep["summary"]["items"][0])


class ConceptLabelAndAttributionTest(StorageSandboxTestCase):
    """CAT 出题目标与归因回归（G7 后 live 验收发现）。

    start 传入的 concept_keys 是 ConceptRef.key（24 位哈希）：直接进提示词
    会让 LLM 读不出主题（选「曲线坐标」出成泊松分布）；归因若拿 concept_id
    对比 key 则永远落空。此处锁定 key→显示名解析与按 key 归因两个契约。
    """

    def _ref(self):
        from app.agents.student_model.evaluation import schema as S
        return S.ConceptRef(
            graph_owner_namespace="public", textbook_id="tb_1",
            file_ids=["f1"], concept_id="custom.tb-tb_1.c.abc",
            concept_revision="cr_1", display_name="曲线坐标")

    def test_concept_label_resolves_display_name(self):
        from app.api.v1.assessment import _concept_label
        ref = self._ref()

        class _Scope:
            allowed_concepts = [ref]

        # scope 缺席（无工作区）→ key 原样，不劣于旧行为
        self.assertEqual(_concept_label(None, [ref.key]), ref.key)
        # 命中 → 概念名进提示词，LLM 可读
        self.assertEqual(_concept_label(_Scope(), [ref.key]), "曲线坐标")
        # 未命中 → key；空 keys → 空
        self.assertEqual(_concept_label(_Scope(), ["deadbeefcafe"]), "deadbeefcafe")
        self.assertEqual(_concept_label(_Scope(), []), "")

    def test_match_concept_refs_by_key_not_concept_id(self):
        from app.api.v1.assessment import _match_concept_refs
        from app.agents.assessment import adaptive_test as cat
        ref = self._ref()
        instance = cat.CatInstance(
            assessment_id="a1", workspace_id="ws_x",
            concept_keys=[ref.key], concept=ref.display_name)

        class _Q:  # LLM 未回显任何同名 knowledge_points 的最坏情形
            concept = ""
            knowledge_points = []

        class _Resolver:
            def resolve(self, sid, ws):
                class _Scope:
                    allowed_concepts = [ref]
                return _Scope()

        with patch("app.agents.student_model.evaluation.scope"
                   ".get_scope_resolver", return_value=_Resolver()):
            out = _match_concept_refs("usr_x", instance, _Q())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].display_name, "曲线坐标")


if __name__ == "__main__":
    unittest.main()
