"""SQLAlchemy implementation of the identity repository protocols.

Every method is self-contained (one session, one transaction), mirroring the
file-mode store semantics where each store function holds the file lock for
its whole operation. Cross-repository invariants (e.g. registration creating
user + personal tenant + membership atomically) are composed by the domain
service inside :meth:`transactional`, never by chaining store calls blind.

``factory`` may be an ``async_sessionmaker`` bound to a test engine; it
defaults to the process factory bound to ``DATABASE_URL``.
"""
from __future__ import annotations

import contextlib
import time
from typing import Any, AsyncIterator

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import db
from ..models import (AuditEventModel, AuthSessionModel, CredentialModel,
                      MembershipModel, RefreshTokenModel, TenantModel,
                      UserModel)
from .protocols import DuplicateEmailError, RepositoryError
from .records import (AccountRecord, AuditEventRecord, AuthSessionRecord,
                      CredentialRecord, MembershipRecord, RefreshTokenRecord,
                      TenantRecord)


def _user_to_record(row: UserModel) -> AccountRecord:
    return AccountRecord(
        user_id=row.id, email=row.email, username=row.username,
        role=row.role, token_version=row.token_version,
        created_at=row.created_at, last_login_at=row.last_login_at,
        profile=dict(row.profile or {}), active_tenant_id=row.active_tenant_id,
    )


def _apply_account(record: AccountRecord, row: UserModel) -> None:
    # Same normalization as the file-mode store: emails are stored lowercased
    # and trimmed, and lookups rely on that canonical form.
    row.email = record.email.strip().lower()
    row.username = record.username
    row.role = record.role
    row.token_version = record.token_version
    row.created_at = record.created_at
    row.last_login_at = record.last_login_at
    row.profile = dict(record.profile or {})
    row.active_tenant_id = record.active_tenant_id


