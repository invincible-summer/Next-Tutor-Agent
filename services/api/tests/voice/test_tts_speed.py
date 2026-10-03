"""Voice: TTS speed control."""
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
class TestTtsSpeed(unittest.TestCase):
    """默认语速略慢 + 每用户 prefs.tts_speed 夹取（不触真实 users/）。"""

    def test_default_speed_slightly_slow(self):
        from app.core.config import settings
        self.assertEqual(settings.voice_tts_speed, 0.9)

    def test_resolve_tts_speed_clamps_and_falls_back(self):
        from types import SimpleNamespace
        from app.api.v1.voice import _resolve_tts_speed
        from app.core.config import settings

        def user_with(pref):
            return SimpleNamespace(
                profile=SimpleNamespace(prefs={"tts_speed": pref}))

        with patch("app.identity.store.get_by_id",
                   return_value=user_with(1.3)):
            self.assertEqual(_resolve_tts_speed("usr_x"), 1.3)
        with patch("app.identity.store.get_by_id", return_value=user_with(9)):
            self.assertEqual(_resolve_tts_speed("usr_x"), 2.0)
        with patch("app.identity.store.get_by_id",
                   return_value=user_with(0.1)):
            self.assertEqual(_resolve_tts_speed("usr_x"), 0.5)
        # 字符串 / True 等非法值与游客（无账号）都回落实例默认。
        for bad in ("fast", True, None):
            with patch("app.identity.store.get_by_id",
                       return_value=user_with(bad)):
                self.assertEqual(_resolve_tts_speed("usr_x"),
                                 settings.voice_tts_speed)
        with patch("app.identity.store.get_by_id", return_value=None):
            self.assertEqual(_resolve_tts_speed("student_default"),
                             settings.voice_tts_speed)
