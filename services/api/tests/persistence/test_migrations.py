"""Alembic migration contract: empty database reaches head without drift.

Runs the real alembic stack against a sandboxed sqlite database — production
runs PostgreSQL, but the DDL is dialect-shared (JSONB via with_variant), and
this lane catches the "migration forgot a column autogenerate knows about"
class of drift on every test run. `alembic check` asserting no residual diff
means future --autogenerate revisions start from a clean baseline.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.support.storage_sandbox import StorageSandboxTestCase

_SERVICES_API = Path(__file__).resolve().parents[2]


class MigrationSmokeTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._db_path = self.root / "migrate.db"
        self._env_overrides = {
            "DATABASE_URL": f"sqlite+aiosqlite:///{self._db_path.as_posix()}",
            "EDU_MIGRATION_DATABASE_URL": "",
        }
        self._saved: dict[str, str | None] = {}
        for key, value in self._env_overrides.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        super().tearDown()

    def _alembic(self, *args: str) -> None:
        from alembic.config import main as alembic_main

        cwd = os.getcwd()
        os.chdir(_SERVICES_API)
        try:
            alembic_main([*args])
        finally:
            os.chdir(cwd)

    def test_empty_database_upgrades_to_head_and_matches_models(self) -> None:
        self._alembic("upgrade", "head")
        import sqlalchemy

        engine = sqlalchemy.create_engine(
            f"sqlite:///{self._db_path.as_posix()}")
        try:
            existing = set(sqlalchemy.inspect(engine).get_table_names())
        finally:
            engine.dispose()
        expected = {
            "users", "credentials", "tenants", "memberships", "auth_sessions",
            "refresh_tokens", "identity_providers", "audit_events",
            "alembic_version",
            *{f"{domain}_documents" for domain in (
                "chat", "library", "textbooks", "notes", "assessment",
                "evidence", "classroom", "orchestration", "assistant")},
        }
        self.assertTrue(expected <= existing, missing := expected - existing)
        self.assertFalse(missing)
        # No residual model drift: autogenerate against the migrated schema
        # must be a no-op (keeps future --autogenerate runs clean).
        self._alembic("check")

    def test_downgrade_to_base_removes_schema(self) -> None:
        self._alembic("upgrade", "head")
        self._alembic("downgrade", "base")
        import sqlalchemy

        engine = sqlalchemy.create_engine(
            f"sqlite:///{self._db_path.as_posix()}")
        try:
            existing = set(sqlalchemy.inspect(engine).get_table_names())
        finally:
            engine.dispose()
        self.assertEqual(existing, {"alembic_version"})


if __name__ == "__main__":
    unittest.main()
