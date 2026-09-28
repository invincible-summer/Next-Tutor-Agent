"""Azure TTS 适配器与统一 TTS service 回归（plan.md §11、§19.2 阶段 F）。

覆盖：SSML 转义、请求头/输出格式、voices list 投影、中英文音色、
401/429/5xx/超时分类、解析失败不伪成功、无 key 零请求；service 层的
课堂 profile 解析（allowlist/策略链）与共享 semaphore（云 2/Melo 1）。
"""
from __future__ import annotations

import asyncio
import io
import struct
import unittest
import wave
from unittest.mock import patch

import httpx

from app.voice.base import (TTSConfigError, TTSOptions, TTSRateLimited,
                            TTSTransient, TTSUnavailable)
from app.voice.tts import azure as azure_mod
from app.voice.tts.azure import AzureTTS, build_ssml


def _wav_bytes(sample_rate: int = 24000, seconds: float = 0.05) -> bytes:
    buf = io.BytesIO()
    count = int(sample_rate * seconds)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 3000) * count)
    return buf.getvalue()


def _run(coro):
    return asyncio.run(coro)


class _TransportRecorder:
    """MockTransport：记录请求并按脚本逐条回放响应。"""

    def __init__(self, script):
        self.script = list(script)   # 每项: response | exception
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else httpx.Response(500)
        if isinstance(step, Exception):
            raise step
        return step

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class TestBuildSSML(unittest.TestCase):
    def test_text_escaped(self):
        ssml = build_ssml('1 < 2 & "x" > 0，单引号\'', voice_id="zh-CN-XiaoxiaoNeural",
                          language="zh-CN")
        self.assertIn("&lt; 2 &amp; &quot;x&quot; &gt; 0", ssml)
        self.assertIn("&apos;", ssml)
        # 原始文本片段不得以未转义形式出现
        self.assertNotIn("< 2", ssml)

    def test_ssml_injection_never_passes_through(self):
        text = '</voice><break time="10s"/><voice name="evil">'
        ssml = build_ssml(text, voice_id="v", language="zh-CN")
        # 攻击者的标签全部被转义，不能提前闭合受控结构
        self.assertNotIn("</voice><break", ssml)
        self.assertIn("&lt;/voice&gt;", ssml)

    def test_speed_mapping(self):
        base = build_ssml("hi", voice_id="v", language="zh-CN",
                          synthesis_speed=1.0)
        self.assertNotIn("prosody", base)
        slower = build_ssml("hi", voice_id="v", language="zh-CN",
                            synthesis_speed=0.9)
        self.assertIn('rate="-10%"', slower)
        faster = build_ssml("hi", voice_id="v", language="zh-CN",
                            synthesis_speed=1.5)
        self.assertIn('rate="+50%"', faster)

    def test_speed_clamped_to_capability_range(self):
        too_fast = build_ssml("hi", voice_id="v", language="zh-CN",
                              synthesis_speed=3.0)
        self.assertIn('rate="+100%"', too_fast)
        too_slow = build_ssml("hi", voice_id="v", language="zh-CN",
                              synthesis_speed=0.1)
        self.assertIn('rate="-50%"', too_slow)


