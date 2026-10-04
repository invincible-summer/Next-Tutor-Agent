"""Identity backend facade: file store (default) or enterprise repository.

File mode keeps every existing behavior — the backend is a thin async shim
over ``app.identity.store``. Enterprise mode (DATABASE_URL set) makes the
PostgreSQL repository authoritative for authentication while SHADOW-WRITING
the file account at registration/login: the many file-store consumers
(classroom runs, guest runtime, site assistant, admin console, account
purge…) keep working unchanged during the migration window. Per-domain
owners migrate their reads in later stages; the runtime importer
(scripts/migrations/runtime_to_enterprise) converges pre-existing accounts.

Known window caveat, by design: profile-field updates still write the file
store only. Authentication fields (role/token_version/credentials) go
through this facade and stay dual-consistent.
"""
from __future__ import annotations

import time
import uuid

from .models import User, UserProfile
from .principal import RequestPrincipal  # noqa: F401  (typing for build_principal)
from .store import create_user as _file_create_user
from .store import email_exists as _file_email_exists
from .store import get_by_email as _file_get_by_email
from .store import get_by_id as _file_get_by_id
from .store import touch_login as _file_touch_login
from .store import update_user as _file_update_user


def _record_to_user(record) -> User:
    from app.persistence.repositories.records import AccountRecord

    assert isinstance(record, AccountRecord)
    return User(
        id=record.user_id, email=record.email, username=record.username,
        password_hash=record.password_hash, role=record.role,
        created_at=record.created_at, last_login_at=record.last_login_at,
        token_version=record.token_version,
        profile=UserProfile.from_dict(record.profile),
    )


def _user_profile_dict(user: User) -> dict:
    return user.profile.to_dict()


class FileIdentityBackend:
    """Default backend: the legacy JSON account store, unchanged."""

    async def email_exists(self, email: str) -> bool:
        return _file_email_exists(email)

    async def get_by_email(self, email: str) -> User | None:
        return _file_get_by_email(email)

    async def get_by_id(self, user_id: str) -> User | None:
        return _file_get_by_id(user_id)

    async def touch_login(self, user_id: str) -> None:
        _file_touch_login(user_id)


class EnterpriseIdentityBackend(FileIdentityBackend):
    """PostgreSQL-backed identity with file-store shadow writes.

    Registration order: file store first (existing semantics and 409
    behavior), then repository (account + personal tenant + owner
    membership + password credential, atomically). A repository failure
    after a successful file write surfaces as an error — the account stays
    usable in file mode and the importer heals the database later.
    """

    def __init__(self, repository) -> None:
        self._repo = repository

    async def register_account(self, *, email: str, username: str,
                               password_hash: str, role: str,
                               profile: UserProfile,
                               client: dict | None = None) -> tuple[User, str]:
        """Create the account in both stores; returns (user, tenant_id)."""
        from app.persistence.repositories.records import (AccountRecord,
                                                          CredentialRecord,
                                                          MembershipRecord,
                                                          TenantRecord)

        # 1. File store (authoritative for legacy consumers, preserves the
        #    existing email-taken error).
        user = _file_create_user(email=email, username=username,
                                 password_hash=password_hash, role=role,
                                 profile=profile)
        # 2. Repository: account + personal tenant + membership + credential.
        now = time.time()
        tenant_id = f"tnt_{uuid.uuid4().hex[:10]}"
        try:
            await self._repo.create_account(AccountRecord(
                user_id=user.id, email=user.email, username=user.username,
                role=role, password_hash=password_hash,
                token_version=user.token_version, created_at=now,
                profile=_user_profile_dict(user), active_tenant_id=tenant_id))
            await self._repo.create_tenant(TenantRecord(
                tenant_id=tenant_id, kind="personal",
                name=f"personal:{user.email}", display_name=user.username,
                owner_user_id=user.id, created_at=now))
            await self._repo.create_membership(MembershipRecord(
                membership_id=f"mem_{uuid.uuid4().hex[:10]}",
                tenant_id=tenant_id, user_id=user.id, tenant_role="owner",
                created_at=now))
            await self._repo.create_credential(CredentialRecord(
                id=f"crd_{uuid.uuid4().hex[:10]}", user_id=user.id,
                kind="password", secret_hash=password_hash, created_at=now,
                updated_at=now))
        except Exception:
            # Shadow-write failure leaves a file-mode-usable account; the
            # runtime importer is idempotent and heals it. Surface loudly.
            import logging

            logging.getLogger(__name__).exception(
                "enterprise registration shadow-write failed for %s",
                user.id)
            raise
        return user, tenant_id

    async def _with_password_hash(self, record):
        """Users table keeps no secrets; the bcrypt hash lives in
        credentials(kind=password) and re-attaches here for the User
        mapping (login verifies against it)."""
        credential = await self._repo.get_password_credential(
            record.user_id)
        if credential is not None:
            record.password_hash = credential.secret_hash
        return record

    async def get_by_email(self, email: str) -> User | None:
        record = await self._repo.get_account_by_email(email)
        if record is None:
            return None
        return _record_to_user(await self._with_password_hash(record))

    async def get_by_id(self, user_id: str) -> User | None:
        record = await self._repo.get_account_by_id(user_id)
        if record is None:
            return None
        return _record_to_user(await self._with_password_hash(record))

    async def touch_login(self, user_id: str) -> None:
        record = await self._repo.get_account_by_id(user_id)
        if record is not None:
            record.last_login_at = time.time()
            await self._repo.update_account(record)
        # Shadow: legacy consumers read last_login_at from the file store.
        _file_touch_login(user_id)

    async def bump_token_version_both(self, user_id: str) -> bool:
        """Revoke-all-devices anchor, kept consistent on both stores."""
        ok_repo = await self._repo.bump_token_version(user_id)
        user = _file_get_by_id(user_id)
        if user is None:
            return ok_repo
        user.token_version += 1
        _file_update_user(user)
        return True

    @property
    def repository(self):
        """Composition root / routes may read the repository explicitly."""
        return self._repo

    async def active_tenant_id(self, user_id: str) -> str | None:
        record = await self._repo.get_account_by_id(user_id)
        return record.active_tenant_id if record else None

    async def build_principal(self, principal) -> "RequestPrincipal":
        """Fill tenant/membership facts from the repository.

        Token claims win when present (they pin the tenant the session was
        issued for); otherwise the account's active tenant applies.
        """
        record = await self._repo.get_account_by_id(principal.user_id)
        if record is None:
            return principal
        tenant_id = principal.tenant_id or record.active_tenant_id
        principal.tenant_id = tenant_id
        if tenant_id:
            membership = await self._repo.get_membership(
                tenant_id, principal.user_id)
            if membership is not None:
                principal.membership_id = membership.membership_id
                principal.tenant_role = membership.tenant_role
        return principal


_DEFAULT_BACKEND: FileIdentityBackend | EnterpriseIdentityBackend | None = None
_DEFAULT_ENTERPRISE_BACKEND: EnterpriseIdentityBackend | None = None


def _build_backend() -> FileIdentityBackend | EnterpriseIdentityBackend:
    from app.persistence import db

    if not db.enterprise_mode():
        return FileIdentityBackend()
    from app.persistence.repositories.identity import (
        SqlAlchemyIdentityRepository)

    return EnterpriseIdentityBackend(SqlAlchemyIdentityRepository())


def identity_backend():
    """Process backend; rebuilt lazily so tests can toggle DATABASE_URL."""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = _build_backend()
    return _DEFAULT_BACKEND


def reset_identity_backend() -> None:
    """Tests: drop the cached backend (mode or engine may have changed)."""
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = None
