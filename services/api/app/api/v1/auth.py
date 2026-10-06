"""M0 Auth API: register / login / logout / me / status + enterprise sessions.

These endpoints create and verify user identities. The chat stream and all
projection APIs resolve the student_id from the JWT these endpoints issue --
they never accept a student_id from the client body/query.

Security notes:
  - password_hash is NEVER returned by any endpoint (User.to_public_dict).
  - JWT secret lives only in config, used only in security.py.
  - Guest learning is independently controlled by the live admin policy.

Enterprise mode (DATABASE_URL set) additionally issues rotating sessions:
login/register responses gain ``access_token``/``refresh_token``/
``expires_in`` alongside the legacy ``token`` (the current Web client keeps
working unchanged during the migration window) and seed an HttpOnly
``edu_refresh`` cookie (Secure+SameSite=Lax, scoped to /api/v1/auth) for
the Web refresh lane. ``/auth/refresh`` accepts the body token (mobile,
unchanged) or the cookie and re-seeds it on every rotation; ``/auth/logout``
revokes the cookie's session family and clears it. ``/auth/sessions`` and
``DELETE /auth/sessions/{id}`` manage the session families; they answer 409
``enterprise_auth_required`` in file mode instead of pretending sessions
exist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.identity import config, is_auth_required
from app.identity.deps import optional_user, require_user, resolve_principal
from app.identity.models import User, UserProfile
from app.identity.security import create_token, hash_password, verify_password
from app.identity.store import (create_user, email_exists, get_by_email,
                                get_by_id, touch_login)
from app.core.ratelimit import check_rate, rate_limit, rate_limited, reset_rate

router = APIRouter(prefix="/auth", tags=["auth"])


# --- request / response schemas --------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    # bcrypt 只取前 72 字节参与哈希：上限与之一致，超限在 validator 里
    # 显式拒绝而不是静截断（截断会让"长密码"的后半段形同虚设）。
    password: str = Field(min_length=8, max_length=128)
    username: str = Field(default="", max_length=40)
    name: str = Field(default="", max_length=40)
    grade: str = Field(default="本科")
    subjects: list[str] = Field(default_factory=list)
    school: str = Field(default="", max_length=80)

    @field_validator("password")
    @classmethod
    def _password_fits_bcrypt(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password_exceeds_bcrypt_72_bytes")
        return v


class LoginRequest(BaseModel):
    # Plain str, not EmailStr: login must accept any address the store holds —
    # including single-label domains like the env-bootstrapped admin account
    # (administrator@administrator), which EmailStr rejects with 422 before
    # credentials are ever checked. The store lookup is the real validation.
    email: str = Field(min_length=3, max_length=254)
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict
    # Enterprise rotating sessions; excluded from the response while empty
    # (file mode keeps the exact legacy payload shape).
    access_token: str = ""
    refresh_token: str = ""
    expires_in: int | None = None


class StatusResponse(BaseModel):
    auth_required: bool
    guest_allowed: bool
    using_default_secret: bool


class RefreshRequest(BaseModel):
    """Body token stays the mobile lane; Web refreshes through the HttpOnly
    cookie and may post an empty body/{}."""
    refresh_token: str = Field(default="", max_length=256)


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class SessionInfo(BaseModel):
    id: str
    created_at: float
    expires_at: float
    last_refreshed_at: float | None = None
    revoked_at: float | None = None
    revoked_reason: str | None = None
    client: dict = {}


def _enterprise_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": {"code": code, "message": message,
                          "retryable": False}})


def _session_service():
    from app.identity.sessions import get_session_service

    service = get_session_service()
    if service is None:
        raise _enterprise_error(
            "enterprise_auth_required",
            "轮换会话需要企业持久化（配置 DATABASE_URL）；文件模式请继续"
            "使用现有 token。")
    return service


def _request_id(request: Request) -> str:
    return (request.headers.get("x-request-id") or "")[:80]


# --- refresh cookie（Web 刷新轨；移动端继续走 body token） -------------------

REFRESH_COOKIE = "edu_refresh"
# Cookie 只随 /auth/* 请求发送，缩小暴露面（其它 API 路径永远看不到它）。
_REFRESH_COOKIE_PATH = "/api/v1/auth"


def _cookie_secure() -> bool:
    # 生产同源 HTTPS 默认 Secure；本地纯 http（非 localhost）联调可显式关。
    import os

    return os.environ.get("AUTH_REFRESH_COOKIE_SECURE", "1") != "0"


def _seed_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE, raw_token,
        max_age=config.AUTH_REFRESH_SESSION_DAYS * 86400,
        path=_REFRESH_COOKIE_PATH, httponly=True, secure=_cookie_secure(),
        samesite="lax")


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)


def _cleared_refresh_cookie_header() -> str:
    """Same deletion as _clear_refresh_cookie, as a raw header value for
    paths that raise HTTPException (FastAPI drops the injected response's
    cookies when an exception response is built)."""
    return (f'{REFRESH_COOKIE}=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; '
            f'Max-Age=0; Path={_REFRESH_COOKIE_PATH}; HttpOnly; SameSite=lax')


def _cookie_refresh_token(request: Request) -> str:
    return (request.cookies.get(REFRESH_COOKIE) or "").strip()


async def _issue_enterprise_tokens(*, user: User, tenant_id: str | None,
                                   request: Request) -> dict:
    """Issue the rotating-session payload for a freshly logged-in user."""
    from app.identity.backend import identity_backend

    service = _session_service()
    client = {"platform": request.headers.get("x-client-platform", ""),
              "user_agent": request.headers.get("user-agent", "")}
    issued = await service.start_session(
        user_id=user.id, token_version=user.token_version,
        tenant_id=tenant_id, client=client,
        request_id=_request_id(request))
    issued.pop("session_id", None)  # internal; clients don't need it yet
    return issued


# --- endpoints --------------------------------------------------------------

@router.get("/status")
def auth_status(response: Response,
                user: User | None = Depends(optional_user)) -> StatusResponse:
    """Tells the frontend whether login is required and if the JWT secret is
    still the insecure default (dev-only warning).

    using_default_secret 只对已登录管理员如实披露：匿名探测不允许得知
    "此刻 token 可用仓库公共默认密钥伪造" 这一事实。
    """
    required = is_auth_required()
    response.headers["Cache-Control"] = "no-store"
    return StatusResponse(
        auth_required=required,
        guest_allowed=not required,
        using_default_secret=(config.using_default_secret()
                              and user is not None and user.role == "admin"),
    )


@router.post("/register", response_model=AuthResponse,
             response_model_exclude_defaults=True,
             dependencies=[Depends(rate_limit("auth_register", 5))])
async def register(req: RegisterRequest, request: Request,
                   response: Response):
    """Create a new user account. The user_id becomes the student namespace
    key for all M2-M9 data."""
    profile = UserProfile(
        name=req.name or req.username or req.email.split("@")[0],
        grade=req.grade, subjects=list(req.subjects), school=req.school,
    )
    from app.persistence import db as persistence_db
    from app.persistence.repositories.protocols import DuplicateEmailError
    if persistence_db.enterprise_mode():
        from app.identity.backend import EnterpriseIdentityBackend, \
            identity_backend

        backend = identity_backend()
        assert isinstance(backend, EnterpriseIdentityBackend)
        if await backend.email_exists(req.email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="email_already_registered")
        try:
            user, tenant_id = await backend.register_account(
                email=req.email, username=req.username,
                password_hash=hash_password(req.password),
                role="student", profile=profile)
        except HTTPException:
            raise
        except DuplicateEmailError:
            # Race window past the pre-check: the DB rejected the email.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="email_already_registered")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="enterprise_registration_failed")
        issued = await _issue_enterprise_tokens(
            user=user, tenant_id=tenant_id, request=request)
        _seed_refresh_cookie(response, issued["refresh_token"])
        return AuthResponse(
            token=create_token(user.id, token_version=user.token_version),
            user=user.to_public_dict(), **issued)
    # 文件模式：行为与之前完全一致。
    if email_exists(req.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="email_already_registered")
    user = create_user(
        email=req.email, username=req.username,
        password_hash=hash_password(req.password),
        profile=profile,
    )
    touch_login(user.id)
    token = create_token(user.id, token_version=user.token_version)
    return AuthResponse(token=token, user=user.to_public_dict())


# 登录失败按账号节流：5 次/15 分钟。原 10 次/5 分钟（≈2880 次/天）对在线
# 猜解过于宽松；bcrypt(12) 抬高了单次成本，锁定阈值收紧后早退还能省下
# 已锁定账号上的 bcrypt CPU 消耗。
_LOGIN_FAIL_MAX = 5
_LOGIN_FAIL_WINDOW = 900


@router.post("/login", response_model=AuthResponse,
             response_model_exclude_defaults=True,
             dependencies=[Depends(rate_limit("auth_login", 10))])
async def login(req: LoginRequest, request: Request,
                response: Response):
    """Authenticate and issue a JWT (plus rotating session in enterprise)."""
    acct_key = f"acct:{req.email.strip().lower()}"
    # 账号已锁定 → 直接 429：不为爆破流量付出 bcrypt 校验成本。
    if rate_limited("auth_login_fail", acct_key,
                    _LOGIN_FAIL_MAX, _LOGIN_FAIL_WINDOW):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too_many_attempts")
    from app.identity.backend import identity_backend
    backend = identity_backend()
    user = await backend.get_by_email(req.email)
    if not user or not verify_password(req.password, user.password_hash):
        # 按账号的失败节流（独立于按 IP 的依赖规则）：轮换来源 IP 的暴力
        # 猜解也必须在单账号上减速。只计失败尝试，正确密码不受影响。
        check_rate("auth_login_fail", acct_key,
                   _LOGIN_FAIL_MAX, _LOGIN_FAIL_WINDOW)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="invalid_credentials")
    # 成功登录清空失败计数：正常用户偶尔输错不留下半锁定状态。
    reset_rate("auth_login_fail", acct_key)
    await backend.touch_login(user.id)
    token = create_token(user.id, token_version=user.token_version)
    from app.persistence import db as persistence_db
    if persistence_db.enterprise_mode():
        from app.identity.backend import EnterpriseIdentityBackend

        assert isinstance(backend, EnterpriseIdentityBackend)
        tenant_id = await backend.active_tenant_id(user.id)
        issued = await _issue_enterprise_tokens(
            user=user, tenant_id=tenant_id, request=request)
        service = _session_service()
        await service.audit_login(user_id=user.id, tenant_id=tenant_id,
                                  request_id=_request_id(request))
        _seed_refresh_cookie(response, issued["refresh_token"])
        return AuthResponse(token=token, user=user.to_public_dict(), **issued)
    return AuthResponse(token=token, user=user.to_public_dict())


@router.post("/logout")
async def logout(request: Request, response: Response,
                 _user: User = Depends(require_user)):
    """Stateless JWT: logout is a client-side token discard. This endpoint
    exists for symmetry and future token-blacklist support.

    Enterprise mode: when the browser holds a refresh cookie, the session
    family behind it is revoked server-side and the cookie cleared — the
    HttpOnly cookie holder is by definition its owner."""
    from app.identity.sessions import get_session_service

    service = get_session_service()
    if service is not None:
        raw = _cookie_refresh_token(request)
        if raw:
            try:
                await service.revoke_by_raw_token(
                    raw_refresh_token=raw, request_id=_request_id(request))
            except Exception:  # revocation is best-effort; cookie clears anyway
                import logging

                logging.getLogger(__name__).warning(
                    "logout session revocation failed", exc_info=True)
    _clear_refresh_cookie(response)
    return {"status": "ok"}


@router.get("/me")
def me(user: User = Depends(require_user)):
    """Return the current user's public profile."""
    return {"status": "ok", "user": user.to_public_dict()}


