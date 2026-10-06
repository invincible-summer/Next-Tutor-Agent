"""Illustration SQL cutover contract
(illustration → assistant_documents, ADR-0017).

Runs the real persistence/scenario API (jobs, runs, immutable artifacts,
sessions, scenario kinds, epoch fence, purge) with the assistant domain
routed to SQL over the sandboxed sqlite lane. Preview PNG bytes stay on
the file layer in both modes.
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


def _quiz_job(job_id: str, question_id: str, revision: int, *,
              status: str = "ready", shadow: bool = False,
              created_at: float = 1.0) -> dict:
    return {"job_id": job_id, "run_id": "illrun_" + "0" * 24,
            "question_id": question_id, "question_revision": revision,
            "status": status, "stage": "frozen", "shadow": shadow,
            "pipeline_mode": "v2", "created_at": created_at,
            "updated_at": created_at}


class IllustrationDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'ill.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "assistant=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

        from app.illustration import sql_store

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

    def test_stage_appends_run_events_and_updates_job(self) -> None:
        from app.illustration import persistence

        job = _quiz_job("illjob_" + "1" * 24, "q-1", 3, status="running")
        persistence.stage("u1", job, "created")
        persistence.stage("u1", job, "rendering", data={"note": "raster"})

        run = persistence.read("u1", "runs", job["run_id"])
        self.assertIsNotNone(run)
        self.assertEqual([e["sequence"] for e in run["events"]], [1, 2])
        self.assertEqual(run["events"][1]["note"], "raster")

        stored = persistence.read("u1", "jobs", job["job_id"])
        self.assertEqual(stored["stage"], "rendering")
        self.assertEqual(stored["question_id"], "q-1")

    def test_find_job_prefers_frozen_ready_and_hides_shadow(self) -> None:
        from app.illustration import persistence

        ready = _quiz_job("illjob_" + "2" * 24, "q-2", 1,
                          created_at=100.0)
        failed = _quiz_job("illjob_" + "3" * 24, "q-2", 1, status="failed",
                           created_at=200.0)
        shadow = _quiz_job("illjob_" + "4" * 24, "q-2", 1, shadow=True)
        for job in (ready, failed):
            persistence.write("u1", "jobs", job["job_id"], job)
        persistence.write("u2", "jobs", shadow["job_id"], shadow)

        found = persistence.find_job("u1", "q-2", 1)
        self.assertEqual(found["job_id"], ready["job_id"])
        self.assertIsNone(persistence.find_job("u1", "q-2", 2))
        # Shadow jobs are invisible unless explicitly requested.
        self.assertIsNone(persistence.find_job("u2", "q-2", 1))
        self.assertEqual(
            persistence.find_job("u2", "q-2", 1, include_shadow=True)
            ["job_id"], shadow["job_id"])
        # Frozen ready material wins forever — even across pipeline modes.
        self.assertEqual(
            persistence.find_job("u1", "q-2", 1, pipeline_mode="v3")
            ["job_id"], ready["job_id"])
        # Without frozen material, a v3 request never falls back to v2.
        persistence.write("u1", "jobs", "illjob_" + "d" * 24,
                          _quiz_job("illjob_" + "d" * 24, "q-5", 1,
                                    status="failed", created_at=300.0))
        persistence.write("u1", "jobs", "illjob_" + "e" * 24,
                          {**_quiz_job("illjob_" + "e" * 24, "q-5", 1,
                                       status="failed", created_at=250.0),
                           "pipeline_mode": "v3"})
        self.assertEqual(
            persistence.find_job("u1", "q-5", 1, pipeline_mode="v3")
            ["pipeline_mode"], "v3")
        self.assertEqual(
            persistence.find_job("u1", "q-5", 1)["pipeline_mode"], "v2")

    def test_immutable_artifact_dedup_and_patch_conflict(self) -> None:
        from app.illustration import persistence
        from app.illustration.contracts import IllustrationError

        artifact = {"artifact_id": "ill_" + "5" * 24,
                    "content_hash": "h" * 64, "status": "frozen"}
        persistence.write("u1", "artifacts", artifact["artifact_id"],
                          artifact, expected_epoch=0, immutable=True)
        # Byte-equal rewrite is a no-op, not a conflict.
        persistence.write("u1", "artifacts", artifact["artifact_id"],
                          dict(artifact), expected_epoch=0, immutable=True)
        with self.assertRaises(IllustrationError) as ctx:
            persistence.write("u1", "artifacts", artifact["artifact_id"],
                              {**artifact, "content_hash": "x" * 64},
                              expected_epoch=0, immutable=True)
        self.assertEqual(str(ctx.exception), "patch_conflict")

    def test_purge_deletes_rows_and_fences_late_epoch_writes(self) -> None:
        from app.illustration import persistence
        from app.illustration.contracts import IllustrationError

        job = _quiz_job("illjob_" + "6" * 24, "q-3", 1)
        persistence.write("u1", "jobs", job["job_id"], job)
        preview = (persistence.owner_dir("u1") / "previews"
                   / "ill_preview.png")
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"\x89PNG")

        self.assertEqual(persistence.iter_owners(), ["u1"])
        persistence.purge("u1")

        self.assertIsNone(persistence.read("u1", "jobs", job["job_id"]))
        self.assertEqual(persistence.iter_owners(), [])
        self.assertFalse(persistence.owner_dir("u1").exists())
        self.assertGreaterEqual(persistence.epoch("u1"), 1)

        # A late write still carrying the pre-purge epoch is fenced out.
        with self.assertRaises(IllustrationError) as ctx:
            persistence.write("u1", "jobs", job["job_id"], job,
                              expected_epoch=0)
        self.assertEqual(str(ctx.exception), "policy_disabled")
        self.assertIsNone(persistence.read("u1", "jobs", job["job_id"]))

        # Writes without an epoch claim start the next generation cleanly.
        persistence.write("u1", "sessions", "scene_" + "7" * 24,
                          {"session_id": "scene_" + "7" * 24, "deleted": False})
        self.assertIsNotNone(
            persistence.read("u1", "sessions", "scene_" + "7" * 24))

    def test_scenario_sessions_list_and_delete(self) -> None:
        from app.illustration import persistence, scenario
        from app.illustration.scenario import SceneError

        job_id = "scenejob_" + "8" * 24
        artifact_id = "sceneart_" + "9" * 24
        session = {"session_id": "scene_" + "a" * 24, "title": "t",
                   "revision": 1, "active_job_id": None,
                   "turns": [{"turn_id": "turn_1", "job_id": job_id,
                              "status": "ready"}],
                   "revision_ids": [artifact_id], "created_at": 1.0,
                   "updated_at": 1.0}
        persistence.write("u1", "sessions", session["session_id"], session)
        persistence.write("u1", "scenario_jobs", job_id,
                          {"job_id": job_id, "status": "ready"})
        persistence.write("u1", "scenario_revisions", artifact_id,
                          {"artifact_id": artifact_id, "revision": 1})
        preview = (persistence.owner_dir("u1") / "previews"
                   / f"{artifact_id}.png")
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"\x89PNG")

        listed = scenario.list_sessions("u1")
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["session_id"],
                         session["session_id"])

        scenario.delete_session("u1", session["session_id"])
        self.assertIsNone(
            persistence.read("u1", "scenario_jobs", job_id))
        self.assertIsNone(
            persistence.read("u1", "scenario_revisions", artifact_id))
        self.assertFalse(preview.exists())
        self.assertEqual(scenario.list_sessions("u1")["items"], [])
        tombstone = persistence.read("u1", "sessions", session["session_id"])
        self.assertTrue(tombstone["deleted"])
        with self.assertRaises(SceneError):
            scenario._session("u1", session["session_id"])

    def test_importer_walks_file_illustration(self) -> None:
        # Seed the file side, then run the illustration walker end to end.
        self._saved_env["DATABASE_URL"] = None  # file mode for seeding
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        from app.illustration import persistence

        job = _quiz_job("illjob_" + "b" * 24, "q-4", 2)
        persistence.write("u_file", "jobs", job["job_id"], job)
        persistence.write("u_file", "runs", job["run_id"],
                          {"run_id": job["run_id"], "events": []})
        session = {"session_id": "scene_" + "c" * 24, "title": "t",
                   "revision": 0, "active_job_id": None, "turns": [],
                   "revision_ids": [], "created_at": 1.0, "updated_at": 1.0}
        persistence.write("u_file", "sessions", session["session_id"],
                          session)

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
            [sys.executable, script, "--domain", "illustration"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        verify = subprocess.run(
            [sys.executable, script, "--domain", "illustration",
             "--verify"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(verify.returncode, 0, verify.stdout[-400:])

        self.assertEqual(persistence.read("u_file", "jobs", job["job_id"]),
                         job)
        self.assertEqual(
            persistence.read("u_file", "sessions", session["session_id"]),
            session)
        self.assertIn("u_file", persistence.iter_owners())


if __name__ == "__main__":    # pragma: no cover
    unittest.main()
