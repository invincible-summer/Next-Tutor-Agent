"""课堂健康告警扫描与恢复动作（plan.md §20.3 / J03）。

覆盖七类告警的触发与不触发、只读性（不 mkdir）、sweep_audio 恢复动作、
云鉴权连击计数（audio.record_cloud_auth_result）。
"""
from __future__ import annotations

import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classroom import health, limits  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

OWNER = "usr_health_test"
WS = "ws_health"


def _lesson(title: str = "健康扫描课") -> sc.Lesson:
    store.ensure_owner(OWNER)
    lesson = sc.Lesson(
        lesson_id=store.new_id("les"), owner_id=OWNER, workspace_id=WS,
        title=title, created_at=store.utcnow(), updated_at=store.utcnow())
    store.save_lesson(lesson)
    store.index_upsert_lesson(OWNER, WS, lesson)
    return lesson


def _job(lesson: sc.Lesson, *, state: sc.JobState,
         error: str | None = None,
         updated_at=None) -> str:
    job_id = store.new_id("job")
    job = sc.GenerationJob(
        job_id=job_id, owner_id=OWNER, workspace_id=WS,
        lesson_id=lesson.lesson_id, target_revision=1, state=state,
        brief_hash="a" * 64, last_error=error,
        created_at=store.utcnow(), updated_at=updated_at or store.utcnow())
    store.save_job(job)
    return job_id


def _codes(report: dict) -> set[str]:
    return {a["code"] for a in report["alerts"]}


class HealthScanTests(StorageSandboxTestCase):
    def test_clean_state_has_no_alerts_and_reads_only(self):
        lesson = _lesson()
        _job(lesson, state=sc.JobState.succeeded)
        before = {p: p.exists() for p in _snapshot(store.classroom_root())}
        report = health.scan_alerts()
        self.assertEqual(report["alert_count"], 0)
        # 只读：不新建任何目录/文件
        after = {p: p.exists() for p in _snapshot(store.classroom_root())}
        self.assertEqual(before, after)

    def test_disk_low_alert(self):
        usage = mock.Mock(free=limits.DISK_ALERT_BYTES - 1)
        with mock.patch.object(health.shutil, "disk_usage",
                               return_value=usage):
            report = health.scan_alerts()
        self.assertIn("disk_low", _codes(report))
        self.assertEqual(report["checked"]["disk_free_bytes"],
                         limits.DISK_ALERT_BYTES - 1)

    def test_cloud_auth_failure_streak_alert(self):
        from app.classroom import audio
        for _ in range(limits.CLOUD_AUTH_FAIL_ALERT - 1):
            audio.record_cloud_auth_result(OWNER, failed=True)
        self.assertNotIn("cloud_auth_failures", _codes(health.scan_alerts()))
        audio.record_cloud_auth_result(OWNER, failed=True)
        report = health.scan_alerts()
        self.assertIn("cloud_auth_failures", _codes(report))
        # 成功一次即清零
        audio.record_cloud_auth_result(OWNER, failed=False)
        self.assertNotIn("cloud_auth_failures",
                         _codes(health.scan_alerts()))

    def test_job_failure_rate_alert(self):
        lesson = _lesson()
        # 5 个样本里 2 个失败（40% > 20%）
        _job(lesson, state=sc.JobState.succeeded)
        _job(lesson, state=sc.JobState.succeeded)
        _job(lesson, state=sc.JobState.succeeded)
        _job(lesson, state=sc.JobState.failed, error="generation_failed: x")
        _job(lesson, state=sc.JobState.failed, error="generation_failed: y")
        report = health.scan_alerts()
        self.assertIn("job_failure_rate", _codes(report))
        # 样本不足（<5 终态）不告警
        report2 = health.scan_alerts()
        self.assertEqual(report2["checked"]["jobs_scanned"], 5)

    def test_queue_stalled_alert(self):
        lesson = _lesson()
        old = store.utcnow() - timedelta(seconds=limits.QUEUE_STALL_SECONDS
                                         + 60)
        _job(lesson, state=sc.JobState.queued, updated_at=old)
        fresh = _job(lesson, state=sc.JobState.queued)
        report = health.scan_alerts()
        self.assertIn("queue_stalled", _codes(report))
        # 新排队 job 不触发
        raw = store.read_json(store.job_meta_path(
            OWNER, WS, lesson.lesson_id, fresh))
        self.assertEqual(raw["state"], "queued")

    def test_renderer_failure_streak_alert(self):
        lesson = _lesson()
        for n in range(limits.RENDERER_FAIL_ALERT):
            _job(lesson, state=sc.JobState.failed,
                 error=f"renderer_unavailable: 启动失败 {n}")
        report = health.scan_alerts()
        self.assertIn("renderer_failures", _codes(report))
        # 最近一次失败不是 renderer → 连击断开
        _job(lesson, state=sc.JobState.failed, error="budget_exceeded: 超限")
        self.assertNotIn("renderer_failures", _codes(health.scan_alerts()))

    def test_damaged_lesson_alert(self):
        lesson = _lesson()
        meta = store.lesson_meta_path(OWNER, WS, lesson.lesson_id)
        meta.write_text("{ corrupted", encoding="utf-8")
        report = health.scan_alerts()
        self.assertIn("damaged_lessons", _codes(report))
        entries = next(a for a in report["alerts"]
                       if a["code"] == "damaged_lessons")["lessons"]
        self.assertEqual(entries[0]["lesson_id"], lesson.lesson_id)


