"""课堂音频引擎回归（plan.md §11.4/§11.5、§19.2 test_classroom_audio）。

覆盖：音频 key 含 owner/voice/provider、single-flight、WAV 时长元数据、
纯 GET、队列满 audio_busy、云失败一次回退本地（锁定 + 只提示一次）、
未授权段不可合成、预取窗口、缓存命中、跨用户不串、每日/run 字符预算、
LRU 淘汰、voice-preview 缓存与 owner 隔离、内容 hash 防损坏。
"""
from __future__ import annotations

import asyncio
import itertools
import struct
import sys
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import audio as ca  # noqa: E402
from app.classroom import limits  # noqa: E402
from app.classroom.audio import (  # noqa: E402
    build_segment_order, clip_id_for, pcm16_to_wav, synthesis_key,
    validate_request_window)
from app.classroom.errors import ClassroomError  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402
from app.voice.base import TTSResult, TTSUnavailable  # noqa: E402
from app.voice.tts import service as tts_service  # noqa: E402

from tests.classroom_fixtures import (  # noqa: E402
    hex_id, make_brief, make_para_block, make_revision, make_segment,
    make_slide, slide_hex)

OWNER = "usr_audio_test"
WS = "ws_audio_test"
LESSON = "les_" + "aa" * 12
_RUN_SEQ = itertools.count(1)


def _next_run_id() -> str:
    return f"run_{next(_RUN_SEQ):024x}"


def _pcm(seconds: float = 0.1, rate: int = 24000) -> bytes:
    return struct.pack("<h", 3000) * int(rate * seconds)


def _result(provider="azure", voice="zh-CN-XiaoxiaoNeural",
            seconds: float = 0.1) -> TTSResult:
    return TTSResult(pcm16=_pcm(seconds), sample_rate=24000,
                     provider=provider, voice_id=voice)


class _CloudStub:
    """可编程云合成桩：默认成功，可切换失败/慢速。"""

    def __init__(self):
        self.calls = 0
        self.fail = False
        self.slow = None   # asyncio.Event：设置后每次调用等它

    async def __call__(self, text, options):
        self.calls += 1
        if self.slow is not None:
            await self.slow.wait()
        if self.fail:
            raise TTSUnavailable("云端暂时不可用")
        return _result()


class _LocalStub:
    def __init__(self):
        self.calls = 0
        self.fail = False

    async def __call__(self, text, options):
        self.calls += 1
        if self.fail:
            raise TTSUnavailable("本地 sidecar 不可达")
        return _result(provider="melo", voice="melo-zh", seconds=0.05)


def _checkpoint_block(n: int = 9) -> sc.CheckpointBlock:
    return sc.CheckpointBlock(id=hex_id("blk", n), checkpoint_id=hex_id("ckp", n))


def _segment_text(n: int) -> str:
    return f"这是第{n}段讲稿：把两个碰撞的小车看成一个系统，内力不改变总动量。"


def _three_slide_spec() -> sc.LessonRevision:
    """3 页 × 每页 2 段（每段讲稿文本不同）；第 2 页挂未解决 checkpoint。"""
    def seg(n: int, block: int) -> sc.NarrationSegment:
        return make_segment(n, block_ids=[hex_id("blk", block)],
                            spoken=_segment_text(n))

    slides = [
        make_slide(1, blocks=[make_para_block(1), make_para_block(2)],
                   segments=[seg(1, 1), seg(2, 2)]),
        make_slide(2, blocks=[make_para_block(3), _checkpoint_block(9)],
                   segments=[seg(3, 3), seg(4, 3)]),
        make_slide(3, blocks=[make_para_block(4)],
                   segments=[seg(5, 4), seg(6, 4)]),
    ]
    return make_revision(1, slides=slides, brief=make_brief())


