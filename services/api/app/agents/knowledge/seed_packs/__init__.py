"""Seed pack registry + aggregator — P6-A2 起考纲谱系已移除。

知识只来自教材：图谱 = 教材图谱（公用 + 自有），不再内置考纲 seed 包。
聚合函数保留空实现以维持既有调用契约（seed.py 透传，主图初始为空）。
遗留数据 `knowledge/graph.json`（reasoner 学习边，引用已删 seed 节点）已清空。
"""
from __future__ import annotations

from typing import Any

#: P6-A2：考纲包已全部删除，注册表为空。
PACK_MODULES: tuple[Any, ...] = ()


def packs() -> list[dict[str, str]]:
    """Metadata for the registered packs (empty since P6-A2)."""
    return []


def all_nodes() -> list[dict[str, Any]]:
    """No seed nodes — the graph starts empty (textbook graphs only)."""
    return []


def all_edges() -> list[dict[str, Any]]:
    """No seed edges."""
    return []


def all_contents() -> list[dict[str, Any]]:
    """No seed contents."""
    return []
