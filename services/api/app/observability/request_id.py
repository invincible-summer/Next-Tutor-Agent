"""X-Request-ID propagation (cross-client trace correlation).

The API client sends ``X-Request-ID``; the server echoes the same id on the
response (sanitized, or freshly generated when absent/invalid), and exposes
it process-wide via :func:`current_request_id` for structured log fields,
audit events and trace correlation. No PII ever rides this header — it is an
opaque correlation token only.
"""
from __future__ import annotations

import contextvars
import re
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

_REQUEST_ID = contextvars.ContextVar("request_id", default="")

# Conservative id shape: printable, bounded, no spaces/controls/unicode.
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,80}$")


def new_request_id() -> str:
    return f"req_{secrets.token_hex(8)}"


def sanitize_request_id(raw: str | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip()
    if _VALID_ID.fullmatch(candidate):
        return candidate
    return None


def current_request_id() -> str:
    """The active request's id ("" outside a request scope)."""
    return _REQUEST_ID.get()


class RequestIdMiddleware:
    """Pure-ASGI middleware: bind the id before routing, echo it after."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])}
        request_id = (sanitize_request_id(headers.get("x-request-id"))
                      or new_request_id())
        token = _REQUEST_ID.set(request_id)

        async def send_with_id(message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers", []))
                raw_headers.append(
                    (b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _REQUEST_ID.reset(token)
