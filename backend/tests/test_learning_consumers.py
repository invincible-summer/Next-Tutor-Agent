"""G4 回归：journal outbox 消费者（plan §6.6 / §18.2 test_learning_consumers）。

- M9 以 consumer="m9" 幂等消费 task_result：SRS quality + consumer_ack 落盘，
  重复消费不重复增长（(event_id, consumer) 键 + M9 侧 attempt 去重双层）。
- verdict=null（未判定）不构成可观察召回：不消费 SM-2，只允许接触记录。
- 复核/撤销事件（review_resolved / interpretation_revoked）重放受影响复习卡。
- accepted attempt 只完成绑定任务（做完≠学会）。
"""
from __future__ import annotations

import time
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_unified_submission import (FakeRunner, _learner_output,
                                           _mc_task)

from app.agents.assessment import manager as am
from app.agents.learning_orchestration import store as orch_store
from app.agents.learning_orchestration.manager import get_orchestration_service
from app.agents.learning_orchestration.schema import (DailyTask,
                                                     DailyTaskStatus, TaskKind)
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime

SID = "usr_consumer_a"


class ConsumerTestBase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)

    def submit_mc(self, answer: str, **kw: Any):
        if not self.runner.outputs:
            self.runner.outputs = [_learner_output()]
        task = _mc_task()
        am.register_task_snapshot(SID, task)
        return am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id=task.question_id,
                                       question_revision=1),
            student_answer=answer, run_inline=True, runner=self.runner, **kw)


class TestTaskResultConsumption(ConsumerTestBase):
    def test_consumed_once_then_acked_and_idempotent(self):
        import asyncio
        receipt = asyncio.run(self.submit_mc("A"))
        state = get_journal(SID).state()
        event_id = f"m9_{receipt.source_id}"
        self.assertIn((event_id, "m9"), state.outbox_unacked)

        svc = get_orchestration_service()
        acked = svc.consume_evaluation_outbox(SID)
        self.assertEqual(acked, 1)
        # consumer_ack 落盘：重放后不再有待领取项
        self.assertNotIn((event_id, "m9"),
                         get_journal(SID).state().outbox_unacked)
        # SRS 卡已建立且按 correct 判定调度
        summary = svc.summary(SID)
        cards = summary["review_queue"]
        self.assertEqual(len(cards), 1)
        card = next(iter(cards.values()))
        self.assertEqual(card["last_quality"], 5)   # correct -> quality 5

        # 重复消费：无事件可领，卡间隔不再增长
        self.assertEqual(get_journal(SID).state().outbox_unacked, {})
        self.assertEqual(svc.consume_evaluation_outbox(SID), 0)

    def test_verdict_null_is_not_observable_recall(self):
        """verdict 为空（未判定/空答）不进 SM-2：只允许接触记录。"""
        svc = get_orchestration_service()
        # 未判定事件不产生（evaluate_submission 只在 verdict 非 null 时
        # 投递）；直接验证消费端对空判定的守门。
        ok = svc.record_quiz_evidence(
            student_id=SID, concept="phys.c1", verdict="unknown",
            attempt_id="att_unknown_1")
        self.assertFalse(ok)
        card = svc.summary(SID)["review_queue"].get("phys.c1")
        # 接触记录可以建卡，但 last_quality 必须为 None（无召回观察）
        self.assertIsNotNone(card)
        self.assertIsNone(card["last_quality"])

    def test_only_bound_task_completes(self):
        import asyncio
        receipt = asyncio.run(self.submit_mc("A"))
        event_id = f"m9_{receipt.source_id}"
        state = orch_store.load_state(SID)
        day = time.strftime("%Y-%m-%d")
        state.daily_tasks = [
            DailyTask(id=f"{day}_t1", day=day, concept_id="phys.c1",
                      kind=TaskKind.PRACTICE),
            DailyTask(id=f"{day}_t2", day=day, concept_id="phys.c1",
                      kind=TaskKind.PRACTICE),
        ]
        orch_store.save_state(SID, state)
        # 把绑定信息塞进待投递事件（模拟 launch_task 的 task_binding）
        journal = get_journal(SID)
        jstate = journal.state()
        item = dict(jstate.outbox_unacked[(event_id, "m9")])
        item["task_binding"] = {"task_id": f"{day}_t1",
                                "concept_id": "phys.c1"}
        journal.append([S.OpConsumerAck(event_id="ctl_probe", consumer="m9"),
                        S.OpResultCommitted(
                            job_id="job_ctl_probe", source_id="src_ctl",
                            source_revision=1, scope_revision="no_scope",
                            interpretation_id="itp_ctl",
                            interpretation=None, abstained=True,
                            outbox=[item])])
        # 清掉原事件（同 event_id 由后写覆盖语义保证唯一未 ack 项）
        journal.append([S.OpConsumerAck(event_id=event_id, consumer="m9")])
        journal.append([S.OpResultCommitted(
            job_id="job_ctl_probe2", source_id="src_ctl2",
            source_revision=1, scope_revision="no_scope",
            interpretation_id="itp_ctl2", interpretation=None,
            abstained=True,
            outbox=[{"event_id": event_id, "consumer": "m9",
                     "kind": "task_result", "source_id": "src_ctl2",
                     "question_id": "q_mc_1", "concept": "并联关系",
                     "verdict": "correct",
                     "task_binding": {"task_id": f"{day}_t1",
                                      "concept_id": "phys.c1"}}])])

        svc = get_orchestration_service()
        acked = svc.consume_evaluation_outbox(SID)
        self.assertEqual(acked, 1)
        after = orch_store.load_state(SID)
        by_id = {t.id: t for t in after.daily_tasks}
        self.assertEqual(by_id[f"{day}_t1"].status, DailyTaskStatus.COMPLETED)
        self.assertEqual(by_id[f"{day}_t1"].completion_source, "quiz_evidence")
        # 同概念的另一任务不被连带完成（A12：只完成绑定任务）
        self.assertEqual(by_id[f"{day}_t2"].status, DailyTaskStatus.PENDING)


