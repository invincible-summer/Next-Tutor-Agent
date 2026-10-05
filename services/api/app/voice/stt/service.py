"""统一 STT service（ADR-0012，服务端中转云语音）。

职责：
- 集中 provider 的配置解析与惰性单例：``SPEECH_STT_PROVIDER`` =
  ``off | stub | azure | auto``。``auto`` 表示已配置 ``AZURE_SPEECH_*`` 就走
  Azure，否则关闭——不伪可用；``stub`` 只用于无凭证联调与测试。
- 并发保护：云端转写与 TTS 共享同一把 cloud semaphore
  （``CLASSROOM_TTS_CLOUD_CONCURRENCY``，同一份 Azure 资源凭证），STT
  不再单独开闸。
- 能力只读投影 ``stt_capabilities``：不触发网络请求，端点据此做服务端
  上限校验（时长/大小/格式），而不是转发任意客户端声明。
"""
from __future__ import annotations

import logging
from typing import Any

from ..tts.service import shared_semaphores
from .base import (STTCapabilities, STTProvider, STTResult, STTUnavailable)

log = logging.getLogger(__name__)


def _azure_available() -> bool:
    from app.core.config import settings
    return bool(settings.azure_speech_key and settings.azure_speech_region)


def azure_stt_available() -> bool:
    """云端转写是否已配置（key+region 齐备）。"""
    return _azure_available()


# ---------------------------------------------------------------------------
# provider 解析（惰性单例 + 配置 key 缓存，镜像 tts/service.phone_provider）
# ---------------------------------------------------------------------------

_STT_INSTANCE: STTProvider | None = None
_STT_KEY: tuple | None = None


def _stt_key() -> tuple:
    from app.core.config import settings
    return (settings.speech_stt_provider,
            bool(settings.azure_speech_key), settings.azure_speech_region,
            settings.azure_speech_endpoint)


def stt_provider() -> STTProvider | None:
    """当前服务端 STT provider（off 时 None）；失败关闭不崩溃。"""
    global _STT_INSTANCE, _STT_KEY
    from app.core.config import settings
    provider = (settings.speech_stt_provider or "off").strip().lower()
    if provider == "off":
        return None
    if provider == "auto":
        provider = "azure" if _azure_available() else "off"
        if provider == "off":
            return None
    key = _stt_key()
    if _STT_INSTANCE is not None and _STT_KEY == key:
        return _STT_INSTANCE
    try:
        if provider == "stub":
            from .stub import StubSTT
            client: STTProvider | None = StubSTT()
        elif provider == "azure":
            if not _azure_available():
                log.warning("SPEECH_STT_PROVIDER=azure 但未配置 AZURE_SPEECH_*，"
                            "服务端转写关闭")
                client = None
            else:
                from .azure import AzureSTT
                client = AzureSTT()
        else:
            log.warning("未知 SPEECH_STT_PROVIDER=%r，服务端转写关闭", provider)
            client = None
    except Exception as exc:
        log.warning("服务端 STT provider 初始化失败，转写关闭: %s", exc)
        _STT_INSTANCE = None
        _STT_KEY = key
        return None
    _STT_INSTANCE = client
    _STT_KEY = key
    return client


def stt_status() -> tuple[bool, str]:
    """(available, reason code)；reason 供 /capabilities 聚合直接引用。"""
    provider = stt_provider()
    if provider is not None:
        return True, ""
    from app.core.config import settings
    configured = (settings.speech_stt_provider or "off").strip().lower()
    if configured == "off":
        return False, "stt_disabled"
    return False, "stt_not_configured"


def stt_limits() -> STTCapabilities:
    """当前 provider 的服务端校验上限（provider 缺省时给保守默认）。"""
    provider = stt_provider()
    if provider is not None:
        return provider.capabilities()
    return STTCapabilities()


async def transcribe(audio: bytes, *, content_type: str = "",
                     language: str = "") -> STTResult:
    """服务端转写入口；云端并发与 TTS 共享同一把 semaphore。"""
    provider = stt_provider()
    if provider is None:
        raise STTUnavailable("服务端语音转写未启用")
    if provider.name == "azure":
        sem = shared_semaphores()[0]
        async with sem:
            return await provider.transcribe(audio, content_type=content_type,
                                             language=language)
    return await provider.transcribe(audio, content_type=content_type,
                                     language=language)


def stt_capabilities() -> dict[str, Any]:
    """能力只读投影（零网络请求）；绝不包含凭证或 endpoint。"""
    available, reason = stt_status()
    limits = stt_limits()
    return {
        "available": available,
        "reason": reason,
        "provider": provider_class(),
        "languages": list(limits.languages),
        "formats": list(limits.formats),
        "limits": {
            "max_duration_seconds": limits.max_duration_seconds,
            "max_audio_bytes": limits.max_audio_bytes,
        },
    }


def provider_class() -> str:
    """对外只暴露 provider 类别名（azure/stub/off），不暴露凭证细节。"""
    provider = stt_provider()
    return provider.name if provider is not None else "off"


def reset_stt_service() -> None:
    """测试/配置重载：丢弃缓存的 provider 实例。"""
    global _STT_INSTANCE, _STT_KEY
    _STT_INSTANCE = None
    _STT_KEY = None
