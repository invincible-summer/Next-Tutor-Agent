"""Plain-data records exchanged between domain services and repositories.

These dataclasses mirror the file-mode ``identity.models.User`` vocabulary
(email/user_id/token_version, float unix timestamps, profile dict) so the
same domain code runs unchanged against either backend. No SQLAlchemy types
appear here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(slots=True)
class AccountRecord:
    user_id: str
    email: str
    username: str = ""
    role: str = "student"
    password_hash: str = ""
    token_version: int = 0
    created_at: float = 0.0
    last_login_at: float = 0.0
    profile: dict[str, Any] = field(default_factory=dict)
    active_tenant_id: str | None = None

    def redacted(self) -> "AccountRecord":
        """Copy without the password hash (for logs / cross-layer passing)."""
        return replace(self, password_hash="")


@dataclass(slots=True)
class CredentialRecord:
    id: str
    user_id: str
    kind: str = "password"
    secret_hash: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_used_at: float | None = None
    revoked_at: float | None = None


@dataclass(slots=True)
class TenantRecord:
    tenant_id: str
    kind: str = "personal"          # personal | organization
    name: str = ""
    display_name: str = ""
    owner_user_id: str | None = None
    created_at: float = 0.0
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MembershipRecord:
    membership_id: str
    tenant_id: str
    user_id: str
    tenant_role: str = "member"     # owner | admin | member
    created_at: float = 0.0


@dataclass(slots=True)
class AuthSessionRecord:
    session_id: str
    user_id: str
    tenant_id: str | None = None
    created_at: float = 0.0
    expires_at: float = 0.0
    last_refreshed_at: float | None = None
    revoked_at: float | None = None
    revoked_reason: str = ""
    client: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RefreshTokenRecord:
    id: str
    session_id: str
    token_hash: str                 # sha256 hex; raw values never persisted
    issued_at: float = 0.0
    expires_at: float = 0.0
    rotated_at: float | None = None
    replaced_by_id: str | None = None

    @property
    def is_active(self) -> bool:
        return self.rotated_at is None


@dataclass(slots=True)
class AuditEventRecord:
    event_type: str                 # "auth.login", "auth.refresh_reuse", …
    actor_user_id: str | None = None
    subject_user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    request_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    occurred_at: float = 0.0
    id: int | None = None
