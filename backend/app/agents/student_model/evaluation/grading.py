"""本题判分：仅由冻结量规权重服务端本地计算（plan §4.5 / A04）。

- 每个 criterion 恰好出现一次；not_applicable 只有任务定义允许时可用。
- 必需（critical）criterion 未观察到 → 整题 indeterminate（score=null），
  不得改成部分正确。
- score=null/verdict=null 贯穿 API/UI/CAT：不进正误统计、难度步进或 SRS
  成功分支。
- 模型输出 schema 不含 score；MC 正误确定性判定（§11.4）。
"""
from __future__ import annotations

from . import schema as S
from .store import answer_fingerprint

_MET_VALUE = {S.CriterionResultKind.MET: 1.0,
              S.CriterionResultKind.PARTIAL: 0.5,
              S.CriterionResultKind.NOT_MET: 0.0,
              S.CriterionResultKind.NOT_OBSERVED: 0.0}


def grade_mc_task(task: S.TaskSnapshot, student_answer: str,
                  *, now: str = "") -> S.TaskResult:
    """MC 正误确定性判定 + 本题分（仅选项字母比较，零 LLM）。"""
    expected = (task.answer or "").strip().upper()
    given = (student_answer or "").strip().upper()
    correct = given == expected and bool(given)
    return S.TaskResult(
        question_ref=S.QuestionRef(question_id=task.question_id,
                                   question_revision=task.question_revision),
        grading_status=S.GradingStatus.GRADED,
        verdict=(S.Verdict.CORRECT if correct else S.Verdict.WRONG),
        task_score=1.0 if correct else 0.0,
        criterion_results=[],
        answer_fingerprint=answer_fingerprint(student_answer),
        computed_at=now or S.utc_now_iso(),
        rubric_hash=task.rubric_hash)


def compute_task_result(task: S.TaskSnapshot,
                        criterion_results: list[S.CriterionResult],
                        student_answer: str,
                        *, now: str = "") -> S.TaskResult:
    """开放题：LLM 的 criterion 判定 + 服务端本地加权计分（§4.5）。"""
    by_id = {c.criterion_id: c for c in criterion_results}
    problems: list[str] = []
    awarded = 0.0
    possible = 0.0
    indeterminate = False
    for criterion in task.rubric:
        result = by_id.get(criterion.id)
        if result is None:
            problems.append(criterion.id)
            if criterion.critical:
                indeterminate = True
            continue
        kind = result.result
        if kind == S.CriterionResultKind.NOT_APPLICABLE:
            if not criterion.allow_not_applicable:
                problems.append(criterion.id)
                indeterminate = indeterminate or criterion.critical
            continue                       # 合法 N/A 不进分母
        if kind == S.CriterionResultKind.NOT_OBSERVED and criterion.critical:
            indeterminate = True
        possible += float(criterion.weight)
        awarded += float(criterion.weight) * _MET_VALUE.get(kind, 0.0)
    if problems and not indeterminate:
        # 非关键 criterion 缺失：题目无法完整评分 → 未判定（不假部分正确）
        indeterminate = True
    if indeterminate or possible <= 0:
        return S.TaskResult(
            question_ref=S.QuestionRef(
                question_id=task.question_id,
                question_revision=task.question_revision),
            grading_status=S.GradingStatus.INDETERMINATE,
            verdict=None, task_score=None,
            criterion_results=criterion_results,
            answer_fingerprint=answer_fingerprint(student_answer),
            computed_at=now or S.utc_now_iso(),
            rubric_hash=task.rubric_hash)
    score = round(awarded / possible, 2)
    verdict = (S.Verdict.CORRECT if score >= 0.75
               else S.Verdict.PARTIAL if score >= 0.25
               else S.Verdict.WRONG)
    return S.TaskResult(
        question_ref=S.QuestionRef(question_id=task.question_id,
                                   question_revision=task.question_revision),
        grading_status=S.GradingStatus.GRADED,
        verdict=verdict, task_score=score,
        criterion_results=criterion_results,
        answer_fingerprint=answer_fingerprint(student_answer),
        computed_at=now or S.utc_now_iso(),
        rubric_hash=task.rubric_hash)
