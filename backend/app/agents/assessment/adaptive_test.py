"""CAT 自适应测评引擎：journal 投影版（plan §11.5 / A10）。

实例身份显式化（assessment_id + workspace 绑定）：题目/作答/报告全部经
journal（TaskSnapshot + SourceReceipt + assessment_session_changed）持久
化，新测评不覆盖旧报告；旧单槽 ``students/<sid>.assessment.json`` 退出。

停止结论统一 ``completed|stopped|abandoned`` ×
``sufficient_for_current_claim|needs_clarification|max_questions|max_time|
user_stopped|generation_failed``（§11.5）；删除 mastered 隐含等级与隐藏
掌握阈值。难度步进只消费本实例 task-local 判定（verdict=null 不参与，
A04）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import (JournalState,
                                                       get_journal)

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_ABANDONED = "abandoned"


@dataclass
class CatInstance:
    assessment_id: str
    workspace_id: str = ""
    purpose: str = "adaptive"           # adaptive | diagnose | practice
    target_claims: list[str] = field(default_factory=list)
    concept_keys: list[str] = field(default_factory=list)
    concept: str = ""                   # 出题目标（generator 接口）
    grade: str = "本科"
    subject: str = ""
    illustration_request: str = "auto"
    count_limit: int = 1
    difficulty: int = 2
    status: str = STATUS_ACTIVE
    stop_reason: str = ""
    stop_code: str = ""
    question_refs: list[S.QuestionRef] = field(default_factory=list)
    answered_question_ids: list[str] = field(default_factory=list)
    probe_ref: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_detail(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "workspace_id": self.workspace_id,
            "purpose": self.purpose,
            "target_claims": list(self.target_claims),
            "concept_keys": list(self.concept_keys),
            "concept": self.concept,
            "grade": self.grade, "subject": self.subject,
            "illustration_request": self.illustration_request,
            "count_limit": self.count_limit, "difficulty": self.difficulty,
            "status": self.status, "stop_reason": self.stop_reason,
            "stop_code": self.stop_code,
            "question_refs": [r.model_dump() for r in self.question_refs],
            "answered_question_ids": list(self.answered_question_ids),
            "probe_ref": dict(self.probe_ref),
            "created_at": self.created_at,
        }

    @classmethod
    def from_detail(cls, d: dict[str, Any]) -> "CatInstance":
        return cls(
            assessment_id=str(d.get("assessment_id") or ""),
            workspace_id=str(d.get("workspace_id") or ""),
            purpose=str(d.get("purpose") or "adaptive"),
            target_claims=list(d.get("target_claims") or []),
            concept_keys=list(d.get("concept_keys") or []),
            concept=str(d.get("concept") or ""),
            grade=str(d.get("grade") or "本科"),
            subject=str(d.get("subject") or ""),
            illustration_request=str(d.get("illustration_request") or "auto"),
            count_limit=int(d.get("count_limit") or 1),
            difficulty=int(d.get("difficulty") or 2),
            status=str(d.get("status") or STATUS_ACTIVE),
            stop_reason=str(d.get("stop_reason") or ""),
            stop_code=str(d.get("stop_code") or ""),
            question_refs=[S.QuestionRef.model_validate(r)
                           for r in (d.get("question_refs") or [])],
            answered_question_ids=list(d.get("answered_question_ids") or []),
            probe_ref=dict(d.get("probe_ref") or {}),
            created_at=str(d.get("created_at") or ""))


def load_instance(state: JournalState,
                  assessment_id: str) -> CatInstance | None:
    detail = state.assessments.get(assessment_id)
    if not detail:
        return None
    return CatInstance.from_detail(detail)


def save_instance(student_id: str, instance: CatInstance, *,
                  change: str = "update") -> None:
    get_journal(student_id).append([S.OpAssessmentSessionChanged(
        assessment_id=instance.assessment_id,
        workspace_id=instance.workspace_id,
        change=change, detail=instance.to_detail())])


def instance_task_results(state: JournalState, instance: CatInstance
                          ) -> list[S.TaskResult]:
    """本实例已受理作答的 TaskResult 序列（按题目顺序）。"""
    out: list[S.TaskResult] = []
    for qref in instance.question_refs:
        for src in state.sources.values():
            ref = src.receipt.task_ref
            if ref is None or ref.question_id != qref.question_id:
                continue
            if src.receipt.assessment_id != instance.assessment_id:
                continue
            for iid in sorted(src.interpretations):
                raw = src.interpretations[iid].get("task_result")
                if isinstance(raw, dict) and raw:
                    out.append(S.TaskResult.model_validate(raw))
                    break
    return out


def verdicts_of(results: list[S.TaskResult]) -> list[str | None]:
    """verdict 序列；indeterminate/unverified 保留 None（不进难度步进，
    A04）。"""
    return [r.verdict.value if r.verdict is not None else None
            for r in results]


def next_difficulty(verdicts: list[str | None], current: int) -> int:
    """最近 5 个有效判定：≥0.8 升一档、≤0.4 降一档（partial=0.5），
    clamp [1,5]；未判定不参与。"""
    valid = [v for v in verdicts if v is not None][-5:]
    if not valid:
        return current
    score = sum(1.0 if v == "correct" else 0.5 if v == "partial" else 0.0
                for v in valid) / len(valid)
    if score >= 0.8:
        return min(5, current + 1)
    if score <= 0.4:
        return max(1, current - 1)
    return current


def should_stop(instance: CatInstance,
                verdicts: list[str | None]) -> tuple[str, str]:
    """(status, stop_code)；空串表示继续。只做硬上限与显式判定，
    不含掌握度阈值（§11.5）。"""
    if len([v for v in verdicts if v is not None]) >= instance.count_limit:
        return STATUS_COMPLETED, "max_questions"
    return "", ""


def apply_continuation(instance: CatInstance,
                       continuation: S.ContinuationAction | None) -> None:
    """P3 continuation 的 continue/probe/finish（§11.5）；硬上限优先。
    probe 的 remaining_claims 进入下一题出题目标。"""
    if continuation is None or instance.status != STATUS_ACTIVE:
        return
    if continuation.action == "finish":
        instance.status = STATUS_COMPLETED
        instance.stop_code = ("sufficient_for_current_claim"
                              if continuation.remaining_claims == []
                              else "needs_clarification")
        instance.stop_reason = continuation.reason
    elif continuation.action == "probe" and continuation.remaining_claims:
        instance.target_claims = list(continuation.remaining_claims[:4])


def continuation_for(state: JournalState, instance: CatInstance
                     ) -> S.ContinuationAction | None:
    """从最后一题的已提交解释读取 continuation（P3 输出，§11.5）。"""
    if not instance.question_refs:
        return None
    last_qid = instance.question_refs[-1].question_id
    for src in state.sources.values():
        ref = src.receipt.task_ref
        if ref is None or ref.question_id != last_qid:
            continue
        if src.receipt.assessment_id != instance.assessment_id:
            continue
        meta = src.interpretations.get(src.current_interpretation_id, {})
        raw = meta.get("continuation")
        if isinstance(raw, dict) and raw:
            try:
                return S.ContinuationAction.model_validate(raw)
            except Exception:
                return None
    return None


def _first_committed_result(src) -> dict | None:
    """source 已落盘的第一份 TaskResult（MC 确定性判分在语义解释之外，
    单独提交为合成解释条目；与 instance_task_results 同一取法）。"""
    for iid in sorted(src.interpretations):
        raw = src.interpretations[iid].get("task_result")
        if isinstance(raw, dict) and raw:
            return raw
    return None


def _evaluation_status(state: JournalState, src) -> str:
    """§10.3 映射：有可发布解释 ready；作业在途 pending；硬故障/取消
    unavailable；无作业（旧数据）保持 pending。"""
    if src.current_interpretation_id:
        return "ready"
    states = [rt.job.state for rt in state.jobs.values()
              if rt.job.source_id == src.receipt.source_id]
    if states and all(s in (S.JobState.FAILED, S.JobState.CANCELLED)
                      for s in states):
        return "unavailable"
    return "pending"


def report(state: JournalState, assessment_id: str) -> dict[str, Any] | None:
    """本次表现 + 语义总结（分清 pending 与题目局部结果，§11.5）。"""
    instance = load_instance(state, assessment_id)
    if instance is None:
        return None
    items: list[dict[str, Any]] = []
    for qref in instance.question_refs:
        for src in state.sources.values():
            ref = src.receipt.task_ref
            if ref is None or ref.question_id != qref.question_id:
                continue
            if src.receipt.assessment_id != assessment_id:
                continue
            interp_id = src.current_interpretation_id
            meta = src.interpretations.get(interp_id, {}) if interp_id else {}
            raw_interp = meta.get("raw_interpretation") or {}
            task = state.tasks.get(qref.question_id, {}).get(qref.question_revision)
            # 题目局部结果（MC 判分）不受语义评价在途/失败影响
            items.append({
                "question_id": qref.question_id,
                "question_revision": qref.question_revision,
                "question": task.public_view().model_dump(mode="json") if task else None,
                "attempt_id": src.receipt.attempt_id,
                "observed_at": src.receipt.observed_at,
                "task_result": _first_committed_result(src),
                "evaluation_status": _evaluation_status(state, src),
                "feedback": (raw_interp.get("feedback") or "")
                if isinstance(raw_interp, dict) else "",
            })
    graded = [i for i in items
              if (i["task_result"] or {}).get("verdict") is not None]
    counts = {"correct": 0, "partial": 0, "wrong": 0}
    for i in graded:
        # 落盘 dict 里的 verdict 可能是 enum 实例：取 .value，避免
        # str() 出 "Verdict.CORRECT" 这种键。
        v = i["task_result"]["verdict"]
        key = getattr(v, "value", str(v))
        counts[key] = counts.get(key, 0) + 1
    return {
        "assessment_id": assessment_id,
        "workspace_id": instance.workspace_id,
        "status": instance.status,
        "stop_reason": instance.stop_reason,
        "stop_code": instance.stop_code,
        "asked": len(items),
        "graded": len(graded),
        "pending": len(items) - len(graded),
        "counts": counts,
        "difficulty": instance.difficulty,
        "items": items,
    }
