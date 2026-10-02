"""TTS provider factory（委托 voice/tts/service.py 统一调度，阶段 F）。

``VOICE_TTS_PROVIDER``: off (默认) | stub | melo | azure | auto。``melo``
走本地 sidecar（VOICE_TTS_BASE_URL）；sidecar 宕机表现为每次调用的
TTSUnavailable，绝不拖垮应用。电话与课堂请求共享全局 semaphore
（云 ``CLASSROOM_TTS_CLOUD_CONCURRENCY`` / Melo 1），由 service 层保证。
"""
from __future__ import annotations

from ..base import TTSProvider


def get_tts_provider() -> TTSProvider | None:
    """旧电话 factory：无参调用语义不变；缓存与并发管理在 service。"""
    from . import service
    return service.phone_provider()


def reset_tts_provider() -> None:
    """Drop the cached provider (tests / config reload)."""
    from . import service
    service.reset_tts_service()
