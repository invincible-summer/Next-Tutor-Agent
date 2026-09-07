"""M0 Auth API: register / login / logout / me / status.

These endpoints create and verify user identities. The chat stream and all
projection APIs resolve the student_id from the JWT these endpoints issue --
they never accept a student_id from the client body/query.

Security notes:
  - password_hash is NEVER returned by any endpoint (User.to_public_dict).
  - JWT secret lives only in config, used only in security.py.
  - AUTH_MODE=0 (guest) means these endpoints still work but are optional;
    the frontend hides login/register UI in that mode.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.identity import config, is_auth_required
from app.identity.deps import optional_user, require_user
from app.identity.models import User, UserProfile
from app.identity.security import create_token, hash_password, verify_password
from app.identity.store import (create_user, email_exists, get_by_email,
                                get_by_id, touch_login)
from app.core.ratelimit import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


# --- request / response schemas --------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    username: str = Field(default="", max_length=40)
    name: str = Field(default="", max_length=40)
    grade: str = Field(default="本科")
    subjects: list[str] = Field(default_factory=list)
    school: str = Field(default="", max_length=80)


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
    using_default_secret: bool


# --- endpoints --------------------------------------------------------------

@router.get("/status")
def auth_status() -> StatusResponse:
    """Tells the frontend whether login is required and if the JWT secret is
    still the insecure default (dev-only warning)."""
    return StatusResponse(
        auth_required=is_auth_required(),
        using_default_secret=config.using_default_secret(),
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
    token = create_token(user.id)
    return AuthResponse(token=token, user=user.to_public_dict())


@router.post("/login", response_model=AuthResponse,
             dependencies=[Depends(rate_limit("auth_login", 10))])
def login(req: LoginRequest):
    """Authenticate and issue a JWT."""
    user = get_by_email(req.email)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="invalid_credentials")
    touch_login(user.id)
    token = create_token(user.id)
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
