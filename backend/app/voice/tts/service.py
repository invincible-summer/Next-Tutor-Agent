"""统一 TTS service（plan.md §11.1/§11.2，阶段 F）。

职责：
- 集中 provider 的配置、能力、健康与并发管理；旧电话 factory
  ``get_tts_provider()`` 无参接口委托到这里，行为不变（off/stub/melo），
  新增 azure/auto（电话也可用云端语音，但不升级强开）。
- 跨电话与课堂的共享并发保护：云端全局并发 = ``settings.
  classroom_tts_cloud_concurrency``（默认 2），Melo 全局并发 = 1。旧电话
  单连接 worker 只保证了单连接串行，不是跨用户保护；这里以 semaphore
  补齐（§11.4）。
- 课堂音色解析 ``resolve_classroom_tts``：run 显式 > 个人偏好 > 实例默认；
  返回不可变 profile，绝不修改全局 factory 实例的音色。
- Azure voices list 缓存与音色 allowlist：管理员配置的
  ``CLASSROOM_TTS_VOICE_ZH/EN`` 是唯一批准音色集合；缓存由显式刷新填充
  （lifespan 启动后台任务/部署脚本），GET capability 永不发网络请求。

并发原语按事件循环缓存：uvicorn 单循环；unittest 的 ``asyncio.run`` 每
用例新建循环，旧循环的 semaphore 必须失效重建，否则
``attached to a different loop`` 崩测试。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from ..base import (TTSOptions, TTSProvider, TTSResult, TTSUnavailable)
from .stub import StubTTS

log = logging.getLogger(__name__)

# voices list 缓存 TTL（§11.6：健康/能力读缓存，不每次打上游）
VOICES_CACHE_TTL = 3600.0


# ---------------------------------------------------------------------------
# 共享 semaphore（跨电话与课堂；按事件循环惰性创建）
# ---------------------------------------------------------------------------

_SEM_GUARD = threading.Lock()
_SEMS_BY_LOOP: dict[int, tuple[Any, asyncio.Semaphore, asyncio.Semaphore]] = {}


def shared_semaphores() -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
    """(cloud, melo) 共享并发闸；调用方必须在事件循环内。"""
    from app.core.config import settings
    loop = asyncio.get_running_loop()
    with _SEM_GUARD:
        for key in [k for k, (l, _, _) in _SEMS_BY_LOOP.items()
                    if l.is_closed()]:
            _SEMS_BY_LOOP.pop(key, None)
        entry = _SEMS_BY_LOOP.get(id(loop))
        if entry is None:
            entry = (loop,
                     asyncio.Semaphore(
                         max(1, settings.classroom_tts_cloud_concurrency)),
                     asyncio.Semaphore(1))  # Melo 单模型全局 1（§11.4）
            _SEMS_BY_LOOP[id(loop)] = entry
        return entry[1], entry[2]


class _SemaphoreGuard(TTSProvider):
    """并发守卫包装：委托内部 provider，acquire 期间串行。"""

    def __init__(self, inner: TTSProvider, pick: Callable[
            [], asyncio.Semaphore]):
        self._inner = inner
        self._pick = pick
        self.name = inner.name

    async def synthesize(self, text: str, *, speed: float | None = None,
                         options: TTSOptions | None = None) -> TTSResult:
        sem = self._pick()
        async with sem:
            return await self._inner.synthesize(text, speed=speed,
                                                options=options)

    def capabilities(self):
        return self._inner.capabilities()


def _cloud_guard(provider: TTSProvider) -> TTSProvider:
    return _SemaphoreGuard(provider, lambda: shared_semaphores()[0])


def _melo_guard(provider: TTSProvider) -> TTSProvider:
    return _SemaphoreGuard(provider, lambda: shared_semaphores()[1])


# ---------------------------------------------------------------------------
# 旧电话 factory（无参兼容；缓存语义与原实现一致）
# ---------------------------------------------------------------------------

_PHONE_INSTANCE: TTSProvider | None = None
_PHONE_KEY: tuple | None = None


def _phone_key() -> tuple:
    from app.core.config import settings
    return (settings.voice_tts_provider, settings.voice_tts_base_url,
            settings.voice_tts_speed,
            bool(settings.azure_speech_key), settings.azure_speech_region,
            settings.azure_speech_endpoint)


def _azure_available() -> bool:
    from app.core.config import settings
    return bool(settings.azure_speech_key and settings.azure_speech_region)


def azure_available() -> bool:
    """云端语音是否已配置（key+region 齐备）。"""
    return _azure_available()


def phone_provider() -> TTSProvider | None:
    """旧电话 provider（off|stub|melo|azure|auto）；失败关闭不崩溃。"""
    global _PHONE_INSTANCE, _PHONE_KEY
    from app.core.config import settings
    provider = (settings.voice_tts_provider or "off").strip().lower()
    if provider == "off":
        return None
    key = _phone_key()
    if _PHONE_INSTANCE is not None and _PHONE_KEY == key:
        return _PHONE_INSTANCE
    try:
        if provider == "stub":
            client: TTSProvider | None = StubTTS()
        elif provider == "melo":
            from .melotts import MeloTTS
            client = _melo_guard(MeloTTS())
        elif provider == "azure":
            if not _azure_available():
                log.warning("VOICE_TTS_PROVIDER=azure 但未配置 AZURE_SPEECH_*，语音 TTS 关闭")
                client = None
            else:
                from .azure import AzureTTS
                client = _cloud_guard(AzureTTS())
        elif provider == "auto":
            # 云端优先：有凭证走 azure；否则本地 melo；都不行则关闭
            if _azure_available():
                from .azure import AzureTTS
                client = _cloud_guard(AzureTTS())
            elif settings.voice_tts_provider:
                from .melotts import MeloTTS
                client = _melo_guard(MeloTTS())
            else:
                client = None
        else:
            log.warning("未知 VOICE_TTS_PROVIDER=%r，语音 TTS 关闭", provider)
            client = None
    except Exception as exc:
        log.warning("语音 TTS provider 初始化失败，TTS 关闭: %s", exc)
        _PHONE_INSTANCE = None
        _PHONE_KEY = key
        return None
    _PHONE_INSTANCE = client
    _PHONE_KEY = key
    return client


# ---------------------------------------------------------------------------
# 课堂 profile 解析（§11.1 有效选择顺序 + §11.3 音色 allowlist）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClassroomVoiceProfile:
    """不可变课堂语音档案；一次 run 期间不再变（切换走 audio-profile 接口）。"""

    policy: str            # 请求策略 auto/cloud/local/silent
    provider: str          # 实际解析结果："azure" | "melo" | ""（silent）
    voice_id: str          # "" 表示该语言无可用音色（合成时报 voice_unavailable）
    language: str          # BCP-47（zh-CN / en-US）
    synthesis_speed: float = 1.0   # 课堂合成基准固定 1.0（§11.4）
    allow_local_fallback: bool = True
    cloud_configured: bool = False
    local_enabled: bool = False
    local_locked: bool = False      # 云端失败后该 run 已锁定本地


def local_tts_enabled() -> bool:
    """课堂本地语音是否启用：显式 CLASSROOM_LOCAL_TTS_ENABLED 优先，
    否则继承 VOICE_TTS_PROVIDER 是否为 melo。"""
    from app.core.config import settings
    if settings.classroom_local_tts_enabled is not None:
        return settings.classroom_local_tts_enabled
    return settings.voice_tts_provider == "melo"


def approved_voices() -> dict[str, str]:
    """管理员批准音色 → locale（§11.3 allowlist；普通用户不可扩）。"""
    from app.core.config import settings
    return {settings.classroom_tts_voice_zh: "zh-CN",
            settings.classroom_tts_voice_en: "en-US"}


def _locale_for_language(language: str) -> str:
    return "en-US" if str(language).startswith("en") else "zh-CN"


def resolve_classroom_tts(prefs: Any | None,
                          language: str = "zh") -> ClassroomVoiceProfile:
    """课堂有效选择顺序：run 显式 > 个人偏好 > 实例默认（§11.1）。

    - auto：可用且已配置云端 → 已启用本地 → silent
    - cloud：云端；仅 ``allow_local_fallback=true`` 且本地可用才回退本地
    - local：仅本地 → silent
    - 无配置显示"文字课堂"，不伪装成本地语音成功
    音色只从管理员批准集合选，且必须匹配课件语言（禁止中文音色硬读英文）。
    """
    from app.core.config import settings
    raw_policy = getattr(prefs, "policy", None)
    policy = str(getattr(raw_policy, "value", raw_policy)
                 or settings.classroom_tts_policy).strip().lower()
    allow_fallback = bool(getattr(prefs, "allow_local_fallback", True))
    locale = _locale_for_language(language)
    cloud_ok = _azure_available()
    local_ok = local_tts_enabled()

    provider = ""
    if policy == "auto":
        provider = "azure" if cloud_ok else ("melo" if local_ok else "")
    elif policy == "cloud":
        if cloud_ok:
            provider = "azure"
        elif allow_fallback and local_ok:
            provider = "melo"
    elif policy == "local":
        provider = "melo" if local_ok else ""

    voice_id = ""
    if provider == "azure":
        voice_id = _select_azure_voice(prefs, locale)
    elif provider == "melo":
        # MeloTTS-Chinese 单音色；英文课件本地回退不可用 → 文字模式
        if locale == "zh-CN":
            from .melotts import MeloTTS
            voice_id = MeloTTS.VOICE_ID
        else:
            provider = ""

    return ClassroomVoiceProfile(
        policy=policy, provider=provider, voice_id=voice_id,
        language=locale, synthesis_speed=1.0,
        allow_local_fallback=allow_fallback,
        cloud_configured=cloud_ok, local_enabled=local_ok)


def _select_azure_voice(prefs: Any | None, locale: str) -> str:
    """批准音色中选一个：显式偏好（须批准且同语言）→ 语言默认 → 缓存校验。"""
    from app.core.config import settings
    approved = approved_voices()
    explicit = str(getattr(prefs, "voice_id", "") or "")
    if explicit and approved.get(explicit) == locale:
        return explicit
    default = (settings.classroom_tts_voice_zh if locale == "zh-CN"
               else settings.classroom_tts_voice_en)
    # voices 缓存可佐证资源是否真的支持该音色；缓存为空时按部署校验配置
    cached = _cached_voice_ids()
    if cached is not None:
        if default in cached:
            return default
        for voice_id, voice_locale in approved.items():
            if voice_locale == locale and voice_id in cached:
                return voice_id
        return ""   # 该语言无任何批准音色可用（合成时报 voice_unavailable）
    return default


# ---------------------------------------------------------------------------
# Azure voices list 缓存
# ---------------------------------------------------------------------------

_VOICES_CACHE: tuple[float, list, str] | None = None   # (fetched_at, voices, error)


def _cached_voice_ids() -> frozenset[str] | None:
    """缓存已填充时返回资源音色 id 集合；未刷新过返回 None（不可判定）。"""
    if _VOICES_CACHE is None:
        return None
    voices = _VOICES_CACHE[1]
    if not voices:
        return frozenset()
    return frozenset(v.voice_id for v in voices)


def cached_voices() -> list[dict[str, str]]:
    """缓存的官方 voices list 投影（无网络请求）。"""
    if _VOICES_CACHE is None:
        return []
    return [v.public() for v in _VOICES_CACHE[1]]


async def refresh_voices(*, force: bool = False) -> list[dict[str, str]]:
    """拉取官方 voices list 并缓存；失败记录错误不抛出（能力只读缓存）。"""
    global _VOICES_CACHE
    import time
    if not _azure_available():
        _VOICES_CACHE = None
        return []
    if (not force and _VOICES_CACHE is not None
            and time.monotonic() - _VOICES_CACHE[0] < VOICES_CACHE_TTL
            and not _VOICES_CACHE[2]):
        return cached_voices()
    from .azure import AzureTTS
    try:
        voices = await AzureTTS().list_voices()
        _VOICES_CACHE = (time.monotonic(), voices, "")
    except Exception as exc:
        log.warning("Azure voices list 刷新失败: %s", exc)
        prev = _VOICES_CACHE
        _VOICES_CACHE = (time.monotonic(), prev[1] if prev else [],
                         str(exc)[:200])
    return cached_voices()


def voices_health() -> dict[str, str]:
    """cloud/local configured/ready/degraded 概览（§11.6，零合成零请求）。"""
    import time
    cloud: dict[str, Any] = {"configured": _azure_available()}
    if _VOICES_CACHE is not None and not _VOICES_CACHE[2]:
        cloud["state"] = "ready"
    elif _VOICES_CACHE is not None:
        cloud["state"] = "degraded"
        cloud["detail"] = _VOICES_CACHE[2]
    else:
        cloud["state"] = "configured" if cloud["configured"] else "off"
    local = {"configured": local_tts_enabled(),
             "state": "ready" if local_tts_enabled() else "off"}
    return {"cloud": cloud, "local": local,
            "voices_cached": len(cached_voices()),
            "cache_age": (round(time.monotonic() - _VOICES_CACHE[0], 1)
                          if _VOICES_CACHE else None)}


# ---------------------------------------------------------------------------
# 课堂合成入口（classroom/audio.py 调用；与电话共享 semaphore）
# ---------------------------------------------------------------------------

async def cloud_synthesize(text: str, options: TTSOptions) -> TTSResult:
    """云端合成（azure 首发）；并发由共享 cloud semaphore 限制。"""
    if not _azure_available():
        raise TTSUnavailable("云端语音未配置")
    from .azure import AzureTTS
    return await _cloud_guard(AzureTTS()).synthesize(text, options=options)


async def local_synthesize(text: str, options: TTSOptions) -> TTSResult:
    """本地合成（Melo sidecar）；全局并发 1，跨电话与课堂。"""
    if not local_tts_enabled():
        raise TTSUnavailable("本地语音未启用")
    from .melotts import MeloTTS
    return await _melo_guard(MeloTTS()).synthesize(text, options=options)


def reset_tts_service() -> None:
    """测试/配置重载：清电话缓存、semaphore 与 voices 缓存。"""
    global _PHONE_INSTANCE, _PHONE_KEY, _VOICES_CACHE
    _PHONE_INSTANCE = None
    _PHONE_KEY = None
    _VOICES_CACHE = None
    with _SEM_GUARD:
        _SEMS_BY_LOOP.clear()
