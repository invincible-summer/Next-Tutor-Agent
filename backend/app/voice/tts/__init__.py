"""TTS provider factory (settings-driven, fail-open like the embedding lane).

``VOICE_TTS_PROVIDER``: off (default) | stub | melo. ``melo`` talks to the
local sidecar (VOICE_TTS_BASE_URL); a sidecar that is down surfaces as a
per-call TTSUnavailable, never as a crashed app.
"""
from __future__ import annotations

import logging

from ..base import TTSProvider
from .stub import StubTTS

log = logging.getLogger(__name__)

_INSTANCE: TTSProvider | None = None
_INSTANCE_KEY: tuple | None = None


def _provider_key() -> tuple:
    from app.core.config import settings
    return (settings.voice_tts_provider, settings.voice_tts_base_url,
            settings.voice_tts_speed)


def reset_tts_provider() -> None:
    """Drop the cached provider (tests / config reload)."""
    global _INSTANCE, _INSTANCE_KEY
    _INSTANCE = None
    _INSTANCE_KEY = None


def get_tts_provider() -> TTSProvider | None:
    global _INSTANCE, _INSTANCE_KEY
    from app.core.config import settings
    provider = (settings.voice_tts_provider or "off").strip().lower()
    if provider == "off":
        return None
    key = _provider_key()
    if _INSTANCE is not None and _INSTANCE_KEY == key:
        return _INSTANCE
    try:
        if provider == "stub":
            client: TTSProvider = StubTTS()
        elif provider == "melo":
            from .melotts import MeloTTS
            client = MeloTTS()
        else:
            log.warning("未知 VOICE_TTS_PROVIDER=%r，语音 TTS 关闭", provider)
            return None
    except Exception as exc:
        log.warning("语音 TTS provider 初始化失败，TTS 关闭: %s", exc)
        _INSTANCE = None
        _INSTANCE_KEY = key
        return None
    _INSTANCE = client
    _INSTANCE_KEY = key
    return client
