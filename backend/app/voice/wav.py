"""Decode MeloTTS sidecar WAV replies to raw PCM16 (stdlib only)."""
from __future__ import annotations

import io
import struct
import wave


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    """Extract (mono PCM16, sample_rate) from a RIFF/WAV payload.

    Multi-channel input is down-mixed by averaging; anything that is not
    16-bit PCM raises ValueError (the sidecar always emits 16-bit WAV).
    """
    with wave.open(io.BytesIO(data), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width: {width * 8} bit")
    if channels == 1:
        return frames, rate
    count = len(frames) // (2 * channels)
    values = struct.unpack(f"<{count * channels}h", frames[:count * channels * 2])
    mono = bytearray(count * 2)
    for i in range(count):
        acc = 0
        for c in range(channels):
            acc += values[i * channels + c]
        mono[i * 2:i * 2 + 2] = int(acc / channels).to_bytes(2, "little", signed=True)
    return bytes(mono), rate
