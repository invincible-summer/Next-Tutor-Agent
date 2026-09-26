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
from . import exports
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

    recent_run = None
    from .runs import _lease_expired
    for run in store.list_runs(student_id, workspace_id, lesson_id):
        if run.status in (sc.RunStatus.active, sc.RunStatus.paused):
            status = run.status
            if status == sc.RunStatus.active and _lease_expired(run):
                status = sc.RunStatus.paused
            recent_run = sc.RunPublic(
                run_id=run.run_id, lesson_id=run.lesson_id,
                lesson_revision=run.lesson_revision, status=status,
                state_revision=run.state_revision, cursor=run.cursor,
                resume_anchor=run.resume_anchor,
                audio_profile=run.audio_profile,
                qa_session_id=run.qa_session_id,
                visited_slide_count=len(set(run.visited_slides)),
                completed_kind=run.completed_kind, created_at=run.created_at,
                updated_at=run.updated_at)
            break

    detail = sc.LessonDetailPublic(
        lesson_id=lesson.lesson_id, workspace_id=lesson.workspace_id,
        title=lesson.title, lifecycle=lesson.lifecycle,
        latest_ready_revision=lesson.latest_ready_revision,
        published_revisions=list(lesson.published_revisions),
        latest_job=latest_job_public, revision=revision_public,
        pending=pending, recent_run=recent_run)
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


_DRAFT_WATERMARK = (
    '<style>.cc-draft-mark{position:fixed;right:26px;bottom:22px;z-index:9999;'
    "padding:7px 16px;border-radius:999px;background:rgba(0,0,0,.5);"
    "color:#fff;font:600 13px/1 system-ui,-apple-system,'PingFang SC',"
    "'Microsoft YaHei',sans-serif;letter-spacing:.22em;pointer-events:none;"
    'opacity:.92}</style>'
    '<div class="cc-draft-mark">草稿 · DRAFT</div>')


