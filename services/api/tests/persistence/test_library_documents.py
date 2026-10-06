"""Library/textbooks SQL cutover contract
(library → library_documents, textbooks → textbooks_documents, ADR-0017).

Runs the real Library + textbook registry API (folders/files, textbook
state machine, restart reconciliation, owner walks) with both domains
routed to SQL over the sandboxed sqlite lane. Extracted texts and
original binaries stay on the file layer in both modes.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class LibraryDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'lib.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "library=sql,textbooks=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

        from app.core import library_sql

        self.assertTrue(library_sql.library_use_sql())
        self.assertTrue(library_sql.textbooks_use_sql())

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

    def test_library_crud_roundtrip_and_chunk_source(self) -> None:
        from app.core import library

        lib = library.load_library("u1")
        self.assertEqual(lib.files, [])
        folder = lib.create_folder("物理资料")
        meta = lib.add_file(folder["id"], "力学.pdf", "惯性与受力的文本内容")
        library.save_library(lib)

        reloaded = library.load_library("u1")
        self.assertEqual(len(reloaded.folders), 1)
        self.assertEqual(len(reloaded.files), 1)
        self.assertEqual(reloaded.find_file(meta["id"])["filename"],
                         "力学.pdf")
        # Chunks lazily rebuild from the file-side extracted text.
        self.assertTrue(reloaded.chunks_for(meta["id"]))
        # .txt lives on the file layer in both modes.
        self.assertTrue((library.library_data_dir("u1")
                         / f"{meta['id']}.txt").exists())

        self.assertTrue(reloaded.remove_file(meta["id"]))
        library.save_library(reloaded)
        self.assertIsNone(library.load_library("u1").find_file(meta["id"]))
        self.assertFalse((library.library_data_dir("u1")
                          / f"{meta['id']}.txt").exists())

    def test_textbook_lifecycle_and_public_scope(self) -> None:
        from app.core import library, textbook

        lib = library.load_library("u1")
        volume = lib.add_file("", "热学.pdf", "热学教材正文" * 20)
        library.save_library(lib)

        rec = textbook.create_textbook(
            "u1", file_id=volume["id"], title="热学",
            subject="物理")
        # Idempotent on file_id: re-registering returns the same record.
        again = textbook.create_textbook(
            "u1", file_id=volume["id"], title="热学2")
        self.assertEqual(again["id"], rec["id"])

        updated = textbook.update_textbook(
            "u1", rec["id"], status="ready", chapter_count=12)
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(textbook.find_textbook("u1", rec["id"])
                         ["chapter_count"], 12)

        # Public namespace: admin-written rows are readable from any
        # account through the scoped lookup.
        pub_lib = library.load_library(textbook.PUBLIC_STUDENT_ID)
        pub_file = pub_lib.add_file("", "公用数学.pdf", "公用教材正文" * 20)
        library.save_library(pub_lib)
        pub_rec = textbook.create_textbook(
            textbook.PUBLIC_STUDENT_ID, file_id=pub_file["id"],
            title="公用数学", scope="public")
        scoped = textbook.find_textbook_scoped("u1", pub_rec["id"])
        self.assertIsNotNone(scoped)
        self.assertEqual(scoped[1], textbook.PUBLIC_STUDENT_ID)

        self.assertTrue(textbook.remove_textbook("u1", rec["id"]))
        self.assertIsNone(textbook.find_textbook("u1", rec["id"]))

    def test_reconcile_and_owner_walks(self) -> None:
        from app.core import library, textbook

        lib = library.load_library("u1")
        volume = lib.add_file("", "光学.pdf", "光学教材正文内容" * 10)
        library.save_library(lib)

        # Interrupted build with intent + attempts exhausted -> failed.
        exhausted = textbook.create_textbook("u1", file_id=volume["id"],
                                             title="光学")
        textbook.set_build_job("u1", exhausted["id"], state="running",
                               attempt=textbook.BUILD_JOB_MAX_AUTO_ATTEMPTS,
                               intent={"use_llm": True})
        # Legacy building record with source text on disk -> auto-recover.
        legacy_file = lib.add_file("", "电磁学.pdf", "电磁学教材正文内容" * 10)
        library.save_library(lib)
        legacy = textbook.create_textbook("u1", file_id=legacy_file["id"],
                                          title="电磁学")

        report = textbook.reconcile_stale_builds()
        self.assertIn(("u1", exhausted["id"], "retry_exhausted"),
                      report.failed)
        recovered_ids = [tb for _, tb, _ in report.recovered]
        self.assertIn(legacy["id"], recovered_ids)
        self.assertEqual(
            textbook.find_textbook("u1", exhausted["id"])["status"],
            "graph_failed")

        queued = [(sid, tb) for sid, tb, _ in
                  textbook.interrupted_build_jobs()]
        self.assertIn(("u1", legacy["id"]), queued)

        # Single → group migration runs through the SQL owner walk.
        migrated = textbook.migrate_legacy_single_to_groups()
        self.assertGreaterEqual(migrated, 1)
        self.assertEqual(
            textbook.find_textbook("u1", legacy["id"])["kind"], "group")
        # Idempotent second pass.
        self.assertEqual(textbook.migrate_legacy_single_to_groups(), 0)

    def test_importer_walks_file_library_and_textbooks(self) -> None:
        # Seed the file side, then run both walkers end to end.
        self._saved_env["DATABASE_URL"] = None  # file mode for seeding
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        from app.core import library, textbook

        lib = library.load_library("u_file")
        meta = lib.add_file("", "种子.pdf", "种子正文")
        library.save_library(lib)
        rec = textbook.create_textbook("u_file", file_id=meta["id"],
                                       title="种子教材")

        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "library=sql,textbooks=sql"
        from app.persistence.documents import bridge

        bridge.reset_worker()
        import subprocess
        import sys

        script = (sys.modules["app"].__file__.rsplit("/app/", 1)[0]
                  + "/../../scripts/migrations/runtime_to_enterprise/"
                  "import_documents.py")
        sub_env = {**os.environ, "NEXT_TUTOR_DATA_DIR": str(self.root)}
        for domain in ("library", "textbooks"):
            result = subprocess.run(
                [sys.executable, script, "--domain", domain],
                capture_output=True, text=True, env=sub_env)
            self.assertEqual(result.returncode, 0, result.stderr[-800:])
            verify = subprocess.run(
                [sys.executable, script, "--domain", domain, "--verify"],
                capture_output=True, text=True, env=sub_env)
            self.assertEqual(verify.returncode, 0, verify.stdout[-400:])

        self.assertEqual(len(library.load_library("u_file").files), 1)
        self.assertEqual(
            textbook.find_textbook("u_file", rec["id"])["title"], "种子教材")


if __name__ == "__main__":    # pragma: no cover
    unittest.main()
