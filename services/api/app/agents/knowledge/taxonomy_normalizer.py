"""Deterministic textbook taxonomy normalization and publication quality gate.

The graph stores clean chapter display names while source volume identity stays in
metadata.  This module is deliberately LLM-free so the same source always yields
the same published taxonomy.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

_EXT_RE = re.compile(r"\.(?:pdf|docx?|pptx?|txt|md|epub)\b", re.I)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|cn|net|org|edu)(?:\.cn)?\b\S*", re.I)
_BRACKET_NOISE_RE = re.compile(
    r"[（(\[]\s*(?:第?\s*\d+\s*版|修订版|新版|教材|讲义|作者[:：].*?|出版社[:：].*?|[^）)\]]*(?:下载|电子书|扫描版)[^）)\]]*)\s*[）)\]]",
    re.I,
)
_FRONT_MATTER = {
    "封面", "扉页", "版权页", "版权信息", "前言", "序言", "序", "目录", "contents",
    "出版说明", "编者的话", "参考文献", "索引", "附录索引",
}
#: 章位的后置事项（back matter）：不是教学单元，整章剔除（实测公共教材里
#: 「本章小结/习题/参考答案/今日物理趣闻G/标题」等被切成章，污染章序信号
#: 与图面）。构侧 Tier1 书签质检与归一化层的 is_teaching_chapter 共用。
_BACK_MATTER = {
    "小结", "本章小结", "习题", "习题参考答案", "参考答案", "答案", "书名", "书名页",
    "标题", "正文", "结语", "后记", "附录", "附录A", "附录B",
}
_BACK_MATTER_RE = re.compile(r"趣闻|习题答案")


def is_back_matter(name: str) -> bool:
    """True = 该标题是章位的后置事项（小结/习题/答案/趣闻等），非教学单元。

    锚定匹配防误伤：完全匹配后置事项名、短「习题N/xx小结」编号变体、或
    含「趣闻/习题答案」的专栏名。真实教学章名（第1章 函数/第一单元 …）
    不会命中。
    """
    value = _SPACE_RE.sub(" ", str(name or "")).strip()
    if not value:
        return False
    if value in _BACK_MATTER or _BACK_MATTER_RE.search(value):
        return True
    if value.startswith("习题") and len(value) <= 6:
        return True
    if value.endswith("小结") and len(value) <= 8:
        return True
    return False
_BAD_FRAGMENT_RE = re.compile(r"(?:pdf|docx?|pptx?|https?://|www\.|\.com|\.cn|\.net|\.org)", re.I)
_SPACE_RE = re.compile(r"\s+")
_SEP_RE = re.compile(r"^[\s·•|｜:：_—–-]+|[\s·•|｜:：_—–-]+$")
_INLINE_SEP_RE = re.compile(r"\s*[·•|｜]\s*")


def _plain_stem(value: str) -> str:
    value = Path(str(value or "")).name
    value = _EXT_RE.sub("", value)
    value = _URL_RE.sub("", value)
    value = _BRACKET_NOISE_RE.sub("", value)
    return _SEP_RE.sub("", _SPACE_RE.sub(" ", value)).strip()


def normalize_chapter_name(raw: str, *, textbook_title: str = "",
                           volume_title: str = "") -> str:
    """Return a clean chapter display name without source-file decoration."""
    name = unicodedata.normalize("NFKC", str(raw or ""))
    # Older group builds used ``<filename>·<chapter>``.  Prefer the final
    # teaching-looking segment before applying the generic source-prefix rules.
    parts = [p.strip() for p in _INLINE_SEP_RE.split(name) if p.strip()]
    if len(parts) > 1:
        name = parts[-1]
    name = _URL_RE.sub("", name)
    name = _EXT_RE.sub("", name)
    name = _BRACKET_NOISE_RE.sub("", name)
    # Remove only source strings at the beginning; never globally erase a valid
    # academic phrase that happens to overlap the book title.
    sources = sorted({_plain_stem(textbook_title), _plain_stem(volume_title)},
                     key=len, reverse=True)
    for source in sources:
        if not source:
            continue
        pattern = re.compile(rf"^\s*{re.escape(source)}\s*[·•|｜:：_—–-]*\s*", re.I)
        name = pattern.sub("", name, count=1)
    name = _SEP_RE.sub("", _SPACE_RE.sub(" ", name)).strip()
    return name[:80]


def is_teaching_chapter(name: str) -> bool:
    value = _SPACE_RE.sub(" ", str(name or "")).strip()
    if not value or value.lower() in _FRONT_MATTER:
        return False
    if _BAD_FRAGMENT_RE.search(value):
        return False
    if is_back_matter(value):
        return False
    return True


def chapter_identity(volume_id: str, order: int, raw_name: str) -> str:
    basis = f"{volume_id}\0{order}\0{unicodedata.normalize('NFKC', raw_name).strip()}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def normalize_section_name(raw: str) -> str:
    """节（课/篇目/小节）显示名归一化。

    与章名不同，**必须保留内部间隔号**（「沁园春·江畔」是完整篇目名，
    normalize_chapter_name 的 · 切分逻辑会把它毁成「长沙」）；只清 URL/
    扩展名/括号噪声、目录页码尾巴与首尾分隔符。
    """
    name = unicodedata.normalize("NFKC", str(raw or ""))
    name = _URL_RE.sub("", name)
    name = _EXT_RE.sub("", name)
    name = _BRACKET_NOISE_RE.sub("", name)
    name = re.sub(r"[\s.．·]*\d{1,4}(?:\s*[-–—~至]\s*\d{1,4})?\s*$", "", name)
    return _SEP_RE.sub("", _SPACE_RE.sub(" ", name)).strip()[:60]


def normalize_textbook_spec(spec: dict[str, Any], *, textbook_title: str,
                            volume_id: str, volume_title: str) -> tuple[dict[str, Any], list[str]]:
    """Normalize one volume spec and attach stable, non-display source metadata."""
    warnings: list[str] = []
    chapters: list[dict[str, Any]] = []
    ranges: dict[str, list[int]] = {}
    section_ranges: dict[str, list[int]] = {}
    seen: dict[str, int] = {}
    raw_ranges = spec.get("page_ranges") or {}
    for order, chapter in enumerate(spec.get("chapters") or [], 1):
        if not isinstance(chapter, dict):
            continue
        raw_name = str(chapter.get("name") or "").strip()
        clean = normalize_chapter_name(raw_name, textbook_title=textbook_title,
                                       volume_title=volume_title)
        if not is_teaching_chapter(clean):
            warnings.append(f"已过滤非教学或污染章节「{raw_name[:50] or '空标题'}」")
            continue
        # Repeated headings in one volume remain separate internally but receive
        # a deterministic display suffix so the navigation is unambiguous.
        seen[clean] = seen.get(clean, 0) + 1
        display = clean if seen[clean] == 1 else f"{clean}（{seen[clean]}）"
        item = dict(chapter)
        key = chapter_identity(volume_id, order, raw_name or clean)
        chapter_meta = {
            "volume_id": volume_id,
            "file_id": volume_id,
            "volume_title": _plain_stem(volume_title),
            "chapter_order": order,
            "raw_heading": raw_name[:160],
        }
        rng = raw_ranges.get(raw_name)
        if isinstance(rng, list) and len(rng) == 2:
            ranges[key] = rng
            # Legacy readers may still look up the clean display name.
            ranges.setdefault(display, rng)
            chapter_meta["page_range"] = [int(rng[0]), int(rng[1])]
        item.update({
            "name": display,
            "chapter_key": key,
            "metadata": chapter_meta,
        })
        # 节级归一化（v5）：显示名保留间隔号；重名加确定性后缀；概念侧的
        # ``c["section"]`` 原名引用同步改写为新显示名。
        raw_sections = [s for s in (chapter.get("sections") or [])
                        if isinstance(s, dict) or str(s or "").strip()]
        section_items: list[dict[str, Any]] = []
        sec_seen: dict[str, int] = {}
        sec_display_by_raw: dict[str, str] = {}
        for so, raw_sec in enumerate(raw_sections[:60], 1):
            if isinstance(raw_sec, dict):
                raw_sname = str(raw_sec.get("name") or "").strip()
                raw_srng = raw_sec.get("page_range") or raw_sec.get("pages") or []
            else:
                raw_sname = str(raw_sec or "").strip()
                raw_srng = []
            sec_clean = normalize_section_name(raw_sname)
            if not sec_clean or not is_teaching_chapter(sec_clean):
                continue
            compact_key = _SPACE_RE.sub("", sec_clean).casefold()
            sec_seen[compact_key] = sec_seen.get(compact_key, 0) + 1
            sec_display = (sec_clean if sec_seen[compact_key] == 1
                           else f"{sec_clean}（{sec_seen[compact_key]}）")
            skey = chapter_identity(f"{volume_id}:{key}", so, raw_sname or sec_clean)
            sec_item: dict[str, Any] = {"name": sec_display, "section_key": skey,
                                        "page_range": []}
            if isinstance(raw_srng, (list, tuple)) and len(raw_srng) >= 2:
                try:
                    srng = [int(raw_srng[0]), int(raw_srng[1])]
                    sec_item["page_range"] = srng
                    section_ranges[skey] = srng
                    section_ranges.setdefault(sec_display, srng)
                except (TypeError, ValueError):
                    pass
            section_items.append(sec_item)
            if raw_sname:
                sec_display_by_raw[raw_sname] = sec_display
            if len(section_items) >= 40:
                break
        if section_items:
            item["sections"] = section_items
        for c in (item.get("concepts") or []):
            if isinstance(c, dict) and str(c.get("section") or "").strip():
                c["section"] = sec_display_by_raw.get(
                    str(c.get("section") or "").strip(), str(c.get("section") or "").strip())
        chapters.append(item)
    out = dict(spec)
    if not chapters:
        fallback_concepts: list[dict[str, Any]] = []
        for chapter in spec.get("chapters") or []:
            if isinstance(chapter, dict):
                fallback_concepts.extend(
                    c for c in (chapter.get("concepts") or []) if isinstance(c, dict))
        if fallback_concepts:
            chapters.append({
                "name": "全书",
                "chapter_key": chapter_identity(volume_id, 1, "全书"),
                "concepts": fallback_concepts,
                "metadata": {
                    "volume_id": volume_id, "file_id": volume_id,
                    "volume_title": _plain_stem(volume_title),
                    "chapter_order": 1, "raw_heading": "",
                    "normalization_fallback": True,
                },
            })
            warnings.append("章节标题均被判定为来源噪声，已使用“全书”确定性兜底")
    out["chapters"] = chapters
    out["page_ranges"] = ranges
    out["section_ranges"] = section_ranges
    return out, warnings


def graph_quality(payload: dict[str, Any], *, textbook_title: str = "") -> dict[str, Any]:
    """Publication gate report. ``ok`` is required before replacing active data."""
    errors: list[str] = []
    chapters = [n for n in payload.get("nodes") or [] if n.get("kind") == "chapter"]
    edges = payload.get("edges") or []
    concept_ids = {n.get("id") for n in payload.get("nodes") or [] if n.get("kind") == "concept"}
    section_ids = {n.get("id") for n in payload.get("nodes") or [] if n.get("kind") == "section"}
    chapter_ids = {n.get("id") for n in chapters}
    # 节层（课/篇目）是合法的 PART_OF 中间层：概念→节→章。归属判定把节
    # 折叠回所属章，旧形状（概念→章）不受影响。
    section_chapter = {e.get("source"): e.get("target") for e in edges
                       if str(e.get("type") or "").lower() == "part_of"
                       and e.get("source") in section_ids
                       and e.get("target") in chapter_ids}
    membership = set()
    for e in edges:
        if str(e.get("type") or "").lower() != "part_of" \
                or e.get("source") not in concept_ids:
            continue
        target = e.get("target")
        membership.add(section_chapter.get(target, target))
    source_stem = _plain_stem(textbook_title).lower()
    empty_concept_chapters = 0
    for node in chapters:
        name = str(node.get("name") or "").strip()
        if not is_teaching_chapter(name):
            errors.append(f"章节名含文件/网址噪声：{name or '空标题'}")
        if source_stem and _plain_stem(name).lower() == source_stem:
            errors.append(f"章节名不能等于教材名：{name}")
        if node.get("id") not in membership:
            # 章结构保留但概念抽取失败（覆盖优先）：计数不阻塞发布；
            # 只有全书一个概念都没有才在下方按致命错误处理。
            empty_concept_chapters += 1
    if not chapters:
        errors.append("没有有效章节")
    if not concept_ids:
        errors.append("没有有效概念")
    orphan_sections = set(section_chapter) - section_ids
    if membership - chapter_ids:
        errors.append("存在指向不存在章节的归属关系")
    if orphan_sections:
        errors.append("存在指向不存在小节的归属关系")
    return {"ok": not errors, "errors": errors[:30], "chapter_count": len(chapters),
            "concept_count": len(concept_ids), "section_count": len(section_ids),
            "empty_concept_chapters": empty_concept_chapters}
