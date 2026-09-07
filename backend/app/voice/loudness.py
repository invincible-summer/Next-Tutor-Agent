"""Per-sentence loudness normalization for streamed TTS audio.

MeloTTS synthesizes each sentence as an independent clip, and consecutive
clips can differ by several dB — on the wire that reads as 「声音忽大忽小」.
Every clip is leveled here, before it hits the WebSocket:

* scale to a common RMS target (≈ -16.5 dBFS, a comfortable speech level),
  with a gain ceiling so a near-silent clip is never blown up into noise;
* re-scale anything whose peaks would clip back under the ceiling (keeps
  brief sibilants from distorting after the RMS lift);
* short linear fades on both ends, so the level jump between two
  normalized clips can never click.

Pure stdlib (``array``), mirroring wav.py's no-numpy rule for the voice
layer: a ~5 s 44.1 kHz clip is ~220k samples and normalizes in tens of
milliseconds — negligible next to the seconds MeloTTS spends synthesizing.
"""
from __future__ import annotations

from array import array

_TARGET_RMS = 0.15      # ≈ -16.5 dBFS
_PEAK_CEILING = 0.92    # headroom for the browser's resample + compressor
_MAX_GAIN = 4.0         # +12 dB: don't amplify (near-)silence into noise
_MIN_RMS = 1e-4         # below this the clip counts as silence: untouched
_FADE_SECONDS = 0.008   # 8 ms linear fade in/out against boundary clicks


def normalize_pcm16(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """Level one synthesized clip to the shared RMS/peak envelope."""
    samples = array("h")
    samples.frombytes(pcm)
    n = len(samples)
    if n == 0:
        return pcm
    total = 0
    for v in samples:
        total += v * v
    rms = (total / n) ** 0.5 / 32768.0
    if rms < _MIN_RMS:
        return pcm
    gain = min(_TARGET_RMS / rms, _MAX_GAIN)
    scaled = [v * gain for v in samples]

    peak = max(map(abs, scaled))
    ceiling = _PEAK_CEILING * 32767
    if peak > ceiling and peak > 0:
        limiter = ceiling / peak
        scaled = [v * limiter for v in scaled]

    fade = min(n // 2, int(sample_rate * _FADE_SECONDS))
    for i in range(fade):
        f = i / fade
        scaled[i] *= f
        scaled[n - 1 - i] *= f

    out = array("h", (max(-32768, min(32767, int(v + 0.5) if v >= 0 else int(v - 0.5)))
                      for v in scaled))
    return out.tobytes()
