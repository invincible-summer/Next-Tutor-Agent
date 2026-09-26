"""课堂能力探测（plan.md §14.1 GET /classroom/capabilities）。

只读配置与本地构建产物，不触发外部计费、不发网络请求。voices 列表由
TTS service（F 阶段）缓存填充；此处只给静态默认候选是否可用的判断依据。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.config import settings
from ..schemas import classroom as sc
from . import limits

_RENDERER_ASSETS_DIR = Path(__file__).resolve().parent / "static" / "generated"


def _renderer_capability() -> sc.RendererCapability:
    manifest_path = _RENDERER_ASSETS_DIR / "manifest.json"
    if not manifest_path.is_file():
        return sc.RendererCapability(
            available=False, reason="renderer_unavailable",
            renderer_version="")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return sc.RendererCapability(available=False,
                                     reason="renderer_unavailable")
    version = str(manifest.get("runtime_version") or
                  manifest.get("version") or "")
    if not version:
        return sc.RendererCapability(available=False,
                                     reason="renderer_unavailable")
    return sc.RendererCapability(available=True, renderer_version=version)


def _research_capability() -> sc.ServiceCapability:
    configured = bool(settings.tavily_api_key)
    if not configured:
        return sc.ServiceCapability(configured=False, available=False,
                                    reason="未配置联网检索服务凭证")
    return sc.ServiceCapability(configured=True, available=True)


def _images_capability() -> sc.ServiceCapability:
    providers = [p.strip() for p in settings.classroom_image_providers.split(",")
                 if p.strip()]
    configured = any(
        (p == "pexels" and settings.pexels_api_key)
        or (p == "pixabay" and settings.pixabay_api_key)
        for p in providers)
    if not configured:
        return sc.ServiceCapability(configured=False, available=False,
                                    reason="未配置图片服务凭证")
    return sc.ServiceCapability(configured=True, available=True)


def _local_tts_enabled() -> bool:
    from ..voice.tts import service as tts_service
    return tts_service.local_tts_enabled()


def _tts_voices(cloud_configured: bool, local_enabled: bool) -> list[sc.VoiceInfo]:
    """允许音色（§14.1）：管理员批准集合 ∩ voices 缓存佐证；零网络请求。

    缓存未刷新时按部署校验的配置返回默认候选；本地启用时附 melo 音色。
    """
    from ..voice.tts import service as tts_service
    voices: list[sc.VoiceInfo] = []
    if cloud_configured:
        approved = tts_service.approved_voices()
        cached = tts_service.cached_voices()
        cached_ids = {v.get("voice_id") for v in cached}
        display = {v.get("voice_id"): v.get("display_name")
                   for v in cached}
        for voice_id, locale in approved.items():
            if cached_ids and voice_id not in cached_ids:
                continue  # 资源实际不支持该音色（缓存佐证）
            voices.append(sc.VoiceInfo(
                voice_id=voice_id, language=locale,
                display_name=display.get(voice_id) or voice_id))
    if local_enabled:
        voices.append(sc.VoiceInfo(voice_id="melo-zh", language="zh-CN",
                                   display_name="本地语音（MeloTTS）"))
    return voices


def _tts_capability() -> sc.TtsCapability:
    from ..voice.tts import service as tts_service
    cloud_configured = tts_service.azure_available()
    local_enabled = _local_tts_enabled()
    available = cloud_configured or local_enabled \
        or settings.classroom_tts_policy == "silent"
    reason = ""
    if not available:
        reason = "未配置云端语音且本地语音未启用（文字课堂）"
    elif not cloud_configured and settings.classroom_tts_policy == "cloud":
        reason = "云端语音未配置"
    return sc.TtsCapability(
        configured=cloud_configured or local_enabled,
        available=available, reason=reason,
        policy=sc.VoicePolicy(settings.classroom_tts_policy),
        local_enabled=local_enabled,
        voices=_tts_voices(cloud_configured, local_enabled))


def user_allowed(student_id: str) -> tuple[bool, str]:
    """总闸 + 灰度 allowlist + 游客策略（§20.4）。"""
    if not settings.classroom_enabled:
        return False, "课堂功能未开放"
    allowlist = {u.strip() for u in settings.classroom_allowed_users.split(",")
                 if u.strip()}
    if allowlist and student_id not in allowlist:
        return False, "课堂功能对该账号未开放"
    from ..agents.student_model.store import DEFAULT_STUDENT_ID
    if student_id == DEFAULT_STUDENT_ID and not settings.classroom_allow_guest:
        return False, "游客账号不能使用课堂功能"
    return True, ""


def build_capabilities(student_id: str) -> sc.ClassroomCapabilities:
    enabled, reason = user_allowed(student_id)
    return sc.ClassroomCapabilities(
        enabled=enabled,
        allow_guest=settings.classroom_allow_guest,
        renderer=_renderer_capability(),
        research=_research_capability(),
        images=_images_capability(),
        tts=_tts_capability(),
        limits=sc.ClassroomLimits(
            max_pages=limits.MAX_PAGES,
            max_duration_minutes=limits.MAX_DURATION_MINUTES,
            max_published_revisions=limits.MAX_PUBLISHED_REVISIONS,
            max_image_searches=limits.IMAGE_SEARCH_BUDGET,
            max_images=limits.IMAGE_COUNT_MAX,
            daily_tts_chars=limits.TTS_DAILY_CLOUD_CHARS,
            generation_daily_limit=limits.GENERATION_DAILY_COUNT,
            audio_cache_mb=limits.AUDIO_OWNER_MB,
        ),
    )
