"""M4 单元回归（统一链版本）：确定性判分 / TaskSnapshot 转换 / CAT 规则。

旧三级文本批改（parse_grade）、derive_concept_status、verdict_for_score
随 raw_grade 旁路删除（A02）；本文件覆盖其替代行为：
- `grade_mc_task`：MC 确定性判分（含大小写/空白）。
- `compute_task_result`：冻结量规加权、indeterminate 贯穿（A04）。
- `task_snapshot_from_quiz_dict` / `task_snapshot_from_legacy`：题目注册
  转换与量规冻结语义。
- `adaptive_test`：难度步进（verdict=null 不参与）/停止规则/continuation。
全部纯函数或沙箱内落盘，无 LLM。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.assessment import manager as am
from app.agents.assessment import adaptive_test as cat
from app.agents.assessment.question import Question
from app.agents.student_model.evaluation import schema as S


def _mc() -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id="q1", question_revision=1,
        q_type=S.QuestionType.MULTIPLE_CHOICE, stem="s",
        options={"A": "1", "B": "2"}, answer="B", explanation="e",
        rubric=[S.FrozenCriterion(id="c1", description="选对", weight=1.0)])


class TestGradeMcTask(unittest.TestCase):
    def test_correct_wrong_and_normalization(self):
        task = _mc()
        self.assertEqual(am.grade_mc_task(task, "B").verdict,
                         S.Verdict.CORRECT)
        self.assertEqual(am.grade_mc_task(task, " b ").verdict,
                         S.Verdict.CORRECT)
        self.assertEqual(am.grade_mc_task(task, "A").verdict, S.Verdict.WRONG)
        self.assertEqual(am.grade_mc_task(task, "").verdict, S.Verdict.WRONG)
        self.assertEqual(am.grade_mc_task(task, "B").task_score, 1.0)


class TestComputeTaskResult(unittest.TestCase):
    def _task(self, crits: list[tuple[str, float, bool]]):
        return S.TaskSnapshot(
            question_id="q", question_revision=1,
            q_type=S.QuestionType.SHORT_ANSWER, stem="s", answer="a",
            explanation="e",
            rubric=[S.FrozenCriterion(id=cid, description=d, weight=w,
                                      critical=crit)
                    for cid, d, w, crit in
                    [(c[0], "判分点", c[1], c[2]) for c in crits]])

    def _cr(self, cid: str, result: str) -> S.CriterionResult:
        return S.CriterionResult(criterion_id=cid,
                                 result=S.CriterionResultKind(result))

    def test_weighted_score(self):
        task = self._task([("k1", 1.0, False), ("k2", 3.0, False)])
        tr = am.compute_task_result.__wrapped__ if False else None
        from app.agents.student_model.evaluation.grading import (
            compute_task_result)
        out = compute_task_result(task, [self._cr("k1", "met"),
                                         self._cr("k2", "partial")], "ans")
        # (1*1 + 3*0.5)/4 = 0.62 → partial
        self.assertEqual(out.grading_status, S.GradingStatus.GRADED)
        self.assertEqual(out.task_score, 0.62)
        self.assertEqual(out.verdict, S.Verdict.PARTIAL)

    def test_critical_not_observed_indeterminate(self):
        task = self._task([("k1", 1.0, True), ("k2", 1.0, False)])
        from app.agents.student_model.evaluation.grading import (
            compute_task_result)
        out = compute_task_result(task, [self._cr("k1", "not_observed"),
                                         self._cr("k2", "met")], "ans")
        self.assertEqual(out.grading_status, S.GradingStatus.INDETERMINATE)
        self.assertIsNone(out.task_score)
        self.assertIsNone(out.verdict)

    def test_na_excluded_from_denominator_only_when_allowed(self):
        task = self._task([("k1", 1.0, False), ("k2", 1.0, False)])
        task.rubric[1].allow_not_applicable = True
        from app.agents.student_model.evaluation.grading import (
            compute_task_result)
        out = compute_task_result(task, [self._cr("k1", "met"),
                                         self._cr("k2", "not_applicable")],
                                  "ans")
        self.assertEqual(out.task_score, 1.0)
        # 未声明 allow_not_applicable 的 N/A → 无法完整评分 → indeterminate
        task2 = self._task([("k1", 1.0, False), ("k2", 1.0, False)])
        out2 = compute_task_result(task2, [self._cr("k1", "met"),
                                           self._cr("k2", "not_applicable")],
                                   "ans")
        self.assertEqual(out2.grading_status, S.GradingStatus.INDETERMINATE)


class TestTaskSnapshotConversion(StorageSandboxTestCase):
    def test_from_quiz_dict_freezes_rubric_and_verification(self):
        qd = {"id": "q_x1", "type": "short_answer", "stem": "题",
              "answer": "答", "explanation": "解析",
              "knowledge_point": "浮力",
              "rubric": {"criteria": [
                  {"id": "c1", "description": "步骤对", "weight": 2.0,
                   "critical": True}],
                  "equivalent_solutions": ["另一种写法"]},
              "verification": {"answer_verified": True}}
        task = am.task_snapshot_from_quiz_dict(qd, workspace_id="ws_1")
        self.assertEqual(task.question_id, "q_x1")
        self.assertEqual(task.rubric[0].critical, True)
        self.assertEqual(task.verification.status, "passed")
        self.assertEqual(task.workspace_id, "ws_1")
        self.assertIn("rh_", task.rubric_hash)

    def test_unverified_question_is_unreviewed(self):
        qd = {"id": "q_x2", "type": "multiple_choice", "stem": "题",
              "options": {"A": "1", "B": "2"}, "answer": "A",
              "explanation": "解析"}
        task = am.task_snapshot_from_quiz_dict(qd)
        self.assertEqual(task.verification.status, "unreviewed")

    def test_legacy_question_object(self):
        question = Question(concept="浮力", q_type="short_answer",
                            stem="题干", answer="答案", explanation="解析",
                            knowledge_points=["浮力"])
        task = am.task_snapshot_from_legacy(question, workspace_id="ws_2")
        self.assertEqual(task.q_type, S.QuestionType.SHORT_ANSWER)
        self.assertEqual(task.workspace_id, "ws_2")
        self.assertEqual(task.source_badge, "浮力")


class TestAdaptiveRules(unittest.TestCase):
    def test_difficulty_steps_ignore_null_verdicts(self):
        self.assertEqual(cat.next_difficulty(["correct", "correct"], 2), 3)
        self.assertEqual(cat.next_difficulty(["wrong", None], 3), 2)
        self.assertEqual(cat.next_difficulty([None, None], 3), 3)  # 不动

    def test_should_stop_only_on_hard_cap(self):
        inst = cat.CatInstance(assessment_id="a", count_limit=2)
        self.assertEqual(cat.should_stop(inst, ["correct"]), ("", ""))
        status, code = cat.should_stop(inst, ["correct", "wrong"])
        self.assertEqual((status, code), (cat.STATUS_COMPLETED,
                                          "max_questions"))

    def test_continuation_finish_and_probe(self):
        inst = cat.CatInstance(assessment_id="a")
        cat.apply_continuation(inst, S.ContinuationAction(
            action="finish", reason="证据充分", remaining_claims=[]))
        self.assertEqual(inst.status, cat.STATUS_COMPLETED)
        self.assertEqual(inst.stop_code, "sufficient_for_current_claim")
        inst2 = cat.CatInstance(assessment_id="b")
        cat.apply_continuation(inst2, S.ContinuationAction(
            action="probe", reason="仍有疑点",
            remaining_claims=["端点条件辨析"]))
        self.assertEqual(inst2.target_claims, ["端点条件辨析"])
        self.assertEqual(inst2.status, cat.STATUS_ACTIVE)


if __name__ == "__main__":
    unittest.main()
