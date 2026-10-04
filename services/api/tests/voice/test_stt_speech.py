"""Server-side STT + speech/synthesis + capabilities aggregation (stage F).

覆盖：Azure REST STT 请求头/URL/错误分类/解析不伪成功/批准域；service
层 provider 解析（off|stub|azure|auto，auto 不伪可用）；speech 端点
multipart 服务端校验（格式/时长/大小）与 stub 端到端；合成受控参数与
WAV 输出；产品能力聚合（reason code、别名键、零凭证回显）。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from tests.support.storage_sandbox import StorageSandboxTestCase
from app.voice.base import TTSResult
from app.voice.stt import azure as azure_stt_mod
from app.voice.stt.azure import AzureSTT
from app.voice.stt.base import (STTConfigError, STTRateLimited, STTTransient,
                                STTUnavailable)


def _run(coro):
    return asyncio.run(coro)


def _success_payload(text: str = "你好世界", duration_ms: int = 1500) -> dict:
    return {"RecognitionStatus": "Success", "DisplayText": text,
            "Offset": 0, "Duration": duration_ms * 10_000}


def _recorder(script, *, key="k", region="eastasia"):
    rec = _TransportRecorder(script)
    provider = AzureSTT(key=key, region=region, transport=rec.transport())
    return rec, provider


class _TransportRecorder:
    """MockTransport：记录请求并按脚本逐条回放响应。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else httpx.Response(500)
        if isinstance(step, Exception):
            raise step
        return step

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class TestAzureSTT(unittest.TestCase):
    def test_success_roundtrip_headers_and_url(self):
        rec, provider = _recorder(
            [httpx.Response(200, json=_success_payload("老师好", 1200))])
        result = _run(provider.transcribe(b"RIFF-bytes",
                                          content_type="audio/wav",
                                          language="zh"))
        self.assertEqual(result.text, "老师好")
        self.assertEqual(result.language, "zh-CN")
        self.assertEqual(result.duration_ms, 1200)
        self.assertEqual(result.provider, "azure")
        req = rec.requests[0]
        self.assertEqual(req.headers["Ocp-Apim-Subscription-Key"], "k")
        self.assertEqual(req.headers["Content-Type"], "audio/wav")
        self.assertIn("/speech/recognition/conversation/cognitiveservices/v1",
                      str(req.url))
        self.assertIn("language=zh-CN", str(req.url))
        self.assertIn("format=simple", str(req.url))

    def test_language_normalization(self):
        rec, provider = _recorder(
            [httpx.Response(200, json=_success_payload())])
        _run(provider.transcribe(b"x", content_type="audio/mpeg",
                                 language="en-GB"))
        self.assertIn("language=en-US", str(rec.requests[0].url))
        rec2, provider2 = _recorder(
            [httpx.Response(200, json=_success_payload())])
        _run(provider2.transcribe(b"x", content_type="audio/wav"))
        self.assertIn("language=zh-CN", str(rec2.requests[0].url))

    def test_unapproved_content_type_rejected_without_request(self):
        rec, provider = _recorder([])
        with self.assertRaises(STTUnavailable):
            _run(provider.transcribe(b"x", content_type="audio/whatever"))
        self.assertEqual(rec.requests, [])   # 白名单拒绝零网络请求

    def test_config_errors_never_retry(self):
        for status in (401, 403):
            rec, provider = _recorder([httpx.Response(status)] * 2)
            with self.assertRaises(STTConfigError):
                _run(provider.transcribe(b"x", content_type="audio/wav"))
            self.assertEqual(len(rec.requests), 1)

    def test_rate_limited_single_retry_then_raise(self):
        rec, provider = _recorder([
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(429, headers={"Retry-After": "0"}),
        ])
        with self.assertRaises(STTRateLimited):
            _run(provider.transcribe(b"x", content_type="audio/wav"))
        self.assertEqual(len(rec.requests), 2)

    def test_rate_limited_retry_recovers(self):
        rec, provider = _recorder([
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=_success_payload()),
        ])
        result = _run(provider.transcribe(b"x", content_type="audio/wav"))
        self.assertEqual(result.text, "你好世界")
        self.assertEqual(len(rec.requests), 2)

    def test_transient_retry_once(self):
        rec, provider = _recorder([
            httpx.ConnectError("boom"),
            httpx.Response(200, json=_success_payload()),
        ])
        result = _run(provider.transcribe(b"x", content_type="audio/wav"))
        self.assertEqual(result.text, "你好世界")

        rec2, provider2 = _recorder(
            [httpx.Response(503)] * 2)
        with self.assertRaises(STTTransient):
            _run(provider2.transcribe(b"x", content_type="audio/wav"))
        self.assertEqual(len(rec2.requests), 2)

    def test_non_success_status_never_fakes_a_transcript(self):
        for payload in ({"RecognitionStatus": "NoMatch"},
                        {"RecognitionStatus": "Success", "DisplayText": "  "},
                        {"RecognitionStatus": "Success"},):
            rec, provider = _recorder([httpx.Response(200, json=payload)])
            with self.assertRaises(STTUnavailable):
                _run(provider.transcribe(b"x", content_type="audio/wav"))

    def test_non_json_response_rejected(self):
        rec, provider = _recorder(
            [httpx.Response(200, content=b"not-json",
                            headers={"Content-Type": "text/plain"})])
        with self.assertRaises(STTUnavailable):
            _run(provider.transcribe(b"x", content_type="audio/wav"))

    def test_endpoint_must_be_https_approved_domain(self):
        for endpoint in ("http://res.api.cognitiveservices.azure.com",
                         "https://evil.example.com"):
            provider = AzureSTT(key="k", region="eastasia",
                                endpoint=endpoint,
                                transport=httpx.MockTransport(
                                    lambda req: httpx.Response(200)))
            with self.assertRaises(STTConfigError):
                _run(provider.transcribe(b"x", content_type="audio/wav"))
        provider = AzureSTT(
            key="k", region="eastasia",
            endpoint="https://res.api.cognitiveservices.azure.com",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=_success_payload())))
        result = _run(provider.transcribe(b"x", content_type="audio/wav"))
        self.assertEqual(result.text, "你好世界")

    def test_missing_key_is_config_error(self):
        provider = AzureSTT(key="", region="eastasia")
        with self.assertRaises(STTConfigError):
            _run(provider.transcribe(b"x", content_type="audio/wav"))

    def test_bad_region_rejected(self):
        provider = AzureSTT(key="k", region="bad region!",
                            transport=httpx.MockTransport(
                                lambda req: httpx.Response(200)))
        with self.assertRaises(STTConfigError):
            _run(provider.transcribe(b"x", content_type="audio/wav"))


