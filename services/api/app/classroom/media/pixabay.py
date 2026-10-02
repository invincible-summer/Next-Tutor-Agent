"""Pixabay 图片检索适配器（plan.md §8.2，C03）。

GET https://pixabay.com/api/；key 只在服务端 query；safesearch=true、
per_page=8、q≤100 字符；下载用 largeImageURL/webformatURL（cd­n.pixabay.com），
不假定 key 拥有 imageURL/vectorURL 等高权限字段，不下载外部 SVG。
搜索结果按平台要求缓存 24h（长期使用的图片一律下载到自身服务）。
"""
from __future__ import annotations

from typing import Any

import httpx

from ...core.config import settings
from .. import limits
from ..errors import ClassroomError
from .base import (ImageCandidate, ImageSearchProvider, RateLimitExceeded,
                   candidate_download_allowed, new_candidate_id,
                   pixabay_rate_counter)

SEARCH_ENDPOINT = "https://pixabay.com/api/"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"
PER_PAGE = 8


class PixabayProvider(ImageSearchProvider):
    name = "pixabay"

    def __init__(self, api_key: str = "", *, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key or settings.pixabay_api_key
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
        if len(text) > limits.PIXABAY_QUERY_MAX_CHARS:
            text = text[:limits.PIXABAY_QUERY_MAX_CHARS]
        if not pixabay_rate_counter.allow():
            raise RateLimitExceeded(self.name)

        lang = "zh" if locale.lower().startswith("zh") else "en"
        cache = shared_research_cache()
        cache_key = {"query": text, "orientation": orientation, "lang": lang}
        cached = cache.get(owner or "*", "pixabay_search", cache_key)
        if cached is not None:
            return list(cached)

        params = {"key": self._api_key, "q": text, "lang": lang,
                  "image_type": "photo", "safesearch": "true",
                  "orientation": ("horizontal" if orientation == "landscape"
                                  else orientation),
                  "per_page": PER_PAGE}
        client = self._ensure_client()
        try:
            resp = await client.get(SEARCH_ENDPOINT, params=params)
        except httpx.HTTPError as exc:
            raise RateLimitExceeded(self.name) from exc
        # key 只在请求参数：异常/日志路径绝不回显 params 或 URL query
        if resp.status_code == 429:
            raise RateLimitExceeded(self.name)
        if resp.status_code in (400, 401, 403):
            raise ClassroomError("image_unavailable",
                                 "图库请求被拒绝（key 或参数问题）",
                                 retryable=False)
        if resp.status_code != 200:
            raise ClassroomError("image_unavailable",
                                 f"图库返回 HTTP {resp.status_code}",
                                 retryable=True)
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}

        candidates: list[ImageCandidate] = []
        for hit in (data or {}).get("hits") or []:
            raw_url = str(hit.get("largeImageURL")
                          or hit.get("webformatURL") or "")
            if not raw_url:
                continue
            tags = str(hit.get("tags") or "")
            alt = ", ".join(t.strip() for t in tags.split(",")[:3] if t.strip())
            user = str(hit.get("user") or "")
            user_id = str(hit.get("user_id") or "")
            creator_url = (f"https://pixabay.com/users/{user}-{user_id}/"
                           if user and user_id else "")
            cand = ImageCandidate(
                candidate_id=new_candidate_id(),
                provider=self.name,
                provider_asset_id=str(hit.get("id") or ""),
                download_url=raw_url,
                thumb_url=str(hit.get("previewURL") or ""),
                page_url=str(hit.get("pageURL") or ""),
                width=int(hit.get("imageWidth") or 0),
                height=int(hit.get("imageHeight") or 0),
                alt=alt[:500] or "图库图片（Pixabay）",
                creator=user[:200],
                creator_url=creator_url[:2048],
                license_url=PIXABAY_LICENSE_URL,
                locale=locale,
            )
            if candidate_download_allowed(cand):
                candidates.append(cand)
        candidates = candidates[:limits.IMAGE_CANDIDATES_PER_INTENT]
        cache.put(owner or "*", "pixabay_search", cache_key, list(candidates))
        return candidates
