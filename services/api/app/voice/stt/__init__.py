"""STT provider factory（服务端转写，ADR-0012）。

``SPEECH_STT_PROVIDER``: off (默认) | stub | azure | auto。云端转写走
Azure Speech REST（与 TTS 共享 ``AZURE_SPEECH_*`` 凭证与 cloud
semaphore）；``auto`` 在凭证齐备时等价 azure，否则关闭——不伪可用。
"""
from __future__ import annotations

from .base import STTProvider


def get_stt_provider() -> STTProvider | None:
    from . import service
    return service.stt_provider()


def reset_stt_provider() -> None:
    from . import service
    service.reset_stt_service()
