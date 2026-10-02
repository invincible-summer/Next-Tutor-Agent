"""课堂外部检索（plan.md §7.3）。provider 无 key 即不可用，不伪联网。"""
from __future__ import annotations

from ...core.config import settings
from .base import (ExtractOutcome, ExtractedPage, ExtractFailure,
                   ResearchBudget, ResearchCache, SearchHit,
                   WebResearchProvider, shared_research_cache)
from .tavily import TavilyResearchProvider

__all__ = [
    "ExtractOutcome", "ExtractedPage", "ExtractFailure", "ResearchBudget",
    "ResearchCache", "SearchHit", "WebResearchProvider",
    "shared_research_cache", "TavilyResearchProvider",
    "build_research_provider",
]


def build_research_provider() -> WebResearchProvider | None:
    """按配置构造 provider；未配置 key 返回 None（capability 相应不可用）。"""
    name = (settings.classroom_web_provider or "").strip().lower()
    if name == "tavily":
        provider = TavilyResearchProvider()
        return provider if provider.configured else None
    return None
