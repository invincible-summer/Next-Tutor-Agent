"""G2 回归：统一受理协议（plan §18.2 test_unified_submission）。

chat/center/CAT 同 runner/prompt/schema；MC 一次语义调用；完整答案指纹；
重复 key 同结果；indeterminate 全链正确；硬校验失败不产生新能力结论。
全部 fake runner（零网络），存储全部落沙箱。
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.assessment import manager as am
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation import service as svc
from app.agents.student_model.evaluation.llm import StructuredOutput
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime

SID = "usr_submit_a"


class FakeRunner:
    """按脚本回放的结构化 runner；记录每次 system/user 供断言。"""

    def __init__(self, outputs: list[S.AssessmentInterpretationOutput | str]):
        self.outputs = list(outputs)
        self.calls: list[tuple[str, str]] = []

    async def run_structured(self, *, system: str, user: str,
                             output_model, max_output_tokens: int = 4000
                             ) -> StructuredOutput:
        self.calls.append((system, user))
        item = self.outputs.pop(0) if self.outputs else "empty_content"
        if isinstance(item, str):
            return StructuredOutput(raw="", error_code=item,
                                    retryable_error=False)
        return StructuredOutput(parsed=item, raw="{}", finish_reason="stop")


def _concept() -> S.ConceptRef:
    return S.ConceptRef(graph_owner_namespace="public", textbook_id="tb_1",
                        file_ids=["f1"], concept_id="phys.c1",
                        concept_revision="cr_1", display_name="并联关系")


def _mc_task(question_id: str = "q_mc_1") -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=question_id, question_revision=1,
        q_type=S.QuestionType.MULTIPLE_CHOICE, stem="哪个是并联？",
        options={"A": "同端点", "B": "上下排列", "C": "随机", "D": "都行"},
        answer="A", explanation="公共端点判断",
        rubric=[S.FrozenCriterion(id="c1", description="选对", weight=1.0)],
        concept_refs=[_concept()])


def _open_task(question_id: str = "q_open_1") -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=question_id, question_revision=1,
        q_type=S.QuestionType.SHORT_ANSWER,
        stem="解释为什么换元后积分限要同步变换",
        answer="因为映射改变了区间端点",
        explanation="代换 x=g(t) 把原区间端点映到新值",
        rubric=[
            S.FrozenCriterion(id="k1", description="指出映射改变端点",
                              weight=1.0, critical=True),
            S.FrozenCriterion(id="k2", description="写出新端点计算", weight=1.0),
        ],
        concept_refs=[_concept()])


def _learner_output(claims: list[dict] | None = None,
                    applicable: bool = True
                    ) -> S.AssessmentInterpretationOutput:
    # applicable=true 必须有观察（§4.3：无有效新观察应 applicable=false）
    if applicable and claims is None:
        claims = [_claim()]
    return S.AssessmentInterpretationOutput.model_validate({
        "criterion_results": [],
        "learner": {
            "applicable": applicable,
            "abstain_reason": None if applicable else "no_new_evidence",
            "observation_claims": claims or [],
            "concept_updates": [], "feedback": "本次反馈",
        },
        "continuation": {"action": "continue", "reason": ""},
    })


def _claim(statement: str = "能在本例用端点说明并联",
           stance: str = "supports") -> dict:
    return {
        "local_id": "ob1", "concept_ref": "c1", "statement": statement,
        "stance": stance, "current_evidence": [
            {"ref": "s1", "start": 0, "end": 1, "quote": "A"}],
        "opportunity_ref": "t1", "warrant": "学生选择了 A",
        "limits": [], "cognitive_processes": ["understand"],
        "knowledge_types": ["conceptual"], "evidence_conditions": [],
        "alternatives": [],
    }


class SubmissionTestBase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)

    def submit(self, task: S.TaskSnapshot, answer: str, **kw: Any):
        am.register_task_snapshot(SID, task)
        return asyncio.run(am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id=task.question_id,
                                       question_revision=1),
            student_answer=answer, run_inline=True, runner=self.runner, **kw))


class TestMcSubmission(SubmissionTestBase):
    def test_mc_deterministic_result_committed_with_receipt(self):
        receipt = self.submit(_mc_task(), "A")
        self.assertEqual(receipt.task_result.grading_status,
                         S.GradingStatus.GRADED)
        self.assertEqual(receipt.task_result.verdict, S.Verdict.CORRECT)
        self.assertEqual(receipt.task_result.task_score, 1.0)
        # 语义 job 同步完成（inline），一次 P3 调用
        self.assertEqual(len(self.runner.calls), 1)
        system, user = self.runner.calls[0]
        self.assertIn("学生作答的学习证据解释者", system)   # P3 角色文本
        self.assertIn("JSON Schema", system)               # schema 在 system
        self.assertIn("只有选项行为可观察", system)          # choice_only 情景
        payload = json.loads(user)
        self.assertEqual(payload["task"]["task_mode"], "multiple_choice")
        self.assertEqual(payload["task"]["task_result"]["verdict"],
                         "correct")

    def test_mc_choice_only_scenario_blocks_reasoning_claim(self):
        """A06/机会门：只有选项行为可观察，声称 analyze 的主张所属 patch
        被拒；解释本身（本题局部反馈）仍可提交。"""
        claim = _claim()
        claim["cognitive_processes"] = ["analyze"]
        claim["current_evidence"] = [
            {"ref": "s1", "start": 0, "end": 1, "quote": "B"}]
        out = _learner_output([claim])
        self.runner.outputs = [out]
        receipt = self.submit(_mc_task(), "B")
        state = get_journal(SID).state()
        src = state.sources[receipt.source_id]
        self.assertTrue(src.current_interpretation_id)
        # 机会缺失的主张不产生任何概念判断
        self.assertEqual(len(state.judgments), 0)

    def test_duplicate_answer_replays_without_second_call(self):
        task = _mc_task()
        self.runner.outputs = [_learner_output()]
        first = self.submit(task, "A")
        calls_after_first = len(self.runner.calls)
        second = self.submit(task, "A")
        self.assertTrue(second.duplicate)
        self.assertEqual(second.attempt_id, first.attempt_id)
        self.assertEqual(len(self.runner.calls), calls_after_first)

    def test_different_answer_same_instance_conflicts(self):
        task = _mc_task()
        self.submit(task, "A")
        with self.assertRaises(am.QuestionAlreadyAnswered):
            self.submit(task, "B")

    def test_wrong_mc_verdict_wrong(self):
        receipt = self.submit(_mc_task(), "D")
        self.assertEqual(receipt.task_result.verdict, S.Verdict.WRONG)
        self.assertEqual(receipt.task_result.task_score, 0.0)


class TestOpenSubmission(SubmissionTestBase):
    def _open_output(self, k1: str = "met", k2: str = "partial"
                     ) -> S.AssessmentInterpretationOutput:
        return S.AssessmentInterpretationOutput.model_validate({
            "criterion_results": [
                {"criterion_id": "k1", "result": k1, "evidence_refs": ["s1"],
                 "comment": ""},
                {"criterion_id": "k2", "result": k2, "evidence_refs": [],
                 "comment": ""},
            ],
            "learner": {"applicable": False,
                        "abstain_reason": "no_new_evidence",
                        "observation_claims": [],
                        "concept_updates": [], "feedback": "分步反馈"},
            "continuation": None,
        })

    def test_open_answer_scored_by_frozen_weights(self):
        self.runner.outputs = [self._open_output("met", "partial")]
        receipt = self.submit(_open_task(), "因为 x=g(t) 把端点 a 映到 g(a)…")
        tr = receipt.task_result
        self.assertEqual(tr.grading_status, S.GradingStatus.GRADED)
        self.assertEqual(tr.task_score, 0.75)
        self.assertEqual(tr.verdict, S.Verdict.CORRECT)
        self.assertEqual(len(tr.criterion_results), 2)

    def test_critical_not_observed_is_indeterminate_not_partial(self):
        """A04：必需 criterion 未观察到 → indeterminate（score=null 贯穿）。"""
        self.runner.outputs = [self._open_output("not_observed", "met")]
        receipt = self.submit(_open_task(), "我不确定")
        tr = receipt.task_result
        self.assertEqual(tr.grading_status, S.GradingStatus.INDETERMINATE)
        self.assertIsNone(tr.task_score)
        self.assertIsNone(tr.verdict)

    def test_missing_criterion_is_indeterminate(self):
        out = S.AssessmentInterpretationOutput.model_validate({
            "criterion_results": [
                {"criterion_id": "k1", "result": "met", "evidence_refs": [],
                 "comment": ""}],
            "learner": {"applicable": False,
                        "abstain_reason": "no_new_evidence",
                        "observation_claims": [],
                        "concept_updates": [], "feedback": ""},
        })
        self.runner.outputs = [out]
        receipt = self.submit(_open_task(), "只答了一半")
        self.assertEqual(receipt.task_result.grading_status,
                         S.GradingStatus.INDETERMINATE)

    def test_full_text_fingerprint_not_prefix(self):
        """A05：答案前 200 字相同、后文不同 → 不合并（新 instance 冲突）。"""
        task = _open_task()
        head = "这是一段足够长的开头，" + "铺垫" * 40
        answer_a = head + "；结论 A：端点被映射改变。"
        answer_b = head + "；结论 B：完全不同的后文。"
        self.runner.outputs = [self._open_output(), self._open_output()]
        self.submit(task, answer_a)
        with self.assertRaises(am.QuestionAlreadyAnswered):
            self.submit(task, answer_b)


class TestValidatorGate(SubmissionTestBase):
    def test_forged_quote_rejects_whole_result(self):
        """伪引文（quote 与原文不一致）→ 硬错误，job 失败，无判断。"""
        claim = _claim()
        claim["current_evidence"] = [
            {"ref": "s1", "start": 0, "end": 5, "quote": "没写过的话"}]
        self.runner.outputs = [_learner_output([claim])]
        receipt = self.submit(_mc_task(), "A")
        state = get_journal(SID).state()
        src = state.sources[receipt.source_id]
        # 硬错误 → CommitRejected → job failed，不产生能力结论
        self.assertFalse(src.current_interpretation_id)
        job = state.jobs[receipt.job_id]
        self.assertEqual(job.job.state, S.JobState.FAILED)

    def test_concept_outside_allowlist_rejected(self):
        claim = _claim()
        claim["concept_ref"] = "c99"     # 不在 pack 白名单
        self.runner.outputs = [_learner_output([claim])]
        receipt = self.submit(_mc_task(), "A")
        state = get_journal(SID).state()
        self.assertFalse(
            state.sources[receipt.source_id].current_interpretation_id)


class TestNoWorkspace(SubmissionTestBase):
    def test_no_workspace_task_only_feedback(self):
        """无 workspace：evaluation=unavailable(workspace_required)；P3
        task-only 模式，不把 source 放进任何工作区（§11.4）。"""
        task = _mc_task()                  # concept_refs 仍绑定，但无 workspace
        self.runner.outputs = [_learner_output(applicable=False)]
        receipt = self.submit(task, "A")
        self.assertEqual(receipt.evaluation_status, "unavailable")
        self.assertEqual(receipt.evaluation_reason, "workspace_required")
        state = get_journal(SID).state()
        src = state.sources[receipt.source_id]
        self.assertEqual(src.receipt.workspace_id_at_observation, "")


class TestAssistanceFloor(SubmissionTestBase):
    def test_hint_assistance_affects_receipt_and_scenario(self):
        task = _mc_task()
        am.register_task_snapshot(SID, task)
        qref = S.QuestionRef(question_id=task.question_id,
                             question_revision=1)
        am.record_assistance(SID, qref,
                             kind=S.AssistanceEventKind.HINT_REQUESTED,
                             detail="提示内容")
        self.runner.outputs = [_learner_output()]
        receipt = asyncio.run(am.evaluate_submission(
            student_id=SID, question_ref=qref, student_answer="A",
            run_inline=True, runner=self.runner))
        state = get_journal(SID).state()
        src = state.sources[receipt.source_id]
        self.assertEqual(src.receipt.assistance_floor,
                         S.AssistanceLevel.KEY_HINTS)
        system, user = self.runner.calls[0]
        payload = json.loads(user)
        self.assertEqual(payload["assistance_before_response"][0]["kind"],
                         "hint_requested")
        # 情景文本进 system（§9.5 顺序：题型→帮助）
        self.assertIn("明确引用答前帮助", system)


class TestInlineRecovery(SubmissionTestBase):
    """首答中断后的自愈契约（G7 后 live 验收发现的三处缺陷）。

    场景原型：客户端中断/未预期异常让 run_inline 评价中途死亡——source
    与 job 已落盘、无终态；此后同题重试撞 QuestionAlreadyAnswered，而
    claim_next FIFO 又会认领旧 job 并因“不是本次的 job”跳过执行。
    """

    def _seed_stale(self, question_id: str) -> str:
        """模拟中断残留：受理 + 入队，但评价从未运行。"""
        am.register_task_snapshot(SID, _open_task(question_id))
        receipt = asyncio.run(am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id=question_id,
                                       question_revision=1),
            student_answer="中断前的旧答案", run_inline=False,
            runner=self.runner))
        rt = get_journal(SID).state().jobs.get(receipt.job_id)
        self.assertEqual(rt.job.state, S.JobState.QUEUED)
        return receipt.job_id

    def test_stale_queued_job_drained_and_own_evaluated(self):
        stale_job = self._seed_stale("q_stale_1")
        # created_at 秒级截断（schema.utc_now_iso 契约）：跨过整秒保证
        # 旧 job 在 claim_next 的 (priority, created_at) 排序里严格在前，
        # 排空顺序确定（同秒内按 job_id 决胜属合法行为，不测）。
        time.sleep(1.05)
        self.runner.outputs = [_learner_output(applicable=False),
                               _learner_output(applicable=False)]
        receipt = self.submit(_open_task("q_fresh_1"), "新提交的答案")
        # 旧 job 被排空执行（不再因 job_id 不匹配被跳过）；fixture 输出
        # applicable=False → 弃权解释，合法终态是 ABSTAINED
        self.assertEqual(
            get_journal(SID).state().jobs[stale_job].job.state,
            S.JobState.ABSTAINED)
        # 本次提交的评价也真实跑完
        self.assertTrue(receipt.interpretation_id)
        self.assertEqual(len(self.runner.calls), 2)

    def test_runner_crash_marks_job_terminal_not_stranded(self):
        class _CrashRunner:
            async def run_structured(self, **kw):
                raise RuntimeError("transport 未分类的意外崩溃")

        am.register_task_snapshot(SID, _open_task("q_crash_1"))
        receipt = asyncio.run(am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id="q_crash_1",
                                       question_revision=1),
            student_answer="任何答案", run_inline=True, runner=_CrashRunner()))
        # 提交受理成功（不向调用方抛 500），评价进入可重试终态
        rt = get_journal(SID).state().jobs.get(receipt.job_id)
        self.assertIn(rt.job.state, (S.JobState.RETRY_WAIT,
                                     S.JobState.FAILED))
        self.assertIn("runner_crashed", rt.job.error_code)

    def test_commit_crash_fails_job_not_request(self):
        """commit 内部意外异常（如 ConceptJudgment 的 pydantic 校验）→
        job 落可重试终态，请求方拿到正常回执而不是裸 500。"""
        from app.agents.student_model.evaluation import service as svc
        task = _open_task("q_cc_1")
        am.register_task_snapshot(SID, task)
        self.runner.outputs = [_learner_output()]

        def boom(self, *a, **kw):
            raise RuntimeError(
                "scope_revision: String should have at least 1 character")

        orig = svc.LearnerEvaluationService.commit_result
        svc.LearnerEvaluationService.commit_result = boom
        try:
            receipt = asyncio.run(am.evaluate_submission(
                student_id=SID,
                question_ref=S.QuestionRef(question_id="q_cc_1",
                                           question_revision=1),
                student_answer="答案", run_inline=True, runner=self.runner))
        finally:
            svc.LearnerEvaluationService.commit_result = orig
        rt = get_journal(SID).state().jobs.get(receipt.job_id)
        self.assertIn(rt.job.state, (S.JobState.RETRY_WAIT,
                                     S.JobState.FAILED))
        self.assertIn("commit_crashed", rt.job.error_code)


class TestSpanNormalization(unittest.TestCase):
    """真实 provider 的证据 span 坐标归一化（live 验收发现的契约缺口）。"""

    TEXT = "J = 1/3。理由：代换后行列式化简为常数。"

    def _claim(self, spans: list[tuple[int, int, str]],
               local_id: str = "oc1") -> S.ObservationClaim:
        return S.ObservationClaim(
            local_id=local_id, concept_ref="c:tb_1.c1",
            statement="能在代换题中写出雅可比", stance=S.ClaimStance.SUPPORTS,
            opportunity_ref="op1",
            current_evidence=[
                S.EvidenceSpan(ref="src_1", start=a, end=b, quote=q)
                for a, b, q in spans])

    def _interp(self, *claims: S.ObservationClaim) -> S.LearnerInterpretation:
        return S.LearnerInterpretation(applicable=True, observation_claims=
                                       list(claims), feedback="反馈")

    def test_zero_offsets_repaired_by_unique_quote(self):
        out = am.normalize_evidence_spans(
            self._interp(self._claim([(0, 0, "J = 1/3")])), self.TEXT)
        span = out.observation_claims[0].current_evidence[0]
        self.assertEqual((span.start, span.end), (0, 7))
        self.assertEqual(self.TEXT[span.start:span.end], span.quote)

    def test_mid_text_quote_gets_real_offsets(self):
        out = am.normalize_evidence_spans(
            self._interp(self._claim([(0, 0, "行列式化简为常数")])), self.TEXT)
        span = out.observation_claims[0].current_evidence[0]
        self.assertEqual(self.TEXT[span.start:span.end], "行列式化简为常数")

    def test_unknown_quote_kept_for_hard_rejection(self):
        """quote 定位不到（疑似伪造）→ 原样保留，交给硬校验拒绝整份。"""
        out = am.normalize_evidence_spans(
            self._interp(self._claim([(0, 0, "原文里没有这句")])), self.TEXT)
        span = out.observation_claims[0].current_evidence[0]
        self.assertEqual((span.start, span.end), (0, 0))

    def test_ambiguous_quote_kept_for_hard_rejection(self):
        """多处出现（坐标不可定）→ 同样保留原样，不做静默取舍。"""
        text = "对 对 对"
        out = am.normalize_evidence_spans(
            self._interp(self._claim([(0, 0, "对")])), text)
        span = out.observation_claims[0].current_evidence[0]
        self.assertEqual((span.start, span.end), (0, 0))

    def test_valid_span_untouched_and_input_immutable(self):
        claim = self._claim([(0, 7, "J = 1/3")])
        interp = self._interp(claim)
        out = am.normalize_evidence_spans(interp, self.TEXT)
        self.assertIs(out, interp)          # 无变化时原样返回
        self.assertEqual(out.observation_claims[0].current_evidence[0].start, 0)


if __name__ == "__main__":
    unittest.main()
