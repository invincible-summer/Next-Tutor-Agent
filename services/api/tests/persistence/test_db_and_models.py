"""Unit lane: async engine + schema + repository behavior on sandboxed sqlite.

File-mode inertness (no engine without DATABASE_URL) is asserted here too —
that contract is the load-bearing one for existing deployments. Integration
coverage against ephemeral PostgreSQL/Redis is marked separately.
"""
from __future__ import annotations

import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class EngineModeTest(StorageSandboxTestCase):
    def test_file_mode_is_inert_without_database_url(self) -> None:
        from app.persistence import db

        self.assertIsNone(db.database_url())
        self.assertFalse(db.enterprise_mode())
        with self.assertRaises(RuntimeError):
            db.get_engine()

    def test_postgresql_url_normalized_to_asyncpg(self) -> None:
        from app.persistence.db import _normalize

        self.assertEqual(_normalize("postgresql://u:p@h:5/db"),
                         "postgresql+asyncpg://u:p@h:5/db")
        self.assertEqual(_normalize("postgresql+asyncpg://u:p@h/db"),
                         "postgresql+asyncpg://u:p@h/db")


class _SqliteTestCase(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    """One file-backed sqlite engine per test, inside the storage sandbox."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from app.persistence import db
        from app.persistence.repositories.identity import (
            SqlAlchemyIdentityRepository)

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'persistence.db').as_posix()}")
        await db.create_all(self._db_url)
        self.repo = SqlAlchemyIdentityRepository(
            db.session_factory(self._db_url))

    async def asyncTearDown(self) -> None:
        from app.persistence import db

        engine = db._engine_cache.get(self._db_url)
        if engine is not None:
            await engine.dispose()
        db._engine_cache.pop(self._db_url, None)
        db._session_factory_cache.pop(self._db_url, None)
        await super().asyncTearDown()


class AccountRepositoryTest(_SqliteTestCase):
    async def test_create_and_fetch_account_roundtrip(self) -> None:
        from app.persistence.repositories.records import AccountRecord

        record = AccountRecord(
            user_id="usr_a1", email="A@Example.com ", username="alice",
            role="student", password_hash="hash", token_version=0,
            created_at=100.0, profile={"grade": "本科", "prefs": {"a": 1}})
        await self.repo.create_account(record)
        fetched = await self.repo.get_account_by_email("a@example.com")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.user_id, "usr_a1")
        self.assertEqual(fetched.email, "a@example.com")
        self.assertEqual(fetched.profile["grade"], "本科")
        self.assertEqual(fetched.profile["prefs"], {"a": 1})
        by_id = await self.repo.get_account_by_id("usr_a1")
        self.assertIsNotNone(by_id)

    async def test_duplicate_email_raises_domain_error(self) -> None:
        from app.persistence.repositories.records import AccountRecord
        from app.persistence.repositories.protocols import DuplicateEmailError

        await self.repo.create_account(
            AccountRecord(user_id="usr_a", email="dup@x.io"))
        with self.assertRaises(DuplicateEmailError):
            await self.repo.create_account(
                AccountRecord(user_id="usr_b", email="dup@x.io"))

    async def test_bump_token_version_and_update(self) -> None:
        from app.persistence.repositories.records import AccountRecord

        record = AccountRecord(user_id="usr_v", email="v@x.io",
                               token_version=3)
        await self.repo.create_account(record)
        self.assertTrue(await self.repo.bump_token_version("usr_v"))
        fetched = await self.repo.get_account_by_id("usr_v")
        assert fetched is not None
        self.assertEqual(fetched.token_version, 4)
        fetched.username = "renamed"
        await self.repo.update_account(fetched)
        again = await self.repo.get_account_by_id("usr_v")
        assert again is not None
        self.assertEqual(again.username, "renamed")

    async def test_delete_account(self) -> None:
        from app.persistence.repositories.records import AccountRecord

        await self.repo.create_account(
            AccountRecord(user_id="usr_d", email="d@x.io"))
        self.assertTrue(await self.repo.delete_account("usr_d"))
        self.assertIsNone(await self.repo.get_account_by_id("usr_d"))
        self.assertFalse(await self.repo.delete_account("usr_d"))


class TenantRepositoryTest(_SqliteTestCase):
    async def test_tenant_membership_roundtrip(self) -> None:
        from app.persistence.repositories.records import (AccountRecord,
                                                          MembershipRecord,
                                                          TenantRecord)

        await self.repo.create_account(
            AccountRecord(user_id="usr_t1", email="t1@x.io"))
        await self.repo.create_tenant(TenantRecord(
            tenant_id="tnt_p1", kind="personal", name="personal",
            owner_user_id="usr_t1", created_at=1.0))
        await self.repo.create_membership(MembershipRecord(
            membership_id="mem_1", tenant_id="tnt_p1", user_id="usr_t1",
            tenant_role="owner", created_at=1.0))
        got = await self.repo.get_membership("tnt_p1", "usr_t1")
        self.assertIsNotNone(got)
        memberships = await self.repo.list_memberships_for_user("usr_t1")
        self.assertEqual(len(memberships), 1)
        await self.repo.set_active_tenant("usr_t1", "tnt_p1")
        account = await self.repo.get_account_by_id("usr_t1")
        assert account is not None
        self.assertEqual(account.active_tenant_id, "tnt_p1")


class SessionRepositoryTest(_SqliteTestCase):
    async def _create_session(self):
        from app.persistence.repositories.records import (AccountRecord,
                                                          AuthSessionRecord,
                                                          RefreshTokenRecord)

        await self.repo.create_account(
            AccountRecord(user_id="usr_s", email="s@x.io"))
        session = AuthSessionRecord(
            session_id="ses_1", user_id="usr_s", tenant_id=None,
            created_at=10.0, expires_at=10.0 + 86400 * 30)
        token = RefreshTokenRecord(
            id="rtx_1", session_id="ses_1", token_hash="h1",
            issued_at=10.0, expires_at=session.expires_at)
        await self.repo.create_session(session, token)
        return session, token

    async def test_session_lifecycle_and_revocation(self) -> None:
        await self._create_session()
        listed = await self.repo.list_sessions_for_user("usr_s")
        self.assertEqual([s.session_id for s in listed], ["ses_1"])
        self.assertTrue(
            await self.repo.revoke_session("ses_1", "user_logout"))
        revoked = await self.repo.get_session("ses_1")
        assert revoked is not None
        self.assertIsNotNone(revoked.revoked_at)
        self.assertEqual(revoked.revoked_reason, "user_logout")
        # idempotent: already-revoked returns False
        self.assertFalse(
            await self.repo.revoke_session("ses_1", "again"))

    async def test_refresh_rotation_and_reuse_detection(self) -> None:
        from app.persistence.repositories.records import RefreshTokenRecord
        from app.persistence.repositories.protocols import RepositoryError

        _, token = await self._create_session()
        replacement = RefreshTokenRecord(
            id="rtx_2", session_id="ses_1", token_hash="h2",
            issued_at=11.0, expires_at=token.expires_at)
        await self.repo.rotate_refresh_token(token, replacement)
        old = await self.repo.find_refresh_token("h1")
        assert old is not None
        self.assertIsNotNone(old.rotated_at)
        self.assertEqual(old.replaced_by_id, "rtx_2")
        # Presenting the already-rotated token again: the repository refuses
        # the second rotation; the service layer maps this to family
        # revocation (reuse => revoke the whole session).
        with self.assertRaises(RepositoryError):
            await self.repo.rotate_refresh_token(
                old, RefreshTokenRecord(
                    id="rtx_3", session_id="ses_1", token_hash="h3",
                    issued_at=12.0, expires_at=token.expires_at))

    async def test_revoke_session_tokens_marks_active_rotated(self) -> None:
        await self._create_session()
        await self.repo.revoke_session_tokens("ses_1")
        token = await self.repo.find_refresh_token("h1")
        assert token is not None
        self.assertIsNotNone(token.rotated_at)


class AuditRepositoryTest(_SqliteTestCase):
    async def test_audit_events_append_and_query(self) -> None:
        from app.persistence.repositories.records import AuditEventRecord

        for i in range(3):
            await self.repo.record_event(AuditEventRecord(
                event_type="auth.login", actor_user_id=f"usr_{i}",
                occurred_at=float(i), detail={"platform": "web"}))
        events = await self.repo.list_events(limit=10)
        self.assertEqual(len(events), 3)
        # newest first (autoincrement id desc)
        self.assertEqual(events[0].actor_user_id, "usr_2")
        mine = await self.repo.list_events(user_id="usr_0", limit=10)
        self.assertEqual(len(mine), 1)


if __name__ == "__main__":
    unittest.main()
