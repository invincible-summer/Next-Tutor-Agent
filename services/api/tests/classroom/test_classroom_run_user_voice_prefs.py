"""课堂 run 创建的个人默认语音偏好回落。

有效选择顺序：run 显式 > 课程 brief 显式 > 个人 prefs.classroom > 系统默认。
brief 的 voice_preferences 全默认（创建弹窗未触碰语音区）视为未配置；
playback_speed 不属于个人偏好层，始终沿用 brief 值。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.classroom import runs as runs_mod  # noqa: E402
from app.classroom.pipeline import ClassroomPipeline  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.classroom.test_classroom_pipeline import OWNER, WS  # noqa: E402
from tests.classroom.test_classroom_revisions import RevisionTestBase, _deps  # noqa: E402

_CLOUD_VOICE = "zh-CN-XiaoxiaoNeural"


class RunUserVoicePrefsTests(RevisionTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for p in [
            mock.patch.object(settings, "azure_speech_key", "k"),
            mock.patch.object(settings, "azure_speech_region", "eastasia"),
            mock.patch.object(settings, "classroom_tts_voice_zh",
                              _CLOUD_VOICE),
        ]:
            p.start()
            self.addCleanup(p.stop)
        # OWNER 注册为真实账号（沙箱 accounts.json），get_by_id 才能命中
        from app.identity import store as id_store
        id_store.create_user(email="voice-owner@example.com", username="",
                             password_hash="x", user_id=OWNER)
        self.lesson_id, _job, self.spec = self._published_course()

    def _set_prefs(self, classroom: dict) -> None:
        from app.identity import store as id_store
        id_store.update_profile_fields(OWNER, {}, {"classroom": classroom})

    def _create(self, request: sc.CreateRunRequest | None = None,
                *, key: str, lesson_id: str | None = None) -> sc.ClassroomRun:
        payload = runs_mod.create_run(
            OWNER, WS, lesson_id or self.lesson_id,
            request or sc.CreateRunRequest(mode=sc.RunStartMode.restart),
            idempotency_key=key)
        run = store.load_run(OWNER, WS, lesson_id or self.lesson_id,
                             payload["run_id"])
        assert run is not None
        return run

    def test_user_prefs_applied_when_no_explicit(self):
        """(a) 无 run/brief 显式配置 → 个人 prefs.classroom 生效。"""
        self._set_prefs({"voice_policy": "cloud", "voice_id": _CLOUD_VOICE,
                         "allow_local_fallback": False})
        run = self._create(key="k-voice-prefs-0001")
        self.assertEqual(run.audio_profile.policy, sc.VoicePolicy.cloud)
        self.assertEqual(run.audio_profile.provider, "azure")
        self.assertEqual(run.audio_profile.voice_id, _CLOUD_VOICE)
        self.assertFalse(run.audio_profile.allow_local_fallback)
        # 静音偏好 → 文字课堂（不伪装本地语音成功）
        self._set_prefs({"voice_policy": "silent"})
        run2 = self._create(key="k-voice-prefs-0002")
        self.assertEqual(run2.audio_profile.policy, sc.VoicePolicy.silent)
        self.assertEqual(run2.audio_profile.provider, "")

    def test_explicit_request_beats_user_prefs(self):
        """(b) run 显式 voice_preferences 优先于个人偏好。"""
        self._set_prefs({"voice_policy": "silent"})
        request = sc.CreateRunRequest(
            mode=sc.RunStartMode.restart,
            voice_preferences=sc.VoicePreferences(
                policy=sc.VoicePolicy.cloud, voice_id=_CLOUD_VOICE))
        run = self._create(request, key="k-voice-req-000001")
        self.assertEqual(run.audio_profile.policy, sc.VoicePolicy.cloud)
        self.assertEqual(run.audio_profile.provider, "azure")
        self.assertEqual(run.audio_profile.voice_id, _CLOUD_VOICE)

    def test_explicit_brief_beats_user_prefs(self):
        """brief 显式语音配置优先于个人偏好（创建弹窗配置过语音区）。"""
        self._set_prefs({"voice_policy": "silent"})
        brief = self._brief(voice_preferences=sc.VoicePreferences(
            policy=sc.VoicePolicy.cloud, voice_id=_CLOUD_VOICE))
        lesson_id, job_id = self._make_job(brief)
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        run = self._create(key="k-voice-brief-0001", lesson_id=lesson_id)
        self.assertEqual(run.audio_profile.policy, sc.VoicePolicy.cloud)
        self.assertEqual(run.audio_profile.provider, "azure")
        self.assertEqual(run.audio_profile.voice_id, _CLOUD_VOICE)

    def test_no_user_prefs_keeps_default(self):
        """(c) 无个人偏好（或无账号数据）→ 行为与现状一致：auto → 实例默认。"""
        run = self._create(key="k-voice-default-01")
        self.assertEqual(run.audio_profile.policy, sc.VoicePolicy.auto)
        self.assertEqual(run.audio_profile.provider, "azure")
        self.assertEqual(run.audio_profile.voice_id, _CLOUD_VOICE)
        self.assertTrue(run.audio_profile.allow_local_fallback)
        # 账号只有非语音 classroom 键（如 captions）同样不触发偏好层
        self._set_prefs({"captions": True})
        run2 = self._create(key="k-voice-default-02")
        self.assertEqual(run2.audio_profile.policy, sc.VoicePolicy.auto)
        self.assertEqual(run2.audio_profile.provider, "azure")

    def test_invalid_stored_prefs_fall_back_per_field(self):
        """历史脏数据：非法 policy 忽略该字段，合法 voice_id 仍采纳。"""
        self._set_prefs({"voice_policy": "loud", "voice_id": _CLOUD_VOICE})
        run = self._create(key="k-voice-dirty-0001")
        self.assertEqual(run.audio_profile.policy, sc.VoicePolicy.auto)
        self.assertEqual(run.audio_profile.provider, "azure")
        self.assertEqual(run.audio_profile.voice_id, _CLOUD_VOICE)


if __name__ == "__main__":
    unittest.main()
