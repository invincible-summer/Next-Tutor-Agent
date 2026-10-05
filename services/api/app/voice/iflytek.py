"""科大讯飞在线语音 WebSocket 适配器。

协议依据讯飞在线语音听写/合成文档：握手 URL 使用 ``host date
request-line`` 的 HMAC-SHA256 签名；业务帧始终由服务端构造，密钥不进入
客户端或公开能力响应。适配器不在导入时建立网络连接，便于无密钥部署。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .stt.base import (STTCapabilities, STTConfigError, STTProvider, STTResult,
                       STTTransient, STTUnavailable)
from .base import (TTSConfigError, TTSCapabilities, TTSOptions, TTSProvider,
                   TTSResult, TTSTransient, TTSUnavailable)
from .wav import wav_to_pcm16

log = logging.getLogger(__name__)

_APPROVED_SUFFIX = ".xfyun.cn"
_DEFAULT_STT = "wss://iat-api.xfyun.cn/v2/iat"
_DEFAULT_TTS = "wss://tts-api.xfyun.cn/v2/tts"


def build_auth_url(endpoint: str, *, api_key: str, api_secret: str,
                   now: datetime | None = None) -> str:
    """Build the documented xfyun HMAC URL without logging credentials."""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"wss", "ws"} or not parsed.hostname:
        raise TTSConfigError("讯飞 endpoint 必须是 ws(s) URL")
    if not parsed.hostname.endswith(_APPROVED_SUFFIX):
        raise TTSConfigError("讯飞 endpoint 仅允许官方 xfyun.cn 域名")
    if not api_key or not api_secret:
        raise TTSConfigError("未配置 IFLYTEK_API_KEY/API_SECRET")
    date = (now or datetime.now(timezone.utc)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    host = parsed.netloc
    path = parsed.path or "/"
    origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).digest()
    ).decode()
    authorization = base64.b64encode(
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'.encode()
    ).decode()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"authorization": authorization, "date": date, "host": host})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _connect(url: str):
    import websockets
    # websockets 14+ renamed extra_headers to additional_headers.
    return websockets.connect(url, open_timeout=10, close_timeout=5)


def _language(language: str) -> str:
    return "en_us" if (language or "").lower().startswith("en") else "cn"


class IflytekTTS(TTSProvider):
    name = "iflytek"

    def __init__(self, *, app_id: str | None = None, api_key: str | None = None,
                 api_secret: str | None = None, endpoint: str | None = None,
                 connector: Callable[[str], Any] | None = None):
        from app.core.config import settings
        self.app_id = (app_id if app_id is not None else settings.iflytek_app_id).strip()
        self.api_key = (api_key if api_key is not None else settings.iflytek_api_key).strip()
        self.api_secret = (api_secret if api_secret is not None else settings.iflytek_api_secret).strip()
        self.endpoint = (endpoint if endpoint is not None else settings.iflytek_tts_endpoint or _DEFAULT_TTS).strip()
        self.connector = connector or _connect

    def _require_config(self) -> None:
        if not self.app_id or not self.api_key or not self.api_secret:
            raise TTSConfigError("未配置 IFLYTEK_APP_ID/API_KEY/API_SECRET")

    async def synthesize(self, text: str, *, speed: float | None = None,
                         options: TTSOptions | None = None) -> TTSResult:
        self._require_config()
        opts = options or TTSOptions(language="zh-CN")
        voice_id = opts.voice_id or ("xiaoyan" if opts.language.startswith("zh") else "x2_catherine")
        rate = max(0, min(100, round((float(opts.synthesis_speed if options else (speed or 1.0)) - .5) * 66.7)))
        payload = {"common": {"app_id": self.app_id},
                   "business": {"aue": "raw", "auf": "audio/L16;rate=16000",
                                 "vcn": voice_id, "tte": "utf8", "speed": rate},
                   "data": {"status": 2, "text": base64.b64encode(text.encode()).decode()}}
        try:
            async with self.connector(build_auth_url(self.endpoint, api_key=self.api_key,
                                                      api_secret=self.api_secret)) as ws:
                await ws.send(json.dumps(payload, ensure_ascii=False))
                chunks: list[bytes] = []
                async for raw in ws:
                    msg = json.loads(raw)
                    code = int(msg.get("code", 0) or msg.get("data", {}).get("code", 0) or 0)
                    if code:
                        raise TTSUnavailable(f"讯飞合成失败（{code}）")
                    data = msg.get("data") or {}
                    if data.get("audio"):
                        chunks.append(base64.b64decode(data["audio"]))
                    if int(data.get("status", 0)) == 2:
                        break
        except (TTSConfigError, TTSUnavailable):
            raise
        except Exception as exc:
            raise TTSTransient(f"讯飞合成请求失败: {exc}") from exc
        if not chunks:
            raise TTSUnavailable("讯飞合成返回空音频")
        return TTSResult(pcm16=b"".join(chunks), sample_rate=16000,
                         provider=self.name, voice_id=voice_id)

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(languages=("zh-CN", "en-US"),
                                voices=("xiaoyan", "x2_catherine"),
                                speed_range=(0.5, 2.0), max_text_chars=2000)


class IflytekSTT(STTProvider):
    name = "iflytek"

    def __init__(self, *, app_id: str | None = None, api_key: str | None = None,
                 api_secret: str | None = None, endpoint: str | None = None,
                 connector: Callable[[str], Any] | None = None):
        from app.core.config import settings
        self.app_id = (app_id if app_id is not None else settings.iflytek_app_id).strip()
        self.api_key = (api_key if api_key is not None else settings.iflytek_api_key).strip()
        self.api_secret = (api_secret if api_secret is not None else settings.iflytek_api_secret).strip()
        self.endpoint = (endpoint if endpoint is not None else settings.iflytek_stt_endpoint or _DEFAULT_STT).strip()
        self.connector = connector or _connect

    def _require_config(self) -> None:
        if not self.app_id or not self.api_key or not self.api_secret:
            raise STTConfigError("未配置 IFLYTEK_APP_ID/API_KEY/API_SECRET")

    async def transcribe(self, audio: bytes, *, content_type: str = "",
                         language: str = "") -> STTResult:
        self._require_config()
        media = (content_type or "").split(";", 1)[0].strip().lower()
        if media not in {"audio/pcm", "audio/raw", "audio/wav", "audio/x-wav"}:
            raise STTUnavailable("讯飞听写要求 PCM（16 kHz/16-bit/mono）音频")
        if media in {"audio/wav", "audio/x-wav"}:
            try:
                audio, rate = wav_to_pcm16(audio)
                if rate != 16000:
                    raise STTUnavailable("讯飞听写仅接受 16 kHz WAV")
            except STTUnavailable:
                raise
            except Exception as exc:
                raise STTUnavailable("讯飞 WAV 解码失败") from exc
        payload = {"common": {"app_id": self.app_id},
                   "business": {"language": _language(language), "domain": "iat",
                                 "accent": "mandarin", "vinfo": 0, "dwa": "wpgs"},
                   "data": {"status": 0, "format": "audio/L16;rate=16000",
                            "encoding": "raw", "audio": base64.b64encode(audio).decode()}}
        text_parts: list[str] = []
        try:
            async with self.connector(build_auth_url(self.endpoint, api_key=self.api_key,
                                                      api_secret=self.api_secret)) as ws:
                await ws.send(json.dumps(payload))
                await ws.send(json.dumps({"data": {"status": 2, "format": "audio/L16;rate=16000",
                                                    "encoding": "raw", "audio": ""}}))
                async for raw in ws:
                    msg = json.loads(raw)
                    if int(msg.get("code", 0) or 0):
                        raise STTUnavailable(f"讯飞听写失败（{msg.get('code')}）")
                    for item in (msg.get("data", {}).get("result", {}).get("ws", []) or []):
                        for candidate in item.get("cw", []) or []:
                            if candidate.get("w"):
                                text_parts.append(str(candidate["w"]))
                    if int(msg.get("data", {}).get("status", 0)) == 2:
                        break
        except (STTConfigError, STTUnavailable):
            raise
        except Exception as exc:
            raise STTTransient(f"讯飞听写请求失败: {exc}") from exc
        text = "".join(text_parts).strip()
        if not text:
            raise STTUnavailable("讯飞听写返回空文本")
        return STTResult(text=text, language="en-US" if _language(language) == "en_us" else "zh-CN",
                         provider=self.name)

    def capabilities(self) -> STTCapabilities:
        return STTCapabilities(languages=("zh-CN", "en-US"),
                                formats=("audio/pcm", "audio/raw", "audio/wav", "audio/x-wav"),
                                max_duration_seconds=60, max_audio_bytes=10 * 1024 * 1024)
