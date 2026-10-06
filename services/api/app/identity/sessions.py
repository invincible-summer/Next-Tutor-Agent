"""Rotating auth sessions (enterprise mode).

Token model:
  - access token: RS256 (kid header), 15 min, claims sub/ver/sid/tenant;
    validated per request against the session row (revocation) and account
    token_version (all-device revocation).
  - refresh token: opaque ``rt_<urlsafe>``; only its SHA-256 hash is stored.
    Each refresh rotates the token; presenting an already-rotated token is
    reuse and revokes the ENTIRE session family.
  - legacy token: the pre-existing HS256 30-day JWT, still issued as the
    ``token`` response field for the current Web client (migration window,
    removed once the frontend moves to memory access + refresh).

The current Web client cannot refresh (localStorage keeps a long-lived
token), so login/register responses carry BOTH: ``token`` (legacy, existing
shape) and ``access_token``/``refresh_token``/``expires_in`` (new clients).
"""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid

import jwt

from . import config
from .keys import default_keyring


class SessionError(Exception):
    """Domain error with a stable code for the API layer."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_access_token(*, user_id: str, token_version: int,
                        session_id: str, tenant_id: str | None,
                        expires_in: int | None = None) -> str:
    """RS256 access token with kid header (10–15 min lifetime)."""
    keyring = default_keyring()
    now = time.time()
    payload = {
        "sub": user_id,
        "ver": int(token_version),
        "sid": session_id,
        "typ": "access",
        "iat": int(now),
        "exp": int(now + (expires_in or config.AUTH_ACCESS_TOKEN_SECONDS)),
    }
    if tenant_id:
        payload["tenant"] = tenant_id
    return jwt.encode(payload, keyring.signing_pem(), algorithm="RS256",
                      headers={"kid": keyring.active_kid()})


def decode_access_token(token: str) -> dict | None:
    """Verify RS256 + kid + typ, without touching the database."""
    try:
        header = jwt.get_unverified_header(token)
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
    kid = header.get("kid") if isinstance(header, dict) else None
    if not kid:
        return None
    public_pem = default_keyring().public_pem_for(str(kid))
    if not public_pem:
        return None
    try:
        payload = jwt.decode(token, public_pem, algorithms=["RS256"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
    if payload.get("typ") != "access":
        return None
    return payload


def new_refresh_token() -> tuple[str, str]:
    """(raw token, sha256 hash). Raw goes to the client only once."""
    raw = f"rt_{secrets.token_urlsafe(48)}"
    return raw, hash_refresh_token(raw)


_SESSION_CACHE_TTL = 10.0  # short cache; revocation propagates within TTL


def _cache() -> object:
    from app.persistence.cache import get_cache_primitives

    return get_cache_primitives()


class AuthSessionService:
    """Session lifecycle on top of the identity repository + audit trail."""

    def __init__(self, repository) -> None:
        self._repo = repository

    # --- issuance ----------------------------------------------------------

    async def start_session(self, *, user_id: str, token_version: int,
                            tenant_id: str | None,
                            client: dict | None = None,
                            request_id: str = "") -> dict:
        """Create a refresh-session family and issue its first tokens.

        Returns {session_id, access_token, refresh_token, expires_in}. The
        legacy ``token`` is crafted by the caller (auth routes) — it is not
        session-bound.
        """
        from app.persistence.repositories.records import (AuthSessionRecord,
                                                          RefreshTokenRecord)

        now = time.time()
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        expires_at = now + config.AUTH_REFRESH_SESSION_DAYS * 86400
        raw_refresh, token_hash = new_refresh_token()
        await self._repo.create_session(
            AuthSessionRecord(
                session_id=session_id, user_id=user_id, tenant_id=tenant_id,
                created_at=now, expires_at=expires_at,
                client=_scrub_client(client)),
            RefreshTokenRecord(
                id=f"rtx_{uuid.uuid4().hex[:12]}", session_id=session_id,
                token_hash=token_hash, issued_at=now, expires_at=expires_at))
        await self._audit("auth.session_created", user_id=user_id,
                          session_id=session_id, tenant_id=tenant_id,
                          request_id=request_id,
                          detail=_scrub_client(client))
        return {
            "session_id": session_id,
            "access_token": create_access_token(
                user_id=user_id, token_version=token_version,
                session_id=session_id, tenant_id=tenant_id),
            "refresh_token": raw_refresh,
            "expires_in": config.AUTH_ACCESS_TOKEN_SECONDS,
        }

    # --- validation ----------------------------------------------------------

    async def session_active(self, session_id: str) -> bool:
        """Revocation/expiry check with a short cache (§12.3 semantics)."""
        cache = _cache()
        key = f"authsess:{session_id}"
        if await cache.get(key) == b"1":
            return True
        session = await self._repo.get_session(session_id)
        if session is None:
            return False
        ok = (session.revoked_at is None
              and session.expires_at > time.time())
        if ok:
            await cache.set(key, b"1", ttl_seconds=_SESSION_CACHE_TTL)
        return ok

    async def invalidate_session_cache(self, session_id: str) -> None:
        await _cache().delete(f"authsess:{session_id}")

    # --- refresh rotation ------------------------------------------------------

    async def refresh(self, *, raw_refresh_token: str,
                      request_id: str = "") -> dict:
        """Rotate a refresh token; reuse revokes the family (§11.3)."""
        from app.persistence.repositories.records import RefreshTokenRecord
        from app.persistence.repositories.protocols import RepositoryError

        token_hash = hash_refresh_token(raw_refresh_token)
        record = await self._repo.find_refresh_token(token_hash)
        if record is None:
            raise SessionError("invalid_refresh_token")
        session = await self._repo.get_session(record.session_id)
        if session is None:
            raise SessionError("invalid_refresh_token")
        if session.revoked_at is not None:
            raise SessionError("session_revoked")
        if session.expires_at <= time.time():
            raise SessionError("session_expired")
        if record.rotated_at is not None:
            # Reuse of a superseded token: assume theft, revoke the family.
            await self._repo.revoke_session(record.session_id,
                                            "refresh_token_reuse")
            await self._repo.revoke_session_tokens(record.session_id)
            await self.invalidate_session_cache(record.session_id)
            await self._audit("auth.refresh_reuse",
                              user_id=session.user_id,
                              session_id=session.session_id,
                              tenant_id=session.tenant_id,
                              request_id=request_id)
            raise SessionError("refresh_token_reused")
        if record.expires_at <= time.time():
            raise SessionError("refresh_token_expired")

        raw_new, new_hash = new_refresh_token()
        now = time.time()
        replacement = RefreshTokenRecord(
            id=f"rtx_{uuid.uuid4().hex[:12]}",
            session_id=record.session_id, token_hash=new_hash,
            issued_at=now, expires_at=session.expires_at)
        try:
            await self._repo.rotate_refresh_token(record, replacement)
        except RepositoryError:
            # Concurrent rotation lost the race: treat as reuse.
            await self._repo.revoke_session(record.session_id,
                                            "refresh_token_reuse")
            await self._repo.revoke_session_tokens(record.session_id)
            await self.invalidate_session_cache(record.session_id)
            raise SessionError("refresh_token_reused") from None
        await self._audit("auth.refresh", user_id=session.user_id,
                          session_id=session.session_id,
                          tenant_id=session.tenant_id,
                          request_id=request_id)
        account = await self._repo.get_account_by_id(session.user_id)
        if account is None:
            raise SessionError("invalid_refresh_token")
        return {
            "session_id": session.session_id,
            "access_token": create_access_token(
                user_id=account.user_id,
                token_version=account.token_version,
                session_id=session.session_id,
                tenant_id=session.tenant_id),
            "refresh_token": raw_new,
            "expires_in": config.AUTH_ACCESS_TOKEN_SECONDS,
        }

    # --- management ---------------------------------------------------------

    async def list_sessions(self, user_id: str) -> list[dict]:
        sessions = await self._repo.list_sessions_for_user(user_id)
        return [{
            "id": s.session_id,
            "created_at": s.created_at,
            "expires_at": s.expires_at,
            "last_refreshed_at": s.last_refreshed_at,
            "revoked_at": s.revoked_at,
            "revoked_reason": s.revoked_reason or None,
            "client": s.client or {},
        } for s in sessions]

    async def revoke_session(self, *, owner_user_id: str, session_id: str,
                             actor_user_id: str | None = None,
                             reason: str = "user_logout",
                             request_id: str = "") -> bool:
        session = await self._repo.get_session(session_id)
        if session is None or session.user_id != owner_user_id:
            return False
        revoked = await self._repo.revoke_session(session_id, reason)
        if revoked:
            await self._repo.revoke_session_tokens(session_id)
            await self.invalidate_session_cache(session_id)
            await self._audit("auth.session_revoked",
                              user_id=actor_user_id or owner_user_id,
                              subject_user_id=owner_user_id,
                              session_id=session_id,
                              tenant_id=session.tenant_id,
                              request_id=request_id, detail={"reason": reason})
        return revoked

    async def session_of_raw_token(self, raw_refresh_token: str) -> str | None:
        """Which session family does this raw refresh token belong to (for
        cookie-vs-target comparisons; None when unknown)."""
        record = await self._repo.find_refresh_token(
            hash_refresh_token(raw_refresh_token))
        return record.session_id if record else None

    async def revoke_by_raw_token(self, *, raw_refresh_token: str,
                                  request_id: str = "") -> bool:
        """Logout lane: revoke the session family a raw refresh token (or
        its already-rotated descendant) belongs to. Possession of the token
        IS the authorization — the HttpOnly cookie holder logs itself out."""
        record = await self._repo.find_refresh_token(
            hash_refresh_token(raw_refresh_token))
        if record is None:
            return False
        session = await self._repo.get_session(record.session_id)
        if session is None:
            return False
        revoked = await self._repo.revoke_session(
            record.session_id, "user_logout")
        if revoked:
            await self._repo.revoke_session_tokens(record.session_id)
            await self.invalidate_session_cache(record.session_id)
            await self._audit("auth.session_revoked",
                              user_id=session.user_id,
                              session_id=session.session_id,
                              tenant_id=session.tenant_id,
                              request_id=request_id,
                              detail={"reason": "user_logout"})
        return revoked

    async def audit_login(self, *, user_id: str, tenant_id: str | None,
                          request_id: str = "") -> None:
        await self._audit("auth.login", user_id=user_id, tenant_id=tenant_id,
                          request_id=request_id)

    async def _audit(self, event_type: str, *, user_id: str,
                     session_id: str | None = None,
                     tenant_id: str | None = None,
                     subject_user_id: str | None = None,
                     request_id: str = "", detail: dict | None = None) -> None:
        from app.persistence.repositories.records import AuditEventRecord

        try:
            await self._repo.record_event(AuditEventRecord(
                event_type=event_type, actor_user_id=user_id,
                subject_user_id=subject_user_id, tenant_id=tenant_id,
                session_id=session_id, request_id=request_id,
                detail=detail or {}, occurred_at=time.time()))
        except Exception:  # audit must never break the auth flow
            import logging

            logging.getLogger(__name__).warning(
                "audit event %s failed", event_type, exc_info=True)


def _scrub_client(client: dict | None) -> dict:
    """Keep only coarse, non-identifying client hints for the session list."""
    if not client:
        return {}
    allowed = {}
    for key in ("platform", "client_name"):
        value = client.get(key)
        if isinstance(value, str) and value:
            allowed[key] = value[:40]
    if "user_agent" in client and isinstance(client["user_agent"], str):
        allowed["user_agent_hash"] = hashlib.sha256(
            client["user_agent"].encode("utf-8")).hexdigest()[:16]
    return allowed


_DEFAULT_SERVICE: AuthSessionService | None = None


def get_session_service() -> AuthSessionService | None:
    """Enterprise mode only; None in file mode (routes gate on this)."""
    global _DEFAULT_SERVICE
    if not config.enterprise_sessions_enabled():
        return None
    if _DEFAULT_SERVICE is None:
        from app.persistence.repositories.identity import (
            SqlAlchemyIdentityRepository)

        _DEFAULT_SERVICE = AuthSessionService(
            SqlAlchemyIdentityRepository())
    return _DEFAULT_SERVICE


def reset_session_service() -> None:
    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = None
