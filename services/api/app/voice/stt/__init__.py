"""STT provider factory（服务端转写，ADR-0012）。

``SPEECH_STT_PROVIDER``: off（默认）| stub | azure | iflytek | deepgram |
auto。云端转写与 TTS 共享 cloud semaphore；``auto`` 按已配置凭证选择
Azure、讯飞或 Deepgram，否则关闭——不伪可用。
"""
from __future__ import annotations

from .base import STTProvider


def get_stt_provider() -> STTProvider | None:
    from . import service
    return service.stt_provider()


def reset_stt_provider() -> None:
    from . import service
    service.reset_stt_service()
