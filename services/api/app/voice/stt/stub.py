"""Deterministic STT stub: mirrors the uploaded bytes in a stable transcript.

用于无云端凭证的端到端联调与测试：不解析音频，只回显一个由字节长度
决定的占位 transcript，让客户端整条上传→转写→回显链路可验证。
"""
from __future__ import annotations

from .azure import normalize_language
from .base import STTCapabilities, STTProvider, STTResult


class StubSTT(STTProvider):
    name = "stub"

    async def transcribe(self, audio: bytes, *, content_type: str = "",
                         language: str = "") -> STTResult:
        text = f"[stub transcript {len(audio)} bytes]"
        lang = normalize_language(language)
        return STTResult(text=text, language=lang, duration_ms=0,
                         provider=self.name)

    def capabilities(self) -> STTCapabilities:
        return STTCapabilities(
            languages=("zh-CN", "en-US"),
            formats=("audio/wav", "audio/x-wav", "audio/webm;codecs=opus",
                     "audio/ogg;codecs=opus", "audio/mpeg", "audio/mp4"),
            max_duration_seconds=60,
            max_audio_bytes=2 * 1024 * 1024)