class TestSTTService(unittest.TestCase):
    def tearDown(self) -> None:
        from app.voice.stt import reset_stt_provider
        from app.voice.tts import reset_tts_provider
        reset_stt_provider()
        reset_tts_provider()
        super().tearDown()

    def _settings(self, **overrides):
        from app.core.config import settings
        defaults = {"speech_stt_provider": "off",
                    "azure_speech_key": "", "azure_speech_region": "",
                    "azure_speech_endpoint": ""}
        defaults.update(overrides)
        return patch.multiple(settings, **defaults)

    def test_off_by_default(self):
        from app.voice.stt import service as svc
        with self._settings():
            svc.reset_stt_service()
            self.assertIsNone(svc.stt_provider())
            self.assertEqual(svc.stt_status(), (False, "stt_disabled"))
            caps = svc.stt_capabilities()
            self.assertFalse(caps["available"])
            self.assertEqual(caps["provider"], "off")
            with self.assertRaises(STTUnavailable):
                _run(svc.transcribe(b"x", content_type="audio/wav"))

    def test_azure_without_credentials_reports_not_configured(self):
        from app.voice.stt import service as svc
        with self._settings(speech_stt_provider="azure"):
            svc.reset_stt_service()
            self.assertEqual(svc.stt_status(), (False, "stt_not_configured"))

    def test_auto_uses_azure_only_when_configured(self):
        from app.voice.stt import service as svc
        with self._settings(speech_stt_provider="auto"):
            svc.reset_stt_service()
            self.assertIsNone(svc.stt_provider())
        with self._settings(speech_stt_provider="auto",
                            azure_speech_key="k", azure_speech_region="eastasia"):
            svc.reset_stt_service()
            provider = svc.stt_provider()
            self.assertIsNotNone(provider)
            self.assertEqual(provider.name, "azure")
            self.assertEqual(svc.stt_status(), (True, ""))

    def test_stub_provider_end_to_end(self):
        from app.voice.stt import service as svc
        with self._settings(speech_stt_provider="stub"):
            svc.reset_stt_service()
            result = _run(svc.transcribe(b"abc", content_type="audio/wav",
                                         language="en"))
            self.assertEqual(result.provider, "stub")
            self.assertEqual(result.language, "en-US")
            self.assertIn("3 bytes", result.text)
            caps = svc.stt_capabilities()
            self.assertTrue(caps["available"])
            self.assertEqual(caps["provider"], "stub")
            self.assertIn("max_audio_bytes", caps["limits"])

    def test_unknown_provider_falls_back_to_off(self):
        from app.voice.stt import service as svc
        with self._settings(speech_stt_provider="gibberish"):
            svc.reset_stt_service()
            self.assertIsNone(svc.stt_provider())

    def test_provider_instance_cached_until_config_changes(self):
        from app.voice.stt import service as svc
        with self._settings(speech_stt_provider="stub"):
            svc.reset_stt_service()
            first = svc.stt_provider()
            second = svc.stt_provider()
            self.assertIs(first, second)
        with self._settings(speech_stt_provider="stub",
                            azure_speech_key="k2"):
            svc.reset_stt_service()
            self.assertIsNot(svc.stt_provider(), first)


