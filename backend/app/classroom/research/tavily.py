"""Tavily research 适配器（plan.md §7.3，C02）。

固定显式参数（search_depth=basic、auto_parameters/include_* 全关、
max_results=5），有时效约束才附带 time_range；extract 处理 results 与
failed_results 的部分失败。Bearer key 只进请求头，绝不进 URL/缓存/异常。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx

from ...core.config import settings
from .. import limits
from ..errors import ClassroomError
from ..netcheck import NetcheckError, parse_public_https_url
from .base import (ExtractOutcome, ExtractedPage, ExtractFailure,
                   ResearchBudget, SearchHit, WebResearchProvider,
                   now_utc, shared_research_cache,
                   validate_query)

SEARCH_ENDPOINT = "https://api.tavily.com/search"
EXTRACT_ENDPOINT = "https://api.tavily.com/extract"

# §7.3：显式固定的搜索参数——禁止自动参数无提示增加请求成本。
FIXED_SEARCH_PARAMS: dict[str, Any] = {
    "search_depth": "basic",
    "auto_parameters": False,
    "include_answer": False,
    "include_raw_content": False,
    "include_images": False,
    "max_results": 5,
}

_TIMELINESS_RANGES = {"day", "week", "month", "year"}
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _parse_date(value: Any) -> date | None:
    """Tavily 日期字段（published_date 等）宽松解析为 date；失败返回 None
    而不是抛错（时效元数据允许缺失，§7.4）。"""
    text = str(value or "").strip()
    m = _DATE_RE.match(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


class TavilyResearchProvider(WebResearchProvider):
    name = "tavily"

    def __init__(self, api_key: str = "", *, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key or settings.tavily_api_key
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

    # ------------------------------------------------------------------ search

    async def search(self, query: str, *, timeliness: str = "basic",
                     owner: str = "",
                     budget: ResearchBudget | None = None,
                     ) -> list[SearchHit]:
        text = validate_query(query)
        if not self.configured:
            raise ClassroomError("research_unavailable",
                                 "检索服务未配置 API key", retryable=False)
        cache = shared_research_cache()
        cache_key = {"query": text, "timeliness": timeliness}
        cached = cache.get(owner or "*", "tavily_search", cache_key)
        if cached is not None:
            return list(cached)  # 缓存命中：不消耗外部预算
        if budget is not None:
            budget.spend_search()

        payload: dict[str, Any] = {"query": text, **FIXED_SEARCH_PARAMS}
        range_key = (timeliness or "basic").lower()
        if range_key in _TIMELINESS_RANGES:
            payload["time_range"] = range_key  # 仅时效约束时附带日期范围

        data = await self._post(SEARCH_ENDPOINT, payload)
        hits: list[SearchHit] = []
        for item in data.get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            hits.append(SearchHit(
                title=str(item.get("title") or "")[:200],
                url=url,
                snippet=str(item.get("content") or "")[:2000],
                score=(float(item["score"])
                       if item.get("score") is not None else None),
                published_at=_parse_date(item.get("published_date")),
            ))
        cache.put(owner or "*", "tavily_search", cache_key, list(hits))
        return hits

    # ----------------------------------------------------------------- extract

    async def extract(self, urls: list[str], *, owner: str = "",
                      budget: ResearchBudget | None = None,
                      ) -> ExtractOutcome:
        if not self.configured:
            raise ClassroomError("research_unavailable",
                                 "检索服务未配置 API key", retryable=False)
        unique = list(dict.fromkeys(urls))
        if not unique:
            return ExtractOutcome()
        if len(unique) > limits.EXTRACT_URL_BUDGET:
            raise ClassroomError("content_invalid",
                                 f"单次提取超过 {limits.EXTRACT_URL_BUDGET} 个 URL 上限")
        # 出站前统一校验：内网/本地/非 https URL 在这里被拒绝（§7.5）
        try:
            parsed = {u: parse_public_https_url(u) for u in unique}
        except NetcheckError as exc:
            raise ClassroomError("content_invalid",
                                 f"提取 URL 不合规：{exc}") from exc

        cache = shared_research_cache()
        cache_key = sorted(unique)
        cached = cache.get(owner or "*", "tavily_extract", cache_key)
        if cached is not None:
            return ExtractOutcome(pages=list(cached[0]),
                                  failures=list(cached[1]))
        if budget is not None:
            budget.spend_extract(len(unique))

        data = await self._post(EXTRACT_ENDPOINT, {"urls": unique})
        outcome = ExtractOutcome()
        stamp = now_utc()
        seen: set[str] = set()
        for item in data.get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            host = parsed[url][0] if url in parsed else url
            body = str(item.get("raw_content") or "")
            outcome.pages.append(ExtractedPage(
                url=url,
                canonical_url=url,
                title=host,
                # 正文只是被引用的数据；单页上限钳制防超大页面
                text=body[:limits.EVIDENCE_CHARS_TOTAL],
                retrieved_at=stamp,
                published_at=_parse_date(item.get("published_date")),
                updated_at=_parse_date(item.get("updated_date")),
            ))
        for item in data.get("failed_results") or []:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            # HTTP 200 不代表逐页成功：失败原样记录为未完成项（§7.3）
            outcome.failures.append(ExtractFailure(
                url=url, reason=str(item.get("error") or "unknown")[:300]))
        cache.put(owner or "*", "tavily_extract", cache_key,
                  (list(outcome.pages), list(outcome.failures)))
        return outcome

    # ------------------------------------------------------------------ httpx

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._ensure_client()
        try:
            resp = await client.post(
                endpoint, json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx.HTTPError as exc:
            raise ClassroomError("research_unavailable",
                                 f"检索服务网络错误：{type(exc).__name__}",
                                 retryable=True) from exc
        if resp.status_code in (401, 403):
            raise ClassroomError("research_unavailable",
                                 "检索服务鉴权失败", retryable=False)
        if resp.status_code == 429:
            raise ClassroomError("research_unavailable",
                                 "检索服务限流", retryable=True)
        if resp.status_code >= 500:
            raise ClassroomError("research_unavailable",
                                 f"检索服务错误（HTTP {resp.status_code}）",
                                 retryable=True)
        if resp.status_code != 200:
            raise ClassroomError("research_unavailable",
                                 f"检索服务返回 HTTP {resp.status_code}",
                                 retryable=False)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ClassroomError("research_unavailable",
                                 "检索服务返回非 JSON 响应",
                                 retryable=True) from exc
        if not isinstance(data, dict):
            raise ClassroomError("research_unavailable",
                                 "检索服务响应结构异常", retryable=False)
        return data
