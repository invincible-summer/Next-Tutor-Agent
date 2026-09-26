"""课堂 run 回归（plan.md §12/§14.2、§19.2 test_classroom_runs）。

覆盖：cursor 校验、过期事件去重、lease 接管（旧 epoch 409）、幂等
progress、lease 续期不推进 state_revision、GET 不写文件（过期 active
投影 paused）、complete 的 listened/browsed 区分、结束后拒绝旧播放事件、
create/resume/restart 语义、audio-profile 切换重置回退锁。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import runs as runs_mod  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, WS  # noqa: E402
from tests.test_classroom_revisions import RevisionTestBase, _deps  # noqa: E402


def _request(**kw) -> sc.CreateRunRequest:
    return sc.CreateRunRequest(**kw)


def _acquire(client: str, *, takeover: bool = False) -> sc.LeaseAcquireRequest:
    return sc.LeaseAcquireRequest(client_id=client, takeover=takeover)


def _progress(lease_epoch: int, state_revision: int, event: str,
              cursor: sc.Cursor, action: sc.ProgressAction
              = sc.ProgressAction.progress) -> sc.ProgressRequest:
    return sc.ProgressRequest(
        expected_state_revision=state_revision, client_event_id=event,
        client_seq=1, lease_epoch=lease_epoch, action=action, cursor=cursor)


class RunFlowTests(RevisionTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for p in [
            mock.patch.object(settings, "azure_speech_key", "k"),
            mock.patch.object(settings, "azure_speech_region", "eastasia"),
            mock.patch.object(settings, "classroom_tts_voice_zh",
                              "zh-CN-XiaoxiaoNeural"),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.lesson_id, _job, base = self._published_course()
        self.spec = base
        self.first_slide = min(base.slides, key=lambda s: s.order)
        self.first_seg = self.first_slide.segments[0]

    def _create(self, mode: str = "resume_or_create",
                key: str = "k-run-create-0001") -> dict:
        return runs_mod.create_run(
            OWNER, WS, self.lesson_id,
            _request(mode=sc.RunStartMode(mode)), idempotency_key=key)

    def test_create_resume_restart(self):
        first = self._create(key="k-run-a-000000000001")
        self.assertFalse(first["resumed"])
        # 同 key 幂等重放
        replay = self._create(key="k-run-a-000000000001")
        self.assertEqual(replay["run_id"], first["run_id"])
        # resume_or_create 复用未终结 run
        second = self._create(key="k-run-b-000000000002")
        self.assertTrue(second["resumed"])
        self.assertEqual(second["run_id"], first["run_id"])
        # restart 开新 run，旧的 ended
        third = self._create(mode="restart", key="k-run-c-000000000003")
        self.assertFalse(third["resumed"])
        self.assertNotEqual(third["run_id"], first["run_id"])
        old = store.load_run(OWNER, WS, self.lesson_id, first["run_id"])
        self.assertEqual(old.status, sc.RunStatus.ended)

    def test_create_initializes_checkpoints_no_audio(self):
        created = self._create()
        run = store.load_run(OWNER, WS, self.lesson_id, created["run_id"])
        expected = sum(
            1 for s in self.spec.slides for b in s.blocks
            if getattr(b, "kind", "") == "checkpoint")
        self.assertEqual(len(run.checkpoint_refs), min(expected, 3))
        self.assertEqual(run.status, sc.RunStatus.paused)
        audio_dir = store.lesson_root(OWNER, WS, self.lesson_id) / "audio"
        self.assertFalse(audio_dir.exists())   # 创建不合成音频

    def test_get_does_not_write_and_projects_expired_lease_paused(self):
        created = self._create()
        run_id = created["run_id"]
        lease = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                       _acquire("client-aaaa-0001"))
        before = store.load_run(OWNER, WS, self.lesson_id, run_id)
        _ = runs_mod.update_progress(
            OWNER, WS, self.lesson_id, run_id,
            _progress(lease.lease_epoch, before.state_revision,
                      "evt-0001-aaaaaaaa", before.cursor))
        # 把 lease 拨到过期
        past = datetime.now(timezone.utc) - timedelta(seconds=1)

        def expire(r: sc.ClassroomRun) -> None:
            if r.lease is not None:
                r.lease = sc.LeaseInfo(client_id=r.lease.client_id,
                                       lease_epoch=r.lease.lease_epoch,
                                       expires_at=past,
                                       heartbeat_at=r.lease.heartbeat_at)
        store.update_run(OWNER, WS, self.lesson_id, run_id, expire,
                         bump_revision=False)
        snap = store.load_run(OWNER, WS, self.lesson_id, run_id)
        # GET 投影 paused，但不写盘
        public = runs_mod.run_public(OWNER, WS, self.lesson_id, run_id)
        self.assertEqual(public["status"], "paused")
        after = store.load_run(OWNER, WS, self.lesson_id, run_id)
        self.assertEqual(after.status, snap.status)
        self.assertEqual(after.state_revision, snap.state_revision)

    def test_lease_takeover_and_old_epoch_rejected(self):
        created = self._create()
        run_id = created["run_id"]
        lease_a = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                         _acquire("client-aaaa-0001"))
        # B 无接管 → 409
        with self.assertRaises(ClassroomError) as ctx:
            runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                   _acquire("client-bbbb-0002"))
        self.assertEqual(ctx.exception.code, "lease_conflict")
        # B 显式接管 → epoch 递增
        lease_b = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                         _acquire("client-bbbb-0002",
                                                 takeover=True))
        self.assertEqual(lease_b.lease_epoch, lease_a.lease_epoch + 1)
        # 旧控制者 A：续期与写进度都 409
        with self.assertRaises(ClassroomError):
            runs_mod.renew_lease(OWNER, WS, self.lesson_id, run_id,
                                 sc.LeaseRenewRequest(
                                     client_id="client-aaaa-0001",
                                     lease_epoch=lease_a.lease_epoch))
        run = store.load_run(OWNER, WS, self.lesson_id, run_id)
        with self.assertRaises(ClassroomError):
            runs_mod.update_progress(OWNER, WS, self.lesson_id, run_id,
                                     _progress(lease_a.lease_epoch,
                                               run.state_revision,
                                               "evt-old-000000001",
                                               run.cursor))
        # 旧 epoch 的 release 不影响新 lease
        runs_mod.release_lease(OWNER, WS, self.lesson_id, run_id,
                               "client-aaaa-0001", lease_a.lease_epoch)
        still = store.load_run(OWNER, WS, self.lesson_id, run_id)
        self.assertIsNotNone(still.lease)
        self.assertEqual(still.lease.client_id, "client-bbbb-0002")

    def test_lease_renew_does_not_bump_state_revision(self):
        """15s 心跳不得推进 state_revision（否则进度 CAS 永远 409）。"""
        created = self._create()
        run_id = created["run_id"]
        lease = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                       _acquire("client-aaaa-0001"))
        before = store.load_run(OWNER, WS, self.lesson_id, run_id)
        for _ in range(3):
            runs_mod.renew_lease(OWNER, WS, self.lesson_id, run_id,
                                 sc.LeaseRenewRequest(
                                     client_id="client-aaaa-0001",
                                     lease_epoch=lease.lease_epoch))
        after = store.load_run(OWNER, WS, self.lesson_id, run_id)
        self.assertEqual(after.state_revision, before.state_revision)

    def test_progress_idempotent_and_cas(self):
        created = self._create()
        run_id = created["run_id"]
        lease = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                       _acquire("client-aaaa-0001"))
        run = store.load_run(OWNER, WS, self.lesson_id, run_id)
        req = _progress(lease.lease_epoch, run.state_revision,
                        "evt-0001-aaaaaaaa", run.cursor)
        first = runs_mod.update_progress(OWNER, WS, self.lesson_id, run_id,
                                         req)
        # 同事件重试：接受、返回当前 revision，不重复推进
        reloaded = store.load_run(OWNER, WS, self.lesson_id, run_id)
        second = runs_mod.update_progress(
            OWNER, WS, self.lesson_id, run_id,
            _progress(lease.lease_epoch, reloaded.state_revision,
                      "evt-0001-aaaaaaaa", reloaded.cursor))
        self.assertEqual(second.state_revision, reloaded.state_revision)
        # CAS 过期事件（旧 expected revision）→ 409 revision_conflict
        with self.assertRaises(ClassroomError) as ctx:
            runs_mod.update_progress(OWNER, WS, self.lesson_id, run_id,
                                     _progress(lease.lease_epoch, 1,
                                               "evt-0002-bbbbbbbb",
                                               reloaded.cursor))
        self.assertEqual(ctx.exception.code, "revision_conflict")

    def test_cursor_validation(self):
        created = self._create()
        run_id = created["run_id"]
        lease = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                       _acquire("client-aaaa-0001"))
        run = store.load_run(OWNER, WS, self.lesson_id, run_id)
        bad_seg = sc.Cursor(slide_id=self.first_slide.slide_id,
                            segment_id="seg_ffffffffffffffffffffffff")
        with self.assertRaises(ClassroomError) as ctx:
            runs_mod.update_progress(OWNER, WS, self.lesson_id, run_id,
                                     _progress(lease.lease_epoch,
                                               run.state_revision,
                                               "evt-0003-cccccccc", bad_seg))
        self.assertEqual(ctx.exception.code, "content_invalid")
        bad_slide = sc.Cursor(slide_id="s_" + "f" * 12,
                              segment_id=self.first_seg.segment_id)
        with self.assertRaises(ClassroomError):
            runs_mod.update_progress(OWNER, WS, self.lesson_id, run_id,
                                     _progress(lease.lease_epoch,
                                               run.state_revision,
                                               "evt-0004-dddddddd", bad_slide))
        # 负 offset 由 schema 层拒绝（Cursor ge=0），引擎不再重复校验

    def test_complete_listened_vs_browsed_and_ended_rejects(self):
        created = self._create()
        run_id = created["run_id"]
        lease = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id,
                                       _acquire("client-aaaa-0001"))
        run = store.load_run(OWNER, WS, self.lesson_id, run_id)
        # 跳到末页结束 → browsed（浏览完成），并记录 skipped
        last_slide = max(self.spec.slides, key=lambda s: s.order)
        last_seg = last_slide.segments[-1]
        skipped = [s.slide_id for s in self.spec.slides
                   if s.slide_id != last_slide.slide_id][:1]
        browsed_cursor = sc.Cursor(slide_id=last_slide.slide_id,
                                   segment_id=last_slide.segments[0].segment_id)
        reloaded = store.load_run(OWNER, WS, self.lesson_id, run_id)
        runs_mod.update_progress(
            OWNER, WS, self.lesson_id, run_id,
            _progress(lease.lease_epoch, reloaded.state_revision,
                      "evt-0006-ffffffff", browsed_cursor,
                      action=sc.ProgressAction.complete))
        run2 = store.load_run(OWNER, WS, self.lesson_id, run_id)
        self.assertEqual(run2.status, sc.RunStatus.completed)
        self.assertEqual(run2.completed_kind, "browsed")
        # 正常听完后 complete → listened
        created2 = self._create(mode="restart", key="k-run-l-0000000004")
        run_id2 = created2["run_id"]
        lease2 = runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id2,
                                        _acquire("client-aaaa-0001"))
        r2 = store.load_run(OWNER, WS, self.lesson_id, run_id2)
        end_cursor = sc.Cursor(slide_id=last_slide.slide_id,
                               segment_id=last_seg.segment_id)
        runs_mod.update_progress(
            OWNER, WS, self.lesson_id, run_id2,
            _progress(lease2.lease_epoch, r2.state_revision,
                      "evt-0007-99999999", end_cursor,
                      action=sc.ProgressAction.complete))
        r3 = store.load_run(OWNER, WS, self.lesson_id, run_id2)
        self.assertEqual(r3.completed_kind, "listened")
        # 结束后旧播放事件全部拒绝
        with self.assertRaises(ClassroomError):
            runs_mod.update_progress(
                OWNER, WS, self.lesson_id, run_id2,
                _progress(lease2.lease_epoch, r3.state_revision,
                          "evt-0008-88888888", end_cursor))
        with self.assertRaises(ClassroomError):
            runs_mod.acquire_lease(OWNER, WS, self.lesson_id, run_id2,
                                   _acquire("client-cccc-0003"))

    def test_audio_profile_switch_resets_fallback_lock(self):
        created = self._create()
        run_id = created["run_id"]

        def lock(r: sc.ClassroomRun) -> None:
            r.tts_local_locked = True
            r.tts_fallback_notified = True
        store.update_run(OWNER, WS, self.lesson_id, run_id, lock,
                         bump_revision=False)
        run = store.load_run(OWNER, WS, self.lesson_id, run_id)
        # 未批准音色被 allowlist 回落为语言默认（§11.3）
        prefs = sc.VoicePreferences(policy=sc.VoicePolicy.cloud,
                                    voice_id="zh-CN-YunxiNeural")
        public = runs_mod.update_audio_profile(
            OWNER, WS, self.lesson_id, run_id,
            sc.AudioProfileRequest(
                expected_state_revision=run.state_revision,
                voice_preferences=prefs))
        updated = store.load_run(OWNER, WS, self.lesson_id, run_id)
        self.assertEqual(updated.audio_profile.voice_id,
                         "zh-CN-XiaoxiaoNeural")
        self.assertEqual(updated.audio_profile.version, 2)
        self.assertFalse(updated.tts_local_locked)
        self.assertEqual(updated.cursor.segment_id, run.cursor.segment_id)
        # 管理员批准的音色（zh 默认）显式选择 → 采纳
        prefs_ok = sc.VoicePreferences(
            policy=sc.VoicePolicy.cloud,
            voice_id="zh-CN-XiaoxiaoNeural")
        reloaded = store.load_run(OWNER, WS, self.lesson_id, run_id)
        runs_mod.update_audio_profile(
            OWNER, WS, self.lesson_id, run_id,
            sc.AudioProfileRequest(
                expected_state_revision=reloaded.state_revision,
                voice_preferences=prefs_ok))
        final = store.load_run(OWNER, WS, self.lesson_id, run_id)
        self.assertEqual(final.audio_profile.voice_id, "zh-CN-XiaoxiaoNeural")
        self.assertEqual(final.audio_profile.version, 3)
        self.assertEqual(public["audio_profile"]["voice_id"],
                         "zh-CN-XiaoxiaoNeural")
        # CAS：旧 expected revision → 409
        with self.assertRaises(ClassroomError):
            runs_mod.update_audio_profile(
                OWNER, WS, self.lesson_id, run_id,
                sc.AudioProfileRequest(
                    expected_state_revision=run.state_revision,
                    voice_preferences=prefs_ok))


if __name__ == "__main__":
    unittest.main()
