"""Voice provider contracts for spoken-reply TTS plugins."""
from __future__ import annotations

from dataclasses import dataclass


class VoiceProviderError(RuntimeError):
    """A configured provider failed (engine missing, sidecar down, ...).

    ``code`` maps to the WebSocket error event the voice endpoint emits;
    callers degrade instead of crashing the connection.
    """

    code = "provider_error"

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message or self.__class__.code)
        if code is not None:
            self.code = code


class TTSUnavailable(VoiceProviderError):
    code = "tts_unavailable"


@dataclass
class TTSResult:
    # Little-endian int16 mono PCM without a RIFF header; the sample rate
    # travels beside the bytes as JSON metadata so the client can build an
    # AudioBuffer at the right rate.
    pcm16: bytes
    sample_rate: int


class TTSProvider:
    """Text to speech. Output is 16-bit mono PCM plus its sample rate."""

    name = "tts"

    async def synthesize(self, text: str, *, speed: float | None = None) -> TTSResult:
        raise NotImplementedError
