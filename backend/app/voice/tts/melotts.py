"""MeloTTS-Chinese through the local sidecar service (HTTP).

MeloTTS ships no official HTTP server (only the ``melo.api.TTS`` Python
class), so ``backend/voice_sidecar/app.py`` wraps it in a tiny FastAPI
process with its own venv. This provider keeps torch out of the main
backend: it POSTs text and normalizes the returned WAV to PCM16 for the
WebSocket wire format.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..base import TTSProvider, TTSResult, TTSUnavailable
from ..wav import wav_to_pcm16

log = logging.getLogger(__name__)


class MeloTTS(TTSProvider):
    name = "melo"

    def __init__(self, base_url: str = "", *, timeout: float = 60.0):
        from app.core.config import settings
        self.base_url = (base_url or settings.voice_tts_base_url).rstrip("/")
        self.timeout = timeout

    async def synthesize(self, text: str, *, speed: float | None = None) -> TTSResult:
        from app.core.config import settings
        speed = settings.voice_tts_speed if speed is None else speed
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/tts",
                                         json={"text": text, "speed": speed})
        except httpx.HTTPError as exc:
            raise TTSUnavailable(f"TTS sidecar 不可达: {exc}") from exc
        if resp.status_code != 200:
            raise TTSUnavailable(
                f"TTS sidecar 错误 {resp.status_code}: {resp.text[:200]}")
        try:
            # Whole-clip stdlib WAV decode is pure-Python sample work; on the
            # event loop it stalls every concurrent stream for the length of
            # the decode, so hand it to a worker thread.
            pcm, rate = await asyncio.to_thread(wav_to_pcm16, resp.content)
        except Exception as exc:
            raise TTSUnavailable(f"TTS sidecar 返回非 WAV 内容: {exc}") from exc
        return TTSResult(pcm16=pcm, sample_rate=rate)
