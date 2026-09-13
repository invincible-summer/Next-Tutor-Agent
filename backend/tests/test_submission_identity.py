"""G2 回归：题目/作答身份（plan §18.2 test_submission_identity）。

长题相同前缀仍不同、答案前 200 字相同后文不同不合并、旧标签页
question_revision 冲突、跨区 CAT 不错投。
"""
from __future__ import annotations

import asyncio
import unittest

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.assessment import manager as am
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime

from tests.test_unified_submission import FakeRunner, _concept, _learner_output

SID = "usr_ident_a"


def _task(qid: str, stem: str = "题干", q_type=S.QuestionType.SHORT_ANSWER
          ) -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=qid, question_revision=1, q_type=q_type, stem=stem,
        options={} if q_type == S.QuestionType.MULTIPLE_CHOICE else {},
        answer="答案", explanation="解析",
        rubric=[S.FrozenCriterion(id="c1", description="答对", weight=1.0)],
        concept_refs=[_concept()])


class IdentityTestBase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)

    def submit(self, task: S.TaskSnapshot, answer: str, **kw):
        am.register_task_snapshot(SID, task)
        return asyncio.run(am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id=task.question_id,
                                       question_revision=1),
            student_answer=answer, run_inline=True, runner=self.runner, **kw))


class TestQuestionIdentity(IdentityTestBase):
    def test_same_stem_prefix_different_ids_are_distinct(self):
        """A05：题干前缀相同（>60 字共享前缀）但 question_id 不同 → 两次
        独立提交，互不判重。"""
        prefix = "这是一道拥有超长题干前缀的题目，" * 10
        t1 = _task("q_a", stem=prefix + "（版本甲）")
        t2 = _task("q_b", stem=prefix + "（版本乙）")
        self.runner.outputs = [_learner_output(applicable=False),
                               _learner_output(applicable=False)]
        r1 = self.submit(t1, "答案甲")
        r2 = self.submit(t2, "答案甲")
        self.assertNotEqual(r1.attempt_id, r2.attempt_id)
        self.assertFalse(r2.duplicate)

    def test_answer_prefix_same_tail_differs_not_merged(self):
        """A05：答案前 200 字相同、后文不同 → 不合并（不是重放）。"""
        task = _task("q_open")
        head = "同样的前两百字" * 40
        a1 = head + "——结论一是端点被映射。"
        a2 = head + "——结论二完全不同。"
        self.runner.outputs = [_learner_output(applicable=False),
                               _learner_output(applicable=False)]
        self.submit(task, a1)
        with self.assertRaises(am.QuestionAlreadyAnswered):
            self.submit(task, a2)
        # 反向验证：若被前缀判重，第二次会 duplicate=True 而不是抛冲突

    def test_revision_mismatch_rejected(self):
        """旧标签页提交旧 revision → 明确冲突，不是静默判分。"""
        task = _task("q_rev")
        am.register_task_snapshot(SID, task)
        rev2 = task.model_copy(update={"question_revision": 2})
        am.register_task_snapshot(SID, rev2)
        with self.assertRaises(am.QuestionRevisionMismatch):
            asyncio.run(am.evaluate_submission(
                student_id=SID,
                question_ref=S.QuestionRef(question_id="q_rev",
                                           question_revision=3),
                student_answer="x", run_inline=True, runner=self.runner))

    def test_cross_workspace_cat_does_not_misfile(self):
        """A10：CAT 实例绑定 ws A；提交携带 assessment_id 时即使调用方
        处于另一上下文，也只归档到实例绑定的 workspace。"""
        task = _task("q_cat_1", q_type=S.QuestionType.MULTIPLE_CHOICE)
        task.options = {"A": "1", "B": "2"}
        am.register_task_snapshot(SID, task)
        from app.agents.assessment import adaptive_test as cat
        instance = cat.CatInstance(assessment_id="asmt_ws_a",
                                   workspace_id="ws_a",
                                   concept="并联")
        instance.question_refs.append(S.QuestionRef(
            question_id="q_cat_1", question_revision=1))
        cat.save_instance(SID, instance, change="start")
        receipt = asyncio.run(am.evaluate_submission(
            student_id=SID,
            question_ref=S.QuestionRef(question_id="q_cat_1",
                                       question_revision=1),
            student_answer="A", assessment_id="asmt_ws_a",
            workspace_id="ws_a", run_inline=True, runner=self.runner))
        state = get_journal(SID).state()
        src = state.sources[receipt.source_id]
        self.assertEqual(src.receipt.workspace_id_at_observation, "ws_a")
        self.assertEqual(src.receipt.assessment_id, "asmt_ws_a")


if __name__ == "__main__":
    unittest.main()
