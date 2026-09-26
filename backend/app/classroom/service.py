"""课堂应用服务（plan.md §14；route 之下的复杂工作层）。

A 阶段落地：课程创建（幂等+配额+占位 job）、列表读取（只读索引）。
后续阶段在同一 service 上扩展修订/run/音频/导出操作。
"""
from __future__ import annotations

import json
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

    from .worker import assert_queue_capacity
    assert_queue_capacity(student_id)
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
    # Brief 完整落盘：job/lesson 实体只存 hash，worker 生成从这里读
    store.stage_file(
        store.job_root(student_id, workspace_id, lesson_id, job_id),
        "brief.json",
        json.dumps(request.brief.model_dump(mode="json", by_alias=True),
                   ensure_ascii=False, indent=1))
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


def workspace_summary(student_id: str, workspace_id: str) -> dict[str, Any]:
    """Sidebar 批量摘要（§3.2.7）：只读可重建索引，绝不逐课解析。

    返回 {lesson_count, active_job_count, last_lesson_id}；索引缺失/空一律
    返回零值（读路径不 mkdir）。active_job_count 统计所有未完结 job
    （queued/running/awaiting_outline/needs_input），供侧栏提示"生成中"。
    """
    try:
        index = store.read_index(student_id, workspace_id)
    except Exception:
        index = {"lessons": {}, "jobs": {}}
    lessons = index.get("lessons") or {}
    active_ids = {lid for lid, e in lessons.items()
                  if (e or {}).get("lifecycle", "active") == "active"}
    last_lesson_id: str | None = None
    last_updated = ""
    for lid, entry in lessons.items():
        if lid not in active_ids:
            continue
        updated = str((entry or {}).get("updated_at") or "")
        if updated >= last_updated:
            last_updated = updated
            last_lesson_id = lid
    active_states = {"queued", "running", "awaiting_outline", "needs_input"}
    active_jobs = 0
    for job in (index.get("jobs") or {}).values():
        if (job or {}).get("lesson_id") in active_ids and \
                (job or {}).get("state") in active_states:
            active_jobs += 1
    return {"lesson_count": len(active_ids),
            "active_job_count": active_jobs,
            "last_lesson_id": last_lesson_id}


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
        items.append(_summary_public(student_id, workspace_id, lesson, job,
                                     lesson_status))
    items.sort(key=lambda s: s.updated_at, reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": [s.model_dump(mode="json", by_alias=True)
                  for s in items[start:start + page_size]],
        "total": total, "page": page, "page_size": page_size,
    }


def _summary_public(owner_id: str, workspace_id: str, lesson: sc.Lesson,
                    job: sc.GenerationJob | None,
                    lesson_status: sc.LessonListStatus) -> sc.LessonSummaryPublic:
    """列表卡投影（§3.3）：主题/章节/时长/风格/页数/状态/更新时间。

    brief 从 job 的 brief.json 读取（小文件）；页数来自阶段产物计数，
    不解析讲稿正文。缺失字段静默降级（旧课程可能没有 brief 落盘）。
    """
    job_public = None
    progress = sc.JobProgress()
    warnings: list[str] = []
    if job is not None:
        progress = _job_progress(owner_id, workspace_id, lesson.lesson_id, job)
        warnings = _job_warnings(owner_id, workspace_id, lesson.lesson_id, job)
        job_public = sc.JobPublic(
            job_id=job.job_id, lesson_id=job.lesson_id, state=job.state,
            phase=job.phase, state_revision=job.state_revision,
            progress=progress, warnings=warnings[:8],
            last_error=job.last_error, cancel_requested=job.cancel_requested,
            start_mode=job.start_mode, created_at=job.created_at,
            updated_at=job.updated_at)

    brief_public = None
    chapter_label = ""
    if job is not None:
        brief_path = store.job_brief_path(owner_id, workspace_id,
                                          lesson.lesson_id, job.job_id)
        if brief_path.is_file():
            try:
                brief = sc.LessonBrief.model_validate(
                    json.loads(brief_path.read_text(encoding="utf-8")))
                brief_public = _brief_public(brief)
                chapters: list[str] = []
                for fsel in brief.source_selection.files:
                    chapters.extend(c.title for c in fsel.chapters)
                chapter_label = " / ".join(chapters)[:120]
            except (OSError, ValueError):
                brief_public = None

    return sc.LessonSummaryPublic(
        lesson_id=lesson.lesson_id, workspace_id=lesson.workspace_id,
        title=lesson.title, status=lesson_status,
        latest_ready_revision=lesson.latest_ready_revision,
        latest_job=job_public, brief=brief_public,
        extra=sc.LessonListStatusExtra(
            chapter_label=chapter_label,
            slide_count=progress.total_slides),
        updated_at=lesson.updated_at)


