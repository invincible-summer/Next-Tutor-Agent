"""Server-mediated speech API（ADR-0012）。

移动端语音流（ADR-0012）：录音 → ``POST /speech/transcriptions``（服务端
Azure、讯飞或 Deepgram STT，凭证只在服务器）→ transcript（发送前可取消/重录）→ 既有 chat stream → 按句 ``POST /speech/synthesis`` → expo-audio 播放。
旧 ``/voice/ws``（浏览器 SpeechRecognition + WS 通话）保留不动，Web 端
继续走原路径。

边界纪律：
- 转写不落存储：音频在内存中校验（格式白名单/大小/时长声明）后调用
  provider，原始音频与 provider 内部信息绝不回传。
- 合成只接受受控参数（text/language/voice/speed），音色仅来自管理员
  批准集合（复用 ``tts/service.resolve_tts_profile``，不复制判定）；
  输出 WAV，客户端直接播放。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

from fastapi import (APIRouter, Depends, File, Form, HTTPException,
                     Response, UploadFile)
from pydantic import BaseModel, Field

from app.core.ratelimit import rate_limit
from app.identity.deps import require_user
from app.identity.models import User
from app.voice.base import TTSOptions, VoiceProviderError

router = APIRouter(prefix="/speech", tags=["speech"],
                   dependencies=[Depends(require_user)])

# 移动端逐 utterance 上传；一次转写一条语音，20/min 覆盖连说场景
_TRANSCRIPTION_RATE = 20
# 合成按句请求（按句请求），一段回答约 10-30 句
_SYNTHESIS_RATE = 60


class SpeechCapabilities(BaseModel):
    """能力只读投影：STT（服务端转写）+ synthesis（受控合成）。"""

    model_config = {"extra": "forbid"}

    stt: dict[str, object]
    synthesis: dict[str, object]


class TranscriptionResponse(BaseModel):
    """一次转写的公开结果；provider 只暴露类别名，不含凭证细节。"""

    model_config = {"extra": "forbid"}

    text: str
    language: str
    duration_ms: int
    provider_class: str


class SynthesisRequest(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=2000)
    language: Literal["zh", "en"] = "zh"
    voice_id: str = Field(default="", max_length=128)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    policy: Literal["auto", "cloud", "local"] = "auto"
    allow_local_fallback: bool = True


@router.get("/capabilities", response_model=SpeechCapabilities)
def get_speech_capabilities(user: User = Depends(require_user)) -> SpeechCapabilities:
    """服务端转写与合成能力（零网络请求、零凭证回显）。"""
    from app.voice.stt import service as stt_service
    from app.voice.tts.service import tts_capabilities
    return SpeechCapabilities(stt=stt_service.stt_capabilities(),
                              synthesis=tts_capabilities())


@router.post("/transcriptions", response_model=TranscriptionResponse,
             dependencies=[Depends(rate_limit("speech_transcriptions",
                                              _TRANSCRIPTION_RATE))])
async def create_transcription(
    file: UploadFile = File(...),
    duration_ms: int = Form(..., ge=1),
    language: str = Form("zh"),
    user: User = Depends(require_user),
) -> TranscriptionResponse:
    """One-utterance audio → transcript（服务端校验后才调用 provider）。"""
    from app.voice.stt import service as stt_service
    if stt_service.stt_provider() is None:
        # 服务端转写整体不可用（off/未配置）先于格式校验上报
        raise HTTPException(503, detail={"code": "stt_unavailable"})
    limits = stt_service.stt_limits()
    content_type = (file.content_type or "").strip()
    media = content_type.split(";")[0].strip().lower()
    if not any(f.split(";")[0] == media for f in limits.formats):
        raise HTTPException(415, detail={"code": "audio_format_rejected"})
    if duration_ms > limits.max_duration_seconds * 1000:
        raise HTTPException(400, detail={"code": "audio_too_long"})
    raw = await file.read(limits.max_audio_bytes + 1)
    if len(raw) > limits.max_audio_bytes:
        raise HTTPException(400, detail={"code": "audio_too_large"})
    if not raw:
        raise HTTPException(400, detail={"code": "audio_empty"})
    try:
        result = await stt_service.transcribe(raw, content_type=content_type,
                                              language=language)
    except VoiceProviderError as exc:
        status = 429 if exc.code == "stt_rate_limited" else 503
        raise HTTPException(status, detail={"code": exc.code}) from None
    return TranscriptionResponse(text=result.text, language=result.language,
                                 duration_ms=result.duration_ms,
                                 provider_class=result.provider)


@router.post(
    "/synthesis",
    dependencies=[Depends(rate_limit("speech_synthesis", _SYNTHESIS_RATE))],
    responses={200: {"description": "Synthesized utterance audio",
                     "content": {"audio/wav": {
                         "schema": {"type": "string",
                                    "format": "binary"}}}}},
)
async def synthesize_speech(body: SynthesisRequest,
                            user: User = Depends(require_user)) -> Response:
    """受控文本 → WAV 音频（音色仅限管理员批准集合）。"""
    from app.classroom.audio import pcm16_to_wav
    from app.voice.tts.service import (cloud_synthesize, local_synthesize,
                                       resolve_tts_profile)
    profile = resolve_tts_profile(
        "speech",
        SimpleNamespace(voice_policy=body.policy, voice_id=body.voice_id,
                        allow_local_fallback=body.allow_local_fallback),
        body.language)
    if not profile.provider or not profile.voice_id:
        raise HTTPException(503, detail={"code": "tts_unavailable"})
    options = TTSOptions(voice_id=profile.voice_id,
                         language=profile.language,
                         synthesis_speed=body.speed)
    try:
        if profile.provider in {"azure", "iflytek", "deepgram"}:
            result = await cloud_synthesize(body.text, options)
        else:
            result = await local_synthesize(body.text, options)
    except VoiceProviderError as exc:
        status = 429 if exc.code == "tts_rate_limited" else 503
        raise HTTPException(status, detail={"code": exc.code}) from None
    wav = pcm16_to_wav(result.pcm16, result.sample_rate)
    return Response(content=wav, media_type="audio/wav",
                    headers={"X-Sample-Rate": str(result.sample_rate),
                             "X-Voice-Id": result.voice_id,
                             "Cache-Control": "no-store"})