class TestAzureSynthesize(unittest.TestCase):
    KEY = "test-key"
    REGION = "eastasia"

    def _tts(self, recorder, **kw) -> AzureTTS:
        return AzureTTS(key=self.KEY, region=self.REGION,
                        transport=recorder.transport(), **kw)

    def _ok(self) -> httpx.Response:
        return httpx.Response(200, content=_wav_bytes())

    def test_headers_format_and_url(self):
        rec = _TransportRecorder([self._ok()])
        tts = self._tts(rec)
        result = _run(tts.synthesize(
            "动量守恒", options=TTSOptions(voice_id="zh-CN-XiaoxiaoNeural",
                                          language="zh-CN")))
        self.assertEqual(len(rec.requests), 1)
        req = rec.requests[0]
        self.assertEqual(
            req.url, "https://eastasia.tts.speech.microsoft.com"
                     "/cognitiveservices/v1")
        self.assertEqual(req.headers["Ocp-Apim-Subscription-Key"], self.KEY)
        self.assertEqual(req.headers["Content-Type"], "application/ssml+xml")
        self.assertEqual(req.headers["X-Microsoft-OutputFormat"],
                         "riff-24khz-16bit-mono-pcm")
        self.assertTrue(req.headers.get("User-Agent"))
        body = req.content.decode("utf-8")
        self.assertIn("动量守恒", body)
        self.assertIn('name="zh-CN-XiaoxiaoNeural"', body)
        self.assertEqual(result.provider, "azure")
        self.assertEqual(result.voice_id, "zh-CN-XiaoxiaoNeural")
        self.assertEqual(result.sample_rate, 24000)
        self.assertEqual(len(result.pcm16) % 2, 0)
        self.assertGreater(len(result.pcm16), 0)

    def test_old_speed_kwarg_still_maps(self):
        rec = _TransportRecorder([self._ok()])
        tts = self._tts(rec)
        _run(tts.synthesize("hi", speed=1.2))
        self.assertIn('rate="+20%"', rec.requests[0].content.decode())

    def test_custom_endpoint_must_be_official_https(self):
        for bad in ("http://evil.example.com",
                    "https://evil.example.com",
                    "https://sub.api.cognitiveservices.azure.com.evil.com"):
            rec = _TransportRecorder([])
            tts = AzureTTS(key=self.KEY, region=self.REGION, endpoint=bad,
                           transport=rec.transport())
            with self.assertRaises(TTSConfigError):
                _run(tts.synthesize("hi"))
            self.assertEqual(len(rec.requests), 0)   # 校验在前，零请求

    def test_custom_endpoint_approved_domain_used(self):
        rec = _TransportRecorder([self._ok()])
        tts = AzureTTS(key=self.KEY, region=self.REGION,
                       endpoint="https://myres.api.cognitiveservices.azure.com",
                       transport=rec.transport())
        _run(tts.synthesize("hi"))
        self.assertIn("myres.api.cognitiveservices.azure.com",
                      str(rec.requests[0].url))
        self.assertNotIn("tts.speech.microsoft.com", str(rec.requests[0].url))

    def test_bad_region_rejected_without_request(self):
        rec = _TransportRecorder([])
        tts = AzureTTS(key=self.KEY, region="bad region/../x",
                       transport=rec.transport())
        with self.assertRaises(TTSConfigError):
            _run(tts.synthesize("hi"))
        self.assertEqual(len(rec.requests), 0)

    def test_invalid_voice_id_rejected(self):
        rec = _TransportRecorder([])
        tts = self._tts(rec)
        with self.assertRaises(TTSUnavailable):
            _run(tts.synthesize("hi", options=TTSOptions(
                voice_id="https://evil.example/tts")))
        self.assertEqual(len(rec.requests), 0)

    def test_401_config_error_no_retry(self):
        rec = _TransportRecorder([httpx.Response(401, text="denied")])
        tts = self._tts(rec)
        with self.assertRaises(TTSConfigError):
            _run(tts.synthesize("hi"))
        self.assertEqual(len(rec.requests), 1)

    def test_429_honors_retry_after_then_single_retry(self):
        rec = _TransportRecorder([
            httpx.Response(429, headers={"Retry-After": "0"}),
            self._ok(),
        ])
        tts = self._tts(rec)
        result = _run(tts.synthesize("hi"))
        self.assertEqual(result.provider, "azure")
        self.assertEqual(len(rec.requests), 2)

    def test_429_persists_as_rate_limited(self):
        rec = _TransportRecorder([
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(429, headers={"Retry-After": "0"}),
        ])
        tts = self._tts(rec)
        with self.assertRaises(TTSRateLimited):
            _run(tts.synthesize("hi"))
        self.assertEqual(len(rec.requests), 2)   # 只重试一次

    def test_5xx_retried_once(self):
        rec = _TransportRecorder([
            httpx.Response(503, text="boom"),
            self._ok(),
        ])
        tts = self._tts(rec)
        result = _run(tts.synthesize("hi"))
        self.assertEqual(result.provider, "azure")

        rec2 = _TransportRecorder([
            httpx.Response(503, text="boom"),
            httpx.Response(503, text="boom"),
        ])
        tts2 = self._tts(rec2)
        with self.assertRaises(TTSTransient):
            _run(tts2.synthesize("hi"))
        self.assertEqual(len(rec2.requests), 2)

    def test_network_timeout_retried_once(self):
        rec = _TransportRecorder([
            httpx.ConnectTimeout("t"),
            self._ok(),
        ])
        tts = self._tts(rec)
        result = _run(tts.synthesize("hi"))
        self.assertEqual(result.provider, "azure")

        rec2 = _TransportRecorder([
            httpx.ConnectTimeout("t"), httpx.ConnectTimeout("t")])
        tts2 = self._tts(rec2)
        with self.assertRaises(TTSTransient):
            _run(tts2.synthesize("hi"))
        self.assertEqual(len(rec2.requests), 2)

    def test_non_wav_body_never_fakes_success(self):
        rec = _TransportRecorder([httpx.Response(200, content=b"NOT-WAV")])
        tts = self._tts(rec)
        with self.assertRaises(TTSUnavailable):
            _run(tts.synthesize("hi"))

    def test_corrupt_wav_raises(self):
        rec = _TransportRecorder([httpx.Response(200, content=b"RIFFxxxx")])
        tts = self._tts(rec)
        with self.assertRaises(TTSUnavailable):
            _run(tts.synthesize("hi"))

    def test_no_key_zero_requests(self):
        rec = _TransportRecorder([])
        tts = AzureTTS(key="", region=self.REGION, transport=rec.transport())
        with self.assertRaises(TTSConfigError):
            _run(tts.synthesize("hi"))
        with self.assertRaises(TTSConfigError):
            _run(tts.list_voices())
        self.assertFalse(tts.configured())
        self.assertEqual(len(rec.requests), 0)