# ---------------------------------------------------------------------------
# GET L：课程详情（§14.1；未完成时只给 brief/progress，不伪造 slides）
# ---------------------------------------------------------------------------

def _brief_public(brief: sc.LessonBrief) -> sc.BriefPublic:
    return sc.BriefPublic(
        topic=brief.topic, goals=list(brief.goals),
        source_policy=brief.source_policy,
        duration_minutes=brief.duration_minutes, language=brief.language,
        grade=brief.grade, pedagogy_id=brief.pedagogy_id,
        theme_id=brief.theme_id, image_density=brief.image_density,
        checkpoint_density=brief.checkpoint_density,
        research_enabled=brief.research.enabled,
        research_timeliness=brief.research.timeliness,
        custom_requirements=brief.custom_requirements)


def _source_public(record: sc.SourceRecord) -> sc.SourcePublic:
    loc = record.locator
    namespace = url = domain = publisher = None
    section_path: list[str] = []
    page = printed_page = None
    if isinstance(loc, sc.FileLocator):
        namespace, section_path = loc.namespace, list(loc.section_path)
        page, printed_page = loc.page, loc.printed_page
    else:
        url, domain, publisher = loc.url, loc.domain, loc.publisher
    return sc.SourcePublic(
        source_id=record.source_id, kind=record.kind, title=record.title,
        status="available", namespace=namespace or "",
        section_path=section_path, page=page, printed_page=printed_page,
        url=url, domain=domain, publisher=publisher,
        published_at=record.published_at, as_of=record.as_of,
        retrieved_at=record.retrieved_at)


