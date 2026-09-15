"""R01/R02（update_plan §4）：评价作业后台 worker 闭环回归。

R01 验收：真实 run_turn 完成后不调用任何测试辅助 drain、不发 HTTP 请求，
后台 worker 自动完成 P4 调用、journal 结果与概念投影；重启（缓存重建）
后 queued/retry_wait/过期 lease 恢复执行。

R02 验收：多类型作业同时排队（dialogue/assessment）、超过六项积压，
全部由 worker 归位到正确执行器，来源不重复受理。
"""
from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import dialogue as dlg
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal, \
    reset_journal_cache
from app.core import learner_runtime
from app.core.session import TutorSession, save_session
from app.core.workspace import Workspace, save_workspace

SID = "usr_worker_a"
WS = "ws_worker"
MSG = "我明白了：判断两个电阻是否并联，要看它们是否共用同一对端点。"


class _ChatLLM:
    """稳定文本回答（无 tool call）：run_turn 走 direct answer 路径。"""

    async def stream(self, messages, tools=None, temperature=None,
                     max_tokens=None, **kw):
        yield {"kind": "answer", "delta": "对，端点判断是关键。"}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"total_tokens": 10}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False, **kw):
        return "对，端点判断是关键。", {"total_tokens": 10}


class _EvalRunner:
    """按 output_model 动态生成合法输出的结构化评价 runner。

    worker 闭环里除来源解释外还会有概念重综合（concept_dirty）等下游
    作业；固定脚本无法预知调用序列，故按请求的输出模型即席构造。
    """

    def __init__(self, outputs: list[Any] | None = None):
        self.outputs = list(outputs or [])
        self.calls: list[tuple[str, str]] = []
        self.models: list[Any] = []
        self.dialogue_calls = 0

    async def run_structured(self, *, system: str, user: str,
                             output_model, max_output_tokens: int = 4000,
                             deadline_at=None):
        from app.agents.student_model.evaluation.llm import StructuredOutput
        self.calls.append((system, user))
        self.models.append(output_model)
        if self.outputs:
            item = self.outputs.pop(0)
        else:
            item = _default_output_for(output_model,
                                       repeat=(self.dialogue_calls > 0))
        if output_model.__name__ == "LearnerInterpretation" and                 "对话学习证据解释者" in system:
            self.dialogue_calls += 1
        if isinstance(item, str):
            return StructuredOutput(raw="", error_code=item,
                                    retryable_error=False)
        return StructuredOutput(parsed=item, raw="{}", finish_reason="stop")


def _concept() -> S.ConceptRef:
    return S.ConceptRef(graph_owner_namespace="public", textbook_id="tb_w",
                        file_ids=["f1"], concept_id="phys.parallel",
                        concept_revision="cr_1", display_name="并联")


class _FixedScopeResolver:
    """注入固定 scope（含 1 个可评价概念），绕开教材/图谱装配。"""

    def __init__(self, concepts: list[S.ConceptRef]):
        self.concepts = concepts

    def resolve(self, student_id: str, workspace_id: str) -> S.EvaluationScope:
        if workspace_id != WS:
            from app.agents.student_model.evaluation.scope import ScopeNotFound
            raise ScopeNotFound(workspace_id)
        return S.EvaluationScope(
            workspace_id=workspace_id, scope_revision="sr_worker_1",
            selected_volumes=[S.VolumeSelection(
                textbook_id="tb_w", graph_owner_namespace="public",
                topic_key="topic_w", file_ids=["f1"],
                graph_revision="gr_1")],
            allowed_concepts=self.concepts,
            graph_revisions=[S.GraphRevisionInfo(
                graph_owner_namespace="public", textbook_id="tb_w",
                graph_revision="gr_1")],
            unresolved_graph_count=0)

    def invalidate(self, student_id: str = "", workspace_id: str = "") -> None:
        pass


