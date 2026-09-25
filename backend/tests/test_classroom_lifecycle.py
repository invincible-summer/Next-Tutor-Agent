"""课堂生命周期回归（plan.md §16.4 / A03）。

覆盖：单课归档→恢复→purge、工作区归档携带课堂子树、purge_account
tombstone 防晚写、uploads_only 上传图清理、orphan 扫描分类、用量分桶。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classroom import lifecycle as lc  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.core import trash as trash_mod  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests import classroom_fixtures as fx  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

OWNER = "usr_lifecycle_test"
WS = "ws_lc_物理"


def build_lesson(title: str = "动量守恒") -> sc.Lesson:
    store.ensure_owner(OWNER)
    lesson = sc.Lesson(
        lesson_id=store.new_id("les"), owner_id=OWNER, workspace_id=WS,
        title=title, created_at=store.utcnow(), updated_at=store.utcnow())
    store.save_lesson(lesson)
    store.index_upsert_lesson(OWNER, WS, lesson)
    return lesson


def add_run(lesson: sc.Lesson, *, active: bool = True,
            with_lease: bool = False) -> sc.ClassroomRun:
    cursor = sc.Cursor(slide_id=fx.slide_hex(), segment_id=fx.hex_id("seg"))
    run = sc.ClassroomRun(
        run_id=store.new_id("run"), owner_id=OWNER, workspace_id=WS,
        lesson_id=lesson.lesson_id, lesson_revision=1, content_hash="c" * 64,
        status=sc.RunStatus.active if active else sc.RunStatus.paused,
        cursor=cursor, created_at=store.utcnow(), updated_at=store.utcnow())
    if with_lease:
        run.lease = sc.LeaseInfo(client_id="client_12345678", lease_epoch=1,
                                 expires_at=store.utcnow(),
                                 heartbeat_at=store.utcnow())
    store.save_run(run)
    return run


def add_job(lesson: sc.Lesson, state: sc.JobState) -> sc.GenerationJob:
    job = sc.GenerationJob(
        job_id=store.new_id("job"), owner_id=OWNER, workspace_id=WS,
        lesson_id=lesson.lesson_id, target_revision=1, state=state,
        brief_hash="a" * 64, created_at=store.utcnow(), updated_at=store.utcnow())
    store.save_job(job)
    return job


class LessonArchiveTests(StorageSandboxTestCase):
    def test_archive_restore_purge_cycle(self):
        lesson = build_lesson()
        run = add_run(lesson, active=True, with_lease=True)
        add_job(lesson, sc.JobState.running)
        # 音频缓存：归档不打包
        audio = store.audio_file_path(OWNER, WS, lesson.lesson_id, "f" * 64)
        store.write_bytes(audio, b"RIFF")

        manifest = trash_mod.archive_classroom_lesson(OWNER, WS, lesson.lesson_id)
        self.assertEqual(manifest["resource_type"], "classroom_lesson")
        self.assertFalse(
            store.lesson_root(OWNER, WS, lesson.lesson_id).exists())
        self.assertNotIn(lesson.lesson_id,
                         store.read_index(OWNER, WS)["lessons"])
        # 快照不含音频，但含 run/job
        bundle = trash_mod._item_dir(OWNER, manifest["id"])
        self.assertFalse((bundle / "payload" / "lesson" / "audio").exists())
        self.assertTrue((bundle / "payload" / "lesson" / "runs").is_dir())

        trash_mod.restore_item(OWNER, manifest["id"])
        restored_run = store.load_run(OWNER, WS, lesson.lesson_id, run.run_id)
        self.assertIsNotNone(restored_run)
        self.assertEqual(restored_run.status, sc.RunStatus.paused)
        self.assertIsNone(restored_run.lease)
        jobs = [p for p in store.jobs_root(OWNER, WS, lesson.lesson_id).iterdir()]
        self.assertTrue(jobs)
        reloaded_job = store.load_job(OWNER, WS, lesson.lesson_id,
                                      jobs[0].name)
        self.assertEqual(reloaded_job.state, sc.JobState.needs_input)
        self.assertEqual(reloaded_job.last_error, "recovered_after_archive")
        lesson_after = store.load_lesson(OWNER, WS, lesson.lesson_id)
        self.assertEqual(lesson_after.lifecycle, sc.LessonLifecycle.active)

        # 同 ID 冲突：再归档一次，直接用 lifecycle 恢复两次
        manifest2 = trash_mod.archive_classroom_lesson(OWNER, WS, lesson.lesson_id)
        bundle2 = trash_mod._item_dir(OWNER, manifest2["id"])
        src = bundle2 / "payload" / "lesson"
        lc.restore_lesson_tree(OWNER, WS, lesson.lesson_id, src)
        with self.assertRaises(FileExistsError):
            lc.restore_lesson_tree(OWNER, WS, lesson.lesson_id, src)
        trash_mod.purge_item(OWNER, manifest2["id"])
        self.assertEqual(
            trash_mod.list_items(OWNER, resource_type="classroom_lesson"), [])

    def test_workspace_archive_carries_classroom(self):
        ws = ws_mod.Workspace(workspace_id=WS, name="物理工作区",
                              student_id=OWNER)
        ws_mod.save_workspace(ws)
        lesson = build_lesson()
        add_run(lesson)

        manifest = trash_mod.archive_workspace(OWNER, WS)
        self.assertEqual(
            manifest["metadata"].get("classroom_lesson_count"), 1)
        self.assertFalse(store.workspace_root(OWNER, WS).exists())

        trash_mod.restore_item(OWNER, manifest["id"])
        self.assertTrue(store.workspace_root(OWNER, WS).exists())
        self.assertIsNotNone(store.load_lesson(OWNER, WS, lesson.lesson_id))
        self.assertEqual(store.list_lesson_ids(OWNER, WS), [lesson.lesson_id])


class AccountPurgeTests(StorageSandboxTestCase):
    def test_purge_owner_tombstone_blocks_late_writes(self):
        build_lesson()
        lc.purge_owner_classroom(OWNER, tombstone=True)
        self.assertFalse(store.owner_root(OWNER).exists())
        with self.assertRaises(store.ClassroomStorageError):
            store.ensure_owner(OWNER)

    def test_clear_chat_data_removes_classroom_without_tombstone(self):
        from app.core import account_data
        build_lesson()
        account_data.clear_chat_data(OWNER, scope="all")
        self.assertFalse(store.owner_root(OWNER).exists())
        # 账号仍在：后续课堂可再写
        store.ensure_owner(OWNER)
        build_lesson("新课")

    def test_scan_storage_buckets(self):
        from app.core import account_data
        lesson = build_lesson()
        store.write_bytes(
            store.audio_file_path(OWNER, WS, lesson.lesson_id, "e" * 64),
            b"x" * 1000)
        buckets = account_data.scan_storage([OWNER])[OWNER]
        self.assertGreater(buckets["classroom_bytes"], 0)
        self.assertGreaterEqual(buckets["audio_bytes"], 1000)
        self.assertEqual(
            buckets["total_bytes"],
            buckets["classroom_bytes"] + buckets["audio_bytes"] +
            sum(buckets[k] for k in ("chat_bytes", "uploads_bytes",
                                     "notes_bytes", "students_bytes",
                                     "knowledge_bytes", "trash_bytes")))


class UploadsOnlyTests(StorageSandboxTestCase):
    def test_strip_uploaded_images(self):
        lesson = build_lesson()
        # 上传图 asset
        upload_asset = sc.AssetRecord(
            asset_id=store.new_id("ast"), sha256="d" * 64, mime="image/png",
            width=10, height=10,
            provenance=sc.AssetProvenance(provider=sc.AssetProvider.upload,
                                          fetched_at=store.utcnow()),
            alt="自绘图", caption="", role=sc.AssetRole.diagram,
            bytes=100, status=sc.AssetStatus.ready)
        store.write_json(store.asset_meta_path(OWNER, WS, lesson.lesson_id,
                                               upload_asset.asset_id),
                         upload_asset.model_dump(mode="json", by_alias=True))
        store.write_bytes(
            store.asset_file_path(OWNER, WS, lesson.lesson_id,
                                  upload_asset.asset_id, "png"), b"\x89PNG")
        # 图库图 asset（保留）
        stock_asset = upload_asset.model_copy(update={
            "asset_id": store.new_id("ast"),
            "provenance": sc.AssetProvenance(
                provider=sc.AssetProvider.pexels,
                fetched_at=store.utcnow())})
        store.write_json(store.asset_meta_path(OWNER, WS, lesson.lesson_id,
                                               stock_asset.asset_id),
                         stock_asset.model_dump(mode="json", by_alias=True))
        # 编译产物与导出（含内嵌 bytes，应删除）
        rev_dir = store.revision_dir(OWNER, WS, lesson.lesson_id, 1)
        store.write_bytes(rev_dir / "frame.html", b"<html>embedded")
        export_id = store.new_id("job")
        store.write_bytes(
            store.export_zip_path(OWNER, WS, lesson.lesson_id, export_id),
            b"PK-zip")

        stripped = lc.strip_uploaded_images(OWNER)
        self.assertEqual(stripped, 1)
        self.assertFalse(store.asset_file_path(
            OWNER, WS, lesson.lesson_id, upload_asset.asset_id, "png").exists())
        meta = store.read_json(store.asset_meta_path(
            OWNER, WS, lesson.lesson_id, upload_asset.asset_id))
        self.assertEqual(meta["status"], "unavailable")
        # 图库图保留 ready
        stock_meta = store.read_json(store.asset_meta_path(
            OWNER, WS, lesson.lesson_id, stock_asset.asset_id))
        self.assertEqual(stock_meta["status"], "ready")
        self.assertFalse((rev_dir / "frame.html").exists())
        self.assertFalse(store.export_zip_path(
            OWNER, WS, lesson.lesson_id, export_id).exists())


class OrphanScanTests(StorageSandboxTestCase):
    def test_orphan_owner_root_detected(self):
        from app.core import orphan_cleanup
        build_lesson()  # OWNER 未注册 → 整个 owner 根是孤儿
        found = orphan_cleanup._collect_orphans(["usr_someone_else"])
        self.assertTrue(any(
            p == store.owner_root(OWNER) for p in found["classroom"]))

    def test_dead_workspace_subtree_detected(self):
        from app.core import orphan_cleanup
        build_lesson()  # OWNER 未注册：整根孤儿
        owner2 = "usr_protected"
        store.ensure_owner(owner2)
        ws2 = "ws_dead"
        lesson2 = sc.Lesson(
            lesson_id=store.new_id("les"), owner_id=owner2, workspace_id=ws2,
            title="课", created_at=store.utcnow(), updated_at=store.utcnow())
        store.save_lesson(lesson2)
        found = orphan_cleanup._collect_orphans(["usr_protected"])
        self.assertTrue(any(p.name == ws2 for p in found["classroom"]))
        # 受保护 owner 的根目录本身不是孤儿（只扫子树）
        self.assertFalse(any(p == store.owner_root(owner2)
                             for p in found["classroom"]))


if __name__ == "__main__":
    unittest.main()
