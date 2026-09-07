"""Deterministic TTS stub: a short beep pattern as PCM16 (tests / no-model dev)."""
from __future__ import annotations

import math
import struct

from ..base import TTSProvider, TTSResult


class StubTTS(TTSProvider):
    name = "stub"

    def __init__(self, sample_rate: int = 16000, seconds: float = 0.2):
        self.sample_rate = int(sample_rate)
        self.seconds = seconds

    async def synthesize(self, text: str, *, speed: float | None = None) -> TTSResult:
        total = int(self.sample_rate * self.seconds)
        pcm = bytearray()
        for i in range(total):
            t = i / self.sample_rate
            gate = 8000 if (t * 8) % 1.0 < 0.5 else 2000
            pcm += struct.pack("<h", int(gate * math.sin(2 * math.pi * 440 * t)))
        return TTSResult(pcm16=bytes(pcm), sample_rate=self.sample_rate)