def _run_fixture(run_id: str | None = None,
                 *, owner: str = OWNER, cursor_seg: int = 1,
                 checkpoint_state: sc.CheckpointRunState | None = None) \
        -> sc.ClassroomRun:
    refs = []
    if checkpoint_state is not None:
        refs = [sc.RunCheckpointRef(
            checkpoint_id=hex_id("ckp", 9), slide_id=slide_hex(2),
            kind=sc.CheckpointKind.question, state=checkpoint_state)]
    now = store.utcnow()
    cursor_slide = slide_hex(1 if cursor_seg <= 2 else 2 if cursor_seg <= 4
                             else 3)
    run = sc.ClassroomRun(
        run_id=run_id or _next_run_id(), owner_id=owner, workspace_id=WS,
        lesson_id=LESSON,
        lesson_revision=1, content_hash="c" * 64,
        cursor=sc.Cursor(slide_id=cursor_slide,
                         segment_id=hex_id("seg", cursor_seg)),
        checkpoint_refs=refs, created_at=now, updated_at=now)
    store.save_run(run)
    return run


def _profile(provider="azure", voice_id="zh-CN-XiaoxiaoNeural",
             allow_local=True) -> tts_service.ClassroomVoiceProfile:
    return tts_service.ClassroomVoiceProfile(
        policy="auto", provider=provider, voice_id=voice_id,
        language="zh-CN",
        allow_local_fallback=allow_local, cloud_configured=True,
        local_enabled=True)


class AudioTestBase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for p in [
            mock.patch.object(settings, "classroom_enabled", True),
            mock.patch.object(settings, "azure_speech_key", "k"),
            mock.patch.object(settings, "azure_speech_region", "eastasia"),
            mock.patch.object(settings, "classroom_tts_voice_zh",
                              "zh-CN-XiaoxiaoNeural"),
            mock.patch.object(settings, "classroom_local_tts_enabled", True),
            mock.patch.object(settings, "voice_tts_provider", "off"),
        ]:
            p.start()
            self.addCleanup(p.stop)
        tts_service.reset_tts_service()
        ca.reset_audio_engine()
        self.addCleanup(tts_service.reset_tts_service)
        self.cloud = _CloudStub()
        self.local = _LocalStub()
        self._patch_cloud = mock.patch.object(tts_service,
                                              "cloud_synthesize", self.cloud)
        self._patch_local = mock.patch.object(tts_service,
                                              "local_synthesize", self.local)
        self._patch_cloud.start()
        self._patch_local.start()
        self.addCleanup(self._patch_cloud.stop)
        self.addCleanup(self._patch_local.stop)
        self.engine = ca.get_audio_engine()
        self.spec = _three_slide_spec()

    def _request_and_wait(self, run, spec=None, profile=None, segs=None):
        """request + wait 必须同一事件循环（lane 任务不跨循环存活）。"""
        profile = profile or _profile()
        segs = segs or [hex_id("seg", 1)]

        async def go():
            clips = await self.engine.request_narration_clips(
                run, spec or self.spec, profile, segs)
            await self.engine.wait_run(run.run_id)
            return clips
        return asyncio.run(go())


class SynthesisKeyTests(unittest.TestCase):
    def test_key_contains_owner_voice_provider(self):
        base = dict(normalized_text="同一段讲稿", provider="azure",
                    voice_id="zh-CN-XiaoxiaoNeural", language="zh-CN",
                    synthesis_speed=1.0)
        k1 = synthesis_key("usr_a", **base)
        self.assertEqual(k1, synthesis_key("usr_a", **base))
        self.assertNotEqual(k1, synthesis_key("usr_b", **base))
        changed = dict(base, voice_id="zh-CN-YunxiNeural")
        self.assertNotEqual(k1, synthesis_key("usr_a", **changed))
        changed = dict(base, provider="melo", voice_id="melo-zh")
        self.assertNotEqual(k1, synthesis_key("usr_a", **changed))
        changed = dict(base, synthesis_speed=1.2)   # playbackRate 不参与，
        self.assertNotEqual(k1, synthesis_key("usr_a", **changed))
        self.assertRegex(k1, r"^[0-9a-f]{64}$")