def _draft_asset_bytes(student_id: str, workspace_id: str, lesson_id: str,
                       assets: list[sc.AssetRecord]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for asset in assets:
        if asset.status.value != "ready":
            continue
        ext = _EXT_BY_MIME.get(asset.mime)
        if ext is None:
            continue
        path = store.asset_file_path(student_id, workspace_id, lesson_id,
                                     asset.asset_id, ext)
        if path.is_file():
            try:
                out[asset.asset_id] = path.read_bytes()
            except OSError:
                continue
    return out


def job_preview(student_id: str, workspace_id: str, lesson_id: str,
                job_id: str, *, slide_id: str | None = None) -> dict[str, Any]:
    """GET J/preview：只读草稿预览（§4.2/§14.1）。

    slides 来自已落盘的阶段产物（author_slides 优先，review 兜底），
    不含答案；outline 供 awaiting_outline 审核；html 仅在 review 阶段
    产物完整（可安全编译整课）时按需编译并注入「草稿」水印，任何失败
    都降级为结构化预览。只读，不写盘。
    """
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    _lesson, job = _load_owned_job(student_id, workspace_id, lesson_id,
                                   job_id)
    stages = store.stages_dir(student_id, workspace_id, lesson_id, job.job_id)

    def _stage(name: str) -> dict[str, Any] | None:
        path = stages / f"{name}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    author = _stage("author_slides")
    review = _stage("review")
    outline_raw = _stage("outline")
    outline = None
    if outline_raw is not None:
        try:
            outline = sc.OutlinePlan.model_validate(outline_raw["plan"])
        except (KeyError, ValueError):
            outline = None

    raw_slides = (author or {}).get("slides") or (review or {}).get("slides") or []
    # author_slides 阶段载荷是 {slide: {...}} 包装；review 阶段是直接 SlideSpec。
    if author is not None:
        raw_slides = [s.get("slide", s) for s in raw_slides]
    slides = [sc.SlideSpec.model_validate(s) for s in raw_slides]
    if slide_id is not None:
        slides = [s for s in slides if s.slide_id == slide_id]

    html: str | None = None
    if review is not None and review.get("slides"):
        try:
            brief_path = store.job_brief_path(student_id, workspace_id,
                                              lesson_id, job.job_id)
            brief = sc.LessonBrief.model_validate(
                json.loads(brief_path.read_text(encoding="utf-8")))
            records = [sc.SourceRecord.model_validate(r) for r in
                       (_stage("resolve_sources") or {}).get("records", [])
                       + (_stage("research") or {}).get("records", [])]
            assets = [sc.AssetRecord.model_validate(a) for a in
                      (_stage("visual_assets") or {}).get("records", [])]
            from .render import assets as render_assets
            draft = sc.LessonRevision(
                revision=job.target_revision, brief=brief,
                source_snapshot=records,
                slides=[sc.SlideSpec.model_validate(s)
                        for s in review["slides"]],
                # compile_html 只取模板的 prompt/kind 渲染检查点占位，
                # verified_question_template（含答案）绝不进入 HTML。
                checkpoint_templates=[
                    sc.CheckpointTemplate.model_validate(t)
                    for t in review.get("templates", [])],
                objectives=[sc.Objective.model_validate(o) for o in
                            review.get("objectives", [])],
                glossary=[sc.GlossaryEntry.model_validate(g) for g in
                          (author or {}).get("glossary", [])],
                assets=assets,
                renderer_version=render_assets.RUNTIME_VERSION,
                content_hash="0" * 64,  # 草稿占位：预览不参与 CAS/发布
                created_at=store.utcnow())
            from .render.compiler import compile_html
            compiled = compile_html(
                draft, mode="presentation",
                asset_bytes=_draft_asset_bytes(
                    student_id, workspace_id, lesson_id, assets))
            if "</body>" in compiled:
                html = compiled.replace(
                    "</body>", f"{_DRAFT_WATERMARK}</body>", 1)
        except (OSError, ValueError, RuntimeError, KeyError):
            html = None  # 渲染器缺失/数据不完整 → 结构化预览

    return sc.JobPreviewResponse(
        state=job.state, phase=job.phase, slides=slides, outline=outline,
        html=html).model_dump(mode="json", by_alias=True)


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


# ---------------------------------------------------------------------------
# E04：版本列表 / 换图搜索 / 自有图片 / 导出（§14.1）
# ---------------------------------------------------------------------------

def _load_owned_lesson(student_id: str, workspace_id: str,
                       lesson_id: str) -> sc.Lesson:
    load_owned_workspace(workspace_id, student_id)
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None or lesson.owner_id != student_id or \
            lesson.workspace_id != workspace_id or \
            lesson.lifecycle != sc.LessonLifecycle.active:
        raise ClassroomError("source_not_found", "课程不存在")
    return lesson


def list_revisions(student_id: str, workspace_id: str, lesson_id: str,
                   *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """GET L/revisions：版本号 + 时间 + 可用性；不附全量 HTML。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    lesson = _load_owned_lesson(student_id, workspace_id, lesson_id)
    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    items: list[sc.RevisionListItem] = []
    for rev in sorted(lesson.published_revisions, reverse=True):
        spec = store.load_revision(student_id, workspace_id, lesson_id, rev)
        items.append(sc.RevisionListItem(
            revision=rev,
            created_at=spec.created_at if spec else store.utcnow(),
            available=spec is not None))
    total = len(items)
    start = (page - 1) * page_size
    payload = sc.RevisionListResponse(
        items=items[start:start + page_size], total=total, page=page,
        page_size=page_size)
    return payload.model_dump(mode="json", by_alias=True)


async def image_search(student_id: str, workspace_id: str,
                       request: sc.ImageSearchRequest,
                       *, idempotency_key: str) -> dict[str, Any]:
    """POST W/image-search：只服务换图，候选经短期登记后一次性消费。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    _load_owned_lesson(student_id, workspace_id, request.lesson_id)

    from .media.service import build_image_providers, register_candidates
    providers = build_image_providers()
    if not providers:
        raise ClassroomError("image_unavailable",
                             "未配置可用图库服务（需管理员凭证）")
    intent = request.visual_intent
    queries = list(intent.query_terms)
    if not queries:
        # 兜底：用意图描述词（≤5 个）构造查询，保持与管线同源语义。
        queries = [w for w in intent.purpose.replace("，", " ").split()
                   if len(w) >= 2][:5]
    if not queries:
        raise ClassroomError("content_invalid", "visual_intent 缺少可用查询词")

    from .media.base import ImageSearchBudget
    budget = ImageSearchBudget()
    from .media.service import ImageSearchService
    service = ImageSearchService(providers)
    provider_filter = request.provider.value if request.provider else None
    if provider_filter:
        service = ImageSearchService(
            [p for p in providers if p.provider == provider_filter]
            or providers)
    candidates = await service.search(
        queries, owner=student_id,
        orientation=intent.orientation or "landscape",
        locale="zh-CN", budget=budget)
    ids = register_candidates(student_id, candidates)
    return {"candidates": [
        {"candidate_id": cid,
         "provider": c.provider,
         "provider_asset_id": c.provider_asset_id,
         "thumbnail_url": c.thumb_url,
         "width": c.width, "height": c.height,
         "creator": c.creator, "source_url": c.page_url,
         "license_url": c.license_url}
        for cid, c in zip(ids, candidates)]}


def _asset_public(record: sc.AssetRecord) -> sc.AssetPublic:
    return sc.AssetPublic(
        asset_id=record.asset_id, mime=record.mime, width=record.width,
        height=record.height, alt=record.alt, caption=record.caption,
        role=record.role, status=record.status,
        provider=record.provenance.provider,
        creator=record.provenance.creator,
        source_url=record.provenance.source_url,
        license_url=record.provenance.license_url)


def upload_asset(student_id: str, workspace_id: str, lesson_id: str,
                 filename: str, raw: bytes) -> dict[str, Any]:
    """POST L/assets：自有图片清洗后入库（AssetRecord 落盘，供
    replace_image{asset_id} 引用）；不接受 SVG/HTML。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    _load_owned_lesson(student_id, workspace_id, lesson_id)
    if len(raw) > 12 * 1024 * 1024:
        raise ClassroomError("content_invalid", "图片过大（≤12MB）")
    lowered = filename.lower()
    if lowered.endswith((".svg", ".html", ".htm")):
        raise ClassroomError("content_invalid", "不支持 SVG/HTML 图片")

    from .media.download import sanitize_image
    try:
        processed = sanitize_image(raw)
    except ClassroomError as exc:
        raise ClassroomError("image_unavailable",
                             f"图片清洗失败：{exc.message}") from exc

    asset_id = store.new_id("ast")
    ext = "png" if processed.mime == "image/png" else "webp"
    path = store.asset_file_path(student_id, workspace_id, lesson_id,
                                 asset_id, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(path, processed.data)
    record = sc.AssetRecord(
        asset_id=asset_id, sha256=processed.sha256, mime=processed.mime,
        width=processed.width, height=processed.height,
        provenance=sc.AssetProvenance(
            provider=sc.AssetProvider.upload,
            provider_asset_id="",
            source_url="", creator=filename[:120], creator_url="",
            license_url="", fetched_at=store.utcnow()),
        alt=filename[:120] or "用户上传图片", caption="", role=sc.AssetRole.scene,
        bytes=len(processed.data), status=sc.AssetStatus.ready)
    store.write_json(store.asset_meta_path(student_id, workspace_id,
                                           lesson_id, asset_id),
                     record.model_dump(mode="json", by_alias=True))
    asset = _asset_public(record)
    return asset.model_dump(mode="json", by_alias=True)


def load_asset_record(student_id: str, workspace_id: str, lesson_id: str,
                      asset_id: str) -> sc.AssetRecord | None:
    """上传资产读取（owner 作用域内路径已隔离）。"""
    data = store.read_json(store.asset_meta_path(student_id, workspace_id,
                                                 lesson_id, asset_id))
    if data is None:
        return None
    try:
        return sc.AssetRecord.model_validate(data)
    except ValueError:
        return None


def asset_content(student_id: str, workspace_id: str, lesson_id: str,
                  asset_id: str) -> tuple[bytes, str]:
    """GET L/assets/{id}/content：图片 bytes（归属链校验）。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    lesson = _load_owned_lesson(student_id, workspace_id, lesson_id)
    record = load_asset_record(student_id, workspace_id, lesson_id, asset_id)
    if record is None:
        # 生成管线下载的图直接挂在已发布 revision 的资产表里
        for rev in lesson.published_revisions:
            spec = store.load_revision(student_id, workspace_id, lesson_id,
                                       rev)
            if spec and asset_id in {a.asset_id for a in spec.assets}:
                record = next(a for a in spec.assets
                              if a.asset_id == asset_id)
                break
    if record is None:
        raise ClassroomError("source_not_found", "图片不存在")
    ext = _EXT_BY_MIME.get(record.mime, "webp")
    path = store.asset_file_path(student_id, workspace_id, lesson_id,
                                 asset_id, ext)
    if not path.is_file():
        raise ClassroomError("source_not_found", "图片文件缺失",
                             retryable=False)
    return path.read_bytes(), record.mime


_EXPORT_TTL_SECONDS = 24 * 3600


def create_export(student_id: str, workspace_id: str, lesson_id: str,
                  revision: int, fmt: str, *, idempotency_key: str) -> dict[str, Any]:
    """POST L/exports：同步构建（HTML ZIP / 讲稿 Markdown），24h 过期。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    if fmt not in ("html_zip", "notes_md"):
        raise ClassroomError("content_invalid", "不支持的导出格式")
    spec = load_published_revision(student_id, workspace_id, lesson_id,
                                   revision)
    try:
        if fmt == "html_zip":
            data = exports.build_export_zip(
                spec,
                read_bytes=lambda aid, ext: _read_asset_bytes(
                    student_id, workspace_id, lesson_id, aid, ext))
            media_type = "application/zip"
        else:
            data = exports.build_notes_markdown(spec)
            media_type = "text/markdown; charset=utf-8"
    except RuntimeError as exc:
        raise ClassroomError("renderer_unavailable",
                             "课件渲染器不可用") from exc

    export_id = store.new_id("job")
    zip_path = store.export_zip_path(student_id, workspace_id, lesson_id,
                                     export_id)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(zip_path, data)
    meta = {"export_id": export_id, "format": fmt, "revision": revision,
            "media_type": media_type, "bytes": len(data),
            "expires_at": (store.utcnow().timestamp()
                           + _EXPORT_TTL_SECONDS)}
    store.write_json(store.export_meta_path(student_id, workspace_id,
                                            lesson_id, export_id), meta)
    return {"export_id": export_id, "format": fmt, "revision": revision,
            "content_url": f"/api/v1/workspaces/{workspace_id}/classroom/"
                           f"lessons/{lesson_id}/exports/{export_id}/content",
            "expires_at": meta["expires_at"]}


def _read_asset_bytes(student_id: str, workspace_id: str, lesson_id: str,
                      asset_id: str, ext: str) -> bytes | None:
    path = store.asset_file_path(student_id, workspace_id, lesson_id,
                                 asset_id, ext)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def export_content(student_id: str, workspace_id: str, lesson_id: str,
                   export_id: str) -> tuple[bytes, dict[str, Any]]:
    """GET L/exports/{id}/content：过期 410（可重建）。"""
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    _load_owned_lesson(student_id, workspace_id, lesson_id)
    meta = store.read_json(store.export_meta_path(student_id, workspace_id,
                                                  lesson_id, export_id))
    if meta is None:
        raise ClassroomError("source_not_found", "导出不存在")
    if float(meta.get("expires_at", 0)) < store.utcnow().timestamp():
        raise ClassroomError("export_expired", "导出已过期，请重新生成",
                             retryable=True)
    path = store.export_zip_path(student_id, workspace_id, lesson_id,
                                 export_id)
    if not path.is_file():
        raise ClassroomError("source_not_found", "导出文件缺失",
                             retryable=False)
    return path.read_bytes(), meta


# ---------------------------------------------------------------------------
# 语音试听（plan.md §14.1 POST W/voice-preview；阶段 F）
# ---------------------------------------------------------------------------

def _preview_rate_allow(student_id: str) -> bool:
    """TTS_PREVIEW_PER_HOUR 滑窗限频（owner.json quota.tts.preview_times）。"""
    import time as _time

    from . import limits
    now = _time.time()
    record = store.read_json(store.owner_meta_path(student_id)) or {}
    tts = (record.get("quota") or {}).get("tts")
    times = tts.get("preview_times") if isinstance(tts, dict) else None
    times = [t for t in (times or []) if isinstance(t, (int, float))]
    window = [t for t in times if now - t < 3600]
    if len(window) >= limits.TTS_PREVIEW_PER_HOUR:
        return False

    def mutate(entry: dict) -> None:
        kept = [t for t in entry.get("preview_times") or []
                if isinstance(t, (int, float)) and now - t < 3600]
        kept.append(now)
        entry["preview_times"] = kept[-limits.TTS_PREVIEW_PER_HOUR * 2:]

    with store.file_lock(store.owner_meta_path(student_id)):
        record2 = store.read_json(store.owner_meta_path(student_id)) or {}
        record2.setdefault("quota", {})
        tts2 = record2["quota"].get("tts")
        tts2 = tts2 if isinstance(tts2, dict) else {}
        mutate(tts2)
        record2["quota"]["tts"] = tts2
        store.write_json(store.owner_meta_path(student_id), record2)
    return True


async def voice_preview(student_id: str, workspace_id: str,
                        request: sc.VoicePreviewRequest,
                        *, idempotency_key: str) -> dict[str, Any]:
    """固定试听句的 WAV（非用户私有文本）；幂等重放不重复合成。"""
    import asyncio

    from . import limits
    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    load_owned_workspace(workspace_id, student_id)
    _ensure_classroom_writable(student_id)

    scope = "voice_preview"
    body = {"workspace_id": workspace_id,
            "language": str(getattr(request.language, "value",
                                    request.language)),
            "voice_preferences": request.voice_preferences.model_dump(
                mode="json", by_alias=True)}
    body_hash = idempotency.body_hash_of(body)
    replayed = idempotency.lookup(student_id, scope, idempotency_key,
                                  body_hash)
    if replayed:
        return replayed
    if not _preview_rate_allow(student_id):
        raise ClassroomError("quota_exceeded", "试听次数过多，请稍后再试")

    from . import audio as classroom_audio
    engine = classroom_audio.get_audio_engine()
    try:
        result = await asyncio.wait_for(
            engine.voice_preview(student_id, request.language,
                                 request.voice_preferences),
            timeout=limits.VOICE_PREVIEW_DEADLINE_SECONDS + 5.0)
    except asyncio.TimeoutError:
        raise ClassroomError("tts_unavailable", "试听超时，请稍后重试",
                             retryable=True)
    payload = {
        "clip_id": result["clip_id"],
        "content_url": (f"/api/v1/workspaces/{workspace_id}/classroom/"
                        f"voice-previews/{result[clip_id]}/content"),
    }
    idempotency.remember(student_id, scope, idempotency_key, body_hash,
                         payload)
    return payload


def voice_preview_content(student_id: str, workspace_id: str,
                          clip_id: str) -> bytes:
    """试听 WAV 内容（owner 隔离；GET 不合成）。"""
    load_owned_workspace(workspace_id, student_id)
    from . import audio as classroom_audio
    return classroom_audio.get_audio_engine().voice_preview_content(
        student_id, clip_id)