def _dialogue_output(text: str) -> S.LearnerInterpretation:
    q = text[:6]
    claim = S.ObservationClaim(
        local_id="ob1", concept_ref="c1",
        statement="能在对话中用公共端点判断并联",
        stance=S.ClaimStance.SUPPORTS,
        current_evidence=[S.EvidenceSpan(ref="s1", start=0, end=len(q),
                                         quote=q)],
        opportunity_ref="dialogue_turn",
        warrant="学生主动陈述判断依据")
    update = S.ConceptUpdate(
        concept_ref="c1", proposed_state=S.ConceptEvalState.SUPPORTED_IN_SCOPE,
        add_claim_local_ids=["ob1"],
        statement="能在对话中用公共端点判断并联")
    return S.LearnerInterpretation(
        applicable=True, observation_claims=[claim],
        concept_updates=[update], feedback="掌握判断依据的表达")


def _default_output_for(output_model: Any, repeat: bool = False) -> Any:
    """按输出模型给出最小合法实例（worker 下游作业需要）。

    repeat=True：同概念已有 active 判断——不再提交 concept_updates
    （旧主张需 retain/revise/close 完整覆盖，裸 add 会被硬拒绝）。
    """
    import app.agents.student_model.evaluation.schema as SM
    if output_model is SM.LearnerInterpretation:
        out = _dialogue_output(MSG)
        if repeat:
            out = out.model_copy(update={"concept_updates": []})
        return out
    if output_model is SM.ScopeSynthesisOutput:
        return SM.ScopeSynthesisOutput(
            scope_type=SM.ScopeType.CONCEPT, statement="综合：并联判断已支持")
    if output_model is SM.AssessmentInterpretationOutput:
        # 作答文本是 "A"：引文按学生原文坐标；观察仅记录，不动概念状态
        claim = SM.ObservationClaim(
            local_id="ob1", concept_ref="c1",
            statement="在选择题中选中并联端点判断",
            stance=SM.ClaimStance.SUPPORTS,
            current_evidence=[SM.EvidenceSpan(ref="s1", start=0, end=1,
                                              quote="A")],
            opportunity_ref="q_mix_1",
            warrant="MC 受理判分 correct")
        learner = SM.LearnerInterpretation(
            applicable=True, observation_claims=[claim],
            concept_updates=[], feedback="本题反馈")
        return SM.AssessmentInterpretationOutput(
            criterion_results=[], learner=learner)
    if getattr(output_model, "__name__", "") == "ReviewDecisionOutput":
        return output_model(decision="uphold", reason="维持原判断")
    if getattr(output_model, "__name__", "") == "TeachingDesignReview":
        return output_model(items=[])
    raise AssertionError(f"unexpected output model {output_model}")


class WorkerFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        save_workspace(Workspace(workspace_id=WS, name="物理",
                                 student_id=SID))
        self.session = TutorSession(session_id="sess_worker", grade="高中",
                                    student_id=SID, workspace_id=WS)
        save_session(self.session)
        self.runner = _EvalRunner()
        # runner 队列按需补足（drain 期间每 job 弹出一个）
        learner_runtime.set_evaluation_runner(self.runner)
        from app.agents.student_model.evaluation.scope import \
            set_scope_resolver
        set_scope_resolver(_FixedScopeResolver([_concept()]))

    def tearDown(self) -> None:
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def feed(self, n: int) -> None:
        for _ in range(n):
            self.runner.outputs.append(_dialogue_output(MSG))

    async def drain(self, timeout: float = 15.0) -> None:
        """轮询等待后台 worker 把队列清空（只观察，不代为执行）。"""
        import time
        from app.agents.student_model.evaluation import store as store_mod
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = 0
            for path in store_mod.STUDENTS_DIR.glob(
                    f"*{store_mod.JOURNAL_SUFFIX}"):
                sid = path.name[: -len(store_mod.JOURNAL_SUFFIX)]
                for rt in get_journal(sid).state().jobs.values():
                    if rt.job.state in (S.JobState.QUEUED,
                                        S.JobState.RUNNING,
                                        S.JobState.RETRY_WAIT):
                        pending += 1
            if pending == 0:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("worker 未在时限内清空队列")


