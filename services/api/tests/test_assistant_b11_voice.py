"""B11 助手语音回归：文本规则、分段、任务与片段生命周期。

覆盖：可朗读文本提取（表格/公式/URL 降级）、句边界分段与上限、
create_job 去重/限流/静音拒绝、run_job（stub provider→wav 片段）、
取消语义、片段鉴权读取与原文修订失效、resolve_tts_profile 策略边界
（local+英文→文字）、偏好 §24.6 默认值与版本合并。
"""
from __future__ import annotations

import unittest
import uuid
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import voice
from app.core import assistant_store as store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


def _message(text: str, revision: int = 3) -> dict:
    return {
        "message_id": "msg_b11_" + _hex(),
        "role": "assistant",
        "revision": revision,
        "conversation_id": "astc_b11",
        "blocks": [
            {"type": "markdown", "text": text},
            {"type": "actions", "items": []},
            {"type": "sources", "items": []},
        ],
    }


class TextRulesTest(StorageSandboxTestCase):
    def test_readable_text_rules(self) -> None:
        text = ("动能定理说明外力做功与动能变化的关系。"
                "详见 https://example.com/doc 。\n\n"
                "| 量 | 值 |\n| --- | --- |\n| v | 2m/s |\n\n"
                "公式 $E_k=\\frac{1}{2}mv^2$ 给出动能。")
        out = voice.readable_text_from_message(_message(text))
        self.assertNotIn("https://", out)
        self.assertIn("详细数据请看表格", out)
        self.assertIn("公式见文字", out)
        self.assertIn("动能定理", out)

    def test_segment_boundaries_and_cap(self) -> None:
        sentence = "这是一句完整的测试句。" * 700  # ~7700 字，超上限
        clips, truncated = voice.segment_text(sentence)
        self.assertTrue(truncated)
        self.assertLessEqual(len(clips), voice.MAX_CLIPS)
        for clip in clips:
            self.assertLessEqual(len(clip), voice.CLIP_MAX_CHARS)
            self.assertTrue(clip.endswith(("。", "！", "？", "；")))


class JobLifecycleTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b11_" + _hex()
        # 本地 provider 可用：voice_tts_provider=melo（stub sidecar 不可达
        # 时 run_job 用注入的 fake；解析层只看开关）。
        from app.core.config import settings
        patcher = mock.patch.object(settings, "voice_tts_provider", "melo")
        patcher.start()
        self.addCleanup(patcher.stop)
        voice._rate_windows.clear()

    def _create(self, text: str, policy: str = "local", language: str = "zh"):
        return voice.create_job(
            self.sid, message=_message(text), policy=policy,
            voice_id="", language=language, allow_local_fallback=True,
            client_request_id="cr_" + _hex())

    def test_silent_policy_rejected(self) -> None:
        with self.assertRaises(voice.VoiceRejected) as ctx:
            self._create("你好。", policy="silent")
        self.assertEqual(ctx.exception.code, "voice_policy_silent")

    def test_local_policy_english_keeps_text(self) -> None:
        # §24.2-5：本地不支持英文 → 拒绝合成并保留文字，不送云端。
        with self.assertRaises(voice.VoiceRejected) as ctx:
            self._create("Hello world.", policy="local", language="en")
        self.assertEqual(ctx.exception.code, "voice_unavailable")

    def test_nothing_to_read(self) -> None:
        with self.assertRaises(voice.VoiceRejected) as ctx:
            self._create("   ")
        self.assertEqual(ctx.exception.code, "nothing_to_read")

    def test_rate_limit(self) -> None:
        for i in range(voice.REQUESTS_PER_MINUTE):
            job, _cached = self._create(f"第{i}条可朗读内容。" * 8)
            self.assertEqual(job["state"], "queued")
        with self.assertRaises(voice.VoiceRejected) as ctx:
            self._create("超限的一条可朗读内容。" * 8)
        self.assertEqual(ctx.exception.code, "rate_limited")

    def test_dedupe_same_text_and_profile(self) -> None:
        text = "同一段足够长的可朗读内容，用来命中内容哈希去重。" * 6
        job1, cached1 = self._create(text)
        self.assertFalse(cached1)
        voice._rate_windows.clear()
        job2, cached2 = self._create(text)
        self.assertTrue(cached2)
        self.assertEqual(job2["job_id"], job1["job_id"])

    def test_run_cancel_and_clip_content(self) -> None:
        import asyncio
        from app.voice.base import TTSOptions, TTSResult
        # 消息先落盘（任务校验消息仍存在且 revision 一致）。
        message = _message("第一句完整内容。" * 40, revision=3)
        message["conversation_id"] = "astc_b11"
        store.save_conversation(self.sid, {
            "conversation_id": "astc_b11", "revision": 1,
            "messages": [dict(message)], "turns": {}, "actions": {},
            "accepted": {}})
        job, _ = voice.create_job(
            self.sid, message=message, policy="local", voice_id="",
            language="zh", allow_local_fallback=True,
            client_request_id="cr_" + _hex())
        fake_pcm = b"" * 256

        async def fake_local(text_arg, options: TTSOptions) -> TTSResult:
            return TTSResult(pcm16=fake_pcm, sample_rate=16000,
                             provider="melo", voice_id="melo")

        with mock.patch(
                "app.voice.tts.service.local_synthesize",
                side_effect=fake_local):
            run1 = asyncio.run(voice.run_job(self.sid, job["job_id"]))
        self.assertEqual(run1["state"], "ready")
        data, media_type, _job = voice.clip_content(
            self.sid, run1["clips"][0]["clip_id"])
        self.assertTrue(len(data) > 44)   # RIFF 头存在
        self.assertEqual(media_type, "audio/wav")
        # 原文修订后失效（§24.4）。
        record = store.load_conversation(self.sid, "astc_b11")
        record["messages"][-1]["revision"] = 4
        store.save_conversation(self.sid, record)
        with self.assertRaises(voice.VoiceRejected) as ctx:
            voice.clip_content(self.sid, run1["clips"][0]["clip_id"])
        self.assertEqual(ctx.exception.code, "clip_expired")

    def test_cancel_before_run(self) -> None:
        job, _ = self._create("取消前的排队内容。" * 10)
        out = voice.cancel_job(self.sid, job["job_id"])
        self.assertEqual(out["state"], "cancelled")
        # 已取消任务不再执行。
        import asyncio
        run = asyncio.run(voice.run_job(self.sid, job["job_id"]))
        self.assertEqual(run["state"], "cancelled")
        self.assertTrue(all(c["state"] == "queued"
                            for c in run["clips"]))

    def test_path_traversal_clip_id_rejected(self) -> None:
        with self.assertRaises(voice.VoiceRejected) as ctx:
            voice.clip_content(self.sid, "../../etc/passwd")
        self.assertEqual(ctx.exception.code, "clip_not_found")


