"""Classroom SQL cutover contract (classroom → classroom_documents,
ADR-0017).

Runs the real classroom storage API (owner lifecycle, lesson/job/run CAS,
workspace index, quota/idempotency accounting, trash snapshot/restore)
with the classroom domain routed to SQL over the sandboxed sqlite lane.
"""
from __future__ import annotations

import datetime
import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase

_NOW = datetime.datetime.now(datetime.timezone.utc)
_HEX = "0123456789abcdef"


def _lesson(owner="u1", ws="w1", n=1) -> "object":
    from app.schemas import classroom as sc

    return sc.Lesson(
        lesson_id=f"les_{_HEX * 2}{n:06d}"[:4 + 24], owner_id=owner,
        workspace_id=ws, title=f"课程{n}", created_at=_NOW, updated_at=_NOW)


def _job(lesson_id, owner="u1", ws="w1", n=1) -> "object":
    from app.schemas import classroom as sc

    return sc.GenerationJob(
        job_id=f"job_{_HEX * 2}{n:06d}"[:4 + 24], owner_id=owner,
        workspace_id=ws, lesson_id=lesson_id, target_revision=1,
        brief_hash="d" * 64, created_at=_NOW, updated_at=_NOW)


def _run(lesson_id, owner="u1", ws="w1", n=1) -> "object":
    from app.schemas import classroom as sc

    return sc.ClassroomRun(
        run_id=f"run_{_HEX * 2}{n:06d}"[:4 + 24], owner_id=owner,
        workspace_id=ws, lesson_id=lesson_id, lesson_revision=1,
        content_hash="e" * 64,
        cursor=sc.Cursor(slide_id="s_" + "0" * 12,
                         segment_id="seg_" + "1" * 24),
        created_at=_NOW, updated_at=_NOW)