class EngineRequestTests(AudioTestBase):
    def _seg(self, n: int) -> str:
        return hex_id("seg", n)

    def test_request_synthesizes_and_roundtrips(self):
        run = _run_fixture()
        clips = self._request_and_wait(run)
        self.assertEqual(len(clips), 1)
        self.assertEqual(self.cloud.calls, 1)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        clip_id = clip_id_for(run.run_id, "narration", self._seg(1))
        self.assertIn(clip_id, stored.audio_refs)
        status = self.engine.clip_status(stored, clip_id)
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["provider"], "azure")
        self.assertEqual(status["voice_id"], "zh-CN-XiaoxiaoNeural")
        data, meta = self.engine.clip_content(stored, clip_id)
        with wave.open(BytesIO(data)) as w:
            self.assertEqual(w.getframerate(), meta["sample_rate"])
            self.assertEqual(w.getnframes(), meta["sample_count"])
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)

    def test_same_text_voice_replay_hits_cache(self):
        run = _run_fixture()
        self._request_and_wait(run)
        clips = self._request_and_wait(run)
        self.assertEqual(clips[0]["state"], "ready")
        self.assertEqual(self.cloud.calls, 1)   # 重放零新合成

    def test_different_owner_not_shared(self):
        run_a = _run_fixture(owner=OWNER)
        self._request_and_wait(run_a)
        run_b = _run_fixture(owner="usr_b2")
        self._request_and_wait(run_b)
        self.assertEqual(self.cloud.calls, 2)   # owner 隔离，各自合成

    def test_different_voice_not_shared(self):
        run = _run_fixture()
        self._request_and_wait(run)
        self._request_and_wait(
            run, profile=_profile(voice_id="zh-CN-YunxiNeural"))
        self.assertEqual(self.cloud.calls, 2)

    def test_single_flight_merges_same_key(self):
        run1 = _run_fixture()
        run2 = _run_fixture()
        # 同 owner 同段：两个 run 的同 key 请求合并为一次合成
        self.cloud.slow = asyncio.Event()

        async def scenario():
            t1 = asyncio.create_task(self.engine.request_narration_clips(
                run1, self.spec, _profile(), [self._seg(1)]))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(self.engine.request_narration_clips(
                run2, self.spec, _profile(), [self._seg(1)]))
            await asyncio.sleep(0.02)
            self.cloud.slow.set()
            await asyncio.gather(t1, t2)
        asyncio.run(scenario())
        self.assertEqual(self.cloud.calls, 1)

    def test_queue_full_returns_audio_busy(self):
        run = _run_fixture()
        self.cloud.slow = asyncio.Event()

        async def scenario():
            with mock.patch.object(limits, "AUDIO_QUEUE_MAX", 1):
                await self.engine.request_narration_clips(
                    run, self.spec, _profile(), [self._seg(1)])
                await asyncio.sleep(0.03)   # 首段进入在途
                try:
                    await self.engine.request_narration_clips(
                        run, self.spec, _profile(), [self._seg(2)])
                    raised = None
                except ClassroomError as exc:
                    raised = exc
                self.cloud.slow.set()
                await self.engine.wait_run(run.run_id)
                return raised
        exc = asyncio.run(scenario())
        self.assertIsNotNone(exc)
        self.assertEqual(exc.code, "audio_busy")
        self.assertTrue(exc.retryable)

    def test_run_char_budget_quota(self):
        run = _run_fixture()
        self._request_and_wait(run)
        with mock.patch.object(limits, "TTS_CHARS_PER_RUN", 1):
            with self.assertRaises(ClassroomError) as ctx:
                asyncio.run(self.engine.request_narration_clips(
                    run, self.spec, _profile(), [self._seg(2)]))
        self.assertEqual(ctx.exception.code, "quota_exceeded")

    def test_daily_cloud_char_budget(self):
        run = _run_fixture()
        with mock.patch.object(limits, "TTS_DAILY_CLOUD_CHARS", 2):
            with self.assertRaises(ClassroomError) as ctx:
                asyncio.run(self.engine.request_narration_clips(
                    run, self.spec, _profile(), [self._seg(1)]))
        self.assertEqual(ctx.exception.code, "quota_exceeded")
        self.assertEqual(self.cloud.calls, 0)

    def test_usage_counters_recorded(self):
        run = _run_fixture()
        self._request_and_wait(run)
        usage = ca.tts_usage(OWNER)
        self.assertEqual(usage.get("cloud_requests_total"), 1)
        today = list(usage.get("cloud_chars_by_day", {}).values())
        self.assertEqual(sum(today), len(ca.normalize_spoken_text(
            _segment_text(1))))


