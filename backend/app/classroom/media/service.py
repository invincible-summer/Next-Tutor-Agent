"""图片检索编排（plan.md §8.3.1–§8.3.2，C03）。

按配置顺序 Pexels→Pixabay 查询；用户/课程指定平台时只查指定 provider；
每个意图 ≤8 候选；中文查询不足时允许一次英文改写（改写词由 D 阶段规划器
提供，服务只执行）；所有真实出站计入每课 8 次预算（缓存命中不计）。
无可用 provider/key → 返回空列表，管线降级无图布局，不阻塞核心内容。
"""
from __future__ import annotations

from ..errors import ClassroomError  # noqa: F401  (供管线错误分类复用)
from .base import (ImageCandidate, ImageSearchBudget, ImageSearchProvider,
                   RateLimitExceeded)
from .pexels import PexelsProvider
from .pixabay import PixabayProvider

_PROVIDERS: dict[str, type] = {"pexels": PexelsProvider,
                               "pixabay": PixabayProvider}


def build_image_providers(transport=None) -> list[ImageSearchProvider]:
    """按 settings.classroom_image_providers 顺序构造已配置 key 的 provider。"""
    from ...core.config import settings

    order = [p.strip() for p in settings.classroom_image_providers.split(",")
             if p.strip()]
    out: list[ImageSearchProvider] = []
    for name in order:
        cls = _PROVIDERS.get(name)
        if cls is None:
            continue
        provider = cls(transport=transport) if transport is not None else cls()
        if provider.configured:
            out.append(provider)
    return out


class ImageSearchService:
    """意图级检索：queries[0] 为主查询，queries[1] 为英文改写（可选）。"""

    def __init__(self, providers: list[ImageSearchProvider] | None = None,
                 *, only_provider: str = "") -> None:
        self._providers = providers if providers is not None \
            else build_image_providers()
        if only_provider:
            self._providers = [p for p in self._providers
                               if p.name == only_provider]
        self._only = only_provider

    @property
    def available(self) -> bool:
        return bool(self._providers)

    async def search(self, queries: list[str], *, owner: str = "",
                     orientation: str = "landscape", locale: str = "zh-CN",
                     budget: ImageSearchBudget | None = None,
                     ) -> list[ImageCandidate]:
        """按 §8.3.2 顺序链检索：主查询先问首选 provider，有结果即不回退、
        不做英文改写；空结果才回退下一家，全部为空才尝试改写词。

        provider 限流/网络失败按"无结果"处理继续链（§8.3.10：仍无候选 →
        无图布局，由调用方记 warning）；缓存命中零预算消耗。"""
        budget = budget or ImageSearchBudget()
        merged: dict[str, ImageCandidate] = {}
        for query in [q for q in queries if q and q.strip()]:
            got_hits = False
            for provider in self._providers:
                if len(merged) >= 8:
                    break
                budget.spend()
                try:
                    hits = await provider.search(
                        query, orientation=orientation, locale=locale,
                        owner=owner)
                except RateLimitExceeded:
                    continue
                if hits:
                    got_hits = True
                for cand in hits:
                    # 去重按 provider+资产 id；跨语言重复命中保留首个
                    key = f"{cand.provider}:{cand.provider_asset_id}"
                    merged.setdefault(key, cand)
                if got_hits:
                    break  # 该查询首选已有结果：不再回退
            if merged:
                break  # 主查询已有结果：不做英文改写
        return list(merged.values())[:8]


async def aclose_providers(providers: list[ImageSearchProvider]) -> None:
    for provider in providers:
        closer = getattr(provider, "aclose", None)
        if closer is not None:
            await closer()