class TestReviewRebuild(ConsumerTestBase):
    def test_interpretation_revoked_replays_card_schedule(self):
        svc = get_orchestration_service()
        # 先长出一张复习卡（两次正确 → 间隔增长）
        svc.record_quiz_evidence(student_id=SID, concept="phys.c1",
                                 verdict="correct", attempt_id="att_r1")
        svc.record_quiz_evidence(student_id=SID, concept="phys.c1",
                                 verdict="correct", attempt_id="att_r2")
        card = svc.summary(SID)["review_queue"]["phys.c1"]
        self.assertGreaterEqual(card["interval"], 1)

        # 复核撤销（lifecycle 同款投递）：受影响概念的复习卡日期状态重放
        get_journal(SID).append([S.OpInterpretationRevoked(
            interpretation_id="itp_probe", source_id="src_probe",
            reason="review_invalidated:test",
            affected_judgment_ids=[],
            outbox=[{"event_id": "m9_revoke_probe", "consumer": "m9",
                     "kind": "interpretation_revoked",
                     "concept_keys": ["phys.c1"]}])])
        acked = svc.consume_evaluation_outbox(SID)
        self.assertEqual(acked, 1)
        rebuilt = svc.summary(SID)["review_queue"]["phys.c1"]
        self.assertEqual(rebuilt["repetitions"], 0)   # SM-2 调度重置
        self.assertEqual(rebuilt["next_review"],
                         rebuilt["created_at"] + 86400)  # 明日复查
        # 事件已 ack，不重复重放
        self.assertEqual(svc.consume_evaluation_outbox(SID), 0)

    def test_unknown_kind_is_acked_without_side_effect(self):
        get_journal(SID).append([S.OpResultCommitted(
            job_id="job_unk", source_id="src_unk", source_revision=1,
            scope_revision="no_scope", interpretation_id="itp_unk",
            interpretation=None, abstained=True,
            outbox=[{"event_id": "m9_future_kind", "consumer": "m9",
                     "kind": "future_kind"}])])
        svc = get_orchestration_service()
        self.assertEqual(svc.consume_evaluation_outbox(SID), 1)
        self.assertEqual(svc.consume_evaluation_outbox(SID), 0)


if __name__ == "__main__":
    unittest.main()
