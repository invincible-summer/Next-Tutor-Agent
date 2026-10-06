"""Site-assistant SQL cutover contract
(assistant → assistant_documents, ADR-0017).

Runs the real store API (conversations/drafts, derived index) with the
assistant domain routed to SQL over the sandboxed sqlite lane.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class AssistantDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'asst.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "assistant=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

        from app.agents.site_assistant import sql_store

        self.assertTrue(sql_store.use_sql())

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

    def test_conversation_lifecycle_and_idempotency(self) -> None:
        from app.agents.site_assistant import store

        r1 = store.create_conversation(
            "u1", client_request_id="cr-1", title="力学问答")
        r2 = store.create_conversation("u1", client_request_id="cr-1")
        self.assertEqual(r1["conversation_id"], r2["conversation_id"])

        r1["messages"].append({"message_id": "m1", "role": "user",
                               "content": "什么是惯性"})
        store.save_conversation("u1", r1)
        reloaded = store.load_conversation("u1", r1["conversation_id"])
        self.assertEqual(len(reloaded["messages"]), 1)

        # Derived index lists newest-first with summaries.
        other = store.create_conversation(
            "u1", client_request_id="cr-2", title="第二会话")
        items, total = store.list_conversations("u1", offset=0, limit=10)
        self.assertEqual(total, 2)
        self.assertEqual(items[0]["conversation_id"],
                         other["conversation_id"])
        self.assertEqual(items[1]["message_count"], 1)

        # Idempotency survives a reload (client_request_id rides the doc).
        r3 = store.create_conversation("u1", client_request_id="cr-1")
        self.assertEqual(r3["conversation_id"], r1["conversation_id"])

    def test_delete_cascades_associated_drafts(self) -> None:
        from app.agents.site_assistant import store

        conv = store.create_conversation("u1", client_request_id="c-1")
        cid = conv["conversation_id"]
        keep = store.create_draft("u1", {"kind": "link",
                                         "conversation_id": "other"})
        drop = store.create_draft("u1", {"kind": "link",
                                         "conversation_id": cid})
        self.assertTrue(store.delete_conversation("u1", cid))
        self.assertIsNone(store.load_conversation("u1", cid))
        self.assertIsNotNone(store.load_draft("u1", keep["draft_id"]))
        self.assertIsNone(store.load_draft("u1", drop["draft_id"]))
        items, _ = store.list_conversations("u1")
        self.assertEqual(items, [])
        self.assertTrue(store.delete_conversation("u1", "missing"))  # 幂等

    def test_draft_consume_and_purge(self) -> None:
        from app.agents.site_assistant import store

        draft = store.create_draft("u1", {"kind": "workflow"})
        loaded = store.load_draft("u1", draft["draft_id"])
        self.assertFalse(loaded.get("expired"))
        self.assertFalse(loaded.get("consumed"))

        consumed = store.consume_draft(
            "u1", draft["draft_id"], result_entity={"id": "ent-1"})
        self.assertTrue(consumed["consumed"])
        self.assertEqual(consumed["result_entity"], {"id": "ent-1"})
        again = store.consume_draft("u1", draft["draft_id"])
        self.assertTrue(again["consumed"])   # 幂等
        self.assertIsNone(store.consume_draft("u1", "astd_missing"))

        # Expired drafts are swept by the purge loop body.
        from app.agents.site_assistant import sql_store

        sql_store.save_draft_payload("u1", {
            "draft_id": "astd_expired", "kind": "link",
            "expires_at": "2000-01-01T00:00:00+00:00", "consumed": False})
        removed = store.purge_expired_drafts("u1")
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(store.load_draft("u1", "astd_expired"))

    def test_owner_isolation_and_scan(self) -> None:
        from app.agents.site_assistant import store

        a = store.create_conversation("alice", client_request_id="a-1")
        store.create_conversation("bob", client_request_id="b-1")
        self.assertIsNone(store.load_conversation("bob", a["conversation_id"]))

        owners = store.list_owners_with_data()
        self.assertIn("alice", owners)
        self.assertIn("bob", owners)
        pairs = dict((cid, owner) for _tenant, owner, rec
                     in store.scan_conversation_records()
                     for cid in [rec["conversation_id"]])
        self.assertEqual(pairs[a["conversation_id"]], "alice")

    def test_importer_walks_file_conversations(self) -> None:
        # Seed the file side, then run the assistant walker end to end.
        self._saved_env["DATABASE_URL"] = None  # file mode for seeding
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        from app.agents.site_assistant import store

        conv = store.create_conversation("u_file", client_request_id="f-1",
                                         title="导入会话")
        conv["messages"].append({"message_id": "m1", "role": "user",
                                 "content": "hi"})
        store.save_conversation("u_file", conv)
        store.create_draft("u_file", {"kind": "link",
                                      "conversation_id":
                                      conv["conversation_id"]})

        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "assistant=sql"
        from app.persistence.documents import bridge

        bridge.reset_worker()
        import subprocess
        import sys

        script = (sys.modules["app"].__file__.rsplit("/app/", 1)[0]
                  + "/../../scripts/migrations/runtime_to_enterprise/"
                  "import_documents.py")
        sub_env = {**os.environ, "NEXT_TUTOR_DATA_DIR": str(self.root)}
        result = subprocess.run(
            [sys.executable, script, "--domain", "assistant"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        again = subprocess.run(
            [sys.executable, script, "--domain", "assistant"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(again.returncode, 0, again.stderr[-500:])
        verify = subprocess.run(
            [sys.executable, script, "--domain", "assistant", "--verify"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(verify.returncode, 0, verify.stdout[-400:])

        # Imported conversation loads through the public API in SQL mode.
        imported = store.load_conversation("u_file", conv["conversation_id"])
        self.assertEqual(len(imported["messages"]), 1)
        items, _ = store.list_conversations("u_file")
        self.assertEqual(items[0]["title"], "导入会话")


if __name__ == "__main__":    # pragma: no cover
    unittest.main()
