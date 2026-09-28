"""Azure Speech REST 适配器（plan.md §11.3，课堂云端 TTS 首发实现）。

要点（与计划逐条对应）：
- 凭证只来自服务器配置 ``AZURE_SPEECH_KEY``/``AZURE_SPEECH_REGION``；
  可选官方 resource endpoint（``AZURE_SPEECH_ENDPOINT``）经 HTTPS 与
  批准域校验，普通用户/模型不能携带任意 base URL。
- 请求头 ``Ocp-Apim-Subscription-Key``、``Content-Type: application/ssml+xml``、
  ``X-Microsoft-OutputFormat: riff-24khz-16bit-mono-pcm``、``User-Agent``。
- 后端构造转义后的 SSML，只允许 speak/voice/prosody 受控结构；模型与
  客户端永远拿不到原始 SSML 通道。
- WAV 解码（``wav_to_pcm16``）与响度归一（``normalize_pcm16``）在 worker
  线程执行，CPU 后处理不占事件循环；采样率读取实际返回值。
- 错误分类（§11.5）：401/403 → 配置错误不重试；429 → 遵守 Retry-After
  纳入总 deadline 单次重试；5xx/网络异常最多重试一次；解析失败绝不伪成功。
- voices list 只投影 ShortName/Locale/DisplayName，不回供应商内部信息。

音频格式与 SSML 结构参见：
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from ..base import (TTSConfigError, TTSCapabilities, TTSOptions, TTSProvider,
                    TTSRateLimited, TTSResult, TTSTransient, TTSUnavailable)
from ..loudness import normalize_pcm16
from ..wav import wav_to_pcm16

log = logging.getLogger(__name__)

PROVIDER_VERSION = "azure-rest-1"
_OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"
_USER_AGENT = "edu-agent-classroom/1.0"
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}[a-z0-9]$")
# 官方自定义域形如 https://<resource>.api.cognitiveservices.azure.com；
# 允许的 endpoint 仅限该后缀（§11.3“批准域”），且必须 HTTPS。
_APPROVED_ENDPOINT_SUFFIX = ".api.cognitiveservices.azure.com"
# SSML 语速区间：Azure prosody rate 相对百分比 -50%..+100%（§11.2 能力声明）
_SPEED_MIN, _SPEED_MAX = 0.5, 2.0

# 官方 voices list 的投影字段（§11.3：只返回 ShortName/语言/展示名）
_VOICE_FIELDS = ("ShortName", "Locale", "DisplayName")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
                .replace("'", "&apos;"))


def build_ssml(text: str, *, voice_id: str, language: str,
               synthesis_speed: float = 1.0) -> str:
    """构造受控 SSML：仅 speak/voice/prosody 三种结构，正文全转义。

    纯函数，测试直接覆盖转义与语速映射；任何调用方都不能把原始 SSML
    送进 ``synthesize``。
    """
    safe_text = _xml_escape(text)
    safe_voice = _xml_escape(voice_id)
    safe_lang = _xml_escape(language)
    rate_attr = ""
    speed = min(_SPEED_MAX, max(_SPEED_MIN, float(synthesis_speed)))
    if abs(speed - 1.0) >= 0.005:
        pct = round((speed - 1.0) * 100)
        sign = "+" if pct >= 0 else "-"
        rate_attr = f'<prosody rate="{sign}{abs(pct)}%">'
        rate_close = "</prosody>"
    else:
        rate_close = ""
    return (f'<speak version="1.0" '
            f'xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{safe_lang}">'
            f'<voice name="{safe_voice}">{rate_attr}{safe_text}'
            f'{rate_close}</voice></speak>')


@dataclass(frozen=True)
class AzureVoice:
    voice_id: str
    locale: str
    display_name: str

    def public(self) -> dict[str, str]:
        return {"voice_id": self.voice_id, "language": self.locale,
                "display_name": self.display_name}


def _parse_retry_after(value: str | None) -> float:
    """Retry-After 秒数或 HTTP 日期 → 等待秒数（解析失败回退 1s）。"""
    if not value:
        return 1.0
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return 1.0


class AzureTTS(TTSProvider):
    """Azure Speech REST（text-to-speech + voices list）段级同步实现。"""

    name = "azure"

    def __init__(self, *, key: str | None = None, region: str | None = None,
                 endpoint: str | None = None,
                 connect_timeout: float = 5.0, request_timeout: float = 25.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        from app.core.config import settings
        self._key = (key if key is not None else settings.azure_speech_key).strip()
        self._region = (region if region is not None
                        else settings.azure_speech_region).strip().lower()
        self._endpoint_cfg = (endpoint if endpoint is not None
                              else settings.azure_speech_endpoint).strip()
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._transport = transport

    # -- 配置与地址 --------------------------------------------------------

    def configured(self) -> bool:
        return bool(self._key and self._region)

    def _base_origin(self) -> str:
        """合成/voices list 共用的服务 origin（返回前完成全部校验）。"""
        if self._endpoint_cfg:
            parsed = httpx.URL(self._endpoint_cfg)
            if parsed.scheme != "https":
                raise TTSConfigError("AZURE_SPEECH_ENDPOINT 必须是 HTTPS")
            host = parsed.host
            if not host.endswith(_APPROVED_ENDPOINT_SUFFIX):
                raise TTSConfigError(
                    "AZURE_SPEECH_ENDPOINT 仅允许官方资源域 "
                    f"(*{_APPROVED_ENDPOINT_SUFFIX})")
            return f"https://{host}"
        if not _REGION_RE.match(self._region or ""):
            raise TTSConfigError("AZURE_SPEECH_REGION 含非法字符")
        return f"https://{self._region}.tts.speech.microsoft.com"

    def _synthesize_url(self) -> str:
        return f"{self._base_origin()}/cognitiveservices/v1"

    def _voices_url(self) -> str:
        return f"{self._base_origin()}/cognitiveservices/voices/list"

    def _require_config(self) -> None:
        if not self._key:
            raise TTSConfigError("未配置 AZURE_SPEECH_KEY，云端语音不可用")
        self._base_origin()  # 触发 endpoint/region 校验

    def _client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(self._request_timeout,
                                connect=self._connect_timeout)
        return httpx.AsyncClient(trust_env=False, timeout=timeout,
                                 transport=self._transport)

    # -- 合成 --------------------------------------------------------------

    async def synthesize(self, text: str, *, speed: float | None = None,
                         options: TTSOptions | None = None) -> TTSResult:
        if options is not None:
            voice_id = options.voice_id
            language = options.language
            synthesis_speed = options.synthesis_speed
        else:
            voice_id = ""
            language = "zh-CN"
            synthesis_speed = 1.0 if speed is None else float(speed)
        self._require_config()
        if voice_id:
            self._validate_voice_id(voice_id)
        ssml = build_ssml(text, voice_id=voice_id, language=language,
                          synthesis_speed=synthesis_speed)
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
            "User-Agent": _USER_AGENT,
        }
        deadline = monotonic() + self._request_timeout
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._client() as client:
                    resp = await client.post(self._synthesize_url(),
                                             content=ssml.encode("utf-8"),
                                             headers=headers)
            except httpx.HTTPError as exc:
                if attempt == 1 and monotonic() < deadline:
                    continue  # 网络异常最多重试一次
                raise TTSTransient(f"Azure 语音请求失败: {exc}") from exc
            status = resp.status_code
            if status == 200:
                return await self._decode(resp.content, provider=self.name,
                                          voice_id=voice_id)
            if status in (401, 403):
                raise TTSConfigError(
                    f"Azure 语音凭证被拒绝（{status}），请检查配置")
            if status == 429:
                if attempt == 1 and monotonic() < deadline:
                    # 遵守 Retry-After 并纳入总 deadline；重试恰好一次
                    wait = min(_parse_retry_after(
                                   resp.headers.get("Retry-After")),
                               max(0.0, deadline - monotonic()))
                    await asyncio.sleep(wait)
                    continue
                raise TTSRateLimited("Azure 语音限流，请稍后重试")
            if status >= 500:
                if attempt == 1 and monotonic() < deadline:
                    continue  # 5xx 最多重试一次
                raise TTSTransient(f"Azure 语音服务错误 {status}")
            if status == 400:
                raise TTSUnavailable(f"Azure 拒绝合成请求（400）: "
                                     f"{resp.text[:200]}")
            raise TTSUnavailable(f"Azure 语音请求错误 {status}: "
                                 f"{resp.text[:200]}")

    @staticmethod
    def _validate_voice_id(voice_id: str) -> None:
        # 只允许 Azure ShortName 形态（字母数字连字符），防任意 payload
        if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]+)*-[A-Za-z0-9]+$",
                        voice_id) or len(voice_id) > 64:
            raise TTSUnavailable(f"非法音色标识: {voice_id!r}")

    async def _decode(self, content: bytes, *, provider: str,
                      voice_id: str) -> TTSResult:
        if not content.startswith(b"RIFF"):
            raise TTSUnavailable("Azure 返回非 WAV 内容，拒绝伪成功")
        try:
            pcm, rate = await asyncio.to_thread(wav_to_pcm16, content)
            pcm = await asyncio.to_thread(normalize_pcm16, pcm, rate)
        except Exception as exc:
            raise TTSUnavailable(f"Azure 音频解码失败: {exc}") from exc
        return TTSResult(pcm16=pcm, sample_rate=rate,
                         provider=provider, voice_id=voice_id)

    # -- voices list ---------------------------------------------------------

    async def list_voices(self) -> list[AzureVoice]:
        """官方 voices list 的受控投影；无 key 零网络请求。"""
        self._require_config()
        try:
            async with self._client() as client:
                resp = await client.get(
                    self._voices_url(),
                    headers={"Ocp-Apim-Subscription-Key": self._key,
                             "User-Agent": _USER_AGENT})
        except httpx.HTTPError as exc:
            raise TTSTransient(f"Azure voices list 请求失败: {exc}") from exc
        if resp.status_code in (401, 403):
            raise TTSConfigError(f"Azure voices list 被拒绝（{resp.status_code}）")
        if resp.status_code != 200:
            raise TTSTransient(f"Azure voices list 错误 {resp.status_code}")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise TTSTransient("Azure voices list 返回非 JSON") from exc
        if not isinstance(raw, list):
            raise TTSTransient("Azure voices list 结构异常")
        voices: list[AzureVoice] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            short = str(item.get("ShortName") or "").strip()
            if not short:
                continue
            voices.append(AzureVoice(
                voice_id=short,
                locale=str(item.get("Locale") or "").strip(),
                display_name=str(item.get("DisplayName") or short).strip()))
        return voices

    def capabilities(self) -> TTSCapabilities:
        from app.core.config import settings
        return TTSCapabilities(
            languages=("zh-CN", "en-US"),
            voices=(settings.classroom_tts_voice_zh,
                    settings.classroom_tts_voice_en),
            speed_range=(_SPEED_MIN, _SPEED_MAX),
            has_word_boundaries=False,
            max_text_chars=1000,
        )