class TestWorkerClosedLoop(WorkerFixture):
    """R01：真实对话轮次 → 后台自动评价 → 概念投影。"""

    def test_real_run_turn_evaluates_without_drain_or_http(self):
        from app.agents.chat_agent import run_turn
        from app.agents.student_model.evaluation.worker import (
            EvaluationWorker, set_evaluation_worker)

        async def scenario() -> None:
            worker = EvaluationWorker(runner_provider=lambda: self.runner)
            set_evaluation_worker(worker)
            worker.start()
            try:
                events = []
                with unittest.mock.patch.dict(
                        os.environ, {"SUPERVISOR_MODE": "legacy"}):
                    async for ev in run_turn(
                            MSG, self.session, [], llm=_ChatLLM(),
                            student_id=SID):
                        events.append(ev)
                # run_turn 正常完成（不因评价 hook 失败）
                self.assertTrue(any(e.get("type") == "done"
                                    for e in events), events[:8])
                # 关键验收：不调用任何 drain 辅助，等待后台完成
                await self.drain()
            finally:
                await worker.stop()

        asyncio.run(scenario())
        # P4 真实发生：首个调用即对话解释（后续可为概念重综合等下游）
        self.assertGreaterEqual(len(self.runner.calls), 1)
        system, user = self.runner.calls[0]
        # P4 对话解释角色合同（渲染后的提示词文本）
        self.assertIn("对话学习证据解释者", system)
        self.assertIn(MSG[:6], user)
        state = get_journal(SID).state()
        dlg_srcs = [s for s in state.sources.values()
                    if s.receipt.kind == S.SourceKind.DIALOGUE]
        self.assertEqual(len(dlg_srcs), 1)
        self.assertTrue(dlg_srcs[0].current_interpretation_id)
        self.assertEqual(len(state.judgments), 1)
        judgment = next(iter(state.judgments.values()))
        self.assertEqual(judgment.state,
                         S.ConceptEvalState.SUPPORTED_IN_SCOPE)
        # 来源不重复：一条消息一个来源
        self.assertEqual(len([s for s in state.sources.values()
                              if s.receipt.kind == S.SourceKind.DIALOGUE]), 1)

    def test_restart_recovers_queued_jobs(self):
        """受理后进程"重启"（缓存丢弃、worker 重建）→ 恢复执行。"""
        from app.agents.student_model.evaluation.worker import (
            EvaluationWorker, set_evaluation_worker)
        src = dlg.register_dialogue_source(
            student_id=SID, session=self.session,
            message={"role": "user", "content": MSG, "message_id": "m_r1"})
        self.assertIsNotNone(src)
        # 模拟重启：清进程缓存（journal 从盘重放、worker 重建）
        reset_journal_cache()
        learner_runtime._SCHEDULER = None
        set_evaluation_worker(None)

        async def scenario() -> None:
            worker = EvaluationWorker(runner_provider=lambda: self.runner)
            set_evaluation_worker(worker)
            worker.start()
            try:
                await self.drain()
            finally:
                await worker.stop()

        asyncio.run(scenario())
        state = get_journal(SID).state()
        dlg_srcs = [s for s in state.sources.values()
                    if s.receipt.kind == S.SourceKind.DIALOGUE]
        self.assertEqual(len(dlg_srcs), 1)
        self.assertTrue(dlg_srcs[0].current_interpretation_id)
        self.assertEqual(len(state.judgments), 1)

    def test_worker_wakes_quickly_on_new_source(self):
        """§6.5：空闲 worker 唤醒目标 ≤1s（wake 事件即时打断退避）。"""
        import time
        from app.agents.student_model.evaluation.worker import (
            EvaluationWorker, set_evaluation_worker)

        async def scenario() -> float:
            worker = EvaluationWorker(runner_provider=lambda: self.runner)
            set_evaluation_worker(worker)
            worker.start()
            try:
                await asyncio.sleep(0.3)   # 进入空闲等待
                t0 = time.monotonic()
                dlg.register_dialogue_source(
                    student_id=SID, session=self.session,
                    message={"role": "user", "content": MSG,
                             "message_id": "m_wake"})
                await self.drain()
                return time.monotonic() - t0
            finally:
                await worker.stop()

        elapsed = asyncio.run(scenario())
        # 唤醒 + 处理完成 < 1s + CI 抖动余量
        self.assertLess(elapsed, 6.0)


