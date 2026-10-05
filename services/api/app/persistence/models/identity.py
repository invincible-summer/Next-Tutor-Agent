"""Identity/tenant/session schema (SQLAlchemy models, enterprise lane).

Covers the core tables of the identity domain:

- ``users`` / ``credentials`` — account records and password/SSO credentials
- ``tenants`` / ``memberships`` — personal & organization tenants
- ``auth_sessions`` / ``refresh_tokens`` — rotating refresh sessions; the
  refresh token values themselves are never stored, only SHA-256 hashes
- ``identity_providers`` — enterprise OIDC provider registrations (schema
  now, SSO flow itself lands with enterprise SSO)
- ``audit_events`` — auth/tenant security audit trail

Timestamps are float unix seconds, matching the file-mode ``User`` dataclass.
JSON columns render as JSONB on PostgreSQL and plain JSON elsewhere (the
unit-test sqlite lane), so both drivers exercise the same mapping.
"""
from __future__ import annotations

from sqlalchemy import (BigInteger, Boolean, Float, ForeignKey, Integer,
                        JSON, String, Text, UniqueConstraint)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


def _json_variant():
    """JSONB on PostgreSQL, portable JSON elsewhere."""
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(120), default="")
    # File-mode parity: role here is the platform role (student/parent/
    # teacher/admin); tenant-scoped roles live on memberships.
    role: Mapped[str] = mapped_column(String(32), default="student")
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    last_login_at: Mapped[float] = mapped_column(Float, default=0.0)
    # UserProfile serialized (name/grade/school/subjects/avatar/prefs).
    profile: Mapped[dict] = mapped_column(_json_variant(), default=dict)
    active_tenant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("tenants.id", name="fk_active_tenant"),
        nullable=True)
    # Soft delete marker for account purge windows; file mode deletes rows,
    # enterprise keeps tombstones for audit referential integrity.
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class CredentialModel(Base):
    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("user_id", "kind",
                                       name="uq_credentials_user_kind"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # "password" today; "oidc:<provider>" and future kinds slot in without
    # schema changes. Secret material is a bcrypt hash, never plaintext.
    kind: Mapped[str] = mapped_column(String(64), default="password")
    secret_hash: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(_json_variant(), default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)
    last_used_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class TenantModel(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), default="personal")
    name: Mapped[str] = mapped_column(String(200), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    # Personal tenants record their owning user; organization tenants may
    # keep it empty until an owner claims administrative membership.
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    settings: Mapped[dict] = mapped_column(_json_variant(), default=dict)


class MembershipModel(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id",
                                       name="uq_memberships_tenant_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # owner/admin/member within the tenant; platform admin stays on users.role.
    tenant_role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuthSessionModel(Base):
    """One refresh-session family per login (device/browser).

    The session IS the revocation family: presenting an already-rotated
    refresh token revokes the whole session (see RefreshTokenModel).
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Tenant context captured at login; switching tenant re-issues tokens.
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[float] = mapped_column(Float, default=0.0)
    last_refreshed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(64), default="")
    # Coarse client fingerprint for the session list UI — platform name and
    # hashed user agent only, never raw headers/IPs (privacy).
    client: Mapped[dict] = mapped_column(_json_variant(), default=dict)


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        index=True)
    # SHA-256 hex of the opaque token; raw token values exist only in the
    # client response.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[float] = mapped_column(Float, default=0.0)
    # Non-null marks a superseded token: presenting it again is reuse and
    # revokes the entire session family.
    rotated_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    replaced_by_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IdentityProviderModel(Base):
    """Enterprise OIDC provider registration (Authorization Code + PKCE)."""

    __tablename__ = "identity_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
        nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="oidc")
    issuer: Mapped[str] = mapped_column(String(500), default="")
    client_id: Mapped[str] = mapped_column(String(200), default="")
    # Client secrets are stored encrypted at rest with the instance key; the
    # SSO flow (later stage) refuses to start when this is plaintext.
    client_secret_encrypted: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[dict] = mapped_column(_json_variant(), default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    extra: Mapped[dict] = mapped_column(_json_variant(), default=dict)


class AuditEventModel(Base):
    """Append-only security audit trail (login/refresh/revoke/password…)."""

    __tablename__ = "audit_events"

    # Server-side monotonic id; sqlite maps autoincrement to INTEGER rowid.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    occurred_at: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True,
                                                      index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(80), default="")
    # Redacted, non-secret details only (no passwords/tokens/raw UA).
    detail: Mapped[dict] = mapped_column(_json_variant(), default=dict)