# --- enterprise session endpoints -------------------------------------------

@router.post("/refresh", response_model=RefreshResponse,
             dependencies=[Depends(rate_limit("auth_refresh", 30))])
async def refresh(request: Request, response: Response,
                  body: RefreshRequest | None = None):
    """Rotate a refresh token; reuse of a rotated token revokes the family.

    Token source: body first (mobile lane, unchanged), then the HttpOnly
    refresh cookie (Web lane — the SPA never sees the raw value). A
    successful rotation re-seeds the cookie so the browser always holds the
    newest token of the family.
    """
    from app.identity.sessions import SessionError

    raw = ((body.refresh_token if body is not None else "").strip()
           or _cookie_refresh_token(request))
    service = _session_service()
    try:
        issued = await service.refresh(
            raw_refresh_token=raw,
            request_id=_request_id(request))
    except SessionError as exc:
        code = exc.code
        http_status = (401 if code in {
            "invalid_refresh_token", "session_revoked", "session_expired",
            "refresh_token_expired", "refresh_token_reused"} else 400)
        # A dead/expired session must not leave a stale refresh cookie in
        # the browser: clear it so the next hydrate goes straight to login.
        headers = ({"Set-Cookie": _cleared_refresh_cookie_header()}
                   if http_status == 401 else None)
        raise HTTPException(
            status_code=http_status,
            detail={"error": {"code": code,
                              "message": "刷新令牌无效或已被撤销。",
                              "retryable": False}},
            headers=headers) from exc
    _seed_refresh_cookie(response, issued["refresh_token"])
    return RefreshResponse(access_token=issued["access_token"],
                           refresh_token=issued["refresh_token"],
                           expires_in=issued["expires_in"])


