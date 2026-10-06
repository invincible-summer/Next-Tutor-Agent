"""Chat domain SQL cutover contract (ADR-0017, chat → chat_documents).

Runs the real app.core.session public API with the chat domain routed to
SQL over the sandboxed sqlite lane — same surface every agent/endpoint
calls, so file-mode callers get identical behavior in SQL mode.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class ChatDocumentsTest(StorageSandboxTestCase):
    """One sqlite database, chat routed to SQL, everything else default."""

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'chat.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "chat=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        # The bridge caches a worker bound to one URL; reset per test.
        bridge.reset_worker()

        from app.core import session as session_mod
        from app.core import session_sql

        self.assertTrue(session_sql.use_sql())
        self.session_mod = session_mod

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

    def _make_session(self, student_id: str, topic: str = "微积分",
                      messages: int = 2):
        from app.core.session import TutorSession

        s = TutorSession(student_id=student_id, title=topic)
        for i in range(messages):
            s.messages.append({"role": "user", "content": f"问题 {i}"})
            s.messages.append({"role": "assistant", "content": f"回答 {i}"})
        return s

    def test_save_load_roundtrip(self) -> None:
        s = self._make_session("u1")
        sid = self.session_mod.save_session(s)
        loaded = self.session_mod.load_session(sid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.student_id, "u1")
        self.assertEqual(len(loaded.messages), 4)
        self.assertEqual(loaded.title, "微积分")
        # message ids frozen on save (G3)
        self.assertTrue(all(m.get("message_id") for m in loaded.messages))

    def test_listing_is_owner_scoped(self) -> None:
        self.session_mod.save_session(self._make_session("u1", "A"))
        self.session_mod.save_session(self._make_session("u2", "B"))
        u1 = self.session_mod.list_sessions("u1")
        u2 = self.session_mod.list_sessions("u2")
        self.assertEqual([s["title"] for s in u1], ["A"])
        self.assertEqual([s["title"] for s in u2], ["B"])
        # No-owner listing refuses to enumerate in SQL mode.
        self.assertEqual(self.session_mod.list_sessions(), [])
        # summaries carry the same fields as file mode
        self.assertEqual(u1[0]["message_count"], 4)
        self.assertEqual(u1[0]["round_count"], 2)

    def test_foreign_session_is_invisible_on_load_contract(self) -> None:
        """load_session by id returns the payload; ownership stays the API
        layer's check (same contract as file mode)."""
        s = self._make_session("u1")
        sid = self.session_mod.save_session(s)
        loaded = self.session_mod.load_session(sid)
        self.assertEqual(loaded.student_id, "u1")
        self.assertIsNone(self.session_mod.load_session("missing"))

    def test_delete_rename_grade(self) -> None:
        s = self._make_session("u1")
        sid = self.session_mod.save_session(s)
        self.assertTrue(self.session_mod.rename_session(sid, "新标题"))
        self.assertEqual(self.session_mod.load_session(sid).title, "新标题")
        self.assertTrue(self.session_mod.set_session_grade(sid, "G7"))
        self.assertEqual(self.session_mod.load_session(sid).grade, "G7")
        self.assertTrue(self.session_mod.delete_session(sid))
        self.assertFalse(self.session_mod.delete_session(sid))
        self.assertIsNone(self.session_mod.load_session(sid))

    def test_trace_refs_and_owner_lookup(self) -> None:
        s = self._make_session("u1")
        sid = self.session_mod.save_session(s)
        self.session_mod.add_trace_id(sid, "run-abc")
        self.session_mod.add_trace_id(sid, "run-abc")  # idempotent
        owner = self.session_mod.trace_owner("run-abc", "student_default")
        self.assertEqual(owner, "u1")
        self.assertIsNone(self.session_mod.trace_owner("run-none",
                                                       "student_default"))
        # deleting the session drops its trace refs
        self.session_mod.delete_session(sid)
        self.assertIsNone(self.session_mod.trace_owner("run-abc",
                                                       "student_default"))

    def test_transcript_append_and_delete(self) -> None:
        from app.core.context import append_transcript

        s = self._make_session("u1")
        sid = self.session_mod.save_session(s)
        append_transcript(sid, 1, [{"role": "user", "content": "你好"}])
        append_transcript(sid, 2, [{"role": "assistant", "content": "在"}])
        from app.core import session_sql

        lines = session_sql.get_transcript_lines(sid)
        self.assertEqual([l["content"] for l in lines], ["你好", "在"])
        self.assertEqual(lines[0]["turn"], 1)
        self.assertTrue(all(l["session_id"] == sid for l in lines))
        self.session_mod.delete_session(sid)
        self.assertEqual(session_sql.get_transcript_lines(sid), [])

    def test_clear_chat_data_removes_sql_sessions(self) -> None:
        from app.core.account_data import clear_chat_data

        sid1 = self.session_mod.save_session(self._make_session("u1", "A"))
        self.session_mod.save_session(self._make_session("u2", "B"))
        report = clear_chat_data("u1", scope="all")
        self.assertEqual(report["sessions"], 1)
        self.assertIsNone(self.session_mod.load_session(sid1))
        self.assertEqual(len(self.session_mod.list_sessions("u2")), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