class FallbackTests(AudioTestBase):
    def _seg(self, n: int) -> str:
        return hex_id("seg", n)

    def test_cloud_failure_falls_back_local_locked_once(self):
        run = _run_fixture()
        self.cloud.fail = True
        clips = self._request_and_wait(run)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        clip_id = clips[0]["clip_id"]
        status = self.engine.clip_status(stored, clip_id)
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["provider"], "melo")
        self.assertEqual(status["voice_id"], "melo-zh")
        # run 锁定本地 + 提示一次标记
        self.assertTrue(stored.tts_local_locked)
        self.assertTrue(stored.tts_fallback_notified)
        # 该 run 后续段不再尝试云端（cloud 调用数保持 1）
        self._request_and_wait(stored, segs=[self._seg(2)])
        self.assertEqual(self.cloud.calls, 1)
        self.assertEqual(self.local.calls, 2)
        # 回退音频不得缓存到 Azure key 下
        key = stored.audio_refs[clip_id]
        self.assertNotIn(key[:8], ("azure",))
        self.assertEqual(
            store.read_json(store.audio_meta_path(
                OWNER, WS, LESSON, key))["provider"], "melo")

    def test_cloud_failure_without_fallback_marks_failed(self):
        run = _run_fixture()
        self.cloud.fail = True
        clips = self._request_and_wait(
            run, profile=_profile(allow_local=False))
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        status = self.engine.clip_status(stored, clips[0]["clip_id"])
        self.assertEqual(status["state"], "failed")
        self.assertIn("云端", status["error"])
        self.assertFalse(stored.tts_local_locked)

    def test_local_failure_marks_failed(self):
        run = _run_fixture()
        self.cloud.fail = True
        self.local.fail = True
        clips = self._request_and_wait(run)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        status = self.engine.clip_status(stored, clips[0]["clip_id"])
        self.assertEqual(status["state"], "failed")
        self.assertTrue(status["error"])


class PureGetAndIntegrityTests(AudioTestBase):
    def _seg(self, n: int) -> str:
        return hex_id("seg", n)

    def test_get_never_synthesizes(self):
        run = _run_fixture()
        clip_id = clip_id_for(run.run_id, "narration", self._seg(1))
        # 未知 clip_id → 404，不合成
        with self.assertRaises(ClassroomError) as ctx:
            self.engine.clip_status(run, clip_id)
        self.assertEqual(ctx.exception.code, "source_not_found")
        # 未就绪 clip 的 content → audio_busy，不合成
        pending_key = "f" * 64
        store.write_json(store.audio_meta_path(
            OWNER, WS, LESSON, pending_key),
            {"clip_id": clip_id, "state": "pending", "provider": "azure"})
        run.audio_refs[clip_id] = pending_key
        store.save_run(run)
        with self.assertRaises(ClassroomError) as ctx2:
            self.engine.clip_content(run, clip_id)
        self.assertEqual(ctx2.exception.code, "audio_busy")
        self.assertEqual(self.cloud.calls, 0)
        self.assertEqual(self.local.calls, 0)

    def test_clip_binding_via_run_refs_only(self):
        # 拿同 owner 另一 run 的 clip_id 不能读取（run 绑定校验）
        run1 = _run_fixture()
        self._request_and_wait(run1)
        clip_id = clip_id_for(run1.run_id, "narration", self._seg(1))
        run2 = _run_fixture()
        with self.assertRaises(ClassroomError):
            self.engine.clip_content(run2, clip_id)

    def test_meta_ready_without_wav_treated_missing(self):
        run = _run_fixture()
        self._request_and_wait(run)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        clip_id = clip_id_for(run.run_id, "narration", self._seg(1))
        key = stored.audio_refs[clip_id]
        wav_path = store.audio_file_path(OWNER, WS, LESSON, key)
        wav_path.unlink()
        with self.assertRaises(ClassroomError):
            self.engine.clip_status(stored, clip_id)

    def test_tampered_content_detected(self):
        run = _run_fixture()
        self._request_and_wait(run)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        clip_id = clip_id_for(run.run_id, "narration", self._seg(1))
        key = stored.audio_refs[clip_id]
        store.audio_file_path(OWNER, WS, LESSON, key).write_bytes(b"RIFFxxxx")
        with self.assertRaises(ClassroomError) as ctx:
            self.engine.clip_content(stored, clip_id)
        self.assertEqual(ctx.exception.code, "damaged")


