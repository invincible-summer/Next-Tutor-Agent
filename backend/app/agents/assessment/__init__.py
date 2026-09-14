"""M4 测评智能 facade：统一作答服务的唯一出口（plan §11.4/§13.4）。

旧 evaluate_and_record / raw_grade / 概念状态推导出口已随本版
删除（A02/A03）；出题工具保留（generator + P1/P2 质量链）。所有作答
（聊天题卡/习题中心/CAT）经 evaluate_submission 进入同一评价协议。
"""
from __future__ import annotations

from .manager import (AnswerTooLarge, AssessmentBindingError,
                      QuestionAlreadyAnswered, QuestionNotFound,
                      QuestionRevisionMismatch, ScopeRevisionConflict,
                      SessionNotOwned, SubmissionError, SubmissionReceipt,
                      WorkspaceNotOwned, assistance_events,
                      evaluate_submission, is_enabled, load_task_snapshot,
                      new_assessment_id, new_attempt_id, record_assistance,
                      register_task_snapshot, resolve_submission_binding,
                      run_assessment_job, task_snapshot_from_legacy)
from .question import Question, QuestionType
from .state import AssessmentContext, AssessmentGoal
from .generator import generate_question

__all__ = [
    "AnswerTooLarge", "AssessmentBindingError", "AssessmentContext",
    "AssessmentGoal", "Question", "QuestionAlreadyAnswered",
    "QuestionNotFound", "QuestionRevisionMismatch", "QuestionType",
    "ScopeRevisionConflict", "SessionNotOwned", "SubmissionError",
    "SubmissionReceipt", "WorkspaceNotOwned", "assistance_events",
    "evaluate_submission", "generate_question", "is_enabled",
    "load_task_snapshot", "new_assessment_id", "new_attempt_id",
    "record_assistance", "register_task_snapshot",
    "resolve_submission_binding", "run_assessment_job",
    "task_snapshot_from_legacy",
]
