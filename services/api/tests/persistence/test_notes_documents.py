"""Notes domain SQL cutover contract (notes → notes_documents, ADR-0017).

Runs the real app.notes public API (NoteVault + agent history) with the
notes domain routed to SQL over the sandboxed sqlite lane.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class NotesDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'notes.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "notes=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

        from app.notes import sql_store

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

    def test_vault_lifecycle_and_isolation(self) -> None:
        from app import notes as notes_store

        v1 = notes_store.load_vault("u1")
        meta = v1.create_note("力学笔记", "牛顿第一定律")
        v1.write_note(meta["id"], "牛顿第一定律：惯性。\n[[光学笔记]]")
        notes_store.save_vault(v1)

        v2 = notes_store.load_vault("u2")
        self.assertEqual(v2.summaries(), [])
        self.assertEqual(len(v2.folders), len(v1.folders))  # seeded folders

        reloaded = notes_store.load_vault("u1")
        self.assertEqual(len(reloaded.summaries()), 1)
        self.assertIn("惯性", reloaded.read_note(meta["id"]))

    def test_write_read_revisions_restore(self) -> None:
        from app import notes as notes_store

        v = notes_store.load_vault("u1")
        meta = v.create_note("版本测试", "v1")
        note_id = meta["id"]
        for i in range(2, 5):
            v.write_note(note_id, f"v{i}")
        revisions = v.list_revisions(note_id)
        self.assertEqual([r["revision"] for r in revisions], [4, 3, 2, 1])
        self.assertEqual(v.read_note(note_id), "v4")
        self.assertEqual(v.read_revision(note_id, 2), "v2")
        restored = v.restore_revision(note_id, 2)
        self.assertEqual(restored["revision"], 5)
        self.assertEqual(v.read_note(note_id), "v2")

    def test_remove_note_clears_all_rows(self) -> None:
        from app import notes as notes_store
        from app.notes import sql_store

        v = notes_store.load_vault("u1")
        meta = v.create_note("删除测试", "content")
        v.write_note(meta["id"], "content")
        notes_store.save_vault(v)
        notes_store.append_agent_message("u1", meta["id"], "user", "hi")

        self.assertTrue(v.remove_note(meta["id"]))
        notes_store.save_vault(v)
        self.assertEqual(sql_store.read_note_content("u1", meta["id"]), "")
        self.assertEqual(sql_store.list_revisions("u1", meta["id"]), [])
        self.assertIsNone(sql_store.load_agent_state("u1", meta["id"]))

    def test_agent_history_roundtrip(self) -> None:
        from app import notes as notes_store

        notes_store.append_agent_message("u1", "", "user", "仓库级对话")
        notes_store.append_agent_message("u1", "note_x", "user", "笔记级")
        view = notes_store.agent_history_view("u1", "note_x")
        self.assertEqual([m["content"] for m in view["messages"]],
                         ["笔记级"])
        vault_view = notes_store.agent_history_view("u1", "")
        self.assertEqual([m["content"] for m in vault_view["messages"]],
                         ["仓库级对话"])
        self.assertTrue(notes_store.delete_agent_history("u1", "note_x"))
        self.assertEqual(notes_store.agent_history_view("u1", "note_x")
                         ["messages"], [])

    def test_importer_walks_file_vaults(self) -> None:
        # Seed the file side, then run the notes walker end to end.
        self._saved_env["DATABASE_URL"] = None  # file mode for seeding
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        from app import notes as notes_store

        v = notes_store.load_vault("u_file")
        meta = v.create_note("导入笔记", "导入内容")
        v.write_note(meta["id"], "导入内容")
        notes_store.save_vault(v)
        notes_store.append_agent_message("u_file", meta["id"], "user", "hi")

        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "notes=sql"
        from app.persistence.documents import bridge

        bridge.reset_worker()
        import asyncio
        import sys

        script = (sys.modules["app"].__file__.rsplit("/app/", 1)[0]
                  + "/../../scripts/migrations/runtime_to_enterprise/"
                  "import_documents.py")
        import subprocess

        sub_env = {**os.environ, "NEXT_TUTOR_DATA_DIR": str(self.root)}
        result = subprocess.run(
            [sys.executable, script, "--domain", "notes"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        verify = subprocess.run(
            [sys.executable, script, "--domain", "notes", "--verify"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(verify.returncode, 0, verify.stdout[-400:])

        # Imported vault loads through the public API in SQL mode.
        imported = notes_store.load_vault("u_file")
        self.assertEqual(imported.read_note(meta["id"]), "导入内容")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
