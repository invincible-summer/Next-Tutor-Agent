"""课堂错误类型与统一 envelope（plan.md §14.3）。

service/worker 抛 ClassroomError；route 层统一映射为固定错误体：
{"error": {code, message, retryable, phase?, request_id}}。
404 对不存在/他人资源统一；409 revision/lease/scope/状态冲突；
422 schema/教材不足/语言不可用；429 配额与排队已满；503 功能不可用。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

from ..schemas.classroom import ClassroomErrorCode, ErrorBody, ErrorResponse
from . import limits

_HTTP_STATUS: dict[str, int] = {
    "classroom_disabled": 403,
    "source_not_ready": 422,
    "source_not_found": 404,
    "source_changed": 409,
    "research_unavailable": 503,
    "freshness_unverified": 422,
    "image_unavailable": 503,
    "content_invalid": 422,
    "layout_overflow": 422,
    "renderer_unavailable": 503,
    "budget_exceeded": 429,
    "quota_exceeded": 429,
    "generation_failed": 500,
    "job_cancelled": 409,
    "revision_conflict": 409,
    "lease_conflict": 409,
    "scope_changed": 409,
    "audio_busy": 429,
    "tts_unavailable": 503,
    "voice_unavailable": 422,
    "export_expired": 410,
    "storage_unavailable": 503,
    "idempotency_conflict": 409,
    "damaged": 500,
}


class ClassroomError(Exception):
    """课堂域统一异常；route 层经 to_response 投影为 §14.3 envelope。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 phase: str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        try:
            self.code = ClassroomErrorCode(code)
        except ValueError:
            self.code = ClassroomErrorCode.generation_failed
        self.message = message
        self.retryable = retryable
        self.phase = phase
        self.http_status = http_status or _HTTP_STATUS.get(
            self.code.value, 500)

    def body(self, request_id: str = "") -> ErrorBody:
        return ErrorBody(code=self.code, message=self.message,
                         retryable=self.retryable,
                         phase=self.phase, request_id=request_id)


def error_response(exc: ClassroomError | Exception,
                   request_id: str = "") -> JSONResponse:
    if not isinstance(exc, ClassroomError):
        exc = ClassroomError("generation_failed", "课堂服务内部错误",
                             retryable=True)
    envelope = ErrorResponse(error=exc.body(request_id))
    return JSONResponse(status_code=exc.http_status,
                        content=envelope.model_dump(mode="json", by_alias=True))


def require_idempotency_key(value: str | None) -> str:
    """创建类操作的 Idempotency-Key（16–128 可打印安全字符）。"""
    if not value:
        raise ClassroomError("content_invalid",
                             "缺少 Idempotency-Key 请求头")
    key = value.strip()
    if not (limits.IDEMPOTENCY_KEY_MIN <= len(key)
            <= limits.IDEMPOTENCY_KEY_MAX):
        raise ClassroomError("content_invalid",
                             f"Idempotency-Key 长度须在 "
                             f"{limits.IDEMPOTENCY_KEY_MIN}–"
                             f"{limits.IDEMPOTENCY_KEY_MAX} 之间")
    if any(not (0x21 <= ord(c) <= 0x7E) for c in key):
        raise ClassroomError("content_invalid",
                             "Idempotency-Key 含非法字符")
    return key