class WindowValidationTests(AudioTestBase):
    def _seg(self, n: int) -> str:
        return hex_id("seg", n)

    def _run(self, cursor: int = 1,
             state: sc.CheckpointRunState | None = None):
        return _run_fixture(cursor_seg=cursor, checkpoint_state=state)

    def test_valid_current_plus_two(self):
        run = self._run(1)
        validate_request_window(
            self.spec, run, [self._seg(1), self._seg(2), self._seg(3)])

    def test_discontiguous_rejected(self):
        run = self._run(1)
        with self.assertRaises(ClassroomError):
            validate_request_window(self.spec, run,
                                    [self._seg(1), self._seg(3)])

    def test_too_many_rejected(self):
        run = self._run(1)
        with mock.patch.object(limits, "AUDIO_REQUEST_SEGMENTS_MAX", 2):
            with self.assertRaises(ClassroomError):
                validate_request_window(
                    self.spec, run, [self._seg(1), self._seg(2), self._seg(3)])

    def test_before_cursor_rejected(self):
        run = self._run(3)
        with self.assertRaises(ClassroomError):
            validate_request_window(self.spec, run, [self._seg(1)])

    def test_beyond_prefetch_window_rejected(self):
        run = self._run(1)
        with self.assertRaises(ClassroomError):
            validate_request_window(
                self.spec, run, [self._seg(4)])   # 距游标 3 段

    def test_cross_two_slides_rejected(self):
        run = self._run(2)   # 第 1 页第 2 段
        # seg2(页1) seg3(页2) seg5(页3)：跨两页边界
        with self.assertRaises(ClassroomError):
            validate_request_window(
                self.spec, run, [self._seg(2), self._seg(3), self._seg(5)])

    def test_unresolved_checkpoint_blocks_prefetch(self):
        run = self._run(3)   # 游标在第 2 页（含未解决 checkpoint）
        with self.assertRaises(ClassroomError):
            validate_request_window(
                self.spec, run, [self._seg(3), self._seg(4), self._seg(5)])

    def test_resolved_checkpoint_allows_prefetch(self):
        run = self._run(3, state=sc.CheckpointRunState.answered)
        validate_request_window(
            self.spec, run, [self._seg(3), self._seg(4), self._seg(5)])

    def test_unknown_segment_rejected_without_synth(self):
        run = self._run(1)
        with self.assertRaises(ClassroomError):
            asyncio.run(self.engine.request_narration_clips(
                run, self.spec, _profile(), ["seg_ffffffffffffffffffffffff"]))
        self.assertEqual(self.cloud.calls, 0)

    def test_build_segment_order(self):
        slots = build_segment_order(self.spec)
        self.assertEqual([s.segment_id for s in slots],
                         [hex_id("seg", i) for i in range(1, 7)])
        self.assertEqual([s.slide_id for s in slots],
                         [slide_hex(1)] * 2 + [slide_hex(2)] * 2
                         + [slide_hex(3)] * 2)


