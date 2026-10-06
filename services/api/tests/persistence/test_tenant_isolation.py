"""A/B tenant isolation matrix (WS5c, ADR-0017).

Same owner id, two tenants: every SQL-routed domain must keep tenant A's
rows structurally invisible to tenant B and to the legacy "" scope, and
B's writes to the same keys must never overwrite A's rows. Knowledge
graphs and exports stay on the file/object-store layer by design (their
owner-directory isolation is unchanged) and are out of this matrix.

Assessment state (CAT instances, submission bindings) rides the evidence
journal, so it is covered by the evidence row below.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase

_TENANT_A = "tnt_aaaaaa"
_TENANT_B = "tnt_bbbbbb"
_OWNER = "usr_shared"


class TenantIsolationMatrixTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'tenants.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        # Default routing (post-cutover): every domain serves from SQL.
        os.environ["DATABASE_URL"] = self._db_url
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

    def tearDown(self) -> None:
        from app.persistence import db
        from app.persistence.documents import bridge

        bridge.reset_worker()
        for key, old in self._saved_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        engine = db._engine_cache.pop(self._db_url, None)
        if engine is not None:
            engine.sync_engine.dispose()
        db._session_factory_cache.pop(self._db_url, None)
        super().tearDown()

    # -- seed one fact per domain under tenant A ---------------------------

    def _seed_tenant_a(self) -> dict:
        from app.agents.learning_orchestration import sql_store as orch_sql
        from app.agents.site_assistant import store as asst_store
        from app.agents.student_model.evaluation import (
            sql_store as evidence_sql)
        from app.classroom import sql_store as classroom_sql
        from app.core import library, session_sql, textbook
        from app.notes import sql_store as notes_sql
        from app.illustration import persistence as ill

        keys: dict[str, str] = {}
        lib = library.Library(_OWNER)
        lib.add_file("", "物理.pdf", "惯性是与受力无关的属性" * 5)
        library.save_library(lib)
        session_sql.save_session_payload(
            {"session_id": "sess_matrix1", "student_id": _OWNER,
             "created_at": 1.0, "title": "A的会话"})
        keys["session_id"] = "sess_matrix1"
        notes_sql.save_vault_payload(
            _OWNER, {"student_id": _OWNER, "threads": {"th1": {}}})
        evidence_sql.save_profile_blob(_OWNER, {"grade": "g9", "profile": 1})
        orch_sql.save_state_payload(
            _OWNER, {"student_id": _OWNER, "goals": [], "schema_version": 1})
        conv = asst_store.create_conversation(
            _OWNER, client_request_id="matrix-1", title="A的助手会话")
        keys["conversation_id"] = conv["conversation_id"]
        ill.write(_OWNER, "jobs", "illjob_" + "1" * 24,
                  {"job_id": "illjob_" + "1" * 24, "question_id": "q-1",
                   "question_revision": 1, "status": "ready",
                   "created_at": 1.0})
        classroom_sql.put_owner_record(_OWNER, {"lifecycle": "active",
                                                "idempotency": {}})
        tb = textbook.create_textbook(_OWNER, file_id="fileofa1",
                                      title="A的教材")
        keys["textbook_id"] = tb["id"]
        return keys

    def _assert_all_invisible(self, keys: dict) -> None:
        """Whatever the current tenant scope: tenant A's facts are gone."""
        from app.agents.learning_orchestration import sql_store as orch_sql
        from app.agents.site_assistant import store as asst_store
        from app.agents.student_model.evaluation import (
            sql_store as evidence_sql)
        from app.classroom import sql_store as classroom_sql
        from app.core import library, session_sql, textbook
        from app.notes import sql_store as notes_sql
        from app.illustration import persistence as ill

        self.assertEqual(library.load_library(_OWNER).files, [])
        self.assertIsNone(
            session_sql.load_session_payload(keys["session_id"]))
        self.assertIsNone(notes_sql.load_vault_payload(_OWNER))
        self.assertIsNone(evidence_sql.load_profile_blob(_OWNER))
        self.assertIsNone(orch_sql.load_state_payload(_OWNER))
        items, total = asst_store.list_conversations(_OWNER)
        self.assertEqual((items, total), ([], 0))
        self.assertIsNone(ill.read(_OWNER, "jobs", "illjob_" + "1" * 24))
        self.assertIsNone(ill.find_job(_OWNER, "q-1", 1))
        self.assertIsNone(classroom_sql.get_owner_record(_OWNER))
        self.assertIsNone(
            textbook.find_textbook(_OWNER, keys["textbook_id"]))

    def test_same_owner_two_tenants_fully_isolated(self) -> None:
        from app.persistence.documents import tenant_scope

        with tenant_scope(_TENANT_A):
            keys = self._seed_tenant_a()

        # Tenant B: same owner id, structurally different world.
        with tenant_scope(_TENANT_B):
            self._assert_all_invisible(keys)
            # B writing the SAME keys must create its own rows, never
            # touch A's (unique key includes the tenant).
            from app.illustration import persistence as ill

            ill.write(_OWNER, "jobs", "illjob_" + "1" * 24,
                      {"job_id": "illjob_" + "1" * 24, "question_id": "q-1",
                       "question_revision": 1, "status": "failed",
                       "created_at": 2.0})

        # Legacy "" scope cannot see tenant rows either.
        with tenant_scope(""):
            self._assert_all_invisible(keys)

        # Tenant A still sees its own untouched facts.
        with tenant_scope(_TENANT_A):
            from app.illustration import persistence as ill

            self.assertEqual(
                ill.read(_OWNER, "jobs", "illjob_" + "1" * 24)["status"],
                "ready")

    def test_background_scope_enumeration_sees_both_tenants(self) -> None:
        """Cross-tenant sweeps enumerate (tenant, owner) scopes — tenant
        rows are never silently skipped by janitors/recovery."""
        from app.agents.site_assistant import store as asst_store
        from app.persistence.documents import tenant_scope

        with tenant_scope(_TENANT_A):
            self._seed_tenant_a()
        scopes = asst_store.list_owner_scopes()
        self.assertIn((_TENANT_A, _OWNER), scopes)

        from app.core import textbook
        from app.illustration import persistence as ill

        with tenant_scope(_TENANT_B):
            ill.write("other_owner", "jobs", "illjob_" + "2" * 24,
                      {"job_id": "illjob_" + "2" * 24, "status": "queued"})
            asst_store.create_conversation(
                "other_owner", client_request_id="matrix-b",
                title="B的助手会话")
        # Assistant scopes enumerate conversation/draft rows across tenants.
        scopes = asst_store.list_owner_scopes()
        self.assertIn((_TENANT_A, _OWNER), scopes)
        self.assertIn((_TENANT_B, "other_owner"), scopes)
        # Illustration rows ride the same table under their own kinds.
        from app.persistence.documents import bridge

        async def _ill_scopes():
            return await bridge.repository("assistant").list_owner_scopes(
                kinds=("jobs",))

        self.assertIn((_TENANT_B, "other_owner"),
                      bridge.call(_ill_scopes))
        # Textbook registry sweeps enumerate scopes the same way.
        with tenant_scope(_TENANT_B):
            textbook.create_textbook("other_owner", file_id="fob1",
                                     title="B的教材")
        self.assertIn((_TENANT_B, "other_owner"),
                      textbook.registry_owner_scopes())

    def test_account_purge_clears_every_tenant(self) -> None:
        from app.illustration import persistence as ill
        from app.persistence.documents import bridge, tenant_scope

        with tenant_scope(_TENANT_A):
            ill.write(_OWNER, "jobs", "illjob_" + "3" * 24,
                      {"job_id": "illjob_" + "3" * 24, "status": "ready"})
        with tenant_scope(""):
            ill.write(_OWNER, "jobs", "illjob_" + "4" * 24,
                      {"job_id": "illjob_" + "4" * 24, "status": "ready"})

        async def _purge() -> int:
            repo = bridge.repository("assistant")
            return await repo.purge_owner(_OWNER, tenant_id=None)

        self.assertGreaterEqual(bridge.call(_purge), 2)
        with tenant_scope(_TENANT_A):
            self.assertIsNone(
                ill.read(_OWNER, "jobs", "illjob_" + "3" * 24))
        with tenant_scope(""):
            self.assertIsNone(
                ill.read(_OWNER, "jobs", "illjob_" + "4" * 24))


if __name__ == "__main__":    # pragma: no cover
    unittest.main()