class ProfileResolutionTest(StorageSandboxTestCase):
    def test_generic_profile_and_capabilities(self) -> None:
        from app.core.config import settings
        from app.voice.tts.service import resolve_tts_profile, tts_capabilities
        with mock.patch.object(settings, "voice_tts_provider", "melo"):
            profile = resolve_tts_profile("assistant", None, "zh")
            self.assertEqual(profile.provider, "melo")
            self.assertTrue(profile.voice_id)
            caps = tts_capabilities()
            self.assertEqual(caps["policies"][0], "auto")
            self.assertIn("zh-CN", caps["local"]["languages"])
        # 云端未配置 + local 策略英文 → 文字模式（不暗中送云端）。
        with mock.patch.object(settings, "voice_tts_provider", "melo"), \
                mock.patch("app.voice.tts.service._azure_available",
                           return_value=False):
            profile = resolve_tts_profile(
                "assistant", type("P", (), {"voice_policy": "local"})(),
                "en")
            self.assertEqual(profile.provider, "")


class PreferencesModelTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b11p_" + _hex()
        from app.identity import store as id_store
        from app.identity.security import hash_password
        id_store.create_user("b11p@example.com", "B11",
                             hash_password("pw123456"), user_id=self.sid)

    def test_defaults_and_version_merge(self) -> None:
        from app.core.assistant_store import (AssistantStoreError,
                                              load_preferences,
                                              save_preferences)
        prefs = load_preferences(self.sid)
        self.assertEqual(prefs["response_length"], "standard")
        self.assertEqual(prefs["tone"], "neutral")
        self.assertEqual(prefs["voice_policy"], "auto")
        self.assertEqual(prefs["playback_rate"], 1)
        self.assertEqual(prefs["volume"], 0.8)
        self.assertFalse(prefs["auto_read"])
        # 版本合并：旧 revision 写入 → 冲突。
        merged = save_preferences(self.sid, {"auto_read": True})
        self.assertTrue(merged["auto_read"])
        self.assertEqual(merged["revision"], 2)
        with self.assertRaises(AssistantStoreError) as ctx:
            save_preferences(self.sid, {"volume": 0.5}, base_revision=1)
        self.assertEqual(ctx.exception.code, "conflict")
        # 其他 prefs 分支不被覆盖。
        from app.identity import store as id_store
        user = id_store.get_by_id(self.sid)
        self.assertIn("assistant", user.profile.prefs)
        self.assertEqual(user.profile.prefs["assistant"]["auto_read"], True)


if __name__ == "__main__":
    unittest.main()
