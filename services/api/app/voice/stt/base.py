"""Server-side STT provider contracts（ADR-0012）。

移动端不持有任何语音凭证：录音以 multipart 上传 ``/speech/transcriptions``，
由服务端调用云端 STT（首发 Azure REST），只把 transcript、语言与时长回传。
与 TTS 契约（``app/voice/base.py``）镜像：provider 通过
:class:`STTCapabilities` 声明语言、接受格式与服务端上限；错误族继承
``VoiceProviderError``（``code`` 供 API 层映射），但类名独立，避免与
TTS 的降级路径混淆。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import VoiceProviderError


class STTUnavailable(VoiceProviderError):
    code = "stt_unavailable"


class STTConfigError(VoiceProviderError):
    """凭证/配置错误（401/403 等）：不重试，需要管理员修复。"""

    code = "stt_config"


class STTRateLimited(VoiceProviderError):
    """上游限流（429）：遵守 Retry-After 后单次自动重试。"""

    code = "stt_rate_limited"


class STTTransient(VoiceProviderError):
    """5xx/网络抖动：最多自动重试一次，仍失败才对客户端报错。"""

    code = "stt_transient"


@dataclass(frozen=True)
class STTCapabilities:
    """Provider 能力声明；服务端据此校验上传，而不是转发任意请求。"""

    languages: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()       # 接受的音频 content-type 白名单
    max_duration_seconds: int = 60
    max_audio_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class STTResult:
    """一次转写的公开结果；绝不携带 provider 凭证或原始音频回传。"""

    text: str
    language: str = ""                  # BCP-47（provider 未回显时用请求语言）
    duration_ms: int = 0
    provider: str = ""


class STTProvider:
    """Speech to text over a complete audio buffer (one utterance)."""

    name = "stt"

    async def transcribe(self, audio: bytes, *, content_type: str = "",
                         language: str = "") -> STTResult:
        raise NotImplementedError

    def capabilities(self) -> STTCapabilities:
        return STTCapabilities()
