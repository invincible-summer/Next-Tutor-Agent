"""WebResearchProvider 契约与共享预算/缓存（plan.md §7.3/§7.4/§15.4）。

适配器只做受控 search/extract：显式参数、结果部分失败、时效元数据、
每 owner 24h 缓存与每课调用预算。外部返回的任何文本只是数据，绝不执行。
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .. import limits
from ..errors import ClassroomError
from ..netcheck import NetcheckError, parse_public_https_url

MAX_QUERY_CHARS = 200


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    score: float | None = None
    published_at: date | None = None


@dataclass(frozen=True)
class ExtractedPage:
    url: str
    canonical_url: str
    title: str
    text: str
    retrieved_at: datetime
    published_at: date | None = None
    updated_at: date | None = None


@dataclass(frozen=True)
class ExtractFailure:
    url: str
    reason: str


@dataclass
class ExtractOutcome:
    pages: list[ExtractedPage] = field(default_factory=list)
    failures: list[ExtractFailure] = field(default_factory=list)


def validate_query(query: str) -> str:
    """搜索词边界：非空、无控制字符、≤200 字。隐私规则（不带姓名/评价/
    私有教材原文）由 D 阶段查询构造器保证；适配器只做最后的机械校验。"""
    text = " ".join((query or "").split())
    if not text:
        raise ClassroomError("content_invalid", "搜索词为空")
    if len(text) > MAX_QUERY_CHARS:
        raise ClassroomError("content_invalid",
                             f"搜索词超过 {MAX_QUERY_CHARS} 字上限")
    return text


def validate_extract_urls(urls: list[str]) -> list[tuple[str, str, int]]:
    """提取 URL 出站前的统一校验：只允许公开 HTTPS 页面（§7.5），
    内网/本地/非法协议 URL 在离开服务端之前就被拒绝。"""
    checked: list[tuple[str, str, int]] = []
    for url in urls:
        try:
            checked.append(parse_public_https_url(url))
        except NetcheckError as exc:
            raise ClassroomError("content_invalid",
                                 f"提取 URL 不合规：{exc}") from exc
    return checked


class ResearchBudget:
    """每课外部检索预算（§15.4）：search 6 次、extract 8 个 URL。

    缓存命中不消耗预算（不产生外部调用）；预算对象跨阶段共享，耗尽即
    budget_exceeded（429，可等待重试语义由 job 层决定）。"""

    def __init__(self, *, search_calls: int = 0, extract_urls: int = 0) -> None:
        self.search_calls = search_calls
        self.extract_urls = extract_urls

    def spend_search(self, count: int = 1) -> None:
        if self.search_calls + count > limits.SEARCH_CALL_BUDGET:
            raise ClassroomError(
                "budget_exceeded",
                f"搜索调用预算耗尽（{limits.SEARCH_CALL_BUDGET} 次/课）",
                retryable=False)
        self.search_calls += count

    def spend_extract(self, count: int) -> None:
        if self.extract_urls + count > limits.EXTRACT_URL_BUDGET:
            raise ClassroomError(
                "budget_exceeded",
                f"URL 提取预算耗尽（{limits.EXTRACT_URL_BUDGET} 个/课）",
                retryable=False)
        self.extract_urls += count

    def as_dict(self) -> dict[str, int]:
        return {"search_calls": self.search_calls,
                "extract_urls": self.extract_urls}


class ResearchCache:
    """每 owner 的检索/提取缓存：同 key 24h 复用，进程内共享。

    缓存内容只有人工可见的查询与结果（无 API key），不同 owner 互不可见；
    TTL 过期惰性清除。单进程 worker 模型下锁只为防线程竞争。"""

    TTL_SECONDS = 24 * 3600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_owner: dict[str, dict[str, tuple[float, Any]]] = {}

    def _key(self, kind: str, payload: Any) -> str:
        import hashlib
        import json

        raw = json.dumps([kind, payload], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, owner: str, kind: str, payload: Any) -> Any | None:
        key = self._key(kind, payload)
        now = time.monotonic()
        with self._lock:
            bucket = self._by_owner.get(owner)
            if not bucket:
                return None
            hit = bucket.get(key)
            if hit is None:
                return None
            expires, value = hit
            if expires < now:
                bucket.pop(key, None)
                return None
            return value

    def put(self, owner: str, kind: str, payload: Any, value: Any) -> None:
        key = self._key(kind, payload)
        with self._lock:
            bucket = self._by_owner.setdefault(owner, {})
            bucket[key] = (time.monotonic() + self.TTL_SECONDS, value)

    def clear(self, owner: str | None = None) -> None:
        with self._lock:
            if owner is None:
                self._by_owner.clear()
            else:
                self._by_owner.pop(owner, None)


# 进程级共享缓存实例（图片搜索缓存同款语义，§8.3.8）
_shared_cache = ResearchCache()


def shared_research_cache() -> ResearchCache:
    return _shared_cache


class WebResearchProvider(ABC):
    """search/extract 契约。实现必须：服务端 httpx、trust_env=False、
    显式固定参数、处理部分失败、返回时效元数据。"""

    name: str = "web"

    @abstractmethod
    async def search(self, query: str, *, timeliness: str = "basic",
                     owner: str = "", budget: ResearchBudget | None = None,
                     ) -> list[SearchHit]:
        """搜索并返回候选；timeliness∈{basic,day,week,month} 仅在非 basic
        时附带日期范围参数。"""

    @abstractmethod
    async def extract(self, urls: list[str], *, owner: str = "",
                      budget: ResearchBudget | None = None,
                      ) -> ExtractOutcome:
        """提取页面正文；HTTP 200 不代表逐页成功，失败进 failures。"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
