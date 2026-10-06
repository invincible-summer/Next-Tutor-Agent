"""Learning-orchestration SQL cutover contract
(orchestration → orchestration_documents, ADR-0017).

Runs the real store/history API with the orchestration domain routed to
SQL over the sandboxed sqlite lane, plus the importer end-to-end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class OrchestrationDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'orch.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "orchestration=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

        from app.agents.learning_orchestration import sql_store

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

    def test_state_roundtrip_and_defaults(self) -> None:
        from app.agents.learning_orchestration import store

        state = store.load_state("u1")
        self.assertEqual(state.goals, [])          # fresh default, no raise

        from app.agents.learning_orchestration.schema import LearningGoal

        state.goals.append(LearningGoal(title="期末数学90分"))
        state.daily_tasks = []
        self.assertTrue(store.save_state("u1", state))

        back = store.load_state("u1")
        self.assertEqual(len(back.goals), 1)
        self.assertEqual(back.goals[0].title, "期末数学90分")
        self.assertGreater(back.updated_at, 0)     # save stamps updated_at

    def test_events_append_read_coverage_limit(self) -> None:
        from app.agents.learning_orchestration import store
        from app.agents.learning_orchestration.schema import OrchestrationEvent

        events, coverage = store.read_events_with_coverage("u1")
        self.assertEqual(events, [])
        self.assertFalse(coverage["exists"])

        for i in range(5):
            self.assertTrue(store.append_event("u1", OrchestrationEvent(
                type="task_status_changed",
                payload={"event_id": f"evt-{i}", "step": i})))

        events, coverage = store.read_events_with_coverage("u1")
        self.assertEqual([e.payload["event_id"] for e in events],
                         [f"evt-{i}" for i in range(5)])   # oldest-first
        self.assertTrue(coverage["exists"])
        self.assertEqual(coverage["total_lines"], 5)
        self.assertEqual(coverage["invalid_count"], 0)
        self.assertFalse(coverage["truncated"])

        tail = store.read_events("u1", limit=2)
        self.assertEqual([e.payload["event_id"] for e in tail],
                         ["evt-3", "evt-4"])

        self.assertEqual(store.state_summary("u1")["event_count"], 5)

    def test_invalid_imported_lines_keep_invalid_semantics(self) -> None:
        from app.agents.learning_orchestration import store, sql_store

        # An imported legacy bad line stays visible as invalid, not dropped.
        sql_store.mutate_event_lines("u1", lambda lines: [
            {"ts": 1.0, "type": "task_status_changed",
             "payload": {"event_id": "ok-1"}},
            "not-json{{",
        ])
        events, coverage = store.read_events_with_coverage("u1")
        self.assertEqual(len(events), 1)
        self.assertEqual(coverage["invalid_count"], 1)
        self.assertEqual(coverage["total_lines"], 2)

    def test_owner_isolation(self) -> None:
        from app.agents.learning_orchestration import store
        from app.agents.learning_orchestration.schema import OrchestrationEvent

        store.append_event("a", OrchestrationEvent(
            type="habit_checked", payload={"event_id": "a-1"}))
        store.append_event("b", OrchestrationEvent(
            type="habit_checked", payload={"event_id": "b-1"}))

        self.assertEqual(
            [e.payload["event_id"] for e in store.read_events("a")], ["a-1"])
        self.assertEqual(
            [e.payload["event_id"] for e in store.read_events("b")], ["b-1"])

    def test_outbox_flush_dedup_via_store(self) -> None:
        from app.agents.learning_orchestration import history, store

        state = store.load_state("u1")
        state.event_outbox = [
            {"type": "task_status_changed", "ts": 1.0,
             "payload": {"event_id": "dup-1", "task_id": "t1"}}]
        self.assertEqual(history.flush_outbox("u1", state=state), 1)
        self.assertEqual(state.event_outbox, [])

        # Same event_id already delivered → idempotent ack, no new row.
        state.event_outbox = [
            {"type": "task_status_changed", "ts": 2.0,
             "payload": {"event_id": "dup-1", "task_id": "t1"}}]
        self.assertEqual(history.flush_outbox("u1", state=state), 1)
        self.assertEqual(len(store.read_events("u1")), 1)

    def test_importer_walks_file_state_and_events(self) -> None:
        # Seed the file side, then run the orchestration walker end to end.
        self._saved_env["DATABASE_URL"] = None  # file mode for seeding
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        from app.agents.learning_orchestration import store
        from app.agents.learning_orchestration.schema import (LearningGoal,
                                                              OrchestrationEvent)
        from app.core import paths

        state = store.load_state("u_file")
        state.goals.append(LearningGoal(title="导入目标"))
        store.save_state("u_file", state)
        for i in range(3):
            store.append_event("u_file", OrchestrationEvent(
                type="goal_created", payload={"event_id": f"imp-{i}"}))
        students_dir = paths.runtime_paths().students
        self.assertTrue(
            (students_dir / "u_file.orchestration.json").exists())
        self.assertTrue(
            (students_dir / "u_file.orchestration_events.jsonl").exists())

        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "orchestration=sql"
        from app.persistence.documents import bridge

        bridge.reset_worker()
        import subprocess
        import sys

        script = (sys.modules["app"].__file__.rsplit("/app/", 1)[0]
                  + "/../../scripts/migrations/runtime_to_enterprise/"
                  "import_documents.py")
        sub_env = {**os.environ, "NEXT_TUTOR_DATA_DIR": str(self.root)}
        result = subprocess.run(
            [sys.executable, script, "--domain", "orchestration"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])

        # Re-import is idempotent; verify digest-compares both sides.
        again = subprocess.run(
            [sys.executable, script, "--domain", "orchestration"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(again.returncode, 0, again.stderr[-500:])
        verify = subprocess.run(
            [sys.executable, script, "--domain", "orchestration", "--verify"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(verify.returncode, 0, verify.stdout[-400:])

        # Imported state/events load through the public API in SQL mode,
        # and post-import appends extend the imported line list.
        imported = store.load_state("u_file")
        self.assertEqual(len(imported.goals), 1)
        self.assertEqual(imported.goals[0].title, "导入目标")
        self.assertEqual(
            [e.payload["event_id"] for e in store.read_events("u_file")],
            ["imp-0", "imp-1", "imp-2"])
        self.assertTrue(store.append_event("u_file", OrchestrationEvent(
            type="goal_created", payload={"event_id": "imp-3"})))
        self.assertEqual(len(store.read_events("u_file")), 4)


if __name__ == "__main__":    # pragma: no cover
    unittest.main()
