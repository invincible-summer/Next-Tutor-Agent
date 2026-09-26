"""课堂模式 HTTP API（plan.md §14）。

route 只做身份、schema、状态码投影；复杂工作由 app.classroom.service 等
执行。所有 ID 均验证完整 owner→workspace→lesson→revision/run/job 链。
功能关闭时：能力端点仍可读，生成类端点返回 classroom_disabled。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from app.identity.deps import resolve_student_id

from app.classroom import capabilities as caps
from app.classroom import service as classroom_service
from app.classroom import templates as templates_mod
from app.classroom.errors import ClassroomError, error_response
from app.schemas.classroom import (
    ClassroomCapabilities,
    ClassroomTemplates,
    CreateLessonRequest,
    CreateLessonResponse,
    LessonDetailPublic,
    LessonListResponse,
)
from app.schemas import classroom as sc

router = APIRouter(tags=["classroom"])


def _request_id() -> str:
    import uuid

    return "req_" + uuid.uuid4().hex[:16]


def classroom_exception_handler(request: Request,
                                exc: ClassroomError) -> JSONResponse:
    """统一 §14.3 错误 envelope（main.create_app 注册）。"""
    return error_response(exc, _request_id())


def require_enabled(student_id: str) -> None:
    allowed, _reason = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")


@router.get("/classroom/capabilities", response_model=ClassroomCapabilities)
def get_capabilities(
        student_id: str = Depends(resolve_student_id)) -> ClassroomCapabilities:
    return caps.build_capabilities(student_id)


@router.get("/classroom/templates", response_model=ClassroomTemplates)
def get_templates(
        student_id: str = Depends(resolve_student_id)) -> ClassroomTemplates:
    require_enabled(student_id)
    return templates_mod.templates_public()


@router.get("/workspaces/{workspace_id}/classroom/lessons",
            response_model=LessonListResponse)
def list_lessons(workspace_id: str, page: int = 1, page_size: int = 5,
                 status: str | None = None,
                 student_id: str = Depends(resolve_student_id)):
    return classroom_service.list_lessons(
        student_id, workspace_id, page=page, page_size=page_size,
        status=status)


@router.post("/workspaces/{workspace_id}/classroom/lessons",
             response_model=CreateLessonResponse, status_code=202)
def create_lesson(workspace_id: str, request: CreateLessonRequest,
                  idempotency_key: str | None = Header(
                      default=None, alias="Idempotency-Key"),
                  student_id: str = Depends(resolve_student_id)):
    from app.classroom.errors import require_idempotency_key

    key = require_idempotency_key(idempotency_key)
    return CreateLessonResponse(**classroom_service.create_lesson(
        student_id, workspace_id, request, idempotency_key=key))


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/revisions",
             response_model=sc.CreateRevisionResponse, status_code=202)
def create_revision(workspace_id: str, lesson_id: str,
                    request: sc.CreateRevisionRequest,
                    idempotency_key: str | None = Header(
                        default=None, alias="Idempotency-Key"),
                    student_id: str = Depends(resolve_student_id)):
    from app.classroom.errors import require_idempotency_key
    from app.classroom import revisions as classroom_revisions

    key = require_idempotency_key(idempotency_key)
    return sc.CreateRevisionResponse(**classroom_revisions.create_revision_job(
        student_id, workspace_id, lesson_id, request, idempotency_key=key))


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}",
            response_model=LessonDetailPublic)
def get_lesson(workspace_id: str, lesson_id: str,
               revision: int | None = None,
               student_id: str = Depends(resolve_student_id)):
    return LessonDetailPublic(**classroom_service.lesson_detail(
        student_id, workspace_id, lesson_id, revision=revision))


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/jobs/{job_id}", response_model=sc.JobPublic)
def get_job(workspace_id: str, lesson_id: str, job_id: str,
            student_id: str = Depends(resolve_student_id)):
    return sc.JobPublic(**classroom_service.job_snapshot(
        student_id, workspace_id, lesson_id, job_id))


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/jobs/{job_id}/preview", response_model=sc.JobPreviewResponse)
def get_job_preview(workspace_id: str, lesson_id: str, job_id: str,
                    slide_id: str | None = None,
                    student_id: str = Depends(resolve_student_id)):
    return sc.JobPreviewResponse(**classroom_service.job_preview(
        student_id, workspace_id, lesson_id, job_id, slide_id=slide_id))


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/jobs/{job_id}/events")
async def job_events(workspace_id: str, lesson_id: str, job_id: str,
                     after_revision: int = 0,
                     student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import StreamingResponse

    generator = classroom_service.job_events(
        student_id, workspace_id, lesson_id, job_id,
        after_revision=after_revision)
    return StreamingResponse(
        generator, media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/jobs/{job_id}/cancel", response_model=sc.JobPublic)
def cancel_job(workspace_id: str, lesson_id: str, job_id: str,
               request: sc.CancelJobRequest,
               student_id: str = Depends(resolve_student_id)):
    return sc.JobPublic(**classroom_service.cancel_job(
        student_id, workspace_id, lesson_id, job_id,
        request.expected_state_revision))


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/jobs/{job_id}/retry", response_model=sc.JobPublic)
def retry_job(workspace_id: str, lesson_id: str, job_id: str,
              request: sc.RetryJobRequest,
              idempotency_key: str | None = Header(
                  default=None, alias="Idempotency-Key"),
              student_id: str = Depends(resolve_student_id)):
    return sc.JobPublic(**classroom_service.retry_job(
        student_id, workspace_id, lesson_id, job_id,
        request.expected_state_revision))


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/jobs/{job_id}/continue", response_model=sc.JobPublic)
def continue_job(workspace_id: str, lesson_id: str, job_id: str,
                 request: sc.ContinueJobRequest,
                 student_id: str = Depends(resolve_student_id)):
    return sc.JobPublic(**classroom_service.continue_job(
        student_id, workspace_id, lesson_id, job_id,
        request.expected_state_revision))


@router.patch("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
              "/jobs/{job_id}/outline", response_model=sc.JobPublic)
def patch_outline(workspace_id: str, lesson_id: str, job_id: str,
                  request: sc.OutlinePatchRequest,
                  student_id: str = Depends(resolve_student_id)):
    return sc.JobPublic(**classroom_service.patch_outline(
        student_id, workspace_id, lesson_id, job_id, request))


@router.patch("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
              "/jobs/{job_id}/brief", response_model=sc.JobPublic)
def patch_brief(workspace_id: str, lesson_id: str, job_id: str,
                request: sc.BriefPatchRequest,
                student_id: str = Depends(resolve_student_id)):
    return sc.JobPublic(**classroom_service.patch_brief(
        student_id, workspace_id, lesson_id, job_id, request))


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/revisions/{revision}/frame")
def get_revision_frame(workspace_id: str, lesson_id: str, revision: int,
                       mode: str = "presentation",
                       student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import HTMLResponse

    html = classroom_service.get_revision_frame(
        student_id, workspace_id, lesson_id, revision, mode=mode)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "private, no-store",
                 "Content-Disposition": "inline"},)


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/revisions", response_model=sc.RevisionListResponse)
def list_revisions(workspace_id: str, lesson_id: str, page: int = 1,
                   page_size: int = 20,
                   student_id: str = Depends(resolve_student_id)):
    return sc.RevisionListResponse(**classroom_service.list_revisions(
        student_id, workspace_id, lesson_id, page=page, page_size=page_size))


@router.post("/workspaces/{workspace_id}/classroom/image-search",
             response_model=sc.ImageSearchResponse, status_code=202)
async def image_search(workspace_id: str, request: sc.ImageSearchRequest,
                       idempotency_key: str | None = Header(
                           default=None, alias="Idempotency-Key"),
                       student_id: str = Depends(resolve_student_id)):
    from app.classroom.errors import require_idempotency_key

    key = require_idempotency_key(idempotency_key)
    return sc.ImageSearchResponse(**await classroom_service.image_search(
        student_id, workspace_id, request, idempotency_key=key))


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/assets", response_model=sc.AssetUploadResponse)
async def upload_asset(workspace_id: str, lesson_id: str, file: UploadFile,
                       student_id: str = Depends(resolve_student_id)):
    raw = await file.read()
    asset = classroom_service.upload_asset(
        student_id, workspace_id, lesson_id, file.filename or "upload", raw)
    return sc.AssetUploadResponse(asset=asset)


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/assets/{asset_id}/content")
def asset_content(workspace_id: str, lesson_id: str, asset_id: str,
                  student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import Response

    data, mime = classroom_service.asset_content(
        student_id, workspace_id, lesson_id, asset_id)
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/exports", status_code=202)
def create_export(workspace_id: str, lesson_id: str,
                  request: sc.ExportCreateRequest,
                  idempotency_key: str | None = Header(
                      default=None, alias="Idempotency-Key"),
                  student_id: str = Depends(resolve_student_id)):
    from app.classroom.errors import require_idempotency_key

    key = require_idempotency_key(idempotency_key)
    return classroom_service.create_export(
        student_id, workspace_id, lesson_id, request.revision,
        request.format, idempotency_key=key)


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/exports/{export_id}/content")
def export_content(workspace_id: str, lesson_id: str, export_id: str,
                   student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import Response

    data, meta = classroom_service.export_content(
        student_id, workspace_id, lesson_id, export_id)
    filename = (f"lesson-{lesson_id}-r{meta.get('revision')}.zip"
                if meta.get("format") == "html_zip"
                else f"lesson-{lesson_id}-r{meta.get('revision')}-notes.md")
    return Response(
        content=data, media_type=str(meta.get("media_type") or "application/zip"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "private, no-store"})


@router.post("/workspaces/{workspace_id}/classroom/voice-preview",
             response_model=sc.VoicePreviewResponse)
async def voice_preview(workspace_id: str,
                        request: sc.VoicePreviewRequest,
                        idempotency_key: str | None = Header(
                            default=None, alias="Idempotency-Key"),
                        student_id: str = Depends(resolve_student_id)):
    """固定试听句 WAV（§14.1）：不接任意 text；幂等重放不重复合成。"""
    from app.classroom.errors import require_idempotency_key

    key = require_idempotency_key(idempotency_key)
    return sc.VoicePreviewResponse(**await classroom_service.voice_preview(
        student_id, workspace_id, request, idempotency_key=key))


@router.get("/workspaces/{workspace_id}/classroom/voice-previews"
            "/{clip_id}/content")
def voice_preview_content(workspace_id: str, clip_id: str,
                          student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import Response

    data = classroom_service.voice_preview_content(
        student_id, workspace_id, clip_id)
    return Response(content=data, media_type="audio/wav",
                    headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------------------
# 课堂 run、lease、进度与音频（plan.md §14.2，阶段 G）
# ---------------------------------------------------------------------------

def _run_base(workspace_id: str, lesson_id: str, run_id: str) -> str:
    return (f"/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            f"/runs/{run_id}")


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}/runs")
def create_run(workspace_id: str, lesson_id: str,
               request: sc.CreateRunRequest,
               idempotency_key: str | None = Header(
                   default=None, alias="Idempotency-Key"),
               student_id: str = Depends(resolve_student_id)):
    """POST L/runs：201 新 run / 200 复用未终结 run；只初始化题目，不合成。"""
    from app.classroom.errors import require_idempotency_key
    from app.classroom import runs as runs_mod

    require_enabled(student_id)
    key = require_idempotency_key(idempotency_key)
    payload = runs_mod.create_run(student_id, workspace_id, lesson_id,
                                  request, idempotency_key=key)
    body = sc.RunCreateResponse(**payload).model_dump(mode="json",
                                                      by_alias=True)
    return JSONResponse(status_code=200 if payload.get("resumed") else 201,
                        content=body)


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}")
def get_run(workspace_id: str, lesson_id: str, run_id: str,
            student_id: str = Depends(resolve_student_id)):
    from app.classroom import runs as runs_mod

    return runs_mod.run_public(student_id, workspace_id, lesson_id, run_id)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/lease", response_model=sc.LeaseResponse)
def acquire_lease(workspace_id: str, lesson_id: str, run_id: str,
                  request: sc.LeaseAcquireRequest,
                  student_id: str = Depends(resolve_student_id)):
    from app.classroom import runs as runs_mod

    return runs_mod.acquire_lease(student_id, workspace_id, lesson_id,
                                  run_id, request)


@router.put("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/lease", response_model=sc.LeaseResponse)
def renew_lease(workspace_id: str, lesson_id: str, run_id: str,
                request: sc.LeaseRenewRequest,
                student_id: str = Depends(resolve_student_id)):
    from app.classroom import runs as runs_mod

    return runs_mod.renew_lease(student_id, workspace_id, lesson_id, run_id,
                                request)


@router.delete("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
               "/runs/{run_id}/lease")
def release_lease(workspace_id: str, lesson_id: str, run_id: str,
                  request: sc.LeaseRenewRequest,
                  student_id: str = Depends(resolve_student_id)):
    from app.classroom import runs as runs_mod

    runs_mod.release_lease(student_id, workspace_id, lesson_id, run_id,
                           request.client_id, request.lease_epoch)
    return {"status": "released"}


@router.put("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/progress", response_model=sc.ProgressResponse)
def update_progress(workspace_id: str, lesson_id: str, run_id: str,
                    request: sc.ProgressRequest,
                    student_id: str = Depends(resolve_student_id)):
    from app.classroom import runs as runs_mod

    return runs_mod.update_progress(student_id, workspace_id, lesson_id,
                                    run_id, request)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/qa-session")
def create_qa_session(workspace_id: str, lesson_id: str, run_id: str,
                      idempotency_key: str | None = Header(
                          default=None, alias="Idempotency-Key"),
                      student_id: str = Depends(resolve_student_id)):
    """POST R/qa-session：幂等创建/返回答疑 session（§14.2；§12.4）。

    只在首条课堂提问时调用；结束的 run 拒绝（scope_changed）。
    """
    from app.classroom import runs as runs_mod
    from app.classroom import idempotency
    from app.classroom.errors import require_idempotency_key

    require_enabled(student_id)
    key = require_idempotency_key(idempotency_key)
    scope = "qa_session"
    body = {"workspace_id": workspace_id, "lesson_id": lesson_id,
            "run_id": run_id}
    body_hash = idempotency.body_hash_of(body)
    replayed = idempotency.lookup(student_id, scope, key, body_hash)
    if replayed:
        return JSONResponse(status_code=200, content=replayed)
    session_id, created = runs_mod.ensure_run_qa_session(
        student_id, workspace_id, lesson_id, run_id)
    payload = {"session_id": session_id}
    idempotency.remember(student_id, scope, key, body_hash, payload)
    return JSONResponse(status_code=201 if created else 200, content=payload)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/notes", status_code=201,
             response_model=sc.RunNoteResponse)
def add_run_note(workspace_id: str, lesson_id: str, run_id: str,
                 request: sc.RunNoteRequest,
                 student_id: str = Depends(resolve_student_id)):
    """POST R/notes：本 run 批注（§14.2/§12.6）；不写个人长期评价。"""
    from app.classroom import runs as runs_mod

    require_enabled(student_id)
    annotation_id = runs_mod.add_run_annotation(
        student_id, workspace_id, lesson_id, run_id, request)
    return sc.RunNoteResponse(annotation_id=annotation_id)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/save-note")
def save_run_note(workspace_id: str, lesson_id: str, run_id: str,
                  request: sc.SaveNoteRequest,
                  idempotency_key: str | None = Header(
                      default=None, alias="Idempotency-Key"),
                  student_id: str = Depends(resolve_student_id)):
    """POST R/save-note：确定性汇总保存到笔记中心（§14.2/§16.3）。

    相同 Idempotency-Key 返回同一 note_id（201 新建 / 200 已存在）。
    """
    from app.classroom import runs as runs_mod
    from app.classroom.errors import require_idempotency_key

    require_enabled(student_id)
    key = require_idempotency_key(idempotency_key)
    note_id, created = runs_mod.save_run_note(
        student_id, workspace_id, lesson_id, run_id, request,
        idempotency_key=key)
    return JSONResponse(
        status_code=201 if created else 200,
        content=sc.SaveNoteResponse(note_id=note_id).model_dump(
            mode="json", by_alias=True))


# ---------------------------------------------------------------------------
# 检查点（§14.2：GET/submit/hint/reveal/skip/submission，阶段 I）
# ---------------------------------------------------------------------------

def _run_for_checkpoint(student_id: str, workspace_id: str, lesson_id: str,
                        run_id: str) -> tuple[sc.ClassroomRun, Any]:
    from app.classroom import assessment_bridge

    run, _spec = _load_run_and_spec(student_id, workspace_id, lesson_id,
                                    run_id)
    # 幂等实例化（§13.2.6）：崩溃后重入收敛；只读 GET 也先确保注册
    run = assessment_bridge.ensure_run_questions(
        student_id, workspace_id, lesson_id, run_id)
    return run, assessment_bridge


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/checkpoints/{checkpoint_id}",
            response_model=sc.CheckpointPublic)
def get_checkpoint(workspace_id: str, lesson_id: str, run_id: str,
                   checkpoint_id: str,
                   student_id: str = Depends(resolve_student_id)):
    """GET R/checkpoints/{cid}：QuestionPublic（未揭晓无答案）+ run 状态。"""
    from app.classroom import assessment_bridge

    require_enabled(student_id)
    run, _spec = _load_run_and_spec(student_id, workspace_id, lesson_id,
                                    run_id)
    run = assessment_bridge.ensure_run_questions(
        student_id, workspace_id, lesson_id, run_id)
    return assessment_bridge.checkpoint_public(
        student_id, workspace_id, lesson_id, run, checkpoint_id)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/checkpoints/{checkpoint_id}/submit")
async def submit_checkpoint(workspace_id: str, lesson_id: str, run_id: str,
                            checkpoint_id: str,
                            request: sc.CheckpointSubmitRequest,
                            student_id: str = Depends(resolve_student_id)):
    """POST submit：唯一受理链（202）；不改变播放进度。"""
    from app.classroom import assessment_bridge

    require_enabled(student_id)
    run, _bridge = _run_for_checkpoint(student_id, workspace_id, lesson_id,
                                       run_id)
    payload = await assessment_bridge.submit_checkpoint(
        student_id, workspace_id, lesson_id, run, checkpoint_id, request)
    return JSONResponse(payload, status_code=202)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/checkpoints/{checkpoint_id}/hint")
def hint_checkpoint(workspace_id: str, lesson_id: str, run_id: str,
                    checkpoint_id: str,
                    idempotency_key: str | None = Header(
                        default=None, alias="Idempotency-Key"),
                    student_id: str = Depends(resolve_student_id)):
    """POST hint：先持久化帮助事件，再返回提示（§13.3）。"""
    from app.classroom import assessment_bridge
    from app.classroom.errors import require_idempotency_key

    require_enabled(student_id)
    require_idempotency_key(idempotency_key)
    run, _bridge = _run_for_checkpoint(student_id, workspace_id, lesson_id,
                                       run_id)
    return assessment_bridge.hint_checkpoint(
        student_id, workspace_id, lesson_id, run, checkpoint_id)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/checkpoints/{checkpoint_id}/reveal")
def reveal_checkpoint(workspace_id: str, lesson_id: str, run_id: str,
                      checkpoint_id: str,
                      idempotency_key: str | None = Header(
                          default=None, alias="Idempotency-Key"),
                      student_id: str = Depends(resolve_student_id)):
    """POST reveal：先记 answer_revealed，再返回答案/解析（§13.3）。"""
    from app.classroom import assessment_bridge
    from app.classroom.errors import require_idempotency_key

    require_enabled(student_id)
    require_idempotency_key(idempotency_key)
    run, _bridge = _run_for_checkpoint(student_id, workspace_id, lesson_id,
                                       run_id)
    return assessment_bridge.reveal_checkpoint(
        student_id, workspace_id, lesson_id, run, checkpoint_id)


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/checkpoints/{checkpoint_id}/skip")
def skip_checkpoint(workspace_id: str, lesson_id: str, run_id: str,
                    checkpoint_id: str,
                    request: sc.CheckpointSkipRequest,
                    student_id: str = Depends(resolve_student_id)):
    """POST skip：不伪称作答、不触发评价（§13.3）。"""
    from app.classroom import assessment_bridge

    require_enabled(student_id)
    run, _bridge = _run_for_checkpoint(student_id, workspace_id, lesson_id,
                                       run_id)
    assessment_bridge.skip_checkpoint(
        student_id, workspace_id, lesson_id, run, checkpoint_id,
        request.expected_state_revision)
    return {"status": "skipped"}


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/checkpoints/{checkpoint_id}/submission")
def get_checkpoint_submission(workspace_id: str, lesson_id: str, run_id: str,
                              checkpoint_id: str,
                              student_id: str = Depends(resolve_student_id)):
    """GET submission：只读已受理结果；未提交返回 null（§14.2）。"""
    from app.classroom import assessment_bridge

    require_enabled(student_id)
    run, _bridge = _run_for_checkpoint(student_id, workspace_id, lesson_id,
                                       run_id)
    return {"submission": assessment_bridge.submission_of(
        student_id, run, checkpoint_id)}


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/qa-audio", status_code=202,
             response_model=sc.QaAudioResponse)
async def request_qa_audio(workspace_id: str, lesson_id: str, run_id: str,
                           request: sc.QaAudioRequest,
                           idempotency_key: str | None = Header(
                               default=None, alias="Idempotency-Key"),
                           student_id: str = Depends(resolve_student_id)):
    """POST R/qa-audio：已保存答疑回复的句级音频（§14.2/§12.5）。

    正文只从本 run 答疑 session 的 assistant 消息读取（message_id 匹配），
    不接客户端 text；lease 校验与 narration 一致。
    """
    from app.classroom import audio as audio_mod
    from app.classroom.errors import require_idempotency_key

    require_enabled(student_id)
    require_idempotency_key(idempotency_key)
    run, _spec = _load_run_and_spec(student_id, workspace_id, lesson_id,
                                    run_id)
    if not run.qa_session_id:
        raise ClassroomError("source_not_found", "课堂尚未创建答疑会话")
    if run.lease is None or run.lease.lease_epoch != request.lease_epoch:
        raise ClassroomError("lease_conflict", "lease 已失效或被接管")

    from app.core.session import load_session
    qa = load_session(run.qa_session_id)
    if qa is None or (qa.student_id and qa.student_id != student_id):
        raise ClassroomError("source_not_found", "答疑会话不存在")
    reply = next((m for m in reversed(qa.messages)
                  if m.get("message_id") == request.reply_message_id), None)
    if reply is None or reply.get("role") != "assistant":
        raise ClassroomError("source_not_found", "答疑回复不存在")
    text = str(reply.get("content") or "")
    profile = _run_profile(run)
    engine = audio_mod.get_audio_engine()
    clips = await engine.request_qa_clips(run, text, profile)
    base = _run_base(workspace_id, lesson_id, run_id)
    return sc.QaAudioResponse(clips=[
        sc.ClipStatus(
            clip_id=c["clip_id"], state=sc.AudioClipState(c["state"]),
            status_url=f"/api/v1{base}/audio/{c['clip_id']}",
            content_url=(f"/api/v1{base}/audio/{c['clip_id']}/content"
                         if c["state"] == "ready" else None))
        for c in clips])


@router.put("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/audio-profile")
def update_audio_profile(workspace_id: str, lesson_id: str, run_id: str,
                         request: sc.AudioProfileRequest,
                         student_id: str = Depends(resolve_student_id)):
    from app.classroom import runs as runs_mod

    return runs_mod.update_audio_profile(student_id, workspace_id,
                                         lesson_id, run_id, request)


def _run_profile(run: sc.ClassroomRun) -> "tts_service.ClassroomVoiceProfile":
    """run 冻结的 audio_profile → 引擎 profile（provider 为空时按策略重解）。"""
    from app.voice.tts import service as tts_service
    profile = run.audio_profile
    if profile.provider:
        return tts_service.ClassroomVoiceProfile(
            policy=str(getattr(profile.policy, "value", profile.policy)),
            provider=profile.provider, voice_id=profile.voice_id,
            language=str(getattr(profile.language, "value",
                                 profile.language)) or "zh",
            synthesis_speed=1.0,
            allow_local_fallback=profile.allow_local_fallback,
            cloud_configured=tts_service.azure_available(),
            local_enabled=tts_service.local_tts_enabled())
    prefs = sc.VoicePreferences(
        policy=profile.policy, voice_id=profile.voice_id,
        allow_local_fallback=profile.allow_local_fallback,
        playback_speed=profile.playback_speed)
    return tts_service.resolve_classroom_tts(
        prefs, str(getattr(profile.language, "value", profile.language))
        or "zh")


def _load_run_and_spec(student_id: str, workspace_id: str, lesson_id: str,
                       run_id: str) -> tuple[sc.ClassroomRun, sc.LessonRevision]:
    from app.classroom import runs as runs_mod

    run = runs_mod.load_owned_run(student_id, workspace_id, lesson_id,
                                  run_id)
    spec = runs_mod.load_run_spec(student_id, workspace_id,
                                  lesson_id, run.lesson_revision)
    return run, spec


@router.post("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
             "/runs/{run_id}/audio", response_model=sc.AudioResponse,
             status_code=202)
async def request_run_audio(workspace_id: str, lesson_id: str, run_id: str,
                            request: sc.AudioRequest,
                            idempotency_key: str | None = Header(
                                default=None, alias="Idempotency-Key"),
                            student_id: str = Depends(resolve_student_id)):
    """POST R/audio：仅当前/合法预取范围；202 返回 clip 状态，GET 不计费。"""
    from app.classroom import audio as audio_mod
    from app.classroom.errors import require_idempotency_key

    require_enabled(student_id)
    # Idempotency-Key 只做形状校验：重放由引擎缓存命中 + single-flight
    # 天然幂等（不落 owner 幂等表，避免高频音频请求挤掉建课条目）。
    require_idempotency_key(idempotency_key)
    run, spec = _load_run_and_spec(student_id, workspace_id, lesson_id,
                                   run_id)
    if run.lease is None or run.lease.lease_epoch != request.lease_epoch:
        raise ClassroomError("lease_conflict", "lease 已失效或被接管")
    profile = _run_profile(run)
    engine = audio_mod.get_audio_engine()
    clips = await engine.request_narration_clips(
        run, spec, profile, request.segment_ids)
    base = _run_base(workspace_id, lesson_id, run_id)
    payload = sc.AudioResponse(clips=[
        sc.ClipStatus(
            clip_id=c["clip_id"], state=sc.AudioClipState(c["state"]),
            status_url=f"/api/v1{base}/audio/{c['clip_id']}",
            content_url=(f"/api/v1{base}/audio/{c['clip_id']}/content"
                         if c["state"] == "ready" else None))
        for c in clips])
    return payload


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/audio/{clip_id}", response_model=sc.ClipStatus)
def get_run_clip(workspace_id: str, lesson_id: str, run_id: str,
                 clip_id: str, student_id: str = Depends(resolve_student_id)):
    from app.classroom import audio as audio_mod

    run, _spec = _load_run_and_spec(student_id, workspace_id, lesson_id,
                                    run_id)
    status = audio_mod.get_audio_engine().clip_status(run, clip_id)
    base = _run_base(workspace_id, lesson_id, run_id)
    return sc.ClipStatus(
        clip_id=clip_id, state=sc.AudioClipState(status["state"]),
        status_url=f"/api/v1{base}/audio/{clip_id}",
        content_url=(f"/api/v1{base}/audio/{clip_id}/content"
                     if status["state"] == "ready" else None))


@router.get("/workspaces/{workspace_id}/classroom/lessons/{lesson_id}"
            "/runs/{run_id}/audio/{clip_id}/content")
def get_run_clip_content(workspace_id: str, lesson_id: str, run_id: str,
                         clip_id: str,
                         student_id: str = Depends(resolve_student_id)):
    from fastapi.responses import Response

    from app.classroom import audio as audio_mod

    run, _spec = _load_run_and_spec(student_id, workspace_id, lesson_id,
                                    run_id)
    data, _meta = audio_mod.get_audio_engine().clip_content(run, clip_id)
    return Response(content=data, media_type="audio/wav",
                    headers={"Cache-Control": "private, no-store"})