def lesson_detail(student_id: str, workspace_id: str, lesson_id: str,
                  *, revision: int | None = None) -> dict[str, Any]:
    """GET L：已发布 revision 投影 + 任务摘要；未完成给 pending brief。

    verified_question_template 绝不进入投影（CheckpointPublic.question=None），
    预览/编辑器不提供答案（§4.3、I03）。
    """
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    load_owned_workspace(workspace_id, student_id)
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None or lesson.owner_id != student_id or \
            lesson.workspace_id != workspace_id or \
            lesson.lifecycle != sc.LessonLifecycle.active:
        raise ClassroomError("source_not_found", "课程不存在")

    job = None
    if lesson.latest_job_id:
        job = store.load_job(student_id, workspace_id, lesson_id,
                             lesson.latest_job_id)
    latest_job_public = None
    if job is not None:
        latest_job_public = sc.JobPublic(
            job_id=job.job_id, lesson_id=job.lesson_id, state=job.state,
            phase=job.phase, state_revision=job.state_revision,
            progress=_job_progress(student_id, workspace_id, lesson_id, job),
            warnings=_job_warnings(student_id, workspace_id, lesson_id, job),
            last_error=job.last_error, cancel_requested=job.cancel_requested,
            start_mode=job.start_mode, created_at=job.created_at,
            updated_at=job.updated_at, next_actions=_next_actions(job))

    rev_no = revision if revision is not None else lesson.latest_ready_revision
    revision_public = None
    if rev_no is not None:
        if rev_no not in lesson.published_revisions:
            raise ClassroomError("source_not_found", "课程版本不存在")
        spec = store.load_revision(student_id, workspace_id, lesson_id, rev_no)
        if spec is None:
            raise ClassroomError("source_not_found", "课程版本已损坏",
                                 retryable=False)
        revision_public = sc.RevisionPublic(
            revision=spec.revision, schema_version=spec.schema_version,
            brief=_brief_public(spec.brief), slides=list(spec.slides),
            objectives=list(spec.objectives),
            glossary=list(spec.glossary),
            source_records=[_source_public(r) for r in spec.source_snapshot],
            assets=[sc.AssetPublic(
                asset_id=a.asset_id, mime=a.mime, width=a.width,
                height=a.height, alt=a.alt, caption=a.caption, role=a.role,
                status=a.status, provider=a.provenance.provider,
                creator=a.provenance.creator,
                source_url=a.provenance.source_url,
                license_url=a.provenance.license_url)
                for a in spec.assets],
            checkpoints=[sc.CheckpointPublic(
                checkpoint_id=c.checkpoint_id, slide_id=c.slide_id,
                kind=c.kind, prompt=c.prompt,
                reflection_seconds=c.reflection_seconds, optional=c.optional)
                for c in spec.checkpoint_templates],
            renderer_version=spec.renderer_version,
            content_hash=spec.content_hash, created_at=spec.created_at)

    pending = None
    if revision_public is None and job is not None:
        brief = None
        brief_path = store.job_brief_path(student_id, workspace_id,
                                          lesson_id, job.job_id)
        if brief_path.is_file():
            try:
                brief = sc.LessonBrief.model_validate(
                    json.loads(brief_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                brief = None
        if brief is not None:
            pending = sc.LessonBriefProgress(
                brief=_brief_public(brief),
                progress=_job_progress(student_id, workspace_id,
                                       lesson_id, job),
                phase=job.phase, state=job.state)

    detail = sc.LessonDetailPublic(
        lesson_id=lesson.lesson_id, workspace_id=lesson.workspace_id,
        title=lesson.title, lifecycle=lesson.lifecycle,
        latest_ready_revision=lesson.latest_ready_revision,
        published_revisions=list(lesson.published_revisions),
        latest_job=latest_job_public, revision=revision_public,
        pending=pending, recent_run=None)  # run 投影在 G 阶段接入
    return detail.model_dump(mode="json", by_alias=True)


# ---------------------------------------------------------------------------
# frame 投影（§9.5/§14.1 GET L/revisions/{rev}/frame）
# ---------------------------------------------------------------------------

_EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def load_published_revision(student_id: str, workspace_id: str, lesson_id: str,
                            revision: int) -> sc.LessonRevision:
    """正式内容 API 只接受 published_revisions 中的版本（§15.1）。"""
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None or lesson.owner_id != student_id or \
            lesson.workspace_id != workspace_id:
        raise ClassroomError("source_not_found", "课程不存在")
    if revision not in lesson.published_revisions:
        raise ClassroomError("source_not_found", "课程版本不存在")
    spec = store.load_revision(student_id, workspace_id, lesson_id, revision)
    if spec is None:
        raise ClassroomError("source_not_found", "课程版本已损坏",
                             retryable=False)
    return spec


def get_revision_frame(student_id: str, workspace_id: str, lesson_id: str,
                       revision: int, *, mode: str = "presentation") -> str:
    """受鉴权自包含 HTML（Cache-Control: private,no-store 由 route 设置）。

    已发布 revision 的冻结 spec 确定性编译；读取不写盘。
    """
    if mode not in ("presentation", "reading"):
        raise ClassroomError("content_invalid", "mode 必须是 presentation|reading")
    spec = load_published_revision(student_id, workspace_id, lesson_id, revision)
    asset_bytes: dict[str, bytes] = {}
    for asset in spec.assets:
        if asset.status.value != "ready":
            continue
        ext = _EXT_BY_MIME.get(asset.mime)
        if ext is None:
            continue
        path = store.asset_file_path(student_id, workspace_id, lesson_id,
                                     asset.asset_id, ext)
        if path.is_file():
            try:
                asset_bytes[asset.asset_id] = path.read_bytes()
            except OSError:
                continue
    try:
        from .render.compiler import compile_html
        html = compile_html(spec, mode="online", asset_bytes=asset_bytes)
    except RuntimeError as exc:
        raise ClassroomError("renderer_unavailable",
                             "课件渲染器不可用", retryable=True) from exc
    except ValueError as exc:
        raise ClassroomError("content_invalid", str(exc)) from exc
    if mode == "reading":
        html = html.replace('<html lang=', '<html data-reading="1" lang=', 1)
    return html


# ---------------------------------------------------------------------------
# Job 控制面（plan.md §14.1，D06）：snapshot / cancel / retry / continue /
# patch_outline / patch_brief + SSE 事件流。
# ---------------------------------------------------------------------------

def _load_owned_job(student_id: str, workspace_id: str, lesson_id: str,
                    job_id: str) -> tuple[sc.Lesson, sc.GenerationJob]:
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None or lesson.owner_id != student_id:
        raise ClassroomError("source_not_found", "课程不存在")
    job = store.load_job(student_id, workspace_id, lesson_id, job_id)
    if job is None or job.owner_id != student_id:
        raise ClassroomError("source_not_found", "任务不存在")
    return lesson, job


def _job_progress(student_id: str, workspace_id: str, lesson_id: str,
                  job: sc.GenerationJob) -> sc.JobProgress:
    total = 0
    completed = 0
    stages = store.stages_dir(student_id, workspace_id, lesson_id,
                              job.job_id)
    outline = stages / "outline.json"
    if outline.is_file():
        try:
            total = len(json.loads(
                outline.read_text(encoding="utf-8"))["plan"]["pages"])
        except (OSError, ValueError, KeyError):
            total = 0
    authored = stages / "author_slides.json"
    if authored.is_file() and total:
        try:
            completed = len(json.loads(
                authored.read_text(encoding="utf-8"))["slides"])
        except (OSError, ValueError, KeyError):
            completed = 0
    if job.state == sc.JobState.succeeded and total:
        completed = total
    return sc.JobProgress(completed_slides=completed, total_slides=total)


def _job_warnings(student_id: str, workspace_id: str, lesson_id: str,
                  job: sc.GenerationJob) -> list[str]:
    warnings: list[str] = []
    stages = store.stages_dir(student_id, workspace_id, lesson_id,
                              job.job_id)
    resolve = stages / "resolve_sources.json"
    if resolve.is_file():
        try:
            issues = json.loads(
                resolve.read_text(encoding="utf-8")).get("issues") or []
            warnings.extend(
                f"{i.get('code')}: {str(i.get('message', ''))[:120]}"
                for i in issues)
        except (OSError, ValueError):
            pass
    return warnings[:32]


def _next_actions(job: sc.GenerationJob) -> list[str]:
    if job.state in (sc.JobState.queued, sc.JobState.running):
        return ["cancel"]
    if job.state == sc.JobState.awaiting_outline:
        return ["patch_outline", "continue", "cancel"]
    if job.state == sc.JobState.needs_input:
        return ["patch_brief", "continue", "cancel"]
    if job.state == sc.JobState.failed:
        return ["retry", "cancel"]
    if job.state == sc.JobState.cancelled:
        return ["retry"]
    return []


def job_snapshot(student_id: str, workspace_id: str, lesson_id: str,
                 job_id: str) -> dict[str, Any]:
    """GET J：完整 snapshot（进度来自阶段产物，只读）。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    public = sc.JobPublic(
        job_id=job.job_id, lesson_id=job.lesson_id, state=job.state,
        phase=job.phase, state_revision=job.state_revision,
        progress=_job_progress(student_id, workspace_id, lesson_id, job),
        warnings=_job_warnings(student_id, workspace_id, lesson_id, job),
        last_error=job.last_error, cancel_requested=job.cancel_requested,
        start_mode=job.start_mode, created_at=job.created_at,
        updated_at=job.updated_at, next_actions=_next_actions(job))
    return public.model_dump(mode="json", by_alias=True)


def _cas_job(student_id: str, workspace_id: str, lesson_id: str,
             job_id: str, expected_state_revision: int,
             mutate) -> sc.GenerationJob:
    try:
        return store.update_job(student_id, workspace_id, lesson_id,
                                job_id, mutate,
                                expected_state_revision=expected_state_revision)
    except store.CasConflictError as exc:
        raise ClassroomError("revision_conflict",
                             "state_revision 已变化，请刷新后重试") from exc


def _wake(student_id: str, workspace_id: str, lesson_id: str,
          job_id: str) -> None:
    if enqueue_job is not None:
        try:
            enqueue_job(student_id, workspace_id, lesson_id, job_id)
        except Exception:
            pass


def cancel_job(student_id: str, workspace_id: str, lesson_id: str,
               job_id: str, expected_state_revision: int) -> dict[str, Any]:
    """POST J/cancel：非终态 → cancel_requested（202）；终态原样返回。"""
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    if job.state in (sc.JobState.succeeded, sc.JobState.failed,
                     sc.JobState.cancelled):
        return job_snapshot(student_id, workspace_id, lesson_id, job_id)

    def mutate(target: sc.GenerationJob) -> None:
        target.cancel_requested = True

    _cas_job(student_id, workspace_id, lesson_id, job_id,
             expected_state_revision, mutate)
    return job_snapshot(student_id, workspace_id, lesson_id, job_id)


_ACTIVE_RETRY_STATES = (sc.JobState.failed, sc.JobState.cancelled,
                        sc.JobState.needs_input)


def retry_job(student_id: str, workspace_id: str, lesson_id: str,
              job_id: str, expected_state_revision: int) -> dict[str, Any]:
    """POST J/retry：显式重试（保留已校验阶段产物与目标 revision）。"""
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    if job.state not in _ACTIVE_RETRY_STATES:
        raise ClassroomError("content_invalid",
                             f"状态 {job.state.value} 不支持重试")

    def mutate(target: sc.GenerationJob) -> None:
        target.state = sc.JobState.queued
        target.phase = None
        target.cancel_requested = False
        target.next_retry_at = None
        target.recovery_count = 0
        target.attempts += 1

    _cas_job(student_id, workspace_id, lesson_id, job_id,
             expected_state_revision, mutate)
    _wake(student_id, workspace_id, lesson_id, job_id)
    return job_snapshot(student_id, workspace_id, lesson_id, job_id)


def continue_job(student_id: str, workspace_id: str, lesson_id: str,
                 job_id: str, expected_state_revision: int) -> dict[str, Any]:
    """POST J/continue：大纲审核后/补齐输入后继续（来源重查由阶段保证）。"""
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    if job.state not in (sc.JobState.awaiting_outline,
                         sc.JobState.needs_input):
        raise ClassroomError("content_invalid",
                             f"状态 {job.state.value} 不需要 continue")

    def mutate(target: sc.GenerationJob) -> None:
        target.state = sc.JobState.queued
        target.phase = None
        target.cancel_requested = False

    _cas_job(student_id, workspace_id, lesson_id, job_id,
             expected_state_revision, mutate)
    _wake(student_id, workspace_id, lesson_id, job_id)
    return job_snapshot(student_id, workspace_id, lesson_id, job_id)


def patch_outline(student_id: str, workspace_id: str, lesson_id: str,
                  job_id: str, request: sc.OutlinePatchRequest,
                  ) -> dict[str, Any]:
    """PATCH J/outline：仅 awaiting_outline/needs_input；替换大纲产物并
    使 author 之后的产物失效（重排/改页数会让逐页产物过期）。"""
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    if job.state not in (sc.JobState.awaiting_outline,
                         sc.JobState.needs_input):
        raise ClassroomError("content_invalid",
                             "只有等待大纲审核的任务可改大纲")
    outline = request.outline
    for index, page in enumerate(outline.pages, start=1):
        page.order = index
    payload = {"plan": outline.model_dump(mode="json", by_alias=True)}
    digest = store.canonical_hash(payload)
    stages = store.stages_dir(student_id, workspace_id, lesson_id, job_id)
    stages.mkdir(parents=True, exist_ok=True)
    store.stage_file(stages, "outline.json",
                     json.dumps(payload, ensure_ascii=False, indent=1))
    invalidate = [sc.JobPhase.author_slides, sc.JobPhase.checkpoints,
                  sc.JobPhase.review, sc.JobPhase.render, sc.JobPhase.publish]

    def mutate(target: sc.GenerationJob) -> None:
        target.artifacts["outline"] = digest
        for phase in invalidate:
            target.artifacts.pop(phase.value, None)
        target.stage_inputs.pop("outline", None)
        target.stage_inputs["outline"] = digest

    _cas_job(student_id, workspace_id, lesson_id, job_id,
             request.expected_state_revision, mutate)
    return job_snapshot(student_id, workspace_id, lesson_id, job_id)


# §4 用户可改字段白名单（PATCH J/brief；owner/lesson/target 不可改）
_BRIEF_PATCH_FIELDS = frozenset({
    "topic", "goals", "source_selection", "source_policy", "research",
    "duration_minutes", "page_plan", "language", "grade", "pedagogy_id",
    "theme_id", "image_density", "checkpoint_density", "voice_preferences",
    "custom_requirements",
})


def patch_brief(student_id: str, workspace_id: str, lesson_id: str,
                job_id: str, request: sc.BriefPatchRequest,
                ) -> dict[str, Any]:
    """PATCH J/brief：白名单整体替换；重算 hash 并失效受影响阶段。"""
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    if job.state not in (sc.JobState.failed, sc.JobState.needs_input,
                         sc.JobState.awaiting_outline):
        raise ClassroomError("content_invalid",
                             f"状态 {job.state.value} 不支持修改 brief")
    if job.operation is not None:
        raise ClassroomError("content_invalid",
                             "修订任务不支持改 brief，请重新发起修订")
    patch = request.brief_patch.model_dump(mode="json", by_alias=True)
    current = json.loads(
        store.job_brief_path(student_id, workspace_id, lesson_id, job_id)
        .read_text(encoding="utf-8"))
    updated = dict(current)
    selection_changed = False
    for key, value in patch.items():
        if key not in _BRIEF_PATCH_FIELDS:
            continue
        selection_changed = selection_changed or key == "source_selection"
        updated[key] = value
    brief = sc.LessonBrief.model_validate(updated)
    store.stage_file(
        store.job_root(student_id, workspace_id, lesson_id, job_id),
        "brief.json",
        json.dumps(brief.model_dump(mode="json", by_alias=True),
                   ensure_ascii=False, indent=1))
    new_hash = store.canonical_hash(
        brief.model_dump(mode="json", by_alias=True))
    if selection_changed:
        invalidate = [sc.JobPhase.resolve_sources, sc.JobPhase.research,
                      sc.JobPhase.outline, sc.JobPhase.visual_assets,
                      sc.JobPhase.author_slides, sc.JobPhase.checkpoints,
                      sc.JobPhase.review, sc.JobPhase.render,
                      sc.JobPhase.publish]
    else:
        invalidate = [sc.JobPhase.research, sc.JobPhase.outline,
                      sc.JobPhase.visual_assets, sc.JobPhase.author_slides,
                      sc.JobPhase.checkpoints, sc.JobPhase.review,
                      sc.JobPhase.render, sc.JobPhase.publish]

    def mutate(target: sc.GenerationJob) -> None:
        target.brief_hash = new_hash
        for phase in invalidate:
            target.artifacts.pop(phase.value, None)

    _cas_job(student_id, workspace_id, lesson_id, job_id,
             request.expected_state_revision, mutate)
    return job_snapshot(student_id, workspace_id, lesson_id, job_id)


async def job_events(student_id: str, workspace_id: str, lesson_id: str,
                     job_id: str, *, after_revision: int = 0,
                     heartbeat_seconds: float = 15.0):
    """GET J/events：SSE 事件流（§14.4）。

    轮询持久化 job（0.5s）：事件 id=state_revision；连接即发完整
    snapshot；终态发 terminal 后关闭；15s 无变化发 heartbeat 注释。
    SSE 断开不影响 worker——磁盘 job 是唯一事实源。
    """
    import asyncio

    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    last_sent = -1
    quiet = 0.0
    while True:
        job = store.load_job(student_id, workspace_id, lesson_id, job_id)
        if job is None:
            yield "event: terminal\ndata: {}\n\n"
            return
        terminal = job.state in (sc.JobState.succeeded, sc.JobState.failed,
                                 sc.JobState.cancelled)
        if job.state_revision != last_sent:
            event = sc.JobSnapshotEvent(
                job_id=job.job_id, state_revision=job.state_revision,
                state=job.state, phase=job.phase,
                completed_slides=_job_progress(
                    student_id, workspace_id, lesson_id,
                    job).completed_slides,
                total_slides=_job_progress(
                    student_id, workspace_id, lesson_id, job).total_slides,
                warnings=_job_warnings(student_id, workspace_id,
                                       lesson_id, job))
            data = event.model_dump_json(by_alias=True)
            name = "terminal" if terminal else "snapshot"
            yield (f"id: {job.state_revision}\nevent: {name}\n"
                   f"data: {data}\n\n")
            last_sent = job.state_revision
            quiet = 0.0
            if terminal:
                return
        await asyncio.sleep(0.5)
        quiet += 0.5
        if quiet >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            quiet = 0.0
