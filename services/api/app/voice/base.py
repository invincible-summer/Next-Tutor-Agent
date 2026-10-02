"""Voice provider contracts for spoken-reply TTS plugins.

课堂模式（plan.md §11.2）兼容扩展：``synthesize`` 接受可选 ``options``
（音色/语言/合成基准速度）；旧 ``speed`` 关键字调用保持原行为。Provider
额外通过 :class:`TTSCapabilities` 声明语言、音色、语速区间、是否有词级
边界与单段文本上限；首发 Azure REST 与 Melo 都是段级同步合成，不允许
凭模型估计字级时间戳（``has_word_boundaries=False``）。
"""
from __future__ import annotations

from dataclasses import dataclass


class VoiceProviderError(RuntimeError):
    """A configured provider failed (engine missing, sidecar down, ...).

    ``code`` maps to the WebSocket error event the voice endpoint emits;
    callers degrade instead of crashing the connection.
    """

    code = "provider_error"

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message or self.__class__.code)
        if code is not None:
            self.code = code


class TTSUnavailable(VoiceProviderError):
    code = "tts_unavailable"


class TTSConfigError(VoiceProviderError):
    """凭证/配置错误（401/403 等）：不重试，需要管理员修复。"""

    code = "tts_config"


class TTSRateLimited(VoiceProviderError):
    """上游限流（429）：遵守 Retry-After 后单次自动重试。"""

    code = "tts_rate_limited"


class TTSTransient(VoiceProviderError):
    """5xx/网络抖动：最多自动重试一次，仍失败按策略回退。"""

    code = "tts_transient"


@dataclass(frozen=True)
class TTSOptions:
    """合成选项（课堂逐段合成用）；电话路径不传、保持旧 ``speed`` 语义。

    ``synthesis_speed`` 是合成基准速度（课堂固定 1.0，plan.md §11.4）；
    课堂个人的 ``tts_speed`` 是播放倍速，由浏览器 ``playbackRate`` 承担，
    不进入合成参数。
    """

    voice_id: str = ""
    language: str = "zh-CN"
    synthesis_speed: float = 1.0


@dataclass(frozen=True)
class TTSCapabilities:
    languages: tuple[str, ...] = ()
    voices: tuple[str, ...] = ()
    speed_range: tuple[float, float] = (0.5, 2.0)
    has_word_boundaries: bool = False
    max_text_chars: int = 400


@dataclass
class TTSResult:
    # Little-endian int16 mono PCM without a RIFF header; the sample rate
    # travels beside the bytes as JSON metadata so the client can build an
    # AudioBuffer at the right rate. provider/voice_id 由 provider 回填，
    # 课堂缓存据其区分音频来源（不能把 Melo 音频记到 Azure key 下）。
    pcm16: bytes
    sample_rate: int
    provider: str = ""
    voice_id: str = ""


class TTSProvider:
    """Text to speech. Output is 16-bit mono PCM plus its sample rate."""

    name = "tts"

    async def synthesize(self, text: str, *, speed: float | None = None,
                         options: TTSOptions | None = None) -> TTSResult:
        raise NotImplementedError

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities()
