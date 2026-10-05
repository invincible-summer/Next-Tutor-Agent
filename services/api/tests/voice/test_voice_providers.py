"""Offline contract tests for iFlytek WebSocket and Deepgram REST adapters."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import unittest
import wave
from datetime import datetime, timezone

import httpx

from app.voice.base import TTSOptions
from app.voice.deepgram import DeepgramSTT, DeepgramTTS
from app.voice.iflytek import IflytekSTT, IflytekTTS, build_auth_url


def _run(coro):
    return asyncio.run(coro)


def _wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(struct.pack("<h", 1000) * 240)
    return out.getvalue()


class _Socket:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def send(self, payload): self.sent.append(json.loads(payload))
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.messages: raise StopAsyncIteration
        return json.dumps(self.messages.pop(0))


class TestIflytek(unittest.TestCase):
    def test_auth_is_deterministic_and_escapes_query(self):
        url = build_auth_url(
            "wss://iat-api.xfyun.cn/v2/iat", api_key="key", api_secret="secret",
            now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
        self.assertIn("authorization=", url)
        self.assertIn("host=iat-api.xfyun.cn", url)
        self.assertNotIn("secret", url)

    def test_tts_decodes_audio_frames_without_network(self):
        sock = _Socket([{"code": 0, "data": {"status": 2,
            "audio": base64.b64encode(b"pcm").decode()}}])
        provider = IflytekTTS(app_id="app", api_key="key", api_secret="secret",
                              connector=lambda _: sock)
        result = _run(provider.synthesize("你好", options=TTSOptions(
            voice_id="xiaoyan", language="zh-CN")))
        self.assertEqual(result.pcm16, b"pcm")
        self.assertEqual(result.sample_rate, 16000)
        self.assertEqual(sock.sent[0]["common"]["app_id"], "app")

    def test_stt_collects_words_without_network(self):
        sock = _Socket([{"code": 0, "data": {"status": 2, "result": {
            "ws": [{"cw": [{"w": "你好"}]}, {"cw": [{"w": "世界"}]}]}}}])
        provider = IflytekSTT(app_id="app", api_key="key", api_secret="secret",
                              connector=lambda _: sock)
        result = _run(provider.transcribe(b"pcm", content_type="audio/pcm", language="zh"))
        self.assertEqual(result.text, "你好世界")
        self.assertEqual(result.provider, "iflytek")
        self.assertEqual(len(sock.sent), 2)


class TestDeepgram(unittest.TestCase):
    def test_stt_and_tts_parse_documented_shapes(self):
        def handler(request: httpx.Request):
            if request.url.path.endswith("/listen"):
                return httpx.Response(200, json={"metadata": {"duration": 1.25},
                    "results": {"channels": [{"alternatives": [{"transcript": "hello"}]}]}})
            return httpx.Response(200, content=_wav())

        transport = httpx.MockTransport(handler)
        stt = DeepgramSTT(api_key="key", transport=transport)
        tts = DeepgramTTS(api_key="key", transport=transport)
        result = _run(stt.transcribe(b"audio", content_type="audio/wav", language="en"))
        self.assertEqual((result.text, result.duration_ms), ("hello", 1250))
        audio = _run(tts.synthesize("hello", options=TTSOptions(
            voice_id="aura-2-thalia-en", language="en-US")))
        self.assertEqual(audio.provider, "deepgram")
        self.assertEqual(audio.sample_rate, 24000)

    def test_missing_key_fails_closed(self):
        with self.assertRaises(Exception):
            _run(DeepgramSTT(api_key="").transcribe(b"x", content_type="audio/wav"))

