"""课堂应用服务（plan.md §14；route 之下的复杂工作层）。

A 阶段落地：课程创建（幂等+配额+占位 job）、列表读取（只读索引）。
后续阶段在同一 service 上扩展修订/run/音频/导出操作。
"""
from __future__ import annotations

from typing import Any

from ..core import classroom_store as store
from ..core.workspace import _owner_of, load_workspace
from ..schemas import classroom as sc
from . import capabilities as caps
from . import idempotency
from .errors import ClassroomError

# worker（D 阶段）在 lifespan 启动时注册的入队回调；无 worker 时 job 保持
# queued 落盘，启动扫描会恢复。
enqueue_job: Any = None


def load_owned_workspace(workspace_id: str, student_id: str):
    ws = load_workspace(workspace_id)
    if ws is None or _owner_of(ws) != student_id:
        raise ClassroomError("source_not_found", "工作学习区不存在")
    return ws


def _ensure_classroom_writable(student_id: str) -> None:
    store.assert_owner_writable(student_id)
    store.ensure_owner(student_id)


def create_lesson(student_id: str, workspace_id: str,
                  request: sc.CreateLessonRequest,
                  *, idempotency_key: str) -> dict[str, Any]:
    """POST W/lessons：幂等 + 配额 + Lesson/占位 Job 落盘。

    返回 §14.1 的 202 body；同 key 同 body 重放返回原结果、不重复建课、
    不重复扣额；同 key 不同 body → 409 idempotency_conflict。
    """
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    load_owned_workspace(workspace_id, student_id)
    _ensure_classroom_writable(student_id)

    scope = "create_lesson"
    body = {"workspace_id": workspace_id,
            "brief": request.brief.model_dump(mode="json", by_alias=True),
            "start_mode": request.start_mode.value}
    body_hash = idempotency.body_hash_of(body)
    replayed = idempotency.lookup(student_id, scope, idempotency_key,
                                  body_hash)
    if replayed:
        return replayed

    idempotency.check_generation_quota(student_id)

    lesson_id = store.new_id("les")
    job_id = store.new_id("job")
    now = store.utcnow()
    lesson = sc.Lesson(
        lesson_id=lesson_id, owner_id=student_id, workspace_id=workspace_id,
        title=request.brief.topic[:120], created_at=now, updated_at=now,
        latest_job_id=job_id)
    store.save_lesson(lesson)

    target_revision = store.allocate_revision(student_id, workspace_id,
                                              lesson_id)
    job = sc.GenerationJob(
        job_id=job_id, owner_id=student_id, workspace_id=workspace_id,
        lesson_id=lesson_id, target_revision=target_revision,
        state=sc.JobState.queued,
        brief_hash=store.canonical_hash(
            request.brief.model_dump(mode="json", by_alias=True)),
        start_mode=request.start_mode, created_at=now, updated_at=now)
    store.save_job(job)
    store.index_upsert_lesson(student_id, workspace_id, lesson, job=job)
    idempotency.consume_generation_quota(student_id)

    result = {
        "lesson_id": lesson_id,
        "job_id": job_id,
        "target_revision": target_revision,
        "status_url": f"/api/v1/workspaces/{workspace_id}/classroom/"
                      f"jobs/{job_id}",
        "events_url": f"/api/v1/workspaces/{workspace_id}/classroom/"
                      f"jobs/{job_id}/events",
    }
    idempotency.remember(student_id, scope, idempotency_key, body_hash,
                         result)
    if enqueue_job is not None:
        try:
            enqueue_job(student_id, workspace_id, lesson_id, job_id)
        except Exception:
            pass  # 落盘 job 是事实源，启动扫描会恢复
    return result


def _lesson_list_status(lesson: sc.Lesson,
                        job: sc.GenerationJob | None) -> sc.LessonListStatus:
    if lesson.lifecycle != sc.LessonLifecycle.active:
        return sc.LessonListStatus.needs_attention
    if job is not None and job.state in (
            sc.JobState.queued, sc.JobState.running,
            sc.JobState.awaiting_outline):
        return sc.LessonListStatus.generating
    if lesson.latest_ready_revision is None:
        return (sc.LessonListStatus.failed
                if job is not None and job.state == sc.JobState.failed
                else sc.LessonListStatus.needs_attention)
    if job is not None and job.state == sc.JobState.needs_input:
        return sc.LessonListStatus.needs_attention
    return sc.LessonListStatus.ready


def list_lessons(student_id: str, workspace_id: str, *, page: int = 1,
                 page_size: int = 5, status: str | None = None) -> dict[str, Any]:
    """GET W/lessons：只读索引，不启动作业、不 mkdir。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    load_owned_workspace(workspace_id, student_id)

    page = max(1, page)
    page_size = min(max(1, page_size), 20)
    index = store.read_index(student_id, workspace_id)
    items: list[sc.LessonSummaryPublic] = []
    for lesson_id, entry in index.get("lessons", {}).items():
        lesson = store.load_lesson(student_id, workspace_id, lesson_id)
        if lesson is None or lesson.lifecycle != sc.LessonLifecycle.active:
            continue
        job = None
        if lesson.latest_job_id:
            job = store.load_job(student_id, workspace_id, lesson_id,
                                 lesson.latest_job_id)
        lesson_status = _lesson_list_status(lesson, job)
        if status and status != lesson_status.value:
            continue
        items.append(_summary_public(lesson, job, lesson_status))
    items.sort(key=lambda s: s.updated_at, reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": [s.model_dump(mode="json", by_alias=True)
                  for s in items[start:start + page_size]],
        "total": total, "page": page, "page_size": page_size,
    }


def _summary_public(lesson: sc.Lesson, job: sc.GenerationJob | None,
                    lesson_status: sc.LessonListStatus) -> sc.LessonSummaryPublic:
    job_public = None
    if job is not None:
        job_public = sc.JobPublic(
            job_id=job.job_id, lesson_id=job.lesson_id, state=job.state,
            phase=job.phase, state_revision=job.state_revision,
            progress=sc.JobProgress(), warnings=[],
            last_error=job.last_error, cancel_requested=job.cancel_requested,
            start_mode=job.start_mode, created_at=job.created_at,
            updated_at=job.updated_at)
    return sc.LessonSummaryPublic(
        lesson_id=lesson.lesson_id, workspace_id=lesson.workspace_id,
        title=lesson.title, status=lesson_status,
        latest_ready_revision=lesson.latest_ready_revision,
        latest_job=job_public,
        extra=sc.LessonListStatusExtra(),
        updated_at=lesson.updated_at)
