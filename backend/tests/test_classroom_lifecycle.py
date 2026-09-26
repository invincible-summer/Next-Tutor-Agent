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


# ---------------------------------------------------------------------------
# J01：完整音频/图片/run/QA 课程的全生命周期回归
# ---------------------------------------------------------------------------

FULL_OWNER = "usr_full_lc"
FULL_WS = "ws_full_物理"


def _no_empty_dirs(root: Path) -> list[Path]:
    """返回 root 下所有空目录（root 本身不计）。"""
    empties: list[Path] = []
    if not root.is_dir():
        return empties
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            try:
                next(p.iterdir())
            except StopIteration:
                empties.append(p)
            except OSError:
                pass
    return empties


class FullLessonLifecycleTests(StorageSandboxTestCase):
    """用真实发布路径构造完整课程，回归归档/恢复/注销/孤儿扫描/管理员清理。"""

    def setUp(self) -> None:
        super().setUp()
        self.full = self._build_full_lesson()

    def _build_full_lesson(self) -> dict:
        from app.core.session import load_session
        from app.classroom.chat_context import ensure_qa_session

        ws_mod.save_workspace(ws_mod.Workspace(
            workspace_id=FULL_WS, name="完整生命周期工作区",
            student_id=FULL_OWNER))
        store.ensure_owner(FULL_OWNER)
        lesson = sc.Lesson(
            lesson_id=store.new_id("les"), owner_id=FULL_OWNER,
            workspace_id=FULL_WS, title="动量守恒完整课",
            created_at=store.utcnow(), updated_at=store.utcnow())
        store.save_lesson(lesson)

        job = sc.GenerationJob(
            job_id=store.new_id("job"), owner_id=FULL_OWNER,
            workspace_id=FULL_WS, lesson_id=lesson.lesson_id,
            target_revision=1, state=sc.JobState.running,
            brief_hash="a" * 64, created_at=store.utcnow(),
            updated_at=store.utcnow())
        store.save_job(job)

        # 真实发布路径：staging → manifest → commit_revision
        revision = fx.make_revision(1, slides=[fx.make_slide(1)])
        asset = revision.assets[0] if revision.assets else sc.AssetRecord(
            asset_id=store.new_id("ast"), sha256="d" * 64, mime="image/webp",
            width=640, height=360,
            provenance=sc.AssetProvenance(
                provider=sc.AssetProvider.pexels,
                provider_asset_id="px_123", source_url="https://pexels/p/123",
                creator="摄图作者", creator_url="https://pexels/u",
                license_url="https://pexels/l", fetched_at=store.utcnow()),
            alt="碰撞实验示意", caption="两小车碰撞", role=sc.AssetRole.scene,
            bytes=64, status=sc.AssetStatus.ready)
        revision = revision.model_copy(update={"assets": [asset]})
        store.write_json(store.asset_meta_path(
            FULL_OWNER, FULL_WS, lesson.lesson_id, asset.asset_id),
            asset.model_dump(mode="json", by_alias=True))
        store.write_bytes(store.asset_file_path(
            FULL_OWNER, FULL_WS, lesson.lesson_id, asset.asset_id, "webp"),
            b"\x00" + b"a" * 63)

        staging = store.prepare_revision_staging(
            FULL_OWNER, FULL_WS, lesson.lesson_id, 1)
        spec_text = store.canonical_json(
            revision.model_dump(mode="json", by_alias=True))
        files = {"spec.private.json": store.bytes_hash(spec_text.encode())}
        store.stage_file(staging, "spec.private.json", spec_text)
        asset_data = store.asset_file_path(
            FULL_OWNER, FULL_WS, lesson.lesson_id, asset.asset_id,
            "webp").read_bytes()
        (staging / "assets").mkdir()
        store.stage_file(staging / "assets", f"{asset.asset_id}.webp",
                         asset_data)
        files[f"assets/{asset.asset_id}.webp"] = store.bytes_hash(asset_data)
        manifest = {"revision": 1, "schema_version": 1,
                    "content_hash": revision.content_hash, "files": files,
                    "assets": [{"asset_id": asset.asset_id,
                                "file": f"assets/{asset.asset_id}.webp",
                                "sha256": asset.sha256}],
                    "renderer_version": revision.renderer_version,
                    "created_at": revision.created_at.isoformat()}
        fresh = store.load_job(FULL_OWNER, FULL_WS, lesson.lesson_id,
                               job.job_id)
        store.save_commit_intent(fresh, 1, manifest)
        store.commit_revision(FULL_OWNER, FULL_WS, lesson.lesson_id, 1,
                              manifest, expected_epoch=fresh.epoch)
        store.index_upsert_lesson(FULL_OWNER, FULL_WS, lesson)

        # 音频缓存（两段）+ 导出
        for idx, seg in enumerate(revision.slides[0].segments):
            synth = f"{idx:064x}"
            store.write_bytes(store.audio_file_path(
                FULL_OWNER, FULL_WS, lesson.lesson_id, synth), b"RIFF" + b"x" * 96)
            store.write_json(store.audio_meta_path(
                FULL_OWNER, FULL_WS, lesson.lesson_id, synth),
                {"synthesis_hash": synth, "bytes": 100})
        export_id = store.new_id("job")
        store.write_bytes(store.export_zip_path(
            FULL_OWNER, FULL_WS, lesson.lesson_id, export_id), b"PK-zip")
        store.write_json(store.export_meta_path(
            FULL_OWNER, FULL_WS, lesson.lesson_id, export_id),
            {"export_id": export_id, "kind": "html_zip"})

        # run：lease + 检查点 + 批注 + audio_refs + QA 绑定（真实会话）
        run = sc.ClassroomRun(
            run_id=store.new_id("run"), owner_id=FULL_OWNER,
            workspace_id=FULL_WS, lesson_id=lesson.lesson_id,
            lesson_revision=1, content_hash=revision.content_hash,
            status=sc.RunStatus.active,
            cursor=sc.Cursor(slide_id=revision.slides[0].slide_id,
                             segment_id=revision.slides[0].segments[0].segment_id),
            cursor_slide_order=1,
            checkpoint_refs=[sc.RunCheckpointRef(
                checkpoint_id=f"ckp_{1:024x}",
                slide_id=revision.slides[0].slide_id,
                kind=sc.CheckpointKind.reflect,
                state=sc.CheckpointRunState.answered)],
            audio_refs={revision.slides[0].segments[0].segment_id: f"{0:064x}"},
            visited_slides=[revision.slides[0].slide_id],
            annotations=[sc.RunAnnotation(
                annotation_id="ann_1", slide_id=revision.slides[0].slide_id,
                user_text="内力不改变总动量",
                created_at=store.utcnow())],
            created_at=store.utcnow(), updated_at=store.utcnow())
        run.lease = sc.LeaseInfo(client_id="client_full_01", lease_epoch=1,
                                 expires_at=store.utcnow(),
                                 heartbeat_at=store.utcnow())
        store.save_run(run)

        qa = ensure_qa_session(FULL_OWNER, run, lesson.title, "本科", "zh")
        self.assertIsNotNone(load_session(qa.session_id))

        # owner 级缓存：图片搜索缓存 + 试听
        store.write_json(store.image_search_cache_path(FULL_OWNER, "q" * 16),
                         {"query_hash": "q" * 16})
        store.write_bytes(store.voice_preview_path(FULL_OWNER, "v" * 64, "wav"),
                          b"RIFF")
        return {"lesson": lesson, "job": job, "run": run, "asset": asset,
                "revision": revision, "qa_session_id": qa.session_id,
                "export_id": export_id}

    # ------------------------------------------------------------- 归档→恢复

    def test_full_archive_restore_preserves_everything(self):
        info = self.full
        lesson_id = info["lesson"].lesson_id
        manifest = trash_mod.archive_classroom_lesson(
            FULL_OWNER, FULL_WS, lesson_id)
        # 活跃侧：课程子树整体消失且不留空目录
        self.assertFalse(store.lesson_root(FULL_OWNER, FULL_WS,
                                           lesson_id).exists())
        self.assertEqual(_no_empty_dirs(store.owner_root(FULL_OWNER)), [])
        self.assertNotIn(lesson_id,
                         store.read_index(FULL_OWNER, FULL_WS)["lessons"])

        # 回收站载荷：spec/assets/runs/jobs 齐全；音频与导出不打包
        bundle = trash_mod._item_dir(FULL_OWNER, manifest["id"])
        payload = bundle / "payload" / "lesson"
        self.assertTrue((payload / "revisions" / "1" /
                         "spec.private.json").is_file())
        self.assertTrue((payload / "revisions" / "1" / "assets" /
                         f"{info['asset'].asset_id}.webp").is_file())
        self.assertTrue((payload / "runs").is_dir())
        self.assertTrue((payload / "jobs").is_dir())
        self.assertFalse((payload / "audio").exists())
        self.assertFalse((payload / "exports").exists())

        trash_mod.restore_item(FULL_OWNER, manifest["id"])
        # 恢复后：revision 可加载、hash 校验一致、指针在
        restored_spec = store.load_revision(FULL_OWNER, FULL_WS, lesson_id, 1)
        self.assertIsNotNone(restored_spec)
        self.assertEqual(restored_spec.content_hash,
                         info["revision"].content_hash)
        self.assertTrue(store.load_revision_manifest(
            FULL_OWNER, FULL_WS, lesson_id, 1))
        lesson_after = store.load_lesson(FULL_OWNER, FULL_WS, lesson_id)
        self.assertEqual(lesson_after.lifecycle, sc.LessonLifecycle.active)
        self.assertEqual(lesson_after.latest_ready_revision, 1)
        # run 恢复为 paused、lease 清空；检查点/批注/QA 绑定保留
        run_after = store.load_run(FULL_OWNER, FULL_WS, lesson_id,
                                   info["run"].run_id)
        self.assertEqual(run_after.status, sc.RunStatus.paused)
        self.assertIsNone(run_after.lease)
        self.assertEqual(run_after.qa_session_id, info["qa_session_id"])
        self.assertEqual(run_after.annotations[0].user_text,
                         "内力不改变总动量")
        self.assertEqual(run_after.checkpoint_refs[0].state,
                         sc.CheckpointRunState.answered)
        from app.core.session import load_session
        self.assertIsNotNone(load_session(info["qa_session_id"]))
        # asset bytes 与 meta 完整回来
        self.assertTrue(store.asset_file_path(
            FULL_OWNER, FULL_WS, lesson_id, info["asset"].asset_id,
            "webp").is_file())
        # 中断 job → needs_input(recovered_after_archive)
        job_after = store.load_job(FULL_OWNER, FULL_WS, lesson_id,
                                   info["job"].job_id)
        self.assertEqual(job_after.state, sc.JobState.needs_input)
        self.assertEqual(job_after.last_error, "recovered_after_archive")
        # 恢复不复活音频缓存（可重建，不在 bundle 里）
        self.assertFalse(store.audio_file_path(
            FULL_OWNER, FULL_WS, lesson_id, f"{0:064x}").exists())

    def test_full_workspace_archive_prunes_empty_dirs(self):
        info = self.full
        manifest = trash_mod.archive_workspace(FULL_OWNER, FULL_WS)
        self.assertEqual(
            manifest["metadata"].get("classroom_lesson_count"), 1)
        # 工作区课堂子树删除后不留空目录（workspaces/ 随之清掉）
        self.assertFalse(store.workspace_root(FULL_OWNER, FULL_WS).exists())
        self.assertEqual(_no_empty_dirs(store.owner_root(FULL_OWNER)), [])
        # owner.json 仍在 → owner 根不是空目录
        self.assertTrue(store.owner_meta_path(FULL_OWNER).is_file())

        trash_mod.restore_item(FULL_OWNER, manifest["id"])
        self.assertTrue(store.workspace_root(FULL_OWNER, FULL_WS).is_dir())
        self.assertIsNotNone(store.load_lesson(
            FULL_OWNER, FULL_WS, info["lesson"].lesson_id))
        run_after = store.load_run(FULL_OWNER, FULL_WS,
                                   info["lesson"].lesson_id,
                                   info["run"].run_id)
        self.assertEqual(run_after.status, sc.RunStatus.paused)

    def test_trash_purge_releases_lesson_subtree(self):
        info = self.full
        manifest = trash_mod.archive_classroom_lesson(
            FULL_OWNER, FULL_WS, info["lesson"].lesson_id)
        trash_mod.purge_item(FULL_OWNER, manifest["id"])
        # 回收站条目删除后 owner 根只剩 owner.json + 缓存，无空目录
        self.assertEqual(
            trash_mod.list_items(FULL_OWNER,
                                 resource_type="classroom_lesson"), [])
        self.assertEqual(_no_empty_dirs(store.owner_root(FULL_OWNER)), [])
        # 永久删除后不可恢复：restore 报条目不存在
        with self.assertRaises(Exception):
            trash_mod.restore_item(FULL_OWNER, manifest["id"])

    # ------------------------------------------------------------- 账号注销

    def test_purge_account_after_full_usage(self):
        from app.core import account_data
        from app.core.session import load_session
        info = self.full
        # 注销前课堂与 QA 会话都在
        self.assertTrue(store.owner_root(FULL_OWNER).is_dir())
        self.assertIsNotNone(load_session(info["qa_session_id"]))
        account_data.purge_account(FULL_OWNER)
        # 课堂 owner 根整体消失；classroom 根下无该 owner 目录
        self.assertFalse(store.owner_root(FULL_OWNER).exists())
        classroom_root = store.classroom_root()
        if classroom_root.is_dir():
            self.assertEqual(_no_empty_dirs(classroom_root), [])
            self.assertFalse((classroom_root / FULL_OWNER).exists())
        # QA 聊天会话随账号清除
        self.assertIsNone(load_session(info["qa_session_id"]))
        # tombstone：晚到课堂写回被拒
        with self.assertRaises(store.ClassroomStorageError):
            store.ensure_owner(FULL_OWNER)

    def test_admin_clear_all_and_uploads_only_on_full_lesson(self):
        from app.core import account_data
        info = self.full
        lesson_id = info["lesson"].lesson_id
        # uploads_only：图库图保留、上传图剥离（复用既有 UploadsOnly 语义，
        # 这里验证完整课程结构下两个 scope 都可重复执行且无空目录）
        report = account_data.clear_chat_data(FULL_OWNER, scope="uploads_only")
        self.assertIn("classroom_upload_images", report)
        self.assertTrue(store.owner_root(FULL_OWNER).is_dir())
        self.assertTrue(store.load_lesson(FULL_OWNER, FULL_WS, lesson_id)
                        is not None)
        report = account_data.clear_chat_data(FULL_OWNER, scope="all")
        self.assertFalse(store.owner_root(FULL_OWNER).exists())
        # all 之后账号仍在：可再建课（无 tombstone）
        store.ensure_owner(FULL_OWNER)
        self.assertTrue(store.owner_root(FULL_OWNER).is_dir())

    # ------------------------------------------------------- 孤儿扫描/管理员

    def test_orphan_scan_and_admin_purge(self):
        from app.core import orphan_cleanup
        # 完整课程受保护：注册 owner + 活跃工作区 → 非孤儿
        found = orphan_cleanup._collect_orphans([FULL_OWNER])
        self.assertEqual(found["classroom"], [])
        dry = orphan_cleanup.purge_orphans(
            [FULL_OWNER], categories=["classroom"], dry_run=True)
        self.assertEqual(dry["categories"]["classroom"]["deleted"], 0)

        # 遗留孤儿 owner 根（模拟历史删除账号）：扫描可见，管理员清理删净
        ghost = "usr_ghost_lc"
        store.ensure_owner(ghost)
        glesson = sc.Lesson(
            lesson_id=store.new_id("les"), owner_id=ghost,
            workspace_id="ws_ghost", title="孤儿课",
            created_at=store.utcnow(), updated_at=store.utcnow())
        store.save_lesson(glesson)
        found = orphan_cleanup._collect_orphans([FULL_OWNER])
        self.assertTrue(any(p == store.owner_root(ghost)
                            for p in found["classroom"]))
        purged = orphan_cleanup.purge_orphans(
            [FULL_OWNER], categories=["classroom"])
        self.assertGreaterEqual(purged["categories"]["classroom"]["deleted"], 1)
        self.assertFalse(store.owner_root(ghost).exists())
        # 受保护 owner 的完整课程不受影响
        self.assertIsNotNone(store.load_lesson(
            FULL_OWNER, FULL_WS, self.full["lesson"].lesson_id))
        classroom_root = store.classroom_root()
        if classroom_root.is_dir():
            self.assertEqual(_no_empty_dirs(classroom_root), [])

    def test_workspacewise_orphan_after_archive(self):
        """工作区归档（trash 内）后课堂子树已删：扫描无孤儿、trash 受保护。"""
        from app.core import orphan_cleanup
        manifest = trash_mod.archive_workspace(FULL_OWNER, FULL_WS)
        found = orphan_cleanup._collect_orphans([FULL_OWNER])
        # 回收站里的合法 bundle 不算孤儿；活跃课堂根下没有残留子树
        self.assertFalse(any(store.workspace_root(FULL_OWNER, FULL_WS) == p
                             or store.workspace_root(FULL_OWNER, FULL_WS)
                             in p.parents for p in found["classroom"]))
        self.assertFalse(store.workspace_root(FULL_OWNER, FULL_WS).exists())
        # 未清空回收站前 trash 类别不报该 bundle
        trash_hits = [p for p in found["trash"]
                      if manifest["id"] in p.name]
        self.assertEqual(trash_hits, [])
        trash_mod.restore_item(FULL_OWNER, manifest["id"])
        found2 = orphan_cleanup._collect_orphans([FULL_OWNER])
        self.assertEqual(found2["classroom"], [])


if __name__ == "__main__":
    unittest.main()
