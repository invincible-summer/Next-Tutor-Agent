"""课堂随堂题回归（plan.md §13.2/§13.3、§19.2 test_classroom_assessment）。

覆盖：题目冻结先于交付（稳定 ID + 未揭晓无答案）；同题同答案幂等 /
不同答案冲突；hint/reveal 先持久化帮助；已揭晓题重听关联原题族并重放
帮助（不是独立新证据）；播放行为零学习证据；跳过不算答错。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.agents.assessment import manager as assessment  # noqa: E402
from app.agents.student_model.evaluation import schema as S  # noqa: E402
from app.agents.student_model.evaluation.store import get_journal  # noqa: E402
from app.classroom import assessment_bridge as bridge  # noqa: E402
from app.classroom import runs as runs_mod  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, WS  # noqa: E402
from tests.test_classroom_revisions import RevisionTestBase  # noqa: E402

TEMPLATE = {
    "id": "q-template-1",
    "type": "multiple_choice",
    "stem": "两个小车在光滑水平面上碰撞，系统动量是否守恒？",
    "options": {"A": "守恒", "B": "不守恒"},
    "answer": "A",
    "explanation": "光滑水平面无摩擦力，合外力为零，动量守恒。",
    "knowledge_point": "动量守恒",
    "verification": {"status": "passed"},
}


class AssessmentBridgeTests(RevisionTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for p in [
            mock.patch.object(settings, "classroom_enabled", True),
            mock.patch.object(settings, "azure_speech_key", "k"),
            mock.patch.object(settings, "azure_speech_region", "eastasia"),
            mock.patch.object(settings, "classroom_tts_voice_zh",
                              "zh-CN-XiaoxiaoNeural"),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.lesson_id, _job, base = self._published_course()
        self.spec = self._inject_question_template(base)
        self.created = runs_mod.create_run(
            OWNER, WS, self.lesson_id, sc.CreateRunRequest(),
            idempotency_key="k-assess-0000000001")
        self.run_id = self.created["run_id"]

    def _inject_question_template(self, base: sc.LessonRevision
                                  ) -> sc.LessonRevision:
        """测试注入 question 模板：覆写 spec.private.json（读路径无 hash
        校验；生产不可变性由发布事务保证）。"""
        checkpoint_slide = max(base.slides, key=lambda s: s.order)
        cp_id = "ckp_" + "e" * 24
        template = sc.CheckpointTemplate(
            checkpoint_id=cp_id, slide_id=checkpoint_slide.slide_id,
            kind=sc.CheckpointKind.question,
            prompt=TEMPLATE["stem"][:500],
            verified_question_template=dict(TEMPLATE),
            optional=True)
        base.checkpoint_templates = [template]
        checkpoint_slide.blocks.append(sc.CheckpointBlock(
            id="blk_" + "f" * 24, checkpoint_id=cp_id))
        spec_path = store.revision_spec_path(OWNER, WS, self.lesson_id,
                                             base.revision)
        spec_path.write_text(base.model_dump_json(indent=2),
                             encoding="utf-8")
        self.checkpoint_id = cp_id
        return base

    def _run(self) -> sc.ClassroomRun:
        return store.load_run(OWNER, WS, self.lesson_id, self.run_id)

    def _qref_str(self) -> str:
        run = self._run()
        ref = next(r for r in run.checkpoint_refs
                   if r.checkpoint_id == self.checkpoint_id)
        return ref.question_ref or ""

    def _submit(self, answer: str, key: str, run=None):
        import asyncio
        return asyncio.run(bridge.submit_checkpoint(
            OWNER, WS, self.lesson_id, run or self._run(),
            self.checkpoint_id,
            sc.CheckpointSubmitRequest(
                question_ref=self._qref_str(), student_answer=answer,
                idempotency_key=key)))

    def _ensure(self) -> sc.ClassroomRun:
        return bridge.ensure_run_questions(OWNER, WS, self.lesson_id,
                                           self.run_id)

    # ---- 冻结先于交付 ------------------------------------------------------

    def test_freeze_before_delivery_and_stable_id(self):
        run = self._ensure()
        ref = next(r for r in run.checkpoint_refs
                   if r.checkpoint_id == self.checkpoint_id)
        self.assertIsNotNone(ref.question_ref)
        thash = bridge.template_hash_of(self.spec.checkpoint_templates[0])
        expected = bridge.checkpoint_question_id(OWNER, self.run_id,
                                                 self.checkpoint_id, thash)
        self.assertEqual(ref.question_ref, f"{expected}:1")
        # journal 已注册冻结任务；公开投影无答案（未提交/未揭晓）
        task = assessment.load_task_snapshot(OWNER, bridge.question_ref_of(
            ref.question_ref))
        self.assertEqual(task.answer, "A")
        public = bridge.checkpoint_public(OWNER, WS, self.lesson_id, run,
                                          self.checkpoint_id)
        self.assertIsNotNone(public.question)
        self.assertNotIn("answer", public.question)
        self.assertNotIn("explanation", public.question)
        self.assertNotIn("rubric", public.question)
        # 幂等：再次 ensure 不改身份、不重复注册
        again = self._ensure()
        ref2 = next(r for r in again.checkpoint_refs
                    if r.checkpoint_id == self.checkpoint_id)
        self.assertEqual(ref.question_ref, ref2.question_ref)

    def test_crash_between_plan_and_register_recovers(self):
        # 模拟「planned IDs 已落盘、注册前崩溃」：清 journal 后重入
        run = self._ensure()
        ref = next(r for r in run.checkpoint_refs
                   if r.checkpoint_id == self.checkpoint_id)
        journal_path = get_journal(OWNER).path
        journal_path.unlink(missing_ok=True)
        recovered = self._ensure()
        ref2 = next(r for r in recovered.checkpoint_refs
                    if r.checkpoint_id == self.checkpoint_id)
        self.assertEqual(ref.question_ref, ref2.question_ref)
        task = assessment.load_task_snapshot(OWNER,
                                             bridge.question_ref_of(ref2.question_ref))
        self.assertIsNotNone(task)

    # ---- 受理与帮助 ---------------------------------------------------------

    def test_submit_idempotent_and_conflict(self):
        run = self._ensure()
        first = self._submit("A", "k-submit-00000000001", run=run)
        self.assertTrue(first["attempt_id"])
        second = self._submit("A", "k-submit-00000000001")
        self.assertTrue(second["duplicate"])
        # 同题不同答案 → 冲突（现有 409 语义）
        with self.assertRaises(Exception) as ctx:
            self._submit("B", "k-submit-00000000002")
        self.assertIn("AlreadyAnswered", type(ctx.exception).__name__)

    def test_hint_and_reveal_record_assistance_first(self):
        run = self._ensure()
        ref = next(r for r in run.checkpoint_refs
                   if r.checkpoint_id == self.checkpoint_id)
        qref = bridge.question_ref_of(ref.question_ref)
        out = bridge.hint_checkpoint(OWNER, WS, self.lesson_id, run,
                                     self.checkpoint_id)
        self.assertTrue(out["hint"])
        kinds = [e.kind for e in assessment.assistance_events(OWNER, qref)]
        self.assertIn(S.AssistanceEventKind.HINT_REQUESTED, kinds)
        # 揭晓：先记 answer_revealed，再返回答案
        revealed = bridge.reveal_checkpoint(OWNER, WS, self.lesson_id,
                                            self._run(), self.checkpoint_id)
        self.assertEqual(revealed["answer"], "A")
        kinds = [e.kind for e in assessment.assistance_events(OWNER, qref)]
        self.assertIn(S.AssistanceEventKind.ANSWER_REVEALED, kinds)

    def test_skip_marks_without_evidence(self):
        run = self._ensure()
        bridge.skip_checkpoint(OWNER, WS, self.lesson_id, run,
                               self.checkpoint_id, None)
        ref = next(r for r in self._run().checkpoint_refs
                   if r.checkpoint_id == self.checkpoint_id)
        self.assertEqual(ref.state, sc.CheckpointRunState.skipped)
        # 跳过后提交被拒（不伪称作答）
        with self.assertRaises(ClassroomError):
            self._submit("A", "k-skip-000000000001")
        # 已作答不回退为跳过
        run2 = runs_mod.create_run(OWNER, WS, self.lesson_id,
                                   sc.CreateRunRequest(mode="restart"),
                                   idempotency_key="k-skip-000000000002")
        run_id2 = run2["run_id"]
        bridge.ensure_run_questions(OWNER, WS, self.lesson_id, run_id2)
        import asyncio
        run2_loaded = store.load_run(OWNER, WS, self.lesson_id, run_id2)
        ref2_str = next(r.question_ref for r in run2_loaded.checkpoint_refs
                        if r.checkpoint_id == self.checkpoint_id)
        asyncio.run(bridge.submit_checkpoint(
            OWNER, WS, self.lesson_id, run2_loaded, self.checkpoint_id,
            sc.CheckpointSubmitRequest(
                question_ref=ref2_str, student_answer="A",
                idempotency_key="k-skip-000000000003")))
        bridge.skip_checkpoint(OWNER, WS, self.lesson_id,
                               store.load_run(OWNER, WS, self.lesson_id,
                                              run_id2),
                               self.checkpoint_id, None)
        ref2 = next(r for r in store.load_run(
            OWNER, WS, self.lesson_id, run_id2).checkpoint_refs
            if r.checkpoint_id == self.checkpoint_id)
        self.assertEqual(ref2.state, sc.CheckpointRunState.answered)

    def test_replay_prior_assistance_on_restart(self):
        run1 = self._ensure()
        bridge.reveal_checkpoint(OWNER, WS, self.lesson_id, run1,
                                 self.checkpoint_id)
        # 重新上一遍：新 run 复用模板 → origin 关联 + 帮助重放
        run2 = runs_mod.create_run(OWNER, WS, self.lesson_id,
                                   sc.CreateRunRequest(mode="restart"),
                                   idempotency_key="k-re-0000000000001")
        run_id2 = run2["run_id"]
        bridge.ensure_run_questions(OWNER, WS, self.lesson_id, run_id2)
        ref2 = next(r for r in store.load_run(
            OWNER, WS, self.lesson_id, run_id2).checkpoint_refs
            if r.checkpoint_id == self.checkpoint_id)
        qref2 = bridge.question_ref_of(ref2.question_ref)
        kinds = [e.kind for e in assessment.assistance_events(OWNER, qref2)]
        self.assertIn(S.AssistanceEventKind.ANSWER_REVEALED, kinds)
        task2 = assessment.load_task_snapshot(OWNER, qref2)
        self.assertIsNotNone(task2.origin_question_ref)

    def test_playback_writes_zero_learning_evidence(self):
        # 仅有播放行为（run 创建 + 进度推进）不产生任何 assessment 观察
        run = self._ensure()
        lease = runs_mod.acquire_lease(
            OWNER, WS, self.lesson_id, self.run_id,
            sc.LeaseAcquireRequest(client_id="client-assess-01"))
        last = max(self.spec.slides, key=lambda s: s.order)
        runs_mod.update_progress(
            OWNER, WS, self.lesson_id, self.run_id,
            sc.ProgressRequest(
                expected_state_revision=run.state_revision,
                client_event_id="evt-assess-00000001", client_seq=1,
                lease_epoch=lease.lease_epoch,
                action=sc.ProgressAction.complete,
                cursor=sc.Cursor(slide_id=last.slide_id,
                                 segment_id=last.segments[-1].segment_id)))
        state = get_journal(OWNER).state()
        classroom_sources = [sid for sid, src in state.sources.items()
                             if src.receipt.source_surface == "classroom"]
        self.assertEqual(classroom_sources, [])


if __name__ == "__main__":
    unittest.main()
