"""Deepgram REST TTS/STT adapters.

Uses the documented ``/v1/listen`` and ``/v1/speak`` HTTP APIs with a server
side ``Authorization: Token`` header. ``httpx`` is already a core dependency;
no provider SDK or credential is shipped in the repository.
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

import httpx

from .base import (TTSConfigError, TTSCapabilities, TTSOptions, TTSProvider,
                   TTSRateLimited, TTSResult, TTSTransient, TTSUnavailable)
from .loudness import normalize_pcm16
from .stt.base import (STTCapabilities, STTConfigError, STTProvider,
                       STTRateLimited, STTResult, STTTransient, STTUnavailable)
from .wav import wav_to_pcm16

log = logging.getLogger(__name__)


def _language(language: str) -> str:
    return "en-US" if (language or "").lower().startswith("en") else "zh-CN"


class _DeepgramBase:
    name = "deepgram"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 request_timeout: float = 30.0):
        from app.core.config import settings
        self.api_key = (api_key if api_key is not None else settings.deepgram_api_key).strip()
        self.base_url = (base_url if base_url is not None else settings.deepgram_base_url).rstrip("/")
        self.transport = transport
        self.request_timeout = request_timeout

    def _require_config(self, error_type):
        if not self.api_key:
            raise error_type("未配置 DEEPGRAM_API_KEY")
        parsed = httpx.URL(self.base_url)
        host = (parsed.host or "").lower().rstrip(".")
        if (parsed.scheme != "https" or
                not host or
                (host != "deepgram.com" and
                 not host.endswith(".deepgram.com"))):
            raise error_type("DEEPGRAM_BASE_URL 仅允许 HTTPS Deepgram 域名")

    def _client(self):
        return httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(self.request_timeout, connect=5.0),
            transport=self.transport,
        )


class DeepgramSTT(_DeepgramBase, STTProvider):
    name = "deepgram"

    async def transcribe(self, audio: bytes, *, content_type: str = "",
                         language: str = "") -> STTResult:
        self._require_config(STTConfigError)
        media = (content_type or "application/octet-stream").strip()
        params = {"model": self._model(), "smart_format": "true",
                  "language": "en" if _language(language) == "en-US" else "zh-CN",
                  "mip_opt_out": "true"}
        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": media}
        try:
            async with self._client() as client:
                resp = await client.post(f"{self.base_url}/v1/listen", params=params,
                                         content=audio, headers=headers)
        except httpx.HTTPError as exc:
            raise STTTransient(f"Deepgram 转写请求失败: {exc}") from exc
        if resp.status_code in (401, 403):
            raise STTConfigError(f"Deepgram 凭证被拒绝（{resp.status_code}）")
        if resp.status_code == 429:
            raise STTRateLimited("Deepgram 转写限流，请稍后重试")
        if resp.status_code >= 500:
            raise STTTransient(f"Deepgram 转写服务错误 {resp.status_code}")
        if resp.status_code != 200:
            raise STTUnavailable(f"Deepgram 转写请求错误 {resp.status_code}")
        try:
            payload = resp.json()
            channel = payload["results"]["channels"][0]
            alternative = channel["alternatives"][0]
            text = str(alternative.get("transcript") or "").strip()
            duration = int(round(float(payload.get("metadata", {}).get("duration", 0)) * 1000))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise STTUnavailable("Deepgram 转写返回结构异常") from exc
        if not text:
            raise STTUnavailable("Deepgram 转写返回空文本")
        return STTResult(text=text, language=_language(language), duration_ms=max(0, duration), provider=self.name)

    def _model(self) -> str:
        from app.core.config import settings
        return settings.deepgram_stt_model or "nova-3"

    def capabilities(self) -> STTCapabilities:
        return STTCapabilities(languages=("zh-CN", "en-US"),
                                formats=("audio/wav", "audio/x-wav", "audio/webm",
                                         "audio/ogg", "audio/mpeg", "audio/mp4",
                                         "audio/pcm", "application/octet-stream"),
                                max_duration_seconds=60, max_audio_bytes=10 * 1024 * 1024)


class DeepgramTTS(_DeepgramBase, TTSProvider):
    name = "deepgram"

    async def synthesize(self, text: str, *, speed: float | None = None,
                         options: TTSOptions | None = None) -> TTSResult:
        self._require_config(TTSConfigError)
        opts = options or TTSOptions(language="en-US")
        speed_value = float(opts.synthesis_speed if options else (speed or 1.0))
        speed_value = min(1.5, max(0.7, speed_value))
        voice_id = opts.voice_id or self._default_voice(opts.language)
        params = {"model": voice_id, "encoding": "linear16", "container": "wav",
                  "sample_rate": "24000", "speed": str(speed_value), "mip_opt_out": "true"}
        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}
        try:
            async with self._client() as client:
                resp = await client.post(f"{self.base_url}/v1/speak", params=params,
                                         json={"text": text}, headers=headers)
        except httpx.HTTPError as exc:
            raise TTSTransient(f"Deepgram 合成请求失败: {exc}") from exc
        if resp.status_code in (401, 403):
            raise TTSConfigError(f"Deepgram 凭证被拒绝（{resp.status_code}）")
        if resp.status_code == 429:
            raise TTSRateLimited("Deepgram 合成限流，请稍后重试")
        if resp.status_code >= 500:
            raise TTSTransient(f"Deepgram 合成服务错误 {resp.status_code}")
        if resp.status_code != 200:
            raise TTSUnavailable(f"Deepgram 合成请求错误 {resp.status_code}")
        if not resp.content.startswith(b"RIFF"):
            raise TTSUnavailable("Deepgram 返回非 WAV 内容")
        try:
            pcm, rate = await asyncio.to_thread(wav_to_pcm16, resp.content)
            pcm = await asyncio.to_thread(normalize_pcm16, pcm, rate)
        except Exception as exc:
            raise TTSUnavailable(f"Deepgram 音频解码失败: {exc}") from exc
        return TTSResult(pcm16=pcm, sample_rate=rate, provider=self.name, voice_id=voice_id)

    @staticmethod
    def _default_voice(language: str) -> str:
        return "aura-2-thalia-en" if (language or "").lower().startswith("en") else "aura-2-asteria-en"

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(languages=("en-US",),
                                voices=("aura-2-thalia-en", "aura-2-asteria-en"),
                                speed_range=(0.7, 1.5), max_text_chars=2000)
