"""产品能力目录（plan.md §8.1）。

目录 JSON 是功能事实源：只收录已实现的产品功能；检索用标题/别名/
关键词匹配，不引入向量索引。`/docs/content` 只能作为补充材料，不能
扩大工具权限或覆盖能力开关。更新导航、功能开关或核心流程时同步
bump catalog_version。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assistant import AssistantRouteId

_CATALOG_PATH = Path(__file__).resolve().parent / "product_catalog.json"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalizedText(_StrictModel):
    zh: str = Field(min_length=1, max_length=2000)
    en: str = Field(min_length=1, max_length=2000)


class LocalizedSteps(_StrictModel):
    zh: list[str] = Field(min_length=1, max_length=8)
    en: list[str] = Field(min_length=1, max_length=8)


class CatalogModule(_StrictModel):
    route_id: AssistantRouteId
    public: bool = False
    name: LocalizedText
    summary: LocalizedText
    aliases: list[str] = Field(default_factory=list, max_length=40)
    steps: LocalizedSteps
    prerequisites: LocalizedText


class ProductCatalog(_StrictModel):
    catalog_version: str = Field(min_length=1, max_length=32)
    modules: list[CatalogModule] = Field(min_length=1, max_length=64)


_lock = threading.Lock()
_cache: ProductCatalog | None = None
_cache_mtime: float | None = None


def load_product_catalog() -> ProductCatalog:
    """读取并校验目录；按文件 mtime 缓存，进程内线程安全。"""
    global _cache, _cache_mtime
    mtime = _CATALOG_PATH.stat().st_mtime_ns
    with _lock:
        if _cache is not None and _cache_mtime == mtime:
            return _cache
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        catalog = ProductCatalog(**raw)
        route_ids = [m.route_id for m in catalog.modules]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("product_catalog.json 存在重复 route_id")
        _cache = catalog
        _cache_mtime = mtime
        return catalog


def get_module(route_id: AssistantRouteId) -> CatalogModule | None:
    for module in load_product_catalog().modules:
        if module.route_id == route_id:
            return module
    return None


def _normalize(text: str) -> str:
    return "".join(text.lower().split())


def match_score(
    module: CatalogModule, query: str, route_hint: AssistantRouteId | None,
) -> tuple[int, int]:
    """固定打分：route 提示 > 别名精确 > 名称精确 > 包含匹配。

    返回 (score, position)：position 为命中别名在问句中的位置，
    分数相同时更靠前的提及优先（「备课入口」里「备课」是主意图）。
    """
    if route_hint is not None and module.route_id == route_hint:
        return 100, 0
    q = _normalize(query)
    if not q:
        return 0, len(q)
    aliases = [_normalize(a) for a in module.aliases]
    names = [_normalize(module.name.zh), _normalize(module.name.en)]
    if q in aliases or q in names:
        return 80, 0
    best_pos = -1
    for alias in aliases:
        if not alias:
            continue
        pos = q.find(alias)
        if pos >= 0:
            if best_pos < 0 or pos < best_pos:
                best_pos = pos
    if best_pos >= 0:
        return 60, best_pos
    for name in names:
        if name and name in q:
            return 50, q.find(name)
    summary = _normalize(module.summary.zh) + _normalize(module.summary.en)
    if q in summary:
        return 20, summary.find(q)
    return 0, len(q)


def find_module_matches(
    query: str,
    lang: str = "zh",
    route_hint: AssistantRouteId | None = None,
    limit: int = 3,
) -> list[CatalogModule]:
    """按打分降序返回候选；同分先出现者优先，声明顺序稳定。"""
    scored: list[tuple[int, int, int, CatalogModule]] = []
    for index, module in enumerate(load_product_catalog().modules):
        score, position = match_score(module, query, route_hint)
        if score > 0:
            scored.append((score, position, index, module))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [module for _, _, _, module in scored[:limit]]


def catalog_digest(lang: str = "zh") -> str:
    """供导览/意图提示使用的紧凑目录摘要（纯文本，无执行语义）。"""
    lines: list[str] = []
    for module in load_product_catalog().modules:
        name = module.name.zh if lang == "zh" else module.name.en
        summary = module.summary.zh if lang == "zh" else module.summary.en
        alias = "、".join(module.aliases[:8])
        lines.append(f"[{module.route_id.value}] {name}：{summary}（别名：{alias}）")
    return "\n".join(lines)