class CleanupActionTests(StorageSandboxTestCase):
    def test_sweep_audio_removes_only_expired(self):
        from app.classroom import audio
        lesson = _lesson()
        old_key, fresh_key = "a" * 64, "b" * 64
        for key in (old_key, fresh_key):
            store.write_bytes(store.audio_file_path(
                OWNER, WS, lesson.lesson_id, key), b"RIFF" + b"x" * 40)
            store.write_json(store.audio_meta_path(
                OWNER, WS, lesson.lesson_id, key), {"synthesis_hash": key})
        # 把 old 摘到 TTL 之外（meta mtime = 最近访问）
        old_meta = store.audio_meta_path(OWNER, WS, lesson.lesson_id, old_key)
        expired = time.time() - (limits.AUDIO_TTL_DAYS * 86400 + 3600)
        for f in (old_meta, old_meta.with_name(f"{old_key}.wav")):
            import os
            os.utime(f, (expired, expired))

        result = health.run_cleanup("sweep_audio")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["audio_pairs_removed"], 1)
        self.assertFalse(store.audio_file_path(
            OWNER, WS, lesson.lesson_id, old_key).exists())
        self.assertTrue(store.audio_file_path(
            OWNER, WS, lesson.lesson_id, fresh_key).exists())
        # 过期音频清掉后不再误报损坏/其他告警
        self.assertNotIn("damaged_lessons", _codes(health.scan_alerts()))

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            health.run_cleanup("nuke_everything")


class DiskFullProjectionTests(StorageSandboxTestCase):
    """磁盘满/权限失败 → ClassroomStorageError（storage_unavailable）。"""

    def test_write_failure_raises_storage_error_not_oserror(self):
        store.ensure_owner(OWNER)
        with mock.patch.object(
                store, "atomic_write_text",
                side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(store.ClassroomStorageError) as ctx:
                store.write_json(store.owner_meta_path(OWNER), {"k": 1})
        self.assertIn("No space left", str(ctx.exception))

    def test_storage_exception_handler_projects_envelope(self):
        from app.api.v1.classroom import storage_exception_handler
        from app.classroom.errors import ClassroomError
        from app.core.classroom_store import LessonDamagedError

        class _Req:
            pass

        resp = storage_exception_handler(
            _Req(), store.ClassroomStorageError("No space left on device"))
        self.assertEqual(resp.status_code, 503)
        body = __import__("json").loads(resp.body)
        self.assertEqual(body["error"]["code"], "storage_unavailable")
        resp2 = storage_exception_handler(
            _Req(), LessonDamagedError("corrupt spec"))
        self.assertEqual(resp2.status_code, 500)
        body2 = __import__("json").loads(resp2.body)
        self.assertEqual(body2["error"]["code"], "damaged")
        # ClassroomError 路径不受影响
        self.assertTrue(issubclass(store.ClassroomStorageError, RuntimeError))
        self.assertTrue(issubclass(ClassroomError, Exception))


def _snapshot(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*"))


if __name__ == "__main__":
    unittest.main()
