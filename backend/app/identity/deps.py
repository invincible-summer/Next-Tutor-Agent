"""FastAPI dependencies for identity resolution.

Two dependencies:
  - resolve_student_id(): the student namespace key. Used by ALL projection
    APIs and the chat stream. Falls back to DEFAULT_STUDENT_ID when auth is off
    or no valid token is present, so the system is always usable.
  - require_user(): the authenticated User object, or HTTP 401. Used only by
    account-management endpoints (login/register/profile).

The student_id dependency is deliberately permissive (never 401): a missing
token means "guest", not "error". This keeps AUTH_MODE=0 fully transparent and
lets AUTH_MODE=1 degrade gracefully for public endpoints.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from .models import User
from .security import decode_token, extract_bearer
from .store import get_by_id

from app.agents.student_model.store import DEFAULT_STUDENT_ID


def _try_user_from_header(authorization: str | None) -> User | None:
    """Best-effort: decode the JWT and load the user. None if absent/invalid."""
    token = extract_bearer(authorization)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    uid = str(payload.get("sub", ""))
    return get_by_id(uid) if uid else None


def resolve_student_id(authorization: str | None = Header(default=None,
                                alias="Authorization")) -> str:
    """The student namespace key for the current request.

    Priority: valid JWT -> user_id, in ANY auth mode — once logged in, the
    user's data (sessions, M2-M9 namespaces) is always keyed to their own
    identity; AUTH_MODE only controls whether login is *enforced*, not
    whether it is honored. No/invalid token -> DEFAULT_STUDENT_ID (guest).
    Never raises -- a bad token just means guest.
    """
    user = _try_user_from_header(authorization)
    return user.id if user else DEFAULT_STUDENT_ID


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
