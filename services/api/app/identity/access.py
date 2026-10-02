"""API-wide default-deny gate; only temporary learning permits guest tokens."""
from __future__ import annotations

import re

from fastapi import Header, HTTPException, Response
from starlette.requests import HTTPConnection

from .deps import _try_user_from_header

_PUBLIC = {
    ("GET", "/health"), ("GET", "/ready"), ("GET", "/model-info"),
    ("GET", "/auth/status"), ("POST", "/auth/login"), ("POST", "/auth/register"),
    ("GET", "/docs/content"), ("GET", "/docs/show"),
}
_GUEST = {
    ("POST", "/chat/stream"), ("GET", "/chat/sessions"),
    ("POST", "/quiz/record"), ("POST", "/quiz/grade"),
    ("GET", "/quiz/submission"), ("GET", "/quiz/hint"),
    ("GET", "/guest/textbooks"), ("POST", "/guest/quiz/generate"),
}
_QUESTION = re.compile(r"^/assessment/questions/[^/]+(?:/(hint|reveal))?$")


def authentication_error(code: str = "authentication_required") -> HTTPException:
    return HTTPException(401, {"error": {"code": code,
                         "message": "请登录后使用此功能。", "retryable": False}},
                         headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})


def require_api_access(request: HTTPConnection, response: Response,
                       authorization: str | None = Header(default=None),
                       x_guest_token: str | None = Header(default=None)) -> None:
    path = request.url.path.removeprefix("/api/v1").rstrip("/") or "/"
    # Voice sockets validate their one-time login ticket before accepting.
    if request.scope["type"] == "websocket":
        return
    method = request.scope["method"]
    # The integration facade verifies its own deployment API key.
    if path in {"/models", "/chat/completions"}:
        return
    if (method, path) in _PUBLIC or (method == "GET" and path.startswith("/docs/show/pages/")):
        return
    user = _try_user_from_header(authorization)
    request.state.identity_user = user
    if user is not None:
        return
    if authorization:
        raise authentication_error("invalid_or_expired_token")
    if (method, path) == ("DELETE", "/guest/session"):
        return  # Cleanup remains available after the policy has been disabled.
    from app.core.guest_policy import guests_allowed
    if not guests_allowed():
        raise authentication_error("guest_disabled")
    if (method, path) == ("POST", "/guest/session"):
        return
    match = _QUESTION.fullmatch(path)
    question_allowed = match and ((method == "GET" and not match.group(1)) or
                                  (method == "POST" and match.group(1)))
    if (method, path) not in _GUEST and not question_allowed:
        raise authentication_error()
    from app.core.guest_runtime import get_context
    request.state.guest_context = get_context(x_guest_token)
    response.headers["Cache-Control"] = "no-store"
