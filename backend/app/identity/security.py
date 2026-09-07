"""Security primitives: password hashing (bcrypt) + JWT (PyJWT).

No API keys or secrets are ever logged or returned by any endpoint. The JWT
secret comes from config.AUTH_JWT_SECRET and is used only here.
"""
from __future__ import annotations

import time
from typing import Any

import bcrypt
import jwt

from . import config


# --- password hashing (bcrypt) ---------------------------------------------

def hash_password(plain: str) -> str:
    """Hash a password with bcrypt. Returns a utf-8 string (not bytes)."""
    salt = bcrypt.gensalt(rounds=config.AUTH_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash. Constant-time on success."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- JWT (sign / verify) ----------------------------------------------------

def create_token(user_id: str, extra: dict[str, Any] | None = None) -> str:
    """Sign a JWT carrying the user_id. Expiry is AUTH_TOKEN_EXPIRE_DAYS."""
    now = time.time()
    payload: dict[str, Any] = {
        "sub": user_id,           # standard claim: subject = user id
        "iat": int(now),
        "exp": int(now) + config.AUTH_TOKEN_EXPIRE_DAYS * 86400,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, config.AUTH_JWT_SECRET,
                      algorithm=config.AUTH_JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Verify a JWT and return its payload, or None if invalid/expired.

    Never raises -- callers treat None as "not authenticated".
    """
    try:
        return jwt.decode(token, config.AUTH_JWT_SECRET,
                          algorithms=[config.AUTH_JWT_ALGORITHM])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def extract_bearer(authorization: str | None) -> str | None:
    """Extract the raw token from an 'Authorization: Bearer <token>' header."""
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None