class TestWorkerFairnessAndBacklog(WorkerFixture):
    def test_more_than_six_backlog_all_completed(self):
        """R02：超过六项积压（inline 旧上限）全部由 worker 完成。"""
        from app.agents.student_model.evaluation.worker import (
            EvaluationWorker, set_evaluation_worker)
        n = 8
        for i in range(n):
            dlg.register_dialogue_source(
                student_id=SID, session=self.session,
                message={"role": "user", "content": f"{MSG}（第{i}次变式）",
                         "message_id": f"m_b{i}"})
        state = get_journal(SID).state()
        self.assertEqual(len(state.jobs), n)

        async def scenario() -> None:
            worker = EvaluationWorker(runner_provider=lambda: self.runner)
            set_evaluation_worker(worker)
            worker.start()
            try:
                await self.drain(timeout=30.0)
            finally:
                await worker.stop()

        asyncio.run(scenario())
        state = get_journal(SID).state()
        for rt in state.jobs.values():
            self.assertEqual(rt.job.state, S.JobState.SUCCEEDED,
                             rt.job.job_id)
        # 每个来源一份解释，不重复受理
        dlg_srcs = [s for s in state.sources.values()
                    if s.receipt.kind == S.SourceKind.DIALOGUE]
        self.assertEqual(len(dlg_srcs), n)
        self.assertTrue(all(s.current_interpretation_id for s in dlg_srcs))

    def test_mixed_kinds_route_to_right_executors(self):
        """R02：dialogue + assessment 混排，各归正确执行器。"""
        from app.agents.assessment import manager as am
        from app.agents.student_model.evaluation.worker import (
            EvaluationWorker, set_evaluation_worker)
        task = S.TaskSnapshot(
            question_id="q_mix_1", question_revision=1,
            q_type=S.QuestionType.MULTIPLE_CHOICE, stem="哪个是并联？",
            options={"A": "同端点", "B": "上下排列"},
            answer="A", explanation="端点判断",
            rubric=[S.FrozenCriterion(id="c1", description="选对",
                                      weight=1.0)],
            concept_refs=[_concept()])
        am.register_task_snapshot(SID, task)
        receipt = asyncio.run(am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id="q_mix_1",
                                       question_revision=1),
            student_answer="A", source_surface="center"))
        # 2 个 dialogue 来源与 assessment 混排
        for i in range(2):
            sid = dlg.register_dialogue_source(
                student_id=SID, session=self.session,
                message={"role": "user", "content": MSG,
                         "message_id": f"m_mix{i}"})
            self.assertIsNotNone(sid)

        async def scenario() -> None:
            worker = EvaluationWorker(runner_provider=lambda: self.runner)
            set_evaluation_worker(worker)
            worker.start()
            try:
                await self.drain()
            finally:
                await worker.stop()

        asyncio.run(scenario())
        state = get_journal(SID).state()
        kinds: dict[Any, list[Any]] = {}
        for rt in state.jobs.values():
            kinds.setdefault(rt.job.kind, []).append(rt.job.state)
        # 裸 MC（仅选项、无推理文本）的学习观察被机会门丢弃 → 弃权是
        # 合法终态（R14/R15：MC 判分保留，能力观察不虚增）
        self.assertEqual(len(kinds[S.JobKind.ASSESSMENT_EVALUATION]), 1)
        self.assertIn(kinds[S.JobKind.ASSESSMENT_EVALUATION][0],
                      (S.JobState.SUCCEEDED, S.JobState.ABSTAINED))
        self.assertEqual(kinds[S.JobKind.DIALOGUE_EVALUATION],
                         [S.JobState.SUCCEEDED] * 2)
        # MC TaskResult 已在受理事务落盘且终局保留
        src = state.sources[receipt.source_id]
        task_result = src.interpretations.get("", {}).get("task_result")
        self.assertIsNotNone(task_result)
        self.assertEqual(task_result["verdict"], "correct")