class TestAzureVoicesList(unittest.TestCase):
    VOICES = [
        {"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN",
         "DisplayName": "Xiaoxiao", "VoiceTag": {"PrivateInternal": "x"}},
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US",
         "DisplayName": "Jenny"},
        {"ShortName": "", "Locale": "zh-CN"},        # 空名跳过
        "garbage",                                    # 非对象跳过
    ]

    def test_projection_only_public_fields(self):
        rec = _TransportRecorder([httpx.Response(200, json=self.VOICES)])
        tts = AzureTTS(key="k", region="eastasia", transport=rec.transport())
        voices = _run(tts.list_voices())
        self.assertEqual(
            str(rec.requests[0].url),
            "https://eastasia.tts.speech.microsoft.com"
            "/cognitiveservices/voices/list")
        self.assertEqual(len(voices), 2)
        self.assertEqual(voices[0].voice_id, "zh-CN-XiaoxiaoNeural")
        self.assertEqual(voices[0].locale, "zh-CN")
        self.assertEqual(voices[0].display_name, "Xiaoxiao")
        # 投影体不含供应商内部字段
        self.assertNotIn("VoiceTag", voices[0].public())

    def test_voices_401_is_config_error(self):
        rec = _TransportRecorder([httpx.Response(401)])
        tts = AzureTTS(key="k", region="eastasia", transport=rec.transport())
        with self.assertRaises(TTSConfigError):
            _run(tts.list_voices())

    def test_voices_non_json_is_transient(self):
        rec = _TransportRecorder([
            httpx.Response(200, content=b"not json", )])
        tts = AzureTTS(key="k", region="eastasia", transport=rec.transport())
        with self.assertRaises(TTSTransient):
            _run(tts.list_voices())

    def test_voices_bad_shape_is_transient(self):
        rec = _TransportRecorder([httpx.Response(200, json={"a": 1})])
        tts = AzureTTS(key="k", region="eastasia", transport=rec.transport())
        with self.assertRaises(TTSTransient):
            _run(tts.list_voices())


