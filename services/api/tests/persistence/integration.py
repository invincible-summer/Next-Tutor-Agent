"""Integration lane: real PostgreSQL + Valkey behind TEST_DATABASE_URL / TEST_CACHE_URL.

The module intentionally carries no ``test_`` prefix: neither unittest
discovery nor the CI shard planner collects it into the ordinary matrix,
because these suites talk to external services. They run in the CI
``backend-enterprise`` job (postgres + valkey service containers) and locally::

    TEST_DATABASE_URL=postgresql://u:p@localhost:5432/tutor_test \\
    TEST_CACHE_URL=redis://localhost:6379/0 \\
        python -m tests tests.persistence.integration

The enterprise acceptance criterion lives here: two independent engines (two
API instances) concurrently read and write the same tenant, coordinated by
the database instead of process-local locks, and concurrent refresh rotation
produces exactly one winner with the losing side revoking the family.
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

from tests.support.storage_sandbox import StorageSandboxTestCase

_SERVICES_API = Path(__file__).resolve().parents[2]
_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
_CACHE_URL = os.getenv("TEST_CACHE_URL", "").strip()


@unittest.skipUnless(_DATABASE_URL,
                     "TEST_DATABASE_URL not set — PostgreSQL integration skipped")
class _PostgresIntegrationTestCase(StorageSandboxTestCase,
                                   unittest.IsolatedAsyncioTestCase):
    """One migrated database per run; each test builds a fresh engine pair."""

    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from alembic.config import main as alembic_main

        cls._saved_env = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "CACHE_URL": os.environ.get("CACHE_URL"),
        }
        os.environ["DATABASE_URL"] = _DATABASE_URL
        if _CACHE_URL:
            # Session-active short cache then goes through the shared tier,
            # matching the enterprise deployment shape.
            os.environ["CACHE_URL"] = _CACHE_URL
        cwd = os.getcwd()
        os.chdir(_SERVICES_API)
        try:
            # downgrade is a no-op on a fresh database; it lets a reused
            # CI/local database start from scratch deterministically.
            alembic_main(["downgrade", "base"])
            alembic_main(["upgrade", "head"])
        finally:
            os.chdir(cwd)

    @classmethod
    def tearDownClass(cls) -> None:
        from app.identity.keys import reset_default_keyring
        from app.identity.sessions import reset_session_service
        from app.persistence import db

        db.reset_engine()
        reset_session_service()
        reset_default_keyring()
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.persistence import db
        from app.persistence.repositories.identity import (
            SqlAlchemyIdentityRepository)

        # Two independent engines + factories on one database == two API
        # processes; nothing is shared except PostgreSQL itself.
        self.engine_a = db.create_engine(_DATABASE_URL)
        self.engine_b = db.create_engine(_DATABASE_URL)
        self.repo_a = SqlAlchemyIdentityRepository(
            async_sessionmaker(self.engine_a, expire_on_commit=False))
        self.repo_b = SqlAlchemyIdentityRepository(
            async_sessionmaker(self.engine_b, expire_on_commit=False))

    async def asyncTearDown(self) -> None:
        for attr in ("engine_a", "engine_b"):
            engine = getattr(self, attr, None)
            if engine is not None:
                await engine.dispose()
        await super().asyncTearDown()

    def _unique(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    async def _seed_account(self, repo, *, user_id: str,
                            tenant_id: str | None = None,
                            with_membership: bool = True) -> None:
        from app.persistence.repositories.records import (AccountRecord,
                                                          CredentialRecord,
                                                          MembershipRecord,
                                                          TenantRecord)

        now = time.time()
        await repo.create_account(
            AccountRecord(user_id=user_id,
                          email=f"{user_id}@integration.test",
                          username=user_id, role="student",
                          created_at=now, last_login_at=now),
            credential=CredentialRecord(
                id=f"crd_{uuid.uuid4().hex[:12]}", user_id=user_id,
                secret_hash="x" * 60, created_at=now, updated_at=now))
        if tenant_id is not None:
            await repo.create_tenant(TenantRecord(
                tenant_id=tenant_id, kind="personal", name=tenant_id,
                display_name=tenant_id, owner_user_id=user_id, created_at=now))
            if with_membership:
                await repo.create_membership(MembershipRecord(
                    membership_id=f"mem_{uuid.uuid4().hex[:12]}",
                    tenant_id=tenant_id, user_id=user_id,
                    tenant_role="owner", created_at=now))
            await repo.set_active_tenant(user_id, tenant_id)


class PostgresMigrationIntegrationTest(_PostgresIntegrationTestCase):
    async def test_migration_creates_identity_schema_on_postgres(self) -> None:
        from sqlalchemy import inspect

        def _table_names(sync_conn) -> set:
            return set(inspect(sync_conn).get_table_names())

        async with self.engine_a.connect() as conn:
            tables = await conn.run_sync(_table_names)
        expected = {"users", "credentials", "tenants", "memberships",
                    "auth_sessions", "refresh_tokens", "identity_providers",
                    "audit_events"}
        self.assertTrue(expected.issubset(tables), expected - tables)

    def test_models_match_the_migrated_schema(self) -> None:
        # Sync on purpose: alembic's env.py drives its own asyncio.run, which
        # cannot nest inside an IsolatedAsyncioTestCase event loop.
        from alembic.config import main as alembic_main

        cwd = os.getcwd()
        os.chdir(_SERVICES_API)
        try:
            # Raises SystemExit on drift; a plain return means the models and
            # the migrated schema agree on PostgreSQL (JSONB variants and all).
            alembic_main(["check"])
        except SystemExit as exc:
            self.fail(f"alembic check reported schema drift: exit {exc.code}")
        finally:
            os.chdir(cwd)


class TwoInstanceTenantConcurrencyTest(_PostgresIntegrationTestCase):
    """The Stage B acceptance: two instances on one tenant, DB-coordinated."""

    async def test_two_instances_concurrently_read_and_write_same_tenant(
            self) -> None:
        from app.persistence.repositories.records import AuditEventRecord

        user_id = self._unique("usr")
        tenant_id = self._unique("tnt")
        await self._seed_account(self.repo_a, user_id=user_id,
                                 tenant_id=tenant_id)

        async def instance_a() -> None:
            account = await self.repo_a.get_account_by_id(user_id)
            assert account is not None
            await self.repo_a.bump_token_version(user_id)
            await self.repo_a.record_event(AuditEventRecord(
                event_type="integration.instance_a", actor_user_id=user_id,
                tenant_id=tenant_id, request_id="req_integration_a",
                occurred_at=time.time()))

        async def instance_b() -> None:
            tenant = await self.repo_b.get_tenant(tenant_id)
            membership = await self.repo_b.get_membership(tenant_id, user_id)
            assert tenant is not None and membership is not None
            await self.repo_b.bump_token_version(user_id)
            await self.repo_b.record_event(AuditEventRecord(
                event_type="integration.instance_b", actor_user_id=user_id,
                tenant_id=tenant_id, request_id="req_integration_b",
                occurred_at=time.time()))

        await asyncio.gather(instance_a(), instance_b())

        # Instance B observed instance A's committed account/tenant/membership
        # through its own engine, and both concurrent writes landed exactly
        # once — serialized by the database rows, not by any process lock.
        account = await self.repo_b.get_account_by_id(user_id)
        assert account is not None
        self.assertEqual(account.token_version, 2)
        self.assertEqual(account.active_tenant_id, tenant_id)

        # The audit trail from both instances is visible through BOTH engines.
        for name, repo in (("a", self.repo_a), ("b", self.repo_b)):
            events = [e for e in await repo.list_events(user_id=user_id,
                                                        limit=50)
                      if e.event_type.startswith("integration.")]
            self.assertEqual(
                {e.event_type for e in events},
                {"integration.instance_a", "integration.instance_b"},
                f"audit events incomplete through instance {name}")

    async def test_cross_instance_unique_constraint_arbitrates_conflict(
            self) -> None:
        from app.persistence.repositories.protocols import RepositoryError
        from app.persistence.repositories.records import MembershipRecord

        user_id = self._unique("usr")
        tenant_id = self._unique("tnt")
        await self._seed_account(self.repo_a, user_id=user_id,
                                 tenant_id=tenant_id, with_membership=False)

        record_a = MembershipRecord(
            membership_id=self._unique("mem"), tenant_id=tenant_id,
            user_id=user_id, tenant_role="member", created_at=time.time())
        record_b = replace(record_a, membership_id=self._unique("mem"))
        results = await asyncio.gather(
            self.repo_a.create_membership(record_a),
            self.repo_b.create_membership(record_b),
            return_exceptions=True)
        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, BaseException)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        self.assertIsInstance(losers[0], RepositoryError)
        # The surviving row is visible through both engines.
        stored = await self.repo_b.get_membership(tenant_id, user_id)
        self.assertIsNotNone(stored)

    async def test_concurrent_refresh_rotation_is_atomic_across_instances(
            self) -> None:
        from app.identity.sessions import AuthSessionService, SessionError

        user_id = self._unique("usr")
        await self._seed_account(self.repo_a, user_id=user_id)
        service_a = AuthSessionService(self.repo_a)
        service_b = AuthSessionService(self.repo_b)

        issuance = await service_a.start_session(
            user_id=user_id, token_version=0, tenant_id=None,
            client={"platform": "integration"})
        raw = issuance["refresh_token"]
        session_id = issuance["session_id"]

        # The same refresh token hits two API instances at once: the
        # conditional UPDATE admits exactly one rotation.
        results = await asyncio.gather(
            service_a.refresh(raw_refresh_token=raw, request_id="req_ra"),
            service_b.refresh(raw_refresh_token=raw, request_id="req_rb"),
            return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        wins = [r for r in results if not isinstance(r, BaseException)]
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], SessionError)
        self.assertEqual(errors[0].code, "refresh_token_reused")

        # The losing side treated the clash as theft and revoked the family —
        # visible through both engines.
        for repo in (self.repo_a, self.repo_b):
            session = await repo.get_session(session_id)
            self.assertIsNotNone(session)
            self.assertIsNotNone(session.revoked_at)
            self.assertEqual(session.revoked_reason, "refresh_token_reuse")

        # The winner's replacement token belongs to a revoked family now.
        with self.assertRaises(SessionError) as ctx:
            await service_b.refresh(
                raw_refresh_token=wins[0]["refresh_token"],
                request_id="req_after_race")
        self.assertEqual(ctx.exception.code, "session_revoked")


@unittest.skipUnless(_CACHE_URL,
                     "TEST_CACHE_URL not set — cache server integration skipped")
class RedisPrimitivesIntegrationTest(StorageSandboxTestCase,
                                     unittest.IsolatedAsyncioTestCase):
    """Shared-tier primitives on real Redis: cross-instance visibility."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from app.persistence.cache.resp import RespCachePrimitives

        self.cache_a = RespCachePrimitives(_CACHE_URL)
        self.cache_b = RespCachePrimitives(_CACHE_URL)

    async def asyncTearDown(self) -> None:
        for attr in ("cache_a", "cache_b"):
            cache = getattr(self, attr, None)
            if cache is not None:
                await cache.aclose()
        await super().asyncTearDown()

    def _unique(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    async def test_rate_limit_counts_across_instances(self) -> None:
        key = self._unique("integration_rl")
        first = await self.cache_a.rate_limit(key, limit=2, window_seconds=60)
        second = await self.cache_b.rate_limit(key, limit=2, window_seconds=60)
        third = await self.cache_a.rate_limit(key, limit=2, window_seconds=60)
        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(third.allowed)
        self.assertGreater(third.retry_after_seconds, 0.0)

    async def test_short_cache_visible_across_instances(self) -> None:
        key = self._unique("integration_kv")
        await self.cache_a.set(key, b"value-42", ttl_seconds=60)
        self.assertEqual(await self.cache_b.get(key), b"value-42")
        await self.cache_b.delete(key)
        self.assertIsNone(await self.cache_a.get(key))
        await self.cache_a.set(key, b"short-lived", ttl_seconds=1)
        await asyncio.sleep(1.2)
        self.assertIsNone(await self.cache_b.get(key))

    async def test_lease_mutual_exclusion_across_instances(self) -> None:
        key = self._unique("integration_lease")
        async with self.cache_a.lease(key, ttl_seconds=30) as held:
            self.assertIsNotNone(held)
            async with self.cache_b.lease(key, ttl_seconds=30) as second:
                self.assertIsNone(second)
        # Released: the other instance can acquire it now.
        async with self.cache_b.lease(key, ttl_seconds=30) as reacquired:
            self.assertIsNotNone(reacquired)

    async def test_unreachable_redis_degrades_to_in_process_primitives(
            self) -> None:
        from app.persistence.cache.resp import RespCachePrimitives

        broken = RespCachePrimitives("redis://127.0.0.1:1/0")
        try:
            result = await broken.rate_limit("integration_broken", 1, 60.0)
            self.assertTrue(result.allowed)
            await broken.set("integration_broken", b"x", ttl_seconds=60)
            self.assertEqual(await broken.get("integration_broken"), b"x")
            self.assertFalse(await broken.ping())
        finally:
            await broken.aclose()
