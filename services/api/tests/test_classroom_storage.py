"""课堂存储回归（plan.md §19.2 test_classroom_storage）。

覆盖：原子写、CAS、读不 mkdir、hash 损坏、重建 index、版本空号、
跨文件操作恢复（发布事务 crash）、发布/取消竞态拦截、路径逃逸、tombstone。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests import classroom_fixtures as fx  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

OWNER = "usr_storage_test"
WS = "ws_20260926_测试工作区"


def make_lesson(lesson_id: str | None = None, title: str = "动量守恒") -> sc.Lesson:
    now = store.utcnow()
    return sc.Lesson(
        lesson_id=lesson_id or store.new_id("les"),
        owner_id=OWNER, workspace_id=WS, title=title,
        created_at=now, updated_at=now,
    )


def make_job(lesson: sc.Lesson, *, job_id: str | None = None) -> sc.GenerationJob:
    now = store.utcnow()
    return sc.GenerationJob(
        job_id=job_id or store.new_id("job"),
        owner_id=lesson.owner_id, workspace_id=lesson.workspace_id,
        lesson_id=lesson.lesson_id, target_revision=1,
        brief_hash="a" * 64, created_at=now, updated_at=now,
    )


def stage_full_revision(lesson: sc.Lesson, job: sc.GenerationJob,
                        revision: int, revision_spec: sc.LessonRevision) -> dict:
    staging = store.prepare_revision_staging(
        lesson.owner_id, lesson.workspace_id, lesson.lesson_id, revision)
    spec_text = revision_spec.model_dump_json(by_alias=True)
    frame = b"<html>frame</html>"
    notes = "# notes"
    store.stage_file(staging, "spec.private.json", spec_text)
    store.stage_file(staging, "spec.public.json", spec_text)
    store.stage_file(staging, "frame.html", frame)
    store.stage_file(staging, "speaker-notes.md", notes)
    manifest = {
        "revision": revision,
        "schema_version": 1,
        "files": {
            "spec.private.json": store.bytes_hash(spec_text.encode("utf-8")),
            "spec.public.json": store.bytes_hash(spec_text.encode("utf-8")),
            "frame.html": store.bytes_hash(frame),
            "speaker-notes.md": store.bytes_hash(notes.encode("utf-8")),
        },
        "content_hash": revision_spec.content_hash,
    }
    store.save_commit_intent(job, revision, manifest)
    return manifest


class PathAndIdTests(StorageSandboxTestCase):
    def test_new_id_validation(self):
        with self.assertRaises(store.ClassroomStorageError):
            store.validate_new_id("les", "les_short")
        with self.assertRaises(store.ClassroomStorageError):
            store.validate_new_id("les", "job_" + "a" * 24)
        store.validate_new_id("les", "les_" + "a" * 24)

    def test_path_segment_allows_chinese_slug(self):
        store.validate_path_segment("ws_20260926_大学物理学", field="workspace_id")

    def test_path_segment_rejects_traversal(self):
        for bad in ("", ".", "..", "a/b", "a\\b", "a\x00b", "../escape"):
            with self.assertRaises(store.ClassroomStorageError):
                store.validate_path_segment(bad, field="workspace_id")

    def test_read_does_not_mkdir(self):
        before = set(self.root.rglob("*"))
        self.assertIsNone(store.load_lesson(OWNER, WS, store.new_id("les")))
        self.assertEqual(store.read_index(OWNER, WS)["lessons"], {})
        self.assertEqual(store.list_lesson_ids(OWNER, WS), [])
        self.assertEqual(store.list_runs(OWNER, WS, store.new_id("les")), [])
        self.assertEqual(before, set(self.root.rglob("*")))

    def test_symlink_escape_rejected(self):
        lesson = make_lesson()
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        # 攻击场景：lesson 目录被换成指向别处（含可读 lesson.json）的 symlink
        escape = self.root / "escape-target"
        escape.mkdir(exist_ok=True)
        real_meta = store.lesson_meta_path(OWNER, WS, lesson.lesson_id)
        escape.joinpath("lesson.json").write_text(
            real_meta.read_text(encoding="utf-8"), encoding="utf-8")
        import shutil as _shutil
        link = store.lesson_root(OWNER, WS, lesson.lesson_id)
        _shutil.rmtree(link)
        link.symlink_to(escape)
        with self.assertRaises(store.ClassroomStorageError):
            store._read_model(store.lesson_meta_path(OWNER, WS, lesson.lesson_id),
                              sc.Lesson)
        with self.assertRaises(store.ClassroomStorageError):
            store.save_lesson(make_lesson(lesson.lesson_id))


class LessonCrudTests(StorageSandboxTestCase):
    def test_lesson_roundtrip_and_update(self):
        lesson = make_lesson()
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        loaded = store.load_lesson(OWNER, WS, lesson.lesson_id)
        self.assertEqual(loaded.title, lesson.title)

        def mutate(l: sc.Lesson) -> None:
            l.title = "碰撞与动量"

        updated = store.update_lesson(OWNER, WS, lesson.lesson_id, mutate)
        self.assertEqual(updated.title, "碰撞与动量")
        self.assertEqual(
            store.load_lesson(OWNER, WS, lesson.lesson_id).title, "碰撞与动量")

    def test_corrupt_lesson_raises_damaged(self):
        lesson = make_lesson()
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        path = store.lesson_meta_path(OWNER, WS, lesson.lesson_id)
        path.write_text("{broken json", encoding="utf-8")
        with self.assertRaises(store.LessonDamagedError):
            store.load_lesson(OWNER, WS, lesson.lesson_id)

    def test_quarantine_moves_lesson(self):
        lesson = make_lesson()
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        dest = store.quarantine_lesson(OWNER, WS, lesson.lesson_id, "corrupt")
        self.assertIsNotNone(dest)
        self.assertFalse(
            store.lesson_root(OWNER, WS, lesson.lesson_id).exists())
        self.assertTrue(dest.exists())

    def test_owner_tombstone_blocks_writes(self):
        lesson = make_lesson()
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        store.mark_owner_purged(OWNER)
        with self.assertRaises(store.ClassroomStorageError):
            store.ensure_owner(OWNER)
        with self.assertRaises(store.ClassroomStorageError):
            store.assert_owner_writable(OWNER)
        self.assertEqual(store.owner_lifecycle(OWNER), "purged")


class CasTests(StorageSandboxTestCase):
    def test_job_cas_conflict(self):
        lesson = make_lesson()
        job = make_job(lesson)
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        store.save_job(job)
        with self.assertRaises(store.CasConflictError):
            store.update_job(OWNER, WS, lesson.lesson_id, job.job_id,
                             lambda j: None, expected_state_revision=99)
        updated = store.update_job(
            OWNER, WS, lesson.lesson_id, job.job_id,
            lambda j: setattr(j, "state", sc.JobState.running),
            expected_state_revision=1)
        self.assertEqual(updated.state, sc.JobState.running)
        self.assertEqual(updated.state_revision, 2)

    def test_run_cas_and_bump(self):
        lesson = make_lesson()
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        now = store.utcnow()
        cursor = sc.Cursor(slide_id=fx.slide_hex(), segment_id=fx.hex_id("seg"))
        run = sc.ClassroomRun(
            run_id=store.new_id("run"), owner_id=OWNER, workspace_id=WS,
            lesson_id=lesson.lesson_id, lesson_revision=1,
            content_hash="c" * 64, cursor=cursor,
            created_at=now, updated_at=now)
        store.save_run(run)
        with self.assertRaises(store.CasConflictError):
            store.update_run(OWNER, WS, lesson.lesson_id, run.run_id,
                             lambda r: None, expected_state_revision=5)
        updated = store.update_run(
            OWNER, WS, lesson.lesson_id, run.run_id,
            lambda r: r.visited_slides.append(fx.slide_hex()),
            expected_state_revision=1)
        self.assertEqual(updated.state_revision, 2)
        self.assertEqual(len(updated.visited_slides), 1)


class PublishTransactionTests(StorageSandboxTestCase):
    def _setup(self):
        lesson = make_lesson()
        job = make_job(lesson)
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        store.save_job(job)
        return lesson, job

    def test_allocate_revision_burns_gap(self):
        lesson, _ = self._setup()
        rev1 = store.allocate_revision(OWNER, WS, lesson.lesson_id)
        self.assertEqual(rev1, 1)
        # 模拟失败：不做发布，直接再次分配 —— 空号不复用
        rev2 = store.allocate_revision(OWNER, WS, lesson.lesson_id)
        self.assertEqual(rev2, 2)
        loaded = store.load_lesson(OWNER, WS, lesson.lesson_id)
        self.assertEqual(loaded.next_revision, 3)
        self.assertEqual(loaded.published_revisions, [])

    def test_allocate_requires_published_base(self):
        lesson, _ = self._setup()
        with self.assertRaises(store.CasConflictError):
            store.allocate_revision(OWNER, WS, lesson.lesson_id, base_revision=1)

    def test_commit_and_recover_publish(self):
        lesson, job = self._setup()
        revision = store.allocate_revision(OWNER, WS, lesson.lesson_id)
        spec = fx.make_revision(revision)
        manifest = stage_full_revision(lesson, job, revision, spec)

        # 模拟步骤 4/5 之间崩溃：rename 成功但指针未提交
        staging = store.revision_staging_dir(OWNER, WS, lesson.lesson_id, revision)
        import os as _os
        target = store.revision_dir(OWNER, WS, lesson.lesson_id, revision)
        _os.rename(staging, target)
        (target / "manifest.json").write_text(
            store.canonical_json(manifest), encoding="utf-8")
        # save_commit_intent 已在 stage_full_revision 落盘
        self.assertEqual(
            store.load_lesson(OWNER, WS, lesson.lesson_id).published_revisions, [])

        reloaded_job = store.load_job(OWNER, WS, lesson.lesson_id, job.job_id)
        recovered = store.recover_pending_publish(
            OWNER, WS, lesson.lesson_id, reloaded_job)
        self.assertTrue(recovered)
        lesson_after = store.load_lesson(OWNER, WS, lesson.lesson_id)
        self.assertEqual(lesson_after.published_revisions, [revision])
        self.assertEqual(lesson_after.latest_ready_revision, revision)

        # 幂等：再次恢复不重复追加
        self.assertFalse(store.recover_pending_publish(
            OWNER, WS, lesson.lesson_id, reloaded_job))

        loaded_spec = store.load_revision(OWNER, WS, lesson.lesson_id, revision)
        self.assertEqual(loaded_spec.revision, revision)

    def test_commit_validates_hashes(self):
        lesson, job = self._setup()
        revision = store.allocate_revision(OWNER, WS, lesson.lesson_id)
        spec = fx.make_revision(revision)
        manifest = stage_full_revision(lesson, job, revision, spec)
        # 篡改 staging 文件
        staging = store.revision_staging_dir(OWNER, WS, lesson.lesson_id, revision)
        (staging / "frame.html").write_bytes(b"tampered")
        with self.assertRaises(store.ClassroomStorageError):
            store.commit_revision(OWNER, WS, lesson.lesson_id, revision, manifest)

    def test_cancel_blocks_publish(self):
        lesson, job = self._setup()
        revision = store.allocate_revision(OWNER, WS, lesson.lesson_id)
        manifest = stage_full_revision(lesson, job, revision,
                                       fx.make_revision(revision))
        with self.assertRaises(store.ClassroomStorageError):
            store.commit_revision(OWNER, WS, lesson.lesson_id, revision, manifest,
                                  cancel_requested=True)
        self.assertEqual(
            store.load_lesson(OWNER, WS, lesson.lesson_id).published_revisions, [])

    def test_commit_happy_path(self):
        lesson, job = self._setup()
        revision = store.allocate_revision(OWNER, WS, lesson.lesson_id)
        manifest = stage_full_revision(lesson, job, revision,
                                       fx.make_revision(revision))
        result = store.commit_revision(OWNER, WS, lesson.lesson_id, revision,
                                       manifest)
        self.assertEqual(result.published_revisions, [revision])
        # 重放幂等
        result2 = store.commit_revision(OWNER, WS, lesson.lesson_id, revision,
                                        manifest)
        self.assertEqual(result2.published_revisions, [revision])


class IndexTests(StorageSandboxTestCase):
    def test_index_rebuild_and_update(self):
        lesson = make_lesson()
        job = make_job(lesson)
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        store.save_job(job)
        store.index_upsert_lesson(OWNER, WS, lesson, job=job)
        index = store.read_index(OWNER, WS)
        self.assertIn(lesson.lesson_id, index["lessons"])
        self.assertIn(job.job_id, index["jobs"])

        # 删除索引后重建
        store.index_path(OWNER, WS).unlink()
        rebuilt = store.rebuild_index(OWNER, WS)
        self.assertIn(lesson.lesson_id, rebuilt["lessons"])
        self.assertIn(job.job_id, rebuilt["jobs"])
        self.assertEqual(store.list_lesson_ids(OWNER, WS),
                         [lesson.lesson_id])

    def test_index_remove_lesson_cleans_jobs(self):
        lesson = make_lesson()
        job = make_job(lesson)
        store.ensure_owner(OWNER)
        store.save_lesson(lesson)
        store.save_job(job)
        store.index_upsert_lesson(OWNER, WS, lesson, job=job)
        store.index_remove_lesson(OWNER, WS, lesson.lesson_id)
        index = store.read_index(OWNER, WS)
        self.assertNotIn(lesson.lesson_id, index["lessons"])
        self.assertEqual(index["jobs"], {})


class AtomicBytesTests(StorageSandboxTestCase):
    def test_atomic_write_bytes(self):
        from app.core.atomic import atomic_write_bytes
        path = self.root / "chat_history" / "classroom" / "b.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, b"\x00\x01\x02")
        self.assertEqual(path.read_bytes(), b"\x00\x01\x02")
        self.assertFalse(path.with_name("b.bin.tmp").exists())

    def test_stage_file_creates_missing_assets_dir(self):
        # 回归：全新课程首次保存图库资产时 assets/ 目录尚不存在，
        # stage_file 必须自建目录（pipeline._stage_visual_assets 路径）
        lesson = make_lesson()
        store.save_lesson(lesson)
        self.assertFalse(store.asset_file_path(
            OWNER, WS, lesson.lesson_id, store.new_id("ast"),
            "webp").parent.exists())
        target = store.asset_file_path(OWNER, WS, lesson.lesson_id,
                                       store.new_id("ast"), "webp")
        path = store.stage_file(target.parent, target.name, b"\x00webp")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b"\x00webp")
        self.assertFalse(path.with_name(path.name + ".tmp").exists())
        # 字符串写入同样适用（未准备的 staging 目录）
        fresh = store.revision_staging_dir(OWNER, WS, lesson.lesson_id, 9)
        self.assertFalse(fresh.exists())
        store.stage_file(fresh, "notes.md", "# n")
        self.assertTrue((fresh / "notes.md").is_file())

    def test_canonical_hash_stable(self):
        a = store.canonical_hash({"b": 1, "a": {"y": [1, 2], "x": "文"}})
        b = store.canonical_hash({"a": {"x": "文", "y": [1, 2]}, "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)


if __name__ == "__main__":
    unittest.main()
