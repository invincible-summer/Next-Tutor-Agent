"""图库 provider 契约、候选 ID、预算与限流（plan.md §8.2/§8.3，C03）。

模型/前端只见到服务端签发的 ``cand_…`` 候选 ID；下载 URL、API key 永远
只在服务端。缓存复用进程级 shared_research_cache（同款 24h/每 owner 语义，
缓存内容不含 key）。用量计数进程内全局共享，尊重同一 key 的总限额。
"""
from __future__ import annotations

import secrets
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .. import limits
from ..errors import ClassroomError
from ..netcheck import parse_public_https_url


@dataclass(frozen=True)
class ImageCandidate:
    """一个可下载候选：署名数据齐全（§8.2），page_url 面向页脚/来源面板。"""

    candidate_id: str          # 服务端签发，对模型不透明
    provider: str              # pexels | pixabay
    provider_asset_id: str
    download_url: str          # 批准 CDN 上的原始图（仅服务端使用）
    thumb_url: str             # 预览缩略图（公开 CDN，可为空）
    page_url: str              # 原始页面（署名链接）
    width: int
    height: int
    alt: str
    creator: str
    creator_url: str
    license_url: str
    locale: str = ""           # 产生该候选的查询语言（双语重试追踪）


def new_candidate_id() -> str:
    return f"cand_{secrets.token_hex(12)}"


class ImageSearchBudget:
    """每课图片搜索请求预算：8 次（含双语重试与 provider 回退）。"""

    def __init__(self, calls: int = 0) -> None:
        self.calls = calls

    def spend(self, count: int = 1) -> None:
        if self.calls + count > limits.IMAGE_SEARCH_BUDGET:
            raise ClassroomError(
                "budget_exceeded",
                f"图片搜索预算耗尽（{limits.IMAGE_SEARCH_BUDGET} 次/课）")
        self.calls += count


class RateLimitExceeded(ClassroomError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            "image_unavailable",
            f"图库 {provider} 触发限流，本轮跳过（可用其他 provider 或无图布局）",
            retryable=True)
        self.provider = provider


class SlidingWindowCounter:
    """进程内共享的滑动窗口计数（Pixabay 60s/100 次类限制）。"""

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._lock = threading.Lock()
        self._events: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            self._events = [t for t in self._events if now - t < self.window]
            if len(self._events) >= self.max_events:
                return False
            self._events.append(now)
            return True


class PexelsRateLimitState:
    """Pexels 双窗口限流：读实际响应头更新，初始用配置默认（§8.2）。

    程序不把默认额度当永久保证；观察到更严格的头即按观察值收紧。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hourly_remaining: int = limits.PEXELS_HOURLY_LIMIT_DEFAULT
        self.monthly_remaining: int = limits.PEXELS_MONTHLY_LIMIT_DEFAULT

    def allow(self) -> bool:
        with self._lock:
            return (self.hourly_remaining > 0
                    and self.monthly_remaining > 0)

    def consume(self) -> None:
        with self._lock:
            self.hourly_remaining = max(0, self.hourly_remaining - 1)
            self.monthly_remaining = max(0, self.monthly_remaining - 1)

    def update_from_headers(self, headers) -> None:
        def _int(name: str) -> int | None:
            raw = headers.get(name)
            if raw is None or not str(raw).strip().isdigit():
                return None
            return int(str(raw))

        hourly = _int("X-RateLimit-Remaining")
        monthly = _int("X-RateLimit-Monthly-Remaining")
        with self._lock:
            if hourly is not None:
                self.hourly_remaining = hourly
            if monthly is not None:
                self.monthly_remaining = monthly


# 进程级共享：同一 key 的用量在所有用户间共享（§8.3.8）
pexels_rate_state = PexelsRateLimitState()
pixabay_rate_counter = SlidingWindowCounter(
    limits.PIXABAY_PER_MINUTE_LIMIT, 60.0)


class ImageSearchProvider(ABC):
    name: str

    @property
    @abstractmethod
    def configured(self) -> bool:
        """无 key 即不可用：直接跳过，不伪联网。"""

    @abstractmethod
    async def search(self, query: str, *, orientation: str = "landscape",
                     locale: str = "zh-CN", owner: str = "",
                     ) -> list[ImageCandidate]:
        """检索候选（每意图 ≤8 个）；中文不足时由服务层做一次英文重试。"""


def candidate_download_allowed(candidate: ImageCandidate) -> bool:
    """候选的下载 URL 必须是批准 CDN 的完整 hostname（§8.3 白名单）。"""
    try:
        host, _path, _port = parse_public_https_url(candidate.download_url)
    except Exception:
        return False
    return host in {h.lower() for h in limits.IMAGE_DOWNLOAD_HOSTS}
