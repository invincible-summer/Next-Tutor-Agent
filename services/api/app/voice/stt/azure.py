"""Azure Speech REST STT 适配器（服务端转写首发实现，ADR-0012）。

与 ``tts/azure.py`` 同一套纪律：
- 凭证只来自服务器配置 ``AZURE_SPEECH_KEY``/``AZURE_SPEECH_REGION``；
  可选官方 resource endpoint（``AZURE_SPEECH_ENDPOINT``）经 HTTPS 与
  批准域校验，普通用户/客户端不能携带任意 base URL，移动端永远拿不到
  Speech secret（§14.4）。
- 请求头 ``Ocp-Apim-Subscription-Key``、``Accept: application/json``、
  受控 ``Content-Type``（服务端白名单，不接受任意 codec 字符串）。
- 错误分类：401/403 → 配置错误不重试；429 → 遵守 Retry-After 纳入总
  deadline 单次重试；5xx/网络异常最多重试一次；``RecognitionStatus`` 非
  Success 或 JSON 解析失败绝不伪成功（空 transcript 按不可用上报）。

识别端点与响应结构参见：
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text
"""
from __future__ import annotations

import asyncio
import logging
import re
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from .base import (STTCapabilities, STTConfigError, STTProvider, STTRateLimited,
                   STTResult, STTTransient, STTUnavailable)

log = logging.getLogger(__name__)

PROVIDER_VERSION = "azure-rest-stt-1"
_USER_AGENT = "edu-agent-speech/1.0"
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}[a-z0-9]$")
# 与 tts/azure.py 相同的官方自定义域批准后缀（ADR-0012 批准域）
_APPROVED_ENDPOINT_SUFFIX = ".api.cognitiveservices.azure.com"

# 服务端接受的音频上传格式（client 不可扩展）；WebM/OGG 统一 Opus。
_APPROVED_FORMATS = (
    "audio/wav",
    "audio/x-wav",
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/mpeg",
    "audio/mp4",
)

# simple 格式响应：RecognitionStatus / DisplayText / Offset / Duration；
# Duration 与 Offset 的单位是 100ns tick。
_TICKS_PER_MS = 10_000.0


def normalize_language(language: str) -> str:
    """客户端语言提示 → 受支持 BCP-47；未知值回落 zh-CN（产品主语言）。"""
    lang = (language or "").strip().lower()
    if lang.startswith("en"):
        return "en-US"
    return "zh-CN"


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


class AzureSTT(STTProvider):
    """Azure Speech REST（speech-to-text）一次性整段转写实现。"""

    name = "azure"

    def __init__(self, *, key: str | None = None, region: str | None = None,
                 endpoint: str | None = None,
                 connect_timeout: float = 5.0, request_timeout: float = 30.0,
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
        """转写服务 origin（返回前完成全部校验）。"""
        if self._endpoint_cfg:
            parsed = httpx.URL(self._endpoint_cfg)
            if parsed.scheme != "https":
                raise STTConfigError("AZURE_SPEECH_ENDPOINT 必须是 HTTPS")
            host = parsed.host
            if not host.endswith(_APPROVED_ENDPOINT_SUFFIX):
                raise STTConfigError(
                    "AZURE_SPEECH_ENDPOINT 仅允许官方资源域 "
                    f"(*{_APPROVED_ENDPOINT_SUFFIX})")
            return f"https://{host}"
        if not _REGION_RE.match(self._region or ""):
            raise STTConfigError("AZURE_SPEECH_REGION 含非法字符")
        return f"https://{self._region}.stt.speech.microsoft.com"

    def _recognition_url(self, language: str) -> str:
        return (f"{self._base_origin()}/speech/recognition/conversation/"
                f"cognitiveservices/v1?language={language}&format=simple")

    def _require_config(self) -> None:
        if not self._key:
            raise STTConfigError("未配置 AZURE_SPEECH_KEY，云端转写不可用")
        self._base_origin()  # 触发 endpoint/region 校验

    def _client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(self._request_timeout,
                                connect=self._connect_timeout)
        return httpx.AsyncClient(trust_env=False, timeout=timeout,
                                 transport=self._transport)

    # -- 转写 --------------------------------------------------------------

    async def transcribe(self, audio: bytes, *, content_type: str = "",
                         language: str = "") -> STTResult:
        self._require_config()
        media_type = (content_type or "").split(";")[0].strip().lower()
        approved = next((f for f in _APPROVED_FORMATS
                         if f.split(";")[0] == media_type), "")
        if not approved:
            raise STTUnavailable(f"不支持的音频格式: {content_type!r}")
        lang = normalize_language(language)
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": approved,
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        deadline = monotonic() + self._request_timeout
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._client() as client:
                    resp = await client.post(self._recognition_url(lang),
                                             content=audio, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == 1 and monotonic() < deadline:
                    continue  # 网络异常最多重试一次
                raise STTTransient(f"Azure 转写请求失败: {exc}") from exc
            status = resp.status_code
            if status == 200:
                return self._parse_result(resp, language=lang)
            if status in (401, 403):
                raise STTConfigError(
                    f"Azure 转写凭证被拒绝（{status}），请检查配置")
            if status == 429:
                if attempt == 1 and monotonic() < deadline:
                    # 遵守 Retry-After 并纳入总 deadline；重试恰好一次
                    wait = min(_parse_retry_after(
                                   resp.headers.get("Retry-After")),
                               max(0.0, deadline - monotonic()))
                    await asyncio.sleep(wait)
                    continue
                raise STTRateLimited("Azure 转写限流，请稍后重试")
            if status >= 500:
                if attempt == 1 and monotonic() < deadline:
                    continue  # 5xx 最多重试一次
                raise STTTransient(f"Azure 转写服务错误 {status}")
            raise STTUnavailable(f"Azure 转写请求错误 {status}: "
                                 f"{resp.text[:200]}")

    def _parse_result(self, resp: httpx.Response, *, language: str) -> STTResult:
        """simple 响应 → 公开结果；非 Success/无文本绝不伪成功。"""
        try:
            payload = resp.json()
        except ValueError as exc:
            raise STTUnavailable("Azure 转写返回非 JSON") from exc
        if not isinstance(payload, dict):
            raise STTUnavailable("Azure 转写响应结构异常")
        status = str(payload.get("RecognitionStatus") or "").strip()
        if status != "Success":
            raise STTUnavailable(f"Azure 转写未成功（{status or 'unknown'}）")
        text = str(payload.get("DisplayText") or "").strip()
        if not text:
            raise STTUnavailable("Azure 转写返回空文本")
        try:
            duration_ms = int(float(payload.get("Duration") or 0)
                              / _TICKS_PER_MS)
        except (TypeError, ValueError):
            duration_ms = 0
        return STTResult(text=text, language=language,
                         duration_ms=max(0, duration_ms),
                         provider=self.name)

    def capabilities(self) -> STTCapabilities:
        return STTCapabilities(
            languages=("zh-CN", "en-US"),
            formats=_APPROVED_FORMATS,
            max_duration_seconds=60,
            max_audio_bytes=10 * 1024 * 1024)
