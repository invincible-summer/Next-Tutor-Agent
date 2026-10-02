"""FastAPI dependencies for identity resolution.

Two dependencies:
  - resolve_student_id(): the student namespace key. Used by ALL projection
    APIs and the chat stream. Valid JWTs resolve to the account; explicitly
    allowed guests resolve to a unique, temporary in-memory namespace.
  - require_user(): the authenticated User object, or HTTP 401. Used only by
    account-management endpoints (login/register/profile).

Missing/invalid credentials fail closed. The API router independently limits
guest tokens to chat and temporary practice.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from .models import User
from .security import decode_token, extract_bearer
from .store import get_by_id

def _try_user_from_header(authorization: str | None) -> User | None:
    """Best-effort: decode the JWT and load the user. None if absent/invalid.

    token_version 吊销检查：payload 的 ver 必须等于账号当前版本，否则视同
    无效 token（改密码/管理员重置凭证后旧 token 立即 401，而不是等自然
    过期）。缺 ver 的历史 token 按版本 0 处理。
    """
    token = extract_bearer(authorization)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    uid = str(payload.get("sub", ""))
    if not uid:
        return None
    user = get_by_id(uid)
    if user is None:
        return None
    try:
        token_ver = int(payload.get("ver", 0) or 0)
    except (TypeError, ValueError):
        token_ver = 0
    if token_ver != user.token_version:
        return None
    return user


def resolve_student_id(authorization: str | None = Header(default=None,
                                alias="Authorization"),
                       request: Request = None,
                       x_guest_token: str | None = Header(default=None)) -> str:
    """The student namespace key for the current request.

    A valid JWT always wins. Otherwise require an enabled guest policy and a
    live opaque guest token. Invalid JWTs never fall back to guest identity.
    """
    authorization = authorization if isinstance(authorization, str) else None
    user = _try_user_from_header(authorization)
    if user is not None:
        return user.id
    from .access import authentication_error
    if authorization:
        raise authentication_error("invalid_or_expired_token")
    from app.core.guest_policy import guests_allowed
    if not guests_allowed():
        raise authentication_error("guest_disabled")
    from app.core.guest_runtime import get_context
    context = getattr(request.state, "guest_context", None) if request is not None else None
    if context is None:
        context = get_context(x_guest_token if isinstance(x_guest_token, str) else None)
    context.check()
    return context.owner_id


def require_user(authorization: str | None = Header(default=None,
                             alias="Authorization")) -> User:
    """The authenticated User, or HTTP 401. For account endpoints only."""
    user = _try_user_from_header(authorization)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not_authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def optional_user(authorization: str | None = Header(default=None,
                              alias="Authorization")) -> Optional[User]:
    """Like require_user but returns None instead of 401. For public endpoints
    that behave differently for logged-in vs anonymous users."""
    return _try_user_from_header(authorization)


def require_admin(user: User = Depends(require_user)) -> User:
    """The authenticated admin User, or 401/403. For admin endpoints only.

    P6-B1：管理员角色（role=="admin"）由 .env 的 ADMIN_EMAIL/ADMIN_PASSWORD
    在启动时引导（见 app.main lifespan 的 ensure_admin_account）。
    """
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="admin_required")
    return user
