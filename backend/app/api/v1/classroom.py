"""课堂模式 HTTP API（plan.md §14）。

route 只做身份、schema、状态码投影；复杂工作由 app.classroom.service 等
执行。所有 ID 均验证完整 owner→workspace→lesson→revision/run/job 链。
功能关闭时：能力端点仍可读，生成类端点返回 classroom_disabled。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
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
    LessonListResponse,
)

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