@router.get("/sessions")
async def list_sessions(user: User = Depends(require_user)):
    """Active + recently revoked refresh sessions for the current user."""
    service = _session_service()
    sessions = await service.list_sessions(user.id)
    return {"status": "ok",
            "sessions": [SessionInfo(**s).model_dump() for s in sessions]}


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, request: Request,
                         response: Response,
                         user: User = Depends(require_user)):
    """Revoke one of the current user's session families (logout a device)."""
    service = _session_service()
    revoked = await service.revoke_session(
        owner_user_id=user.id, session_id=session_id,
        actor_user_id=user.id, reason="user_logout",
        request_id=_request_id(request))
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="session_not_found")
    # 当被撤销的正是本浏览器 cookie 所属的会话时同步清 cookie；撤销
    # 其它设备不应把当前设备踢出刷新轨。
    raw = _cookie_refresh_token(request)
    if raw and await service.session_of_raw_token(raw) == session_id:
        _clear_refresh_cookie(response)
    return {"status": "ok"}


@router.get("/principal")
async def principal(principal=Depends(resolve_principal)):
    """Enterprise tenant context for the current request (debug/console)."""
    return {"status": "ok", "principal": {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "membership_id": principal.membership_id,
        "tenant_role": principal.tenant_role,
        "platform_role": principal.platform_role,
        "auth_session_id": principal.auth_session_id,
    }}
