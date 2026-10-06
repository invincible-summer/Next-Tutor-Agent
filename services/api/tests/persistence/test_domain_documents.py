"""Domain document repository contract on the sandboxed sqlite lane.

Covers the generic SqlDocumentRepository: keying, isolation (owner and
tenant), epoch compare-and-swap, mutate read-modify-write, purge, and the
per-domain routing config that gates SQL activation.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class DocumentConfigTest(StorageSandboxTestCase):
    def tearDown(self) -> None:
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        os.environ.pop("DATABASE_URL", None)
        super().tearDown()

    def test_file_mode_never_routes_to_sql(self) -> None:
        from app.persistence.documents import sql_enabled

        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "all"
        self.assertFalse(sql_enabled("chat"))

    def test_defaults_and_overrides(self) -> None:
        from app.persistence.documents import backend_for, sql_enabled

        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
        # Transition default: file until the cutover flips it.
        self.assertEqual(backend_for("chat"), "file")
        self.assertFalse(sql_enabled("chat"))

        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "chat=sql"
        self.assertEqual(backend_for("chat"), "sql")
        self.assertEqual(backend_for("notes"), "file")
        self.assertTrue(sql_enabled("chat"))
        self.assertFalse(sql_enabled("notes"))

        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "all"
        self.assertEqual(backend_for("evidence"), "sql")

        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "default=sql,chat=file"
        self.assertEqual(backend_for("chat"), "file")
        self.assertEqual(backend_for("classroom"), "sql")

        # Unknown backend values never widen routing.
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "chat=bogus"
        self.assertEqual(backend_for("chat"), "file")

        with self.assertRaises(ValueError):
            backend_for("not-a-domain")


class _DocumentRepoTestCase(StorageSandboxTestCase,
                            unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from app.persistence import db
        from app.persistence.documents import SqlDocumentRepository

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'documents.db').as_posix()}")
        await db.create_all(self._db_url)
        self.repo = SqlDocumentRepository(
            "chat", db.session_factory(self._db_url))

    async def asyncTearDown(self) -> None:
        from app.persistence import db

        engine = db._engine_cache.pop(self._db_url, None)
        if engine is not None:
            await engine.dispose()
        db._session_factory_cache.pop(self._db_url, None)
        await super().asyncTearDown()


class SqlDocumentRepositoryTest(_DocumentRepoTestCase):
    async def test_put_get_roundtrip_preserves_payload(self) -> None:
        from app.persistence.documents import DocumentRecord

        record = await self.repo.put(DocumentRecord(
            doc_id="s1", owner_id="u1", kind="session",
            payload={"title": "微积分 第3章", "messages": [
                {"role": "user", "text": "什么是洛必达法则？"},
                {"role": "assistant", "text": "…"}]}))
        self.assertEqual(record.epoch, 1)
        loaded = await self.repo.get("u1", "s1", kind="session")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.payload["title"], "微积分 第3章")
        self.assertEqual(len(loaded.payload["messages"]), 2)

    async def test_get_filters_on_every_key_component(self) -> None:
        from app.persistence.documents import DocumentRecord

        await self.repo.put(DocumentRecord(doc_id="s1", owner_id="u1",
                                           kind="session", payload={"a": 1}))
        self.assertIsNone(await self.repo.get("u2", "s1", kind="session"))
        self.assertIsNone(await self.repo.get("u1", "s1", kind="other"))
        self.assertIsNone(await self.repo.get("u1", "s1", kind="session",
                                              tenant_id="t2"))
        self.assertIsNotNone(await self.repo.get("u1", "s1", kind="session"))

    async def test_update_bumps_epoch_and_updated_at(self) -> None:
        from app.persistence.documents import DocumentRecord

        first = await self.repo.put(DocumentRecord(
            doc_id="s1", owner_id="u1", kind="session", payload={"v": 1}))
        second = await self.repo.put(DocumentRecord(
            doc_id="s1", owner_id="u1", kind="session", payload={"v": 2}))
        self.assertEqual(second.epoch, 2)
        self.assertGreaterEqual(second.updated_at, first.updated_at)
        loaded = await self.repo.get("u1", "s1", kind="session")
        self.assertEqual(loaded.payload["v"], 2)

    async def test_expected_epoch_compare_and_swap(self) -> None:
        from app.persistence.documents import (DocumentConflictError,
                                               DocumentRecord)

        first = await self.repo.put(DocumentRecord(
            doc_id="s1", owner_id="u1", kind="session", payload={"v": 1}))
        stale = await self.repo.put(DocumentRecord(
            doc_id="s1", owner_id="u1", kind="session", payload={"v": 2}),
            expected_epoch=first.epoch)
        self.assertEqual(stale.epoch, 2)
        with self.assertRaises(DocumentConflictError):
            await self.repo.put(DocumentRecord(
                doc_id="s1", owner_id="u1", kind="session", payload={"v": 3}),
                expected_epoch=first.epoch)
        # Absent row compares as epoch 0.
        with self.assertRaises(DocumentConflictError):
            await self.repo.put(DocumentRecord(
                doc_id="s9", owner_id="u1", kind="session", payload={}),
                expected_epoch=1)

    async def test_mutate_reads_modifies_and_writes_atomically(self) -> None:
        from app.persistence.documents import DocumentRecord

        await self.repo.put(DocumentRecord(
            doc_id="ops", owner_id="u1", kind="journal",
            payload={"events": [1, 2]}))

        def append(existing):
            events = list((existing or {}).get("events", []))
            events.append(3)
            return {"events": events}

        result = await self.repo.mutate("u1", "ops", append, kind="journal")
        self.assertEqual(result.payload["events"], [1, 2, 3])
        self.assertEqual(result.epoch, 2)

        # mutate on an absent row creates it.
        created = await self.repo.mutate(
            "u1", "new", lambda existing: {"n": 0 if existing else 1},
            kind="journal")
        self.assertEqual(created.payload["n"], 1)
        self.assertEqual(created.epoch, 1)

        # returning None deletes.
        gone = await self.repo.mutate("u1", "new", lambda existing: None,
                                      kind="journal")
        self.assertEqual(gone.epoch, 0)
        self.assertIsNone(await self.repo.get("u1", "new", kind="journal"))

    async def test_list_counts_and_delete(self) -> None:
        from app.persistence.documents import DocumentRecord

        for i in range(3):
            await self.repo.put(DocumentRecord(
                doc_id=f"s{i}", owner_id="u1", kind="session",
                payload={"i": i}))
        await self.repo.put(DocumentRecord(
            doc_id="t0", owner_id="u1", kind="thread", payload={}))
        await self.repo.put(DocumentRecord(
            doc_id="other", owner_id="u2", kind="session", payload={}))

        self.assertEqual(await self.repo.count_documents("u1"), 4)
        self.assertEqual(
            await self.repo.count_documents("u1", kind="session"), 3)
        listed = await self.repo.list_documents("u1", kind="session")
        # Newest updated_at first; same-timestamp ties break by row id desc,
        # so a rapid-fire insert lists in reverse insertion order.
        self.assertEqual([r.doc_id for r in listed], ["s2", "s1", "s0"])
        limited = await self.repo.list_documents("u1", kind="session",
                                                 limit=2)
        self.assertEqual(len(limited), 2)

        self.assertTrue(await self.repo.delete("u1", "t0", kind="thread"))
        self.assertFalse(await self.repo.delete("u1", "t0", kind="thread"))
        self.assertEqual(await self.repo.count_documents("u1"), 3)

    async def test_purge_owner_scopes_by_tenant_when_asked(self) -> None:
        from app.persistence.documents import DocumentRecord

        await self.repo.put(DocumentRecord(doc_id="a", owner_id="u1",
                                           payload={}))
        await self.repo.put(DocumentRecord(doc_id="b", owner_id="u1",
                                           tenant_id="t2", payload={}))
        removed = await self.repo.purge_owner("u1", tenant_id="t2")
        self.assertEqual(removed, 1)
        self.assertIsNotNone(await self.repo.get("u1", "a"))
        self.assertEqual(await self.repo.purge_owner("u1"), 1)
        self.assertEqual(await self.repo.count_documents("u1"), 0)

    async def test_domains_isolate_tables(self) -> None:
        from app.persistence import db
        from app.persistence.documents import DocumentRecord
        from app.persistence.documents.repository import SqlDocumentRepository

        notes = SqlDocumentRepository("notes",
                                      db.session_factory(self._db_url))
        await self.repo.put(DocumentRecord(doc_id="same", owner_id="u1",
                                           kind="session", payload={}))
        await notes.put(DocumentRecord(doc_id="same", owner_id="u1",
                                       kind="note", payload={}))
        self.assertIsNotNone(await self.repo.get("u1", "same",
                                                 kind="session"))
        self.assertIsNotNone(await notes.get("u1", "same", kind="note"))
        self.assertEqual(await self.repo.count_documents("u1"), 1)
        self.assertEqual(await notes.count_documents("u1"), 1)


class AccountPurgeDocumentsTest(StorageSandboxTestCase):
    """account purge must empty the domain document tables in SQL mode."""

    def test_purge_account_removes_document_rows(self) -> None:
        import asyncio

        db_url = f"sqlite+aiosqlite:///{(self.root / 'purge.db').as_posix()}"
        os.environ["DATABASE_URL"] = db_url

        async def _seed() -> None:
            from app.persistence import db
            from app.persistence.documents import DocumentRecord
            from app.persistence.documents.repository import (
                SqlDocumentRepository)

            await db.create_all(db_url)
            factory = db.session_factory(db_url)
            for domain in ("chat", "notes", "assistant"):
                await SqlDocumentRepository(domain, factory).put(
                    DocumentRecord(doc_id="d1", owner_id="u_purge",
                                   payload={"k": 1}))
            await SqlDocumentRepository("chat", factory).put(
                DocumentRecord(doc_id="keep", owner_id="u_other",
                               payload={}))

        async def _counts() -> dict[str, int]:
            from app.persistence import db
            from app.persistence.documents.repository import (
                SqlDocumentRepository)

            factory = db.session_factory(db_url)
            out = {}
            for domain in ("chat", "notes", "assistant"):
                out[domain] = await SqlDocumentRepository(
                    domain, factory).count_documents("u_purge")
            out["other_user_chat"] = await SqlDocumentRepository(
                "chat", factory).count_documents("u_other")
            return out

        try:
            asyncio.run(_seed())
            from app.core import account_data

            account_data.purge_account("u_purge")
            counts = asyncio.run(_counts())
        finally:
            os.environ.pop("DATABASE_URL", None)
        self.assertEqual(counts["chat"], 0)
        self.assertEqual(counts["notes"], 0)
        self.assertEqual(counts["assistant"], 0)
        self.assertEqual(counts["other_user_chat"], 1)