class ClassroomDocumentsTest(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        from app.persistence import db
        from app.persistence.documents import bridge

        self._db_url = (
            f"sqlite+aiosqlite:///{(self.root / 'cls.db').as_posix()}")
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DOMAIN_DOCUMENT_BACKENDS")}
        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "classroom=sql"
        import asyncio

        asyncio.run(db.create_all(self._db_url))
        bridge.reset_worker()

        from app.classroom import sql_store

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

    def test_owner_lifecycle_and_tombstone_fence(self) -> None:
        from app.classroom import storage

        storage.ensure_owner("u1")
        self.assertEqual(storage.owner_lifecycle("u1"), "active")
        self.assertEqual(storage.owner_lifecycle("ghost"), "unknown")

        storage.mark_owner_purged("u1")
        self.assertEqual(storage.owner_lifecycle("u1"), "purged")
        with self.assertRaises(storage.ClassroomStorageError):
            storage.ensure_owner("u1")     # 晚到写入被拒

        storage.clear_owner_tombstone("u1")
        self.assertEqual(storage.owner_lifecycle("u1"), "active")

    def test_lesson_job_run_roundtrip_and_cas(self) -> None:
        from app.classroom import storage

        storage.ensure_owner("u1")
        lesson = _lesson(n=1)
        storage.save_lesson(lesson)

        updated = storage.update_lesson("u1", "w1", lesson.lesson_id,
                                        lambda m: setattr(m, "title", "新课"))
        self.assertEqual(updated.title, "新课")
        reloaded = storage.load_lesson("u1", "w1", lesson.lesson_id)
        self.assertEqual(reloaded.title, "新课")
        self.assertIsNone(storage.load_lesson("u1", "w1", "les_missing"))

        with self.assertRaises(storage.ClassroomStorageError):   # 缺失文件语义
            storage.update_lesson("u1", "w1", "les_missing", lambda m: None)

        job = _job(lesson.lesson_id, n=2)
        storage.save_job(job)
        got = storage.update_job(
            "u1", "w1", lesson.lesson_id, job.job_id,
            lambda j: setattr(j, "state", type(j.state).running))
        self.assertEqual(got.state_revision, 2)
        with self.assertRaises(storage.CasConflictError):
            storage.update_job(
                "u1", "w1", lesson.lesson_id, job.job_id, lambda j: None,
                expected_state_revision=1)  # 已推进到 2

        run = _run(lesson.lesson_id, n=3)
        storage.save_run(run)
        bumped = storage.update_run("u1", "w1", lesson.lesson_id, run.run_id,
                                    lambda r: None)
        self.assertEqual(bumped.state_revision, 2)
        quiet = storage.update_run("u1", "w1", lesson.lesson_id, run.run_id,
                                   lambda r: None, bump_revision=False)
        self.assertEqual(quiet.state_revision, 2)   # 记账写不推进 CAS

        runs = storage.list_runs("u1", "w1", lesson.lesson_id)
        self.assertEqual([r.run_id for r in runs], [run.run_id])
        self.assertEqual(storage.list_runs("u2", "w1", lesson.lesson_id), [])

    def test_index_upsert_rebuild_and_lesson_ids(self) -> None:
        from app.classroom import storage

        storage.ensure_owner("u1")
        lesson = _lesson(n=5)
        storage.save_lesson(lesson)
        storage.index_upsert_lesson("u1", "w1", lesson)
        index = storage.read_index("u1", "w1")
        self.assertIn(lesson.lesson_id, index["lessons"])
        self.assertEqual(storage.list_lesson_ids("u1", "w1"),
                         [lesson.lesson_id])

        rebuilt = storage.rebuild_index("u1", "w1")
        self.assertIn(lesson.lesson_id, rebuilt["lessons"])
        storage.index_remove_lesson("u1", "w1", lesson.lesson_id)
        self.assertEqual(storage.read_index("u1", "w1")["lessons"], {})

    def test_quota_and_idempotency_accounting(self) -> None:
        from app.classroom import idempotency, storage

        storage.ensure_owner("u1")
        idempotency.remember("u1", "lesson.create", "k1", "h1",
                             {"lesson_id": "les_x"})
        hit = idempotency.lookup("u1", "lesson.create", "k1", "h1")
        self.assertEqual(hit, {"lesson_id": "les_x"})
        idempotency.consume_generation_quota("u1")
        record = storage.owner_record("u1")
        self.assertEqual(len(record["quota"]["generation_window"]), 1)

    def test_trash_snapshot_and_restore_roundtrip(self) -> None:
        from app.classroom import lifecycle, storage

        storage.ensure_owner("u1")
        lesson = _lesson(n=7)
        storage.save_lesson(lesson)
        storage.index_upsert_lesson("u1", "w1", lesson)
        job = _job(lesson.lesson_id, n=8)
        storage.save_job(job)
        run = _run(lesson.lesson_id, n=9)
        storage.save_run(run)

        bundle = self.root / "trash-bundle"
        summary = lifecycle.snapshot_lesson_into(
            "u1", "w1", lesson.lesson_id, bundle / lesson.lesson_id)
        self.assertEqual(summary["lesson_id"], lesson.lesson_id)
        self.assertTrue((bundle / lesson.lesson_id / "lesson.json").exists())

        lifecycle.delete_lesson_active("u1", "w1", lesson.lesson_id)
        self.assertIsNone(storage.load_lesson("u1", "w1", lesson.lesson_id))
        self.assertIsNone(storage.load_job("u1", "w1", lesson.lesson_id,
                                           job.job_id))
        self.assertEqual(storage.list_runs("u1", "w1", lesson.lesson_id), [])

        result = lifecycle.restore_lesson_tree(
            "u1", "w1", lesson.lesson_id, bundle / lesson.lesson_id)
        self.assertEqual(result["lesson_id"], lesson.lesson_id)
        back = storage.load_lesson("u1", "w1", lesson.lesson_id)
        self.assertIsNotNone(back)
        self.assertEqual(back.title, lesson.title)
        # 恢复后 active job 转 needs_input、run 转 paused（回灌后可调）
        restored_job = storage.load_job("u1", "w1", lesson.lesson_id,
                                        job.job_id)
        self.assertIsNotNone(restored_job)
        with self.assertRaises(FileExistsError):
            lifecycle.restore_lesson_tree(
                "u1", "w1", lesson.lesson_id, bundle / lesson.lesson_id)

    def test_importer_walks_file_classroom(self) -> None:
        # Seed the file side, then run the classroom walker end to end.
        self._saved_env["DATABASE_URL"] = None  # file mode for seeding
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DOMAIN_DOCUMENT_BACKENDS", None)
        from app.classroom import storage

        storage.ensure_owner("u_file")
        lesson = _lesson(owner="u_file", n=11)
        storage.save_lesson(lesson)
        storage.save_job(_job(lesson.lesson_id, owner="u_file", n=12))
        storage.save_run(_run(lesson.lesson_id, owner="u_file", n=13))

        os.environ["DATABASE_URL"] = self._db_url
        os.environ["DOMAIN_DOCUMENT_BACKENDS"] = "classroom=sql"
        from app.persistence.documents import bridge

        bridge.reset_worker()
        import subprocess
        import sys

        script = (sys.modules["app"].__file__.rsplit("/app/", 1)[0]
                  + "/../../scripts/migrations/runtime_to_enterprise/"
                  "import_documents.py")
        sub_env = {**os.environ, "NEXT_TUTOR_DATA_DIR": str(self.root)}
        result = subprocess.run(
            [sys.executable, script, "--domain", "classroom"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        verify = subprocess.run(
            [sys.executable, script, "--domain", "classroom", "--verify"],
            capture_output=True, text=True, env=sub_env)
        self.assertEqual(verify.returncode, 0, verify.stdout[-400:])

        imported = storage.load_lesson("u_file", "w1", lesson.lesson_id)
        self.assertIsNotNone(imported)
        self.assertEqual(imported.title, lesson.title)


if __name__ == "__main__":    # pragma: no cover
    unittest.main()