class CancelAndLruTests(AudioTestBase):
    def _seg(self, n: int) -> str:
        return hex_id("seg", n)

    def test_cancel_pending_keeps_inflight_result_cached(self):
        run = _run_fixture()
        self.cloud.slow = asyncio.Event()

        async def scenario():
            first = asyncio.create_task(self.engine.request_narration_clips(
                run, self.spec, _profile(), [self._seg(1)]))
            await asyncio.sleep(0.01)   # seg1 进入在途
            second = asyncio.create_task(self.engine.request_narration_clips(
                run, self.spec, _profile(), [self._seg(2)]))
            await asyncio.sleep(0.01)   # seg2 排队
            removed = self.engine.cancel_pending(run.run_id)
            self.assertEqual(removed, 1)
            self.cloud.slow.set()      # 在途完成（结果仍缓存）
            await asyncio.gather(first, second)
        asyncio.run(scenario())
        self.assertEqual(self.cloud.calls, 1)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        status = self.engine.clip_status(
            stored, clip_id_for(run.run_id, "narration", self._seg(1)))
        self.assertEqual(status["state"], "ready")

    def test_owner_lru_evicts_oldest(self):
        run = _run_fixture()
        with mock.patch.object(limits, "AUDIO_OWNER_MB", 0):
            self._request_and_wait(run)
            stored = store.load_run(OWNER, WS, LESSON, run.run_id)
            key1 = stored.audio_refs[
                clip_id_for(run.run_id, "narration", self._seg(1))]
            self.assertTrue(store.audio_file_path(
                OWNER, WS, LESSON, key1).exists())
            import time as _time
            _time.sleep(0.02)
            self._request_and_wait(stored, segs=[self._seg(2)])
            # 预算 0：写入第二段后最旧的第一段被淘汰
            self.assertFalse(store.audio_file_path(
                OWNER, WS, LESSON, key1).exists())
            self.assertFalse(store.audio_meta_path(
                OWNER, WS, LESSON, key1).exists())

    def test_sweep_expired_audio(self):
        import os
        import time as _time
        run = _run_fixture()
        self._request_and_wait(run)
        stored = store.load_run(OWNER, WS, LESSON, run.run_id)
        key1 = stored.audio_refs[
            clip_id_for(run.run_id, "narration", self._seg(1))]
        old = _time.time() - 8 * 86400
        for p in (store.audio_file_path(OWNER, WS, LESSON, key1),
                  store.audio_meta_path(OWNER, WS, LESSON, key1)):
            os.utime(p, (old, old))
        removed = ca.sweep_expired_audio(OWNER)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(store.audio_file_path(OWNER, WS, LESSON,
                                               key1).exists())


class VoicePreviewTests(AudioTestBase):
    def test_preview_roundtrip_and_cache(self):
        from app.schemas.classroom import VoicePreferences
        prefs = VoicePreferences()
        first = asyncio.run(self.engine.voice_preview(OWNER, "zh", prefs))
        self.assertTrue(first["clip_id"].startswith("vp_"))
        self.assertEqual(first["provider"], "azure")
        second = asyncio.run(self.engine.voice_preview(OWNER, "zh", prefs))
        self.assertEqual(first["clip_id"], second["clip_id"])
        self.assertEqual(self.cloud.calls, 1)   # 命中 owner 级缓存
        data = self.engine.voice_preview_content(OWNER, first["clip_id"])
        self.assertTrue(data.startswith(b"RIFF"))

    def test_preview_owner_isolation(self):
        from app.schemas.classroom import VoicePreferences
        first = asyncio.run(self.engine.voice_preview(
            OWNER, "zh", VoicePreferences()))
        with self.assertRaises(ClassroomError):
            self.engine.voice_preview_content("usr_other", first["clip_id"])

    def test_preview_falls_back_local(self):
        from app.schemas.classroom import VoicePreferences
        self.cloud.fail = True
        result = asyncio.run(self.engine.voice_preview(
            OWNER, "zh", VoicePreferences()))
        self.assertEqual(result["provider"], "melo")
        self.assertEqual(self.local.calls, 1)

    def test_preview_no_voice_unavailable(self):
        from app.schemas.classroom import VoicePreferences
        # 英文 + 仅本地（melo 不支持英文）→ voice_unavailable，不伪装
        with mock.patch.object(tts_service, "_azure_available",
                               return_value=False), \
             mock.patch.object(tts_service, "local_tts_enabled",
                               return_value=True):
            with self.assertRaises(ClassroomError) as ctx:
                asyncio.run(self.engine.voice_preview(
                    OWNER, "en", VoicePreferences()))
        self.assertEqual(ctx.exception.code, "voice_unavailable")
        self.assertEqual(self.cloud.calls, 0)


class WavHelperTests(unittest.TestCase):
    def test_pcm16_to_wav_roundtrip(self):
        pcm = _pcm(0.05)
        wav = pcm16_to_wav(pcm, 24000)
        self.assertTrue(wav.startswith(b"RIFF"))
        with wave.open(BytesIO(wav)) as w:
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.readframes(w.getnframes()), pcm)


if __name__ == "__main__":
    unittest.main()