class TestClassroomProfileResolution(unittest.TestCase):
    """service.resolve_classroom_tts：策略链 + 音色 allowlist（§11.1/§11.3）。"""

    def setUp(self):
        from app.voice.tts import service as tts_service
        self.service = tts_service
        tts_service.reset_tts_service()
        from app.core.config import settings
        self.patches = [
            patch.object(settings, "azure_speech_key", "k"),
            patch.object(settings, "azure_speech_region", "eastasia"),
            patch.object(settings, "classroom_tts_policy", "auto"),
            patch.object(settings, "classroom_tts_voice_zh",
                         "zh-CN-XiaoxiaoNeural"),
            patch.object(settings, "classroom_tts_voice_en",
                         "en-US-JennyNeural"),
            patch.object(settings, "classroom_local_tts_enabled", None),
            patch.object(settings, "voice_tts_provider", "off"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.addCleanup(tts_service.reset_tts_service)

    def prefs(self, policy="auto", voice_id="", allow_local=True):
        from app.schemas.classroom import VoicePreferences
        return VoicePreferences(policy=policy, voice_id=voice_id,
                                allow_local_fallback=allow_local)

    def test_auto_prefers_cloud(self):
        profile = self.service.resolve_classroom_tts(self.prefs(), "zh")
        self.assertEqual(profile.provider, "azure")
        self.assertEqual(profile.voice_id, "zh-CN-XiaoxiaoNeural")
        self.assertEqual(profile.language, "zh-CN")
        self.assertEqual(profile.synthesis_speed, 1.0)

    def test_english_lesson_gets_english_voice(self):
        profile = self.service.resolve_classroom_tts(self.prefs(), "en")
        self.assertEqual(profile.voice_id, "en-US-JennyNeural")
        self.assertEqual(profile.language, "en-US")

    def test_explicit_voice_only_if_approved_and_language_matches(self):
        # 批准且同语言 → 采纳
        p = self.service.resolve_classroom_tts(
            self.prefs(voice_id="en-US-JennyNeural"), "en")
        self.assertEqual(p.voice_id, "en-US-JennyNeural")
        # 批准但语言不匹配（禁止中文音色读英文整页）→ 回落语言默认
        p2 = self.service.resolve_classroom_tts(
            self.prefs(voice_id="zh-CN-XiaoxiaoNeural"), "en")
        self.assertEqual(p2.voice_id, "en-US-JennyNeural")
        # 任意未批准值/URL → 回落默认，不进合成参数
        p3 = self.service.resolve_classroom_tts(
            self.prefs(voice_id="https://evil.example/v"), "zh")
        self.assertEqual(p3.voice_id, "zh-CN-XiaoxiaoNeural")

    def test_cloud_policy_local_fallback_gate(self):
        # 云端未配置 + 允许回退 + 本地启用 → 本地
        with patch.object(self.service, "_azure_available", return_value=False), \
             patch.object(self.service, "local_tts_enabled", return_value=True):
            p = self.service.resolve_classroom_tts(
                self.prefs(policy="cloud", allow_local=True), "zh")
            self.assertEqual(p.provider, "melo")
            # 不允许回退 → silent，不伪装
            p2 = self.service.resolve_classroom_tts(
                self.prefs(policy="cloud", allow_local=False), "zh")
            self.assertEqual(p2.provider, "")

    def test_auto_falls_back_to_local_then_silent(self):
        with patch.object(self.service, "_azure_available", return_value=False), \
             patch.object(self.service, "local_tts_enabled", return_value=True):
            p = self.service.resolve_classroom_tts(self.prefs(), "zh")
            self.assertEqual(p.provider, "melo")
        with patch.object(self.service, "_azure_available", return_value=False), \
             patch.object(self.service, "local_tts_enabled", return_value=False):
            p2 = self.service.resolve_classroom_tts(self.prefs(), "zh")
            self.assertEqual(p2.provider, "")

    def test_local_policy_english_falls_silent(self):
        with patch.object(self.service, "_azure_available", return_value=False), \
             patch.object(self.service, "local_tts_enabled", return_value=True):
            p = self.service.resolve_classroom_tts(self.prefs(policy="local"),
                                                   "en")
            self.assertEqual(p.provider, "")   # 文字课堂，不伪本地语音
            p2 = self.service.resolve_classroom_tts(self.prefs(policy="local"),
                                                    "zh")
            self.assertEqual(p2.provider, "melo")
            self.assertEqual(p2.voice_id, "melo-zh")

    def test_nothing_available_is_silent(self):
        with patch.object(self.service, "_azure_available", return_value=False), \
             patch.object(self.service, "local_tts_enabled", return_value=False):
            p = self.service.resolve_classroom_tts(self.prefs(), "zh")
            self.assertEqual(p.provider, "")

    def test_voices_cache_blocks_unsupported_default(self):
        # 缓存佐证资源只支持另一批准音色 → 选它
        with patch.object(self.service, "_cached_voice_ids",
                          return_value=frozenset({"zh-CN-YunxiNeural"})), \
             patch.object(self.service, "approved_voices",
                          return_value={"zh-CN-XiaoxiaoNeural": "zh-CN",
                                        "zh-CN-YunxiNeural": "zh-CN"}):
            p = self.service.resolve_classroom_tts(self.prefs(), "zh")
            self.assertEqual(p.voice_id, "zh-CN-YunxiNeural")
        # 该语言一个都不支持 → 空 voice（合成时报 voice_unavailable）
        with patch.object(self.service, "_cached_voice_ids",
                          return_value=frozenset({"en-US-AriaNeural"})):
            p2 = self.service.resolve_classroom_tts(self.prefs(), "zh")
            self.assertEqual(p2.voice_id, "")


class TestSharedSemaphores(unittest.TestCase):
    """云 2 / Melo 全局 1，跨电话与课堂同一把闸（§11.4/F02）。"""

    def setUp(self):
        from app.voice.tts import service as tts_service
        self.service = tts_service
        tts_service.reset_tts_service()
        self.addCleanup(tts_service.reset_tts_service)

    def _fake_provider(self, tracker):
        from app.voice.base import TTSProvider, TTSResult

        class Fake(TTSProvider):
            name = "fake"
            current = 0
            peak = 0

            async def synthesize(self, text, *, speed=None, options=None):
                type(self).current += 1
                type(self).peak = max(type(self).peak, type(self).current)
                tracker.append(type(self).peak)
                await asyncio.sleep(0.02)
                type(self).current -= 1
                return TTSResult(pcm16=b"\x00\x00", sample_rate=24000,
                                 provider=self.name)
        return Fake

    def test_melo_guard_serializes(self):
        peaks = []
        fake_cls = self._fake_provider(peaks)
        from app.voice.tts.service import _melo_guard
        guard = _melo_guard(fake_cls())

        async def scenario():
            await asyncio.gather(*[guard.synthesize(f"t{i}") for i in range(6)])
        asyncio.run(scenario())
        self.assertLessEqual(max(peaks), 1)

    def test_cloud_guard_allows_configured_concurrency(self):
        peaks = []
        fake_cls = self._fake_provider(peaks)
        from app.voice.tts.service import _cloud_guard
        guard = _cloud_guard(fake_cls())
        from app.core.config import settings
        limit = settings.classroom_tts_cloud_concurrency

        async def scenario():
            await asyncio.gather(*[guard.synthesize(f"t{i}")
                                   for i in range(limit * 3)])
        asyncio.run(scenario())
        self.assertLessEqual(max(peaks), limit)

    def test_semapores_shared_across_loops_reset(self):
        async def grab():
            cloud, melo = self.service.shared_semaphores()
            return id(cloud), id(melo)
        first = asyncio.run(grab())
        second = asyncio.run(grab())
        # 新循环拿到新实例（旧循环 semaphore 不得复用）
        self.assertNotEqual(first, second)


class TestPhoneFactory(unittest.TestCase):
    """旧电话 factory 无参兼容 + azure/auto 新语义。"""

    def setUp(self):
        from app.voice.tts import service as tts_service
        tts_service.reset_tts_service()
        from app.core.config import settings
        self.settings = settings
        self._restore = (settings.voice_tts_provider,
                         settings.azure_speech_key,
                         settings.azure_speech_region)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        from app.voice.tts import service as tts_service
        (self.settings.voice_tts_provider, self.settings.azure_speech_key,
         self.settings.azure_speech_region) = self._restore
        tts_service.reset_tts_service()

    def _set(self, provider, key="", region=""):
        self.settings.voice_tts_provider = provider
        self.settings.azure_speech_key = key
        self.settings.azure_speech_region = region

    def test_off_and_unknown_return_none(self):
        from app.voice.tts import get_tts_provider
        self._set("off")
        self.assertIsNone(get_tts_provider())
        self._set("bogus")
        self.assertIsNone(get_tts_provider())

    def test_stub_and_melo_preserved(self):
        from app.voice.tts import get_tts_provider
        self._set("stub")
        stub = get_tts_provider()
        self.assertEqual(stub.name, "stub")
        self._set("melo")
        melo = get_tts_provider()
        self.assertEqual(melo.name, "melo")

    def test_azure_requires_credentials(self):
        from app.voice.tts import get_tts_provider
        self._set("azure", key="", region="")
        self.assertIsNone(get_tts_provider())
        self._set("azure", key="k", region="eastasia")
        self.assertEqual(get_tts_provider().name, "azure")

    def test_auto_prefers_cloud_then_local(self):
        from app.voice.tts import get_tts_provider
        self._set("auto", key="k", region="eastasia")
        self.assertEqual(get_tts_provider().name, "azure")
        self._set("auto", key="", region="")
        self.assertEqual(get_tts_provider().name, "melo")

    def test_old_speed_call_shape_still_works(self):
        from app.voice.tts import get_tts_provider
        self._set("stub")
        result = _run(get_tts_provider().synthesize("hi", speed=0.9))
        self.assertEqual(result.provider, "stub")


if __name__ == "__main__":
    unittest.main()
