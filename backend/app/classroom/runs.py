"""课堂 run：创建/恢复、lease、进度 CAS、audio profile（plan.md §12/§14.2）。

职责：
- POST L/runs：resume_or_create 复用未终结 run（200 resumed）或从
  latest_ready_revision 冻结创建（201）；restart 显式开新 run 并把旧
  active/paused run 标记 ended。创建只初始化题目引用，不合成音频。
- lease：15s 心跳、45s TTL、takeover 递增 epoch；旧 epoch 心跳/写进度
  409（§5.4 双标签互斥）。GET 读侧不写文件；过期 active lease 投影为
  paused（§12.1）。
- 进度：load→CAS(state_revision)→merge→atomic write；client_event_id
  有界去重（最近 256）；段/offset 对固定 revision 校验；complete 只在
  抵达末页末段或显式文字阅读完成时成立（跳页到末页 → browsed 文案）。
- audio profile：暂停/段边界生效，version 递增，进度不变。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core import classroom_store as store
from ..schemas import classroom as sc
from ..voice.tts import service as tts_service
from . import limits
from .errors import ClassroomError
from .service import load_owned_workspace

LEASE_TTL_SECONDS = 45
LEASE_HEARTBEAT_SECONDS = 15
_EVENT_DEDUP_MAX = limits.PROGRESS_DEDUP_EVENTS   # 256


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_owned_run(student_id: str, workspace_id: str, lesson_id: str,
                    run_id: str) -> sc.ClassroomRun:
    run = store.load_run(student_id, workspace_id, lesson_id, run_id)
    if run is None:
        raise ClassroomError("source_not_found", "课堂 run 不存在")
    return run


def load_run_spec(owner_id: str, workspace_id: str,
                             lesson_id: str, revision: int) \
        -> sc.LessonRevision:
    spec = store.load_revision(owner_id, workspace_id, lesson_id, revision)
    lesson = store.load_lesson(owner_id, workspace_id, lesson_id)
    if spec is None or lesson is None \
            or revision not in lesson.published_revisions:
        raise ClassroomError("source_not_found", "课程版本不存在或未发布")
    return spec


def _checkpoint_refs_from_spec(spec: sc.LessonRevision) \
        -> list[sc.RunCheckpointRef]:
    refs: list[sc.RunCheckpointRef] = []
    for slide in sorted(spec.slides, key=lambda s: s.order):
        for block in slide.blocks:
            if getattr(block, "kind", "") == "checkpoint":
                refs.append(sc.RunCheckpointRef(
                    checkpoint_id=block.checkpoint_id,
                    slide_id=slide.slide_id, kind=sc.CheckpointKind.reflect))
    # RunCheckpointRef 上限 3（schema max_length）；超出按页序保留前 3
    return refs[:3]


# ---------------------------------------------------------------------------
# 创建 / 恢复（POST L/runs）
# ---------------------------------------------------------------------------

def create_run(student_id: str, workspace_id: str, lesson_id: str,
               request: sc.CreateRunRequest,
               *, idempotency_key: str) -> dict[str, Any]:
    from . import idempotency

    scope = "create_run"
    body = {"workspace_id": workspace_id, "lesson_id": lesson_id,
            "lesson_revision": request.lesson_revision,
            "mode": str(getattr(request.mode, "value", request.mode)),
            "voice_preferences": request.voice_preferences.model_dump(
                mode="json", by_alias=True)
            if request.voice_preferences is not None else None}
    body_hash = idempotency.body_hash_of(body)
    replayed = idempotency.lookup(student_id, scope, idempotency_key,
                                  body_hash)
    if replayed:
        return replayed

    load_owned_workspace(workspace_id, student_id)
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None:
        raise ClassroomError("source_not_found", "课程不存在")
    if lesson.lifecycle not in ("active", "archived"):
        raise ClassroomError("source_not_found", "课程不可用")
    revision = request.lesson_revision or lesson.latest_ready_revision
    spec = load_run_spec(student_id, workspace_id, lesson_id,
                                    revision)

    resumed_run: sc.ClassroomRun | None = None
    if request.mode == sc.RunStartMode.resume_or_create:
        for run in store.list_runs(student_id, workspace_id, lesson_id):
            if run.lesson_revision == revision and run.status in (
                    sc.RunStatus.active, sc.RunStatus.paused):
                if run.status == sc.RunStatus.active and _lease_expired(run):
                    _mark_run(run, sc.RunStatus.paused, clear_lease=True)
                resumed_run = run
                break

    if resumed_run is not None:
        payload = {"run_id": resumed_run.run_id,
                   "lesson_revision": resumed_run.lesson_revision,
                   "status": resumed_run.status.value, "resumed": True}
        idempotency.remember(student_id, scope, idempotency_key, body_hash,
                             payload)
        return payload

    # restart：旧未终结 run 一律 ended，开新 run
    for run in store.list_runs(student_id, workspace_id, lesson_id):
        if run.status in (sc.RunStatus.active, sc.RunStatus.paused):
            _mark_run(run, sc.RunStatus.ended, clear_lease=True)

    now = _utcnow()
    first_slide = min(spec.slides, key=lambda s: s.order)
    first_segment = first_slide.segments[0]
    prefs = request.voice_preferences or spec.brief.voice_preferences
    profile = tts_service.resolve_classroom_tts(prefs, spec.brief.language)
    run = sc.ClassroomRun(
        run_id=f"run_{secrets.token_hex(12)}",
        owner_id=student_id, workspace_id=workspace_id, lesson_id=lesson_id,
        lesson_revision=revision, content_hash=spec.content_hash,
        status=sc.RunStatus.paused,
        cursor=sc.Cursor(slide_id=first_slide.slide_id,
                         segment_id=first_segment.segment_id),
        cursor_slide_order=first_slide.order,
        checkpoint_refs=_checkpoint_refs_from_spec(spec),
        audio_profile=sc.AudioProfile(
            policy=sc.VoicePolicy(profile.policy),
            provider=profile.provider, voice_id=profile.voice_id,
            language=spec.brief.language,
            allow_local_fallback=profile.allow_local_fallback,
            playback_speed=prefs.playback_speed, version=1),
        created_at=now, updated_at=now)
    store.ensure_owner(student_id)
    store.save_run(run)
    payload = {"run_id": run.run_id, "lesson_revision": revision,
               "status": run.status.value, "resumed": False}
    idempotency.remember(student_id, scope, idempotency_key, body_hash,
                         payload)
    return payload


def _mark_run(run: sc.ClassroomRun, status: sc.RunStatus,
              *, clear_lease: bool = False) -> None:
    def mutate(r: sc.ClassroomRun) -> None:
        r.status = status
        r.ended_at = _utcnow() if status in (sc.RunStatus.completed,
                                             sc.RunStatus.ended) else None
        if clear_lease:
            r.lease = None

    store.update_run(run.owner_id, run.workspace_id, run.lesson_id,
                     run.run_id, mutate)
    run.status = status
    if clear_lease:
        run.lease = None


# ---------------------------------------------------------------------------
# 读侧投影（GET R：不写文件）
# ---------------------------------------------------------------------------

def _lease_expired(run: sc.ClassroomRun) -> bool:
    if run.lease is None:
        return True
    return run.lease.expires_at <= _utcnow()


def run_public(student_id: str, workspace_id: str, lesson_id: str,
               run_id: str) -> dict[str, Any]:
    run = load_owned_run(student_id, workspace_id, lesson_id, run_id)
    # §12.1：服务端读侧把过期 active lease 投影为 paused，不落盘
    status = run.status
    if status == sc.RunStatus.active and _lease_expired(run):
        status = sc.RunStatus.paused
    spec = load_run_spec(student_id, workspace_id, lesson_id,
                         run.lesson_revision)
    visited = len(set(run.visited_slides))
    public = sc.RunPublic(
        run_id=run.run_id, lesson_id=run.lesson_id,
        lesson_revision=run.lesson_revision, status=status,
        state_revision=run.state_revision, cursor=run.cursor,
        cursor_slide_order=slide_order_of(spec, run.cursor.slide_id),
        resume_anchor=run.resume_anchor, audio_profile=run.audio_profile,
        qa_session_id=run.qa_session_id, visited_slide_count=visited,
        completed_kind=run.completed_kind, created_at=run.created_at,
        updated_at=run.updated_at)
    payload = public.model_dump(mode="json", by_alias=True)
    payload["lease"] = {
        "held": run.lease is not None,
        "expired": _lease_expired(run),
        "client_id": run.lease.client_id if run.lease else None,
        "lease_epoch": run.lease.lease_epoch if run.lease else 0,
        "expires_at": run.lease.expires_at.isoformat()
        if run.lease else None,
    } if run.lease is not None else {"held": False, "expired": True,
                                     "client_id": None, "lease_epoch": 0,
                                     "expires_at": None}
    payload["cursor_index"] = _segment_index(spec, run.cursor.segment_id)
    payload["segment_total"] = sum(len(s.segments) for s in spec.slides)
    payload["tts_local_locked"] = run.tts_local_locked
    payload["tts_fallback_notified"] = run.tts_fallback_notified
    return payload


def _segment_index(spec: sc.LessonRevision, segment_id: str) -> int:
    order = 0
    for slide in sorted(spec.slides, key=lambda s: s.order):
        for seg in slide.segments:
            if seg.segment_id == segment_id:
                return order
            order += 1
    return 0


def slide_order_of(spec: sc.LessonRevision, slide_id: str) -> int:
    """游标页序（1-based）；找不到时保守返回 1（游标校验已挡坏 ID）。"""
    for slide in spec.slides:
        if slide.slide_id == slide_id:
            return slide.order
    return 1


# ---------------------------------------------------------------------------
# Lease（§5.4：15s 心跳、45s TTL、显式接管）
# ---------------------------------------------------------------------------

def _require_holdable(run: sc.ClassroomRun, client_id: str,
                      *, takeover: bool, epoch: int | None) -> None:
    if run.status in (sc.RunStatus.completed, sc.RunStatus.ended):
        raise ClassroomError("scope_changed", "课堂已结束，不能占用播放控制")
    lease = run.lease
    if lease is None or _lease_expired(run):
        return
    if lease.client_id == client_id:
        if epoch is not None and lease.lease_epoch != epoch:
            raise ClassroomError("lease_conflict", "lease epoch 已变更")
        return
    if not takeover:
        raise ClassroomError("lease_conflict", "另一设备正在播放本课堂")


def acquire_lease(student_id: str, workspace_id: str, lesson_id: str,
                  run_id: str, request: sc.LeaseAcquireRequest) \
        -> sc.LeaseResponse:
    run = load_owned_run(student_id, workspace_id, lesson_id, run_id)
    _require_holdable(run, request.client_id, takeover=request.takeover,
                      epoch=None)
    now = _utcnow()
    expires = now + timedelta(seconds=LEASE_TTL_SECONDS)

    def mutate(r: sc.ClassroomRun) -> None:
        same_client = r.lease is not None \
            and r.lease.client_id == request.client_id
        held_live_other = (r.lease is not None and r.lease.expires_at > now
                           and r.lease.client_id != request.client_id)
        if held_live_other and not request.takeover:
            raise ClassroomError("lease_conflict", "另一设备正在播放本课堂")
        base = r.lease.lease_epoch if r.lease else 0
        # 同客户端延续当前 epoch；新占用/接管递增，旧持有者立即失效
        epoch = base if same_client and not held_live_other else base + 1
        r.lease = sc.LeaseInfo(client_id=request.client_id,
                               lease_epoch=max(1, epoch),
                               expires_at=expires, heartbeat_at=now)
        if r.status == sc.RunStatus.active and held_live_other:
            r.status = sc.RunStatus.paused

    store.update_run(student_id, workspace_id, lesson_id, run_id, mutate,
                     bump_revision=False)
    reloaded = load_owned_run(student_id, workspace_id, lesson_id, run_id)
    assert reloaded.lease is not None
    return sc.LeaseResponse(lease_epoch=reloaded.lease.lease_epoch,
                            expires_at=reloaded.lease.expires_at)


def renew_lease(student_id: str, workspace_id: str, lesson_id: str,
                run_id: str, request: sc.LeaseRenewRequest) \
        -> sc.LeaseResponse:
    run = load_owned_run(student_id, workspace_id, lesson_id, run_id)
    if run.lease is None or run.lease.client_id != request.client_id \
            or run.lease.lease_epoch != request.lease_epoch:
        raise ClassroomError("lease_conflict", "lease 已失效或被接管")
    expires = _utcnow() + timedelta(seconds=LEASE_TTL_SECONDS)

    def mutate(r: sc.ClassroomRun) -> None:
        if r.lease is None or r.lease.client_id != request.client_id \
                or r.lease.lease_epoch != request.lease_epoch:
            raise ClassroomError("lease_conflict", "lease 已失效或被接管")
        r.lease = sc.LeaseInfo(client_id=request.client_id,
                               lease_epoch=request.lease_epoch,
                               expires_at=expires, heartbeat_at=_utcnow())

    store.update_run(student_id, workspace_id, lesson_id, run_id, mutate,
                     bump_revision=False)
    return sc.LeaseResponse(lease_epoch=request.lease_epoch,
                            expires_at=expires)


def release_lease(student_id: str, workspace_id: str, lesson_id: str,
                  run_id: str, client_id: str, lease_epoch: int) -> None:
    run = load_owned_run(student_id, workspace_id, lesson_id, run_id)

    def mutate(r: sc.ClassroomRun) -> None:
        # 旧 epoch 不影响新 lease（§14.2）：只释放仍属于自己的 lease
        if r.lease is None or r.lease.client_id != client_id \
                or r.lease.lease_epoch != lease_epoch:
            return
        r.lease = None
        if r.status == sc.RunStatus.active:
            r.status = sc.RunStatus.paused

    store.update_run(student_id, workspace_id, lesson_id, run_id, mutate,
                     bump_revision=False)


# ---------------------------------------------------------------------------
# 进度 CAS（§12.3）
# ---------------------------------------------------------------------------

def update_progress(student_id: str, workspace_id: str, lesson_id: str,
                    run_id: str, request: sc.ProgressRequest) \
        -> sc.ProgressResponse:
    run = load_owned_run(student_id, workspace_id, lesson_id, run_id)
    if run.lease is None or run.lease.lease_epoch != request.lease_epoch:
        raise ClassroomError("lease_conflict", "lease 已失效或被接管")
    # 已接受事件的确定性重放：直接返回当前接受状态，不重复写文件
    if request.client_event_id in run.progress_event_ids:
        return sc.ProgressResponse(state_revision=run.state_revision)
    spec = load_run_spec(student_id, workspace_id, lesson_id,
                         run.lesson_revision)
    cursor = request.cursor or run.cursor
    _validate_cursor(spec, cursor)

    def mutate(r: sc.ClassroomRun) -> None:
        if r.lease is None or r.lease.lease_epoch != request.lease_epoch:
            raise ClassroomError("lease_conflict", "lease 已失效或被接管")
        if r.status in (sc.RunStatus.completed, sc.RunStatus.ended):
            raise ClassroomError("scope_changed",
                                 "课堂已结束，不再接受播放事件")
        # client_event_id 去重：同事件重试直接返回当前接受状态（幂等）
        if request.client_event_id in r.progress_event_ids:
            return
        r.progress_event_ids.append(request.client_event_id)
        if len(r.progress_event_ids) > _EVENT_DEDUP_MAX:
            del r.progress_event_ids[:-_EVENT_DEDUP_MAX]
        r.cursor = cursor
        r.cursor_slide_order = slide_order_of(spec, cursor.slide_id)
        if request.action == sc.ProgressAction.progress:
            r.status = sc.RunStatus.active
        elif request.action == sc.ProgressAction.pause:
            r.status = sc.RunStatus.paused
        elif request.action == sc.ProgressAction.complete:
            r.status = sc.RunStatus.completed
            r.completed_kind = _complete_kind(spec, cursor)
            r.ended_at = _utcnow()
        elif request.action == sc.ProgressAction.end:
            r.status = sc.RunStatus.ended
            r.ended_at = _utcnow()
        for sid in request.played_segment_ids:
            if sid not in r.listened_segments:
                r.listened_segments.append(sid)
        del r.listened_segments[:-576]
        for slide_id in request.skipped_slide_ids:
            if slide_id not in r.skipped_slides:
                r.skipped_slides.append(slide_id)
        if cursor.slide_id not in r.visited_slides:
            r.visited_slides.append(cursor.slide_id)
        r.updated_at = _utcnow()

    from ..core.classroom_store import CasConflictError

    try:
        updated = store.update_run(
            student_id, workspace_id, lesson_id, run_id, mutate,
            expected_state_revision=request.expected_state_revision)
    except CasConflictError as exc:
        raise ClassroomError("revision_conflict",
                             "状态已更新，请刷新后重试") from exc
    return sc.ProgressResponse(state_revision=updated.state_revision)


def _validate_cursor(spec: sc.LessonRevision, cursor: sc.Cursor) -> None:
    for slide in spec.slides:
        if slide.slide_id != cursor.slide_id:
            continue
        if not any(seg.segment_id == cursor.segment_id
                   for seg in slide.segments):
            raise ClassroomError("content_invalid", "段不存在于该页")
        if cursor.chunk_index < 0 or cursor.offset_ms < 0:
            raise ClassroomError("content_invalid", "游标偏移非法")
        return
    raise ClassroomError("content_invalid", "游标页面不存在于固定版本")


def _complete_kind(spec: sc.LessonRevision,
                   cursor: sc.Cursor) -> str:
    last_slide = max(spec.slides, key=lambda s: s.order)
    last_segment = last_slide.segments[-1]
    if cursor.slide_id == last_slide.slide_id \
            and cursor.segment_id == last_segment.segment_id:
        return "listened"
    return "browsed"


# ---------------------------------------------------------------------------
# audio profile（PUT R/audio-profile：暂停/段边界生效，进度不变）
# ---------------------------------------------------------------------------

def update_audio_profile(student_id: str, workspace_id: str, lesson_id: str,
                         run_id: str, request: sc.AudioProfileRequest) \
        -> dict[str, Any]:
    run = load_owned_run(student_id, workspace_id, lesson_id, run_id)
    spec = load_run_spec(student_id, workspace_id, lesson_id,
                         run.lesson_revision)
    profile = tts_service.resolve_classroom_tts(
        request.voice_preferences, spec.brief.language)

    def mutate(r: sc.ClassroomRun) -> None:
        r.audio_profile = sc.AudioProfile(
            policy=sc.VoicePolicy(profile.policy),
            provider=profile.provider, voice_id=profile.voice_id,
            language=spec.brief.language,
            allow_local_fallback=profile.allow_local_fallback,
            playback_speed=request.voice_preferences.playback_speed,
            version=r.audio_profile.version + 1)
        r.audio_profile_version_note = "voice_switch"
        r.tts_local_locked = False   # 显式切换重置回退锁
        r.updated_at = _utcnow()

    from ..core.classroom_store import CasConflictError

    try:
        updated = store.update_run(
            student_id, workspace_id, lesson_id, run_id, mutate,
            expected_state_revision=request.expected_state_revision)
    except CasConflictError as exc:
        raise ClassroomError("revision_conflict",
                             "状态已更新，请刷新后重试") from exc
    return run_public(student_id, workspace_id, lesson_id, updated.run_id)
