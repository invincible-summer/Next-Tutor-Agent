"""M0 Auth API: register / login / logout / me / status.

These endpoints create and verify user identities. The chat stream and all
projection APIs resolve the student_id from the JWT these endpoints issue --
they never accept a student_id from the client body/query.

Security notes:
  - password_hash is NEVER returned by any endpoint (User.to_public_dict).
  - JWT secret lives only in config, used only in security.py.
  - Guest learning is independently controlled by the live admin policy.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.identity import config, is_auth_required
from app.identity.deps import optional_user, require_user
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
    # 显式拒绝而不是静默截断（截断会让"长密码"的后半段形同虚设）。
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


class StatusResponse(BaseModel):
    auth_required: bool
    guest_allowed: bool
    using_default_secret: bool


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
             dependencies=[Depends(rate_limit("auth_register", 5))])
def register(req: RegisterRequest):
    """Create a new user account. The user_id becomes the student namespace
    key for all M2-M9 data."""
    if email_exists(req.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="email_already_registered")
    profile = UserProfile(
        name=req.name or req.username or req.email.split("@")[0],
        grade=req.grade, subjects=list(req.subjects), school=req.school,
    )
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
             dependencies=[Depends(rate_limit("auth_login", 10))])
def login(req: LoginRequest):
    """Authenticate and issue a JWT."""
    acct_key = f"acct:{req.email.strip().lower()}"
    # 账号已锁定 → 直接 429：不为爆破流量付出 bcrypt 校验成本。
    if rate_limited("auth_login_fail", acct_key,
                    _LOGIN_FAIL_MAX, _LOGIN_FAIL_WINDOW):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too_many_attempts")
    user = get_by_email(req.email)
    if not user or not verify_password(req.password, user.password_hash):
        # 按账号的失败节流（独立于按 IP 的依赖规则）：轮换来源 IP 的暴力
        # 猜解也必须在单账号上减速。只计失败尝试，正确密码不受影响。
        check_rate("auth_login_fail", acct_key,
                   _LOGIN_FAIL_MAX, _LOGIN_FAIL_WINDOW)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="invalid_credentials")
    # 成功登录清空失败计数：正常用户偶尔输错不留下半锁定状态。
    reset_rate("auth_login_fail", acct_key)
    touch_login(user.id)
    token = create_token(user.id, token_version=user.token_version)
    return AuthResponse(token=token, user=user.to_public_dict())


@router.post("/logout")
def logout(_user: User = Depends(require_user)):
    """Stateless JWT: logout is a client-side token discard. This endpoint
    exists for symmetry and future token-blacklist support."""
    return {"status": "ok"}


@router.get("/me")
def me(user: User = Depends(require_user)):
    """Return the current user's public profile."""
    return {"status": "ok", "user": user.to_public_dict()}