class SqlAlchemyIdentityRepository:
    """Account/Tenant/Session/Audit protocols over the enterprise schema."""

    def __init__(self, factory: Any = None) -> None:
        self._factory = factory

    def _sessionmaker(self):
        return self._factory if self._factory is not None else db.session_factory()

    @contextlib.asynccontextmanager
    async def _sess(self) -> AsyncIterator[Any]:
        async with self._sessionmaker()() as sess:
            yield sess

    @contextlib.asynccontextmanager
    async def _txn(self) -> AsyncIterator[Any]:
        async with self._sessionmaker()() as sess:
            async with sess.begin():
                yield sess

    transactional = _txn  # domain-service composition scope (public alias)

    # --- accounts ---------------------------------------------------------

    async def create_account(self, record: AccountRecord,
                             credential: CredentialRecord | None = None,
                             ) -> AccountRecord:
        row = UserModel(id=record.user_id)
        _apply_account(record, row)
        try:
            async with self._txn() as sess:
                sess.add(row)
                if credential is not None:
                    sess.add(CredentialModel(
                        id=credential.id, user_id=record.user_id,
                        kind=credential.kind,
                        secret_hash=credential.secret_hash,
                        extra=dict(credential.extra or {}),
                        created_at=credential.created_at,
                        updated_at=credential.updated_at))
        except IntegrityError as exc:
            raise DuplicateEmailError(record.email) from exc
        return record

    async def get_account_by_email(self, email: str) -> AccountRecord | None:
        key = email.strip().lower()
        async with self._sess() as sess:
            row = (await sess.execute(
                select(UserModel).where(UserModel.email == key)
            )).scalar_one_or_none()
            return _user_to_record(row) if row else None

    async def get_account_by_id(self, user_id: str) -> AccountRecord | None:
        async with self._sess() as sess:
            row = (await sess.execute(
                select(UserModel).where(UserModel.id == user_id)
            )).scalar_one_or_none()
            return _user_to_record(row) if row else None

    async def update_account(self, record: AccountRecord) -> AccountRecord:
        async with self._txn() as sess:
            row = await sess.get(UserModel, record.user_id)
            if row is None:
                raise RepositoryError(f"account_not_found:{record.user_id}")
            _apply_account(record, row)
        return record

    async def bump_token_version(self, user_id: str) -> bool:
        async with self._txn() as sess:
            row = await sess.get(UserModel, user_id)
            if row is None:
                return False
            row.token_version += 1
        return True

    async def list_accounts(self) -> list[AccountRecord]:
        async with self._sess() as sess:
            rows = (await sess.execute(
                select(UserModel).order_by(UserModel.created_at)
            )).scalars().all()
            return [_user_to_record(r) for r in rows]

    async def delete_account(self, user_id: str) -> bool:
        async with self._txn() as sess:
            row = await sess.get(UserModel, user_id)
            if row is None:
                return False
            await sess.delete(row)
        return True

    async def get_password_credential(self, user_id: str) -> CredentialRecord | None:
        async with self._sess() as sess:
            row = (await sess.execute(
                select(CredentialModel).where(
                    CredentialModel.user_id == user_id,
                    CredentialModel.kind == "password")
            )).scalar_one_or_none()
            if row is None:
                return None
            return CredentialRecord(
                id=row.id, user_id=row.user_id, kind=row.kind,
                secret_hash=row.secret_hash, extra=dict(row.extra or {}),
                created_at=row.created_at, updated_at=row.updated_at,
                last_used_at=row.last_used_at, revoked_at=row.revoked_at)

    async def create_credential(self, credential: CredentialRecord) -> CredentialRecord:
        try:
            async with self._txn() as sess:
                sess.add(CredentialModel(
                    id=credential.id, user_id=credential.user_id,
                    kind=credential.kind, secret_hash=credential.secret_hash,
                    extra=dict(credential.extra or {}),
                    created_at=credential.created_at,
                    updated_at=credential.updated_at))
        except IntegrityError as exc:
            raise RepositoryError(
                f"credential_conflict:{credential.user_id}:{credential.kind}"
            ) from exc
        return credential

    async def update_password_credential(self, credential: CredentialRecord) -> None:
        async with self._txn() as sess:
            row = await sess.get(CredentialModel, credential.id)
            if row is None:
                raise RepositoryError(
                    f"credential_not_found:{credential.id}")
            row.secret_hash = credential.secret_hash
            row.updated_at = credential.updated_at
            row.revoked_at = credential.revoked_at
            row.last_used_at = credential.last_used_at

    # --- tenants & memberships --------------------------------------------

    async def create_tenant(self, record: TenantRecord) -> TenantRecord:
        try:
            async with self._txn() as sess:
                sess.add(TenantModel(
                    id=record.tenant_id, kind=record.kind, name=record.name,
                    display_name=record.display_name,
                    owner_user_id=record.owner_user_id,
                    created_at=record.created_at,
                    settings=dict(record.settings or {})))
        except IntegrityError as exc:  # pragma: no cover - defensive
            raise RepositoryError(f"tenant_conflict:{record.tenant_id}") from exc
        return record

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        async with self._sess() as sess:
            row = await sess.get(TenantModel, tenant_id)
            if row is None:
                return None
            return TenantRecord(
                tenant_id=row.id, kind=row.kind, name=row.name,
                display_name=row.display_name, owner_user_id=row.owner_user_id,
                created_at=row.created_at, settings=dict(row.settings or {}))

    async def create_membership(self, record: MembershipRecord) -> MembershipRecord:
        try:
            async with self._txn() as sess:
                sess.add(MembershipModel(
                    id=record.membership_id, tenant_id=record.tenant_id,
                    user_id=record.user_id, tenant_role=record.tenant_role,
                    created_at=record.created_at))
        except IntegrityError as exc:
            raise RepositoryError(
                f"membership_conflict:{record.tenant_id}:{record.user_id}"
            ) from exc
        return record

    async def get_membership(self, tenant_id: str,
                             user_id: str) -> MembershipRecord | None:
        async with self._sess() as sess:
            row = (await sess.execute(
                select(MembershipModel).where(
                    MembershipModel.tenant_id == tenant_id,
                    MembershipModel.user_id == user_id)
            )).scalar_one_or_none()
            if row is None:
                return None
            return MembershipRecord(
                membership_id=row.id, tenant_id=row.tenant_id,
                user_id=row.user_id, tenant_role=row.tenant_role,
                created_at=row.created_at)

    async def list_memberships_for_user(self, user_id: str) -> list[MembershipRecord]:
        async with self._sess() as sess:
            rows = (await sess.execute(
                select(MembershipModel).where(MembershipModel.user_id == user_id)
            )).scalars().all()
            return [MembershipRecord(
                membership_id=r.id, tenant_id=r.tenant_id, user_id=r.user_id,
                tenant_role=r.tenant_role, created_at=r.created_at)
                for r in rows]

    async def set_active_tenant(self, user_id: str, tenant_id: str) -> None:
        async with self._txn() as sess:
            row = await sess.get(UserModel, user_id)
            if row is None:
                raise RepositoryError(f"account_not_found:{user_id}")
            row.active_tenant_id = tenant_id

    # --- auth sessions & refresh tokens ------------------------------------

    async def create_session(self, session: AuthSessionRecord,
                             refresh_token: RefreshTokenRecord) -> AuthSessionRecord:
        async with self._txn() as sess:
            sess.add(AuthSessionModel(
                id=session.session_id, user_id=session.user_id,
                tenant_id=session.tenant_id, created_at=session.created_at,
                expires_at=session.expires_at,
                last_refreshed_at=session.last_refreshed_at,
                client=dict(session.client or {})))
            sess.add(RefreshTokenModel(
                id=refresh_token.id, session_id=refresh_token.session_id,
                token_hash=refresh_token.token_hash,
                issued_at=refresh_token.issued_at,
                expires_at=refresh_token.expires_at))
        return session

    async def get_session(self, session_id: str) -> AuthSessionRecord | None:
        async with self._sess() as sess:
            row = await sess.get(AuthSessionModel, session_id)
            if row is None:
                return None
            return AuthSessionRecord(
                session_id=row.id, user_id=row.user_id, tenant_id=row.tenant_id,
                created_at=row.created_at, expires_at=row.expires_at,
                last_refreshed_at=row.last_refreshed_at,
                revoked_at=row.revoked_at, revoked_reason=row.revoked_reason,
                client=dict(row.client or {}))

    async def list_sessions_for_user(self, user_id: str) -> list[AuthSessionRecord]:
        async with self._sess() as sess:
            rows = (await sess.execute(
                select(AuthSessionModel)
                .where(AuthSessionModel.user_id == user_id)
                .order_by(AuthSessionModel.created_at.desc())
            )).scalars().all()
            return [AuthSessionRecord(
                session_id=r.id, user_id=r.user_id, tenant_id=r.tenant_id,
                created_at=r.created_at, expires_at=r.expires_at,
                last_refreshed_at=r.last_refreshed_at, revoked_at=r.revoked_at,
                revoked_reason=r.revoked_reason, client=dict(r.client or {}))
                for r in rows]

    async def revoke_session(self, session_id: str, reason: str) -> bool:
        now = time.time()
        async with self._txn() as sess:
            result = await sess.execute(
                update(AuthSessionModel)
                .where(AuthSessionModel.id == session_id,
                       AuthSessionModel.revoked_at.is_(None))
                .values(revoked_at=now, revoked_reason=reason))
            return bool(result.rowcount)

    async def find_refresh_token(self, token_hash: str) -> RefreshTokenRecord | None:
        async with self._sess() as sess:
            row = (await sess.execute(
                select(RefreshTokenModel).where(
                    RefreshTokenModel.token_hash == token_hash)
            )).scalar_one_or_none()
            if row is None:
                return None
            return RefreshTokenRecord(
                id=row.id, session_id=row.session_id, token_hash=row.token_hash,
                issued_at=row.issued_at, expires_at=row.expires_at,
                rotated_at=row.rotated_at, replaced_by_id=row.replaced_by_id)

    async def rotate_refresh_token(self, old: RefreshTokenRecord,
                                   replacement: RefreshTokenRecord) -> None:
        now = time.time()
        async with self._txn() as sess:
            claimed = await sess.execute(
                update(RefreshTokenModel)
                .where(RefreshTokenModel.id == old.id,
                       RefreshTokenModel.rotated_at.is_(None))
                .values(rotated_at=now, replaced_by_id=replacement.id))
            if not claimed.rowcount:
                raise RepositoryError("refresh_token_already_rotated")
            sess.add(RefreshTokenModel(
                id=replacement.id, session_id=replacement.session_id,
                token_hash=replacement.token_hash,
                issued_at=replacement.issued_at,
                expires_at=replacement.expires_at))

    async def revoke_session_tokens(self, session_id: str) -> None:
        # Mark every still-active token as rotated: refreshing with them then
        # trips the reuse detector, which revokes the whole family. Session
        # revocation itself (revoked_at) is the primary gate.
        now = time.time()
        async with self._txn() as sess:
            await sess.execute(
                update(RefreshTokenModel)
                .where(RefreshTokenModel.session_id == session_id,
                       RefreshTokenModel.rotated_at.is_(None))
                .values(rotated_at=now)
                .execution_options(synchronize_session=False))

    # --- audit ---------------------------------------------------------------

    async def record_event(self, event: AuditEventRecord) -> None:
        async with self._txn() as sess:
            sess.add(AuditEventModel(
                occurred_at=event.occurred_at, event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                subject_user_id=event.subject_user_id,
                tenant_id=event.tenant_id, session_id=event.session_id,
                request_id=event.request_id, detail=dict(event.detail or {})))

    async def list_events(self, *, user_id: str | None = None,
                          limit: int = 100) -> list[AuditEventRecord]:
        async with self._sess() as sess:
            stmt = select(AuditEventModel).order_by(
                AuditEventModel.id.desc()).limit(limit)
            if user_id is not None:
                stmt = stmt.where(
                    (AuditEventModel.actor_user_id == user_id)
                    | (AuditEventModel.subject_user_id == user_id))
            rows = (await sess.execute(stmt)).scalars().all()
            return [AuditEventRecord(
                id=r.id, event_type=r.event_type,
                actor_user_id=r.actor_user_id,
                subject_user_id=r.subject_user_id, tenant_id=r.tenant_id,
                session_id=r.session_id, request_id=r.request_id,
                detail=dict(r.detail or {}), occurred_at=r.occurred_at)
                for r in rows]
