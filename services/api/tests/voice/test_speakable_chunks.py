"""Voice: speakable chunk assembly."""
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
class TestSpeakableChunks(unittest.TestCase):
    def test_short_text_single_chunk(self):
        from app.api.v1.voice import _speakable_chunks
        self.assertEqual(_speakable_chunks("短句。"), ["短句。"])

    def test_long_text_chunks_at_punctuation(self):
        from app.api.v1.voice import _speakable_chunks
        text = "这一句用来填充长度，" * 40  # 520 chars, cut points everywhere
        chunks = _speakable_chunks(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 240)
        self.assertEqual("".join(chunks), text)

    def test_unpunctuated_text_hard_split_under_sidecar_cap(self):
        from app.api.v1.voice import _speakable_chunks
        text = "字" * 600
        chunks = _speakable_chunks(text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 240)
        self.assertEqual("".join(chunks), text)