class _SpeechApiCase(StorageSandboxTestCase):
    """speech 端点脚手架：沙盒存储 + 认证头 + stub STT 设置。"""

    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for patcher in (
            patch.object(settings, "speech_stt_provider", "stub"),
            patch.multiple(settings, azure_speech_key="",
                           azure_speech_region="",
                           azure_speech_endpoint=""),
        ):
            patcher.start()
            self._patches.append(patcher)
        self.addCleanup(self._reset_stt)

        from app.main import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())
        from app.identity.store import create_user
        from app.identity.security import create_token
        user = create_user("speech-fixture@test.local", "", "unused")
        self.auth = {"Authorization": "Bearer " + create_token(user.id)}

    @staticmethod
    def _reset_stt() -> None:
        from app.voice.stt import reset_stt_provider
        reset_stt_provider()

    def _upload(self, *, content_type="audio/wav", duration_ms=1000,
                language="zh", data=b"RIFF", filename="utterance.wav"):
        return self.client.post(
            "/api/v1/speech/transcriptions",
            headers=self.auth,
            files={"file": (filename, data, content_type)},
            data={"duration_ms": str(duration_ms), "language": language},
        )


class TestSpeechEndpoints(_SpeechApiCase):
    def test_stub_transcription_roundtrip(self):
        resp = self._upload(duration_ms=1500)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(
            body, {"text": "[stub transcript 4 bytes]", "language": "zh-CN",
                   "duration_ms": 0, "provider_class": "stub"})

    def test_unapproved_content_type_rejected_415(self):
        resp = self._upload(content_type="audio/whatever")
        self.assertEqual(resp.status_code, 415)
        self.assertEqual(resp.json()["detail"]["code"],
                         "audio_format_rejected")

    def test_duration_declaration_enforced(self):
        resp = self._upload(duration_ms=61_000)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["code"], "audio_too_long")

    def test_size_limit_enforced(self):
        from app.voice.stt import service as stt_service
        with patch.object(stt_service, "stt_limits",
                          lambda: type("L", (), {
                              "formats": ("audio/wav",),
                              "max_duration_seconds": 60,
                              "max_audio_bytes": 8})()):
            resp = self._upload(data=b"123456789")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["code"], "audio_too_large")

    def test_empty_audio_rejected(self):
        resp = self._upload(data=b"")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["code"], "audio_empty")

    def test_disabled_provider_maps_to_503(self):
        from app.core.config import settings
        with patch.object(settings, "speech_stt_provider", "off"):
            from app.voice.stt import reset_stt_provider
            reset_stt_provider()
            resp = self._upload()
            self.assertEqual(resp.status_code, 503)
            self.assertEqual(resp.json()["detail"]["code"], "stt_unavailable")

    def test_capabilities_projection(self):
        resp = self.client.get("/api/v1/speech/capabilities",
                               headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["stt"]["available"])
        self.assertEqual(body["stt"]["provider"], "stub")
        self.assertIn("formats", body["stt"])
        self.assertIn("policies", body["synthesis"])
        # 投影绝不携带凭证或 endpoint
        self.assertNotIn("key", json.dumps(body))
        self.assertNotIn("endpoint", json.dumps(body))

    def test_requires_authentication(self):
        resp = self.client.get("/api/v1/speech/capabilities")
        self.assertIn(resp.status_code, (401, 403))


