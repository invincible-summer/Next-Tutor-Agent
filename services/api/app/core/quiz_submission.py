"""Read-only chat-card projection of an accepted answer and its evaluation.

The learner journal is authoritative, including while grading is pending.
Session result snapshots are only a cache; old missing snapshots can recover
by question identity without resubmitting or invoking a model.
"""
from __future__ import annotations

from typing import Any

from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal


def quiz_submission(student_id: str, question_id: str,
                    question_revision: int) -> dict[str, Any] | None:
    state = get_journal(student_id).state()
    for src in state.sources.values():
        ref = src.receipt.task_ref
        if ref is None or (ref.question_id, ref.question_revision) != (
                question_id, question_revision):
            continue
        receipt = src.receipt
        iid = src.current_interpretation_id
        meta = src.interpretations.get(iid, {}) if iid else {}
        if meta.get("revoked"):
            meta = {}
            iid = ""
        # The reducer keeps the latest task-only/MC result under the empty
        # key, independently of the current long-term interpretation.
        task_result = src.interpretations.get("", {}).get("task_result")
        if task_result is None:
            task_result = meta.get("task_result")
        jobs = [rt.job for rt in state.jobs.values()
                if rt.job.source_id == receipt.source_id
                and rt.job.source_revision == receipt.source_revision]
        active = [j for j in jobs if j.state in (
            S.JobState.QUEUED, S.JobState.RUNNING, S.JobState.RETRY_WAIT)]
        latest = jobs[-1] if jobs else None
        if not receipt.workspace_id_at_observation:
            evaluation_status = "unavailable"
        elif active:
            evaluation_status = "pending"
        elif latest and latest.state in (S.JobState.FAILED, S.JobState.CANCELLED):
            evaluation_status = "failed"
        elif iid:
            evaluation_status = "ready"
        else:
            evaluation_status = "disabled" if not jobs else "unavailable"
        raw = meta.get("raw_interpretation") or {}
        task = state.tasks.get(question_id, {}).get(question_revision)
        return {
            "status": "ok", "attempt_id": receipt.attempt_id,
            "source_id": receipt.source_id,
            "job_id": (active[0].job_id if active else
                       latest.job_id if latest else ""),
            "question_id": question_id,
            "question_revision": question_revision,
            "student_answer": receipt.canonical_text,
            "verdict": (task_result or {}).get("verdict"),
            "task_result": task_result,
            "evaluation": {"status": evaluation_status,
                           "interpretation_id": iid},
            "feedback": raw.get("feedback", "") if isinstance(raw, dict) else "",
            "pending": bool(active),
            "revealed": ({"answer": task.answer, "explanation": task.explanation}
                         if task is not None and task_result is not None else None),
        }
    return None
