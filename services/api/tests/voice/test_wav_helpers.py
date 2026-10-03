"""Voice: WAV frame helpers."""
from __future__ import annotations
import io
import math
import json
import struct
import wave
import unittest
from array import array
from unittest.mock import patch
from tests.support.storage_sandbox import StorageSandboxTestCase
from app.voice.sentences import split_sentences, take_complete, take_speech_cuts
from app.voice.speak_text import to_speakable
from app.voice.wav import wav_to_pcm16
"""Voice layer regressions for browser text input and MeloTTS output."""
if __name__ == "__main__":
    unittest.main()
class TestWavHelpers(unittest.TestCase):
    def test_decode_sidecar_wav(self):
        pcm = b"\x01\x02" * 1600
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(44100)
            wav_file.writeframes(pcm)
        out, rate = wav_to_pcm16(buf.getvalue())
        self.assertEqual(out, pcm)
        self.assertEqual(rate, 44100)
