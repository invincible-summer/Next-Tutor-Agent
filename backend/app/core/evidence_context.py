"""P9 上下文重建：把证据门选中的碎片重组成课文级可读语境。

确定性、零 LLM。两级扩展，消费 structured chunker 写入但此前从未被检索
读取的链式/课题元数据（parent_id / prev_id / next_id / lesson / is_lesson）：

- 课文合并：同 (file_id, lesson) 的多个选中块（或课题标题命中的单块）合并为
  一条「课文《X》节选（教材第a–b页）」，适配「X讲了什么」类概览查询——
  此前模型只能看到 3-4 条互不相连的 250 字符句窗孤岛。
- 邻块扩展：命中块摘录过短时按 prev/next_id 取邻块头部补语境。

chunk_lookup 由调用方注入（chunk_id → dict(text/prev_id/next_id)），
模块本身不触碰任何存储，保持纯函数可测。
"""
from __future__ import annotations

import re
from typing import Any, Callable

LESSON_GROUP_CAP = 1600
NEIGHBOR_CHARS = 200
THIN_EXCERPT_CHARS = 350


def _clean_head(text: str, chars: int) -> str:
    """取文本头部，尽量在标点处收口，避免半个句子/公式。"""
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if len(text) <= chars:
        return text
    window = text[:chars]
    for cut in range(len(window) - 1, max(30, chars // 2), -1):
        if window[cut] in "。！？；，、":
            return window[:cut + 1]
    return window


def expand_with_neighbors(item: dict[str, Any],
                          chunk_lookup: Callable[[str], dict[str, Any] | None] | None,
                          *, neighbor_chars: int = NEIGHBOR_CHARS) -> dict[str, Any]:
    """摘录过薄时按 prev/next 链取邻块头部补语境（就地更新 excerpt）。"""
    excerpt = str(item.get("evidence_excerpt") or "")
    if len(excerpt) >= THIN_EXCERPT_CHARS or chunk_lookup is None:
        return item
    chunk = chunk_lookup(str(item.get("chunk_id") or "")) or {}
    metadata = chunk.get("metadata") or {}
    pieces: list[str] = []
    prev = chunk_lookup(str(metadata.get("prev_id") or "")) if metadata.get("prev_id") else None
    if prev and prev.get("text"):
        head = _clean_head(prev.get("text"), neighbor_chars)
        if head and head not in excerpt:
            pieces.append(f"……{head}")
    pieces.append(excerpt)
    nxt = chunk_lookup(str(metadata.get("next_id") or "")) if metadata.get("next_id") else None
    if nxt and nxt.get("text"):
        head = _clean_head(nxt.get("text"), neighbor_chars)
        if head and head not in excerpt:
            pieces.append(f"{head}……")
    if len(pieces) > 1:
        item["evidence_excerpt"] = "\n".join(pieces)
        item["neighbor_expanded"] = True
    return item


def merge_lesson_groups(items: list[dict[str, Any]], *,
                        group_cap: int = LESSON_GROUP_CAP) -> list[dict[str, Any]]:
    """同课题块合并为单条课文节选；单块课题命中（标题契合/课题标题块）同样升级。

    合并保持首次出现位置，置信度取组内最高，页码取印刷页范围；非课题命中
    的块原样保留。合并后的块在 lesson_label 字段携带课题名供输出层展示。
    """
    def _group_key(item: dict[str, Any]) -> tuple[str, str] | None:
        lesson = str(item.get("lesson") or "").strip()
        if not lesson:
            return None
        return (str(item.get("file_id") or item.get("source") or ""), lesson)

    groups: dict[tuple[str, str], list[int]] = {}
    for i, item in enumerate(items):
        key = _group_key(item)
        if key:
            groups.setdefault(key, []).append(i)
    merged_indices: set[int] = set()
    merges: dict[int, dict[str, Any]] = {}
    for key, indices in groups.items():
        lesson = key[1]
        title_hit = any((item.get("title_match") or 0) >= 0.99
                        or item.get("is_lesson")
                        or item.get("selection_reason") == "lesson_title_match+mmr"
                        for item in (items[i] for i in indices))
        if len(indices) < 2 and not title_hit:
            continue
        head_index = indices[0]
        excerpts: list[str] = []
        for i in indices:
            ex = str(items[i].get("evidence_excerpt") or "").strip()
            if ex and ex not in excerpts:
                excerpts.append(ex)
        if not excerpts:
            continue
        joined = "\n……\n".join(excerpts)
        if len(joined) > group_cap:
            joined = joined[:group_cap].rstrip() + "……"
        merged = dict(items[head_index])
        merged["evidence_excerpt"] = joined
        merged["lesson_label"] = lesson
        merged["selection_reason"] = f"lesson_group({len(indices)})"
        merged["confidence"] = max(float(items[i].get("confidence") or 0.0)
                                   for i in indices)
        printed = [int(items[i]["printed_page"]) for i in indices
                   if items[i].get("printed_page")]
        if len(printed) >= 2:
            merged["printed_page_range"] = [min(printed), max(printed)]
        merges[head_index] = merged
        merged_indices.update(indices)
    out: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if i in merges:
            out.append(merges[i])
        elif i not in merged_indices:
            out.append(item)
    return out


def reconstruct_evidence(items: list[dict[str, Any]],
                         chunk_lookup: Callable[[str], dict[str, Any] | None] | None = None,
                         *, group_cap: int = LESSON_GROUP_CAP,
                         neighbor_chars: int = NEIGHBOR_CHARS) -> list[dict[str, Any]]:
    """入口：先逐块邻块扩展，再课题分组合并（保持 gate 排序）。"""
    expanded = [expand_with_neighbors(dict(item), chunk_lookup,
                                      neighbor_chars=neighbor_chars)
                for item in items]
    return merge_lesson_groups(expanded, group_cap=group_cap)
