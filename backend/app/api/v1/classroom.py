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
