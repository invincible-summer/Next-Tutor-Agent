"""Pexels 图片检索适配器（plan.md §8.2，C03）。

GET https://api.pexels.com/v1/search；Authorization 为原始 key；参数固定
query/orientation/locale/per_page=8；每次响应读取实际限流头更新共享状态；
署名数据（photo ID、摄影师、原始页面、尺寸、授权链接）全部保留。
"""
from __future__ import annotations

from typing import Any

import httpx

from ...core.config import settings
from .. import limits
from ..errors import ClassroomError
from .base import (ImageCandidate, ImageSearchProvider, RateLimitExceeded,
                   candidate_download_allowed, new_candidate_id,
                   pexels_rate_state)

SEARCH_ENDPOINT = "https://api.pexels.com/v1/search"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PER_PAGE = 8


class PexelsProvider(ImageSearchProvider):
    name = "pexels"

    def __init__(self, api_key: str = "", *, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key or settings.pexels_api_key
        self._transport = transport
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {"trust_env": False,
                                      "timeout": self._timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, *, orientation: str = "landscape",
                     locale: str = "zh-CN", owner: str = "",
                     ) -> list[ImageCandidate]:
        from ..research.base import shared_research_cache

        text = (query or "").strip()
        if not text:
            return []
        if not pexels_rate_state.allow():
            raise RateLimitExceeded(self.name)

        cache = shared_research_cache()
        cache_key = {"query": text, "orientation": orientation,
                     "locale": locale}
        cached = cache.get(owner or "*", "pexels_search", cache_key)
        if cached is not None:
            return list(cached)

        params = {"query": text, "orientation": orientation,
                  "locale": locale, "per_page": PER_PAGE}
        client = self._ensure_client()
        try:
            resp = await client.get(
                SEARCH_ENDPOINT, params=params,
                headers={"Authorization": self._api_key})
        except httpx.HTTPError as exc:
            raise RateLimitExceeded(self.name) from exc
        pexels_rate_state.consume()
        pexels_rate_state.update_from_headers(resp.headers)
        if resp.status_code == 429:
            raise RateLimitExceeded(self.name)
        if resp.status_code != 200:
            raise _error_from_status(resp.status_code)
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        candidates: list[ImageCandidate] = []
        for photo in (data or {}).get("photos") or []:
            src = photo.get("src") or {}
            raw_url = str(src.get("large2x") or src.get("large") or "")
            if not raw_url:
                continue
            alt = str(photo.get("alt") or "").strip()
            cand = ImageCandidate(
                candidate_id=new_candidate_id(),
                provider=self.name,
                provider_asset_id=str(photo.get("id") or ""),
                download_url=raw_url,
                thumb_url=str(src.get("medium") or ""),
                page_url=str(photo.get("url") or ""),
                width=int(photo.get("width") or 0),
                height=int(photo.get("height") or 0),
                alt=alt[:500] or "图库照片（Pexels）",
                creator=str(photo.get("photographer") or "")[:200],
                creator_url=str(photo.get("photographer_url") or "")[:2048],
                license_url=PEXELS_LICENSE_URL,
                locale=locale,
            )
            # 下载 URL 不在批准 CDN 的候选直接丢弃（§8.3 未知 CDN 不可用）
            if candidate_download_allowed(cand):
                candidates.append(cand)
        candidates = candidates[:limits.IMAGE_CANDIDATES_PER_INTENT]
        cache.put(owner or "*", "pexels_search", cache_key, list(candidates))
        return candidates


def _error_from_status(status: int) -> ClassroomError:
    if status in (401, 403):
        return ClassroomError("image_unavailable",
                              "图库鉴权失败", retryable=False)
    return ClassroomError("image_unavailable",
                          f"图库返回 HTTP {status}", retryable=True)