class TestSpeechSynthesis(_SpeechApiCase):
    def _post(self, payload):
        return self.client.post("/api/v1/speech/synthesis",
                                headers=self.auth, json=payload)

    def test_synthesis_returns_wav(self):
        fake = TTSResult(pcm16=b"\x01\x00" * 1600, sample_rate=24000,
                         provider="azure", voice_id="zh-CN-XiaoxiaoNeural")

        async def fake_synth(text, options):
            return fake

        from app.voice.tts import service as tts_service
        profile = SimpleNamespace(provider="azure",
                                  voice_id="zh-CN-XiaoxiaoNeural",
                                  language="zh-CN")
        with patch.object(tts_service, "resolve_tts_profile",
                          lambda *a, **k: profile), \
                patch.object(tts_service, "cloud_synthesize", fake_synth):
            resp = self._post({"text": "勾股定理是对的。"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers["content-type"], "audio/wav")
        self.assertTrue(resp.content.startswith(b"RIFF"))
        self.assertEqual(resp.headers["X-Sample-Rate"], "24000")
        self.assertEqual(resp.headers["X-Voice-Id"],
                         "zh-CN-XiaoxiaoNeural")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_synthesis_unavailable_maps_to_503(self):
        from app.voice.tts import service as tts_service
        empty = SimpleNamespace(provider="", voice_id="", language="zh-CN")
        with patch.object(tts_service, "resolve_tts_profile",
                          lambda *a, **k: empty):
            resp = self._post({"text": "读不出。"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["code"], "tts_unavailable")

    def test_synthesis_rejects_out_of_range_speed(self):
        resp = self._post({"text": "x", "speed": 3.0})
        self.assertEqual(resp.status_code, 422)

    def test_synthesis_rejects_oversized_text(self):
        resp = self._post({"text": "字" * 2001})
        self.assertEqual(resp.status_code, 422)


class TestProductCapabilities(_SpeechApiCase):
    def test_aggregation_shape_and_reason_codes(self):
        from app.core.config import settings
        with patch.object(settings, "classroom_enabled", False):
            resp = self.client.get("/api/v1/capabilities",
                                   headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        for key in ("chat", "upload", "classroom", "cloud_stt", "cloud_tts",
                    "assistant", "illustration.quiz", "illustration.scenario",
                    "illustration.v3", "diagram.materials"):
            self.assertIn(key, body)
            self.assertIn("available", body[key])
        self.assertTrue(body["upload"]["available"])
        self.assertEqual(body["classroom"]["reason"], "classroom_disabled")
        # 脚手架将 SPEECH_STT_PROVIDER 置 stub：聚合如实反映为可用。
        self.assertTrue(body["cloud_stt"]["available"])
        self.assertEqual(body["cloud_stt"]["reason"], "")

    def test_cloud_stt_reason_when_disabled(self):
        from app.core.config import settings
        with patch.object(settings, "speech_stt_provider", "off"):
            from app.voice.stt import reset_stt_provider
            reset_stt_provider()
            resp = self.client.get("/api/v1/capabilities",
                                   headers=self.auth)
        body = resp.json()
        self.assertFalse(body["cloud_stt"]["available"])
        self.assertEqual(body["cloud_stt"]["reason"], "stt_disabled")

    def test_anonymous_request_denied_by_api_access(self):
        # capabilities 只读无状态，但 api 级访问边界（登录或游客 token）
        # 仍然生效；匿名裸请求不得拿到能力面。
        resp = self.client.get("/api/v1/capabilities")
        self.assertEqual(resp.status_code, 401)

    def test_classroom_available_when_enabled_and_permitted(self):
        from app.core.config import settings
        with patch.multiple(settings, classroom_enabled=True,
                            classroom_allowed_users="",
                            classroom_allow_guest=False):
            resp = self.client.get("/api/v1/capabilities",
                                   headers=self.auth)
        body = resp.json()
        self.assertTrue(body["classroom"]["available"])
        self.assertEqual(body["classroom"]["reason"], "")

    def test_guest_identity_reports_guest_reason(self):
        from app.core.config import settings
        # 模拟游客身份路径：认证缺省时聚合器按 DEFAULT_STUDENT_ID 判定，
        # classroom 对游客关闭时报 classroom_guest_denied（用默认游客身份
        # 直接调用聚合函数验证，绕开 api 访问层）。
        from app.api.v1.capabilities import _classroom_capability
        from app.agents.student_model.store import DEFAULT_STUDENT_ID
        with patch.multiple(settings, classroom_enabled=True,
                            classroom_allowed_users="",
                            classroom_allow_guest=False):
            entry = _classroom_capability(DEFAULT_STUDENT_ID)
        self.assertFalse(entry.available)
        self.assertEqual(entry.reason, "classroom_guest_denied")

    def test_no_credentials_leak(self):
        resp = self.client.get("/api/v1/capabilities")
        dumped = json.dumps(resp.json())
        self.assertNotIn("AZURE_SPEECH_KEY", dumped)
        self.assertNotIn("api.cognitiveservices", dumped)


if __name__ == "__main__":
    unittest.main()
