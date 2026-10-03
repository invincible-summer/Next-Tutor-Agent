"""全站实体搜索（B02）。

只读、按权限先过滤再排序分页；不引入持久化向量库——复用各模块元数据
（标题/别名/标签/摘要），正文检索仅在 include_content=true 且来源为本人
笔记时开启。排序：精确 ID/名称 → 当前范围标题 → 其他标题 → 摘要/内容；
同档 updated_at 降序、ID 升序稳定 tie-break。
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import readers

SEARCH_KINDS = ("chat", "note", "lesson", "file", "textbook", "concept",
                "task", "goal", "archive")

# 进程内按用户缓存（≤30s，源失效由 TTL 兜底；§20.4）。
_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# 标点在查询与标题两侧对称剥离：书名号/引号包裹的标题、句尾标点不再
# 阻断包含匹配（中英文标点各一份）。
_PUNCT_CHARS = "《》〈〉「」『』【】（）［］，。！？；：、·…()[],.!?;:\"'“”‘’"
_NORM_TABLE = str.maketrans("", "", _PUNCT_CHARS)


def _norm(text: str) -> str:
    return "".join(str(text or "").lower().translate(_NORM_TABLE).split())


def _match_tier(query_norm: str, entity_id: str, title: str,
                aliases: list[str], summary: str, *, in_scope: bool) -> int | None:
    """返回匹配档位：0=精确 ID/名称，1=范围内标题，2=其他标题，3=摘要。

    不匹配返回 None。标题/别名做双向包含：查询含于标题，或（规范化后
    ≥2 字的）标题含于查询——自然语言整句（「找到我的笔记《定积分》」）
    也能命中其中出现的完整标题。摘要只做正向包含，防长摘要误报。查询
    为空时返回 None（空查询不列出实体，由调用方决定空态行为）。
    """
    if not query_norm:
        return None
    if query_norm == _norm(entity_id) or query_norm == _norm(title):
        return 0
    for alias in aliases or []:
        if query_norm == _norm(alias):
            return 0
    tier = 1 if in_scope else 2
    title_norm = _norm(title)
    if query_norm in title_norm or (
            len(title_norm) >= 2 and title_norm in query_norm):
        return tier
    for alias in aliases or []:
        alias_norm = _norm(alias)
        if query_norm in alias_norm or (
                len(alias_norm) >= 2 and alias_norm in query_norm):
            return tier
    if query_norm in _norm(summary):
        return 3
    return None


# 查询净化（§20.4 自然语言检索）：整句 → 实体检索词。书名号内文是
# 用户显式命名的实体名，直接采用；否则剥离导航/查找动词、填充词、
# 页码与疑问尾词。两个力度：
# - 激进（默认）：连实体类词（笔记/课程/讲义…）一并剥离，适合
#   「我的高数笔记」「找到笔记《X》」这类「类词 + 名字」表达；
# - 保守：保留类词，适合类词本身是实体名一部分（「微积分讲义.pdf」）
#   的场景。调用方用 expand_query 同时检索两个力度并按档位合并。
_BOOK_TITLE_RE = re.compile(r"《([^《》]{1,120})》")
_FILLER_CORE_RE = re.compile(
    r"带[我咱]?去|打开|进入|跳转|前往|转到|导航|回到|回去|回一次|返回|"
    r"找(到|一下|出)?|查(找|一下|询)?|搜索|搜一下|检索|"
    r"帮我|麻烦|我想|我要|请|能不能|可以|一下|看看|"
    r"我(的)?|上次|上回|最近|近期|刚才|之前|以前|新的|那份|这个|那个|"
    r"第[0-9一二三四五六七八九十百零两]+页|"
    r"page\s*[0-9]{1,4}|在哪|哪里|呢|啊|吧|吗", re.IGNORECASE)
_ENTITY_WORD_RE = re.compile(
    r"工作区|学习区|课程|课件|讲义|课|笔记|教材|对话|会话|聊天记录|"
    r"文件|资料|文档|错题|题目|记录", re.IGNORECASE)


def clean_query(text: str, *, keep_entity_words: bool = False) -> str:
    """把自然语言句子净化成实体检索词；净化为空时返回原文。"""
    text = str(text or "").strip()
    if not text:
        return ""
    m = _BOOK_TITLE_RE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    cleaned = _FILLER_CORE_RE.sub("", text)
    if not keep_entity_words:
        cleaned = _ENTITY_WORD_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    return cleaned or text


def expand_query(text: str) -> list[str]:
    """两个力度的检索词（激进在前），去重去空。"""
    out, seen = [], set()
    for keep in (False, True):
        q = clean_query(text, keep_entity_words=keep)
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def search_with_expanded_query(
    student_id: str, *, text: str, kinds: list[str] | None = None,
    workspace_id: str = "", offset: int = 0, limit: int = 5,
) -> dict[str, Any]:
    """自然语言检索入口：两个力度各查一次并按档位合并（exact 优先）。

    同一实体在两个力度下都命中时保留更精确的 match_kind；排序仍遵循
    §20.4（档位 → updated_at 降序 → ID 升序）。
    """
    rank = {"exact": 0, "title": 1, "summary": 2, "content": 3}
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for needle in expand_query(text):
        out = search_site_entities(student_id, q=needle, kinds=kinds,
                                   workspace_id=workspace_id, limit=20)
        for item in out.get("items") or []:
            key = (str(item.get("entity_kind")), str(item.get("entity_id")))
            prev = merged.get(key)
            if prev is None or rank.get(item.get("match_kind"), 4) < \
                    rank.get(prev.get("match_kind"), 4):
                merged[key] = item
    ranked = sorted(
        merged.values(),
        key=lambda it: (rank.get(it.get("match_kind"), 4),
                        -float(it.get("updated_at") or 0.0),
                        it.get("entity_id")))
    limit = max(1, min(20, limit))
    offset = max(0, offset)
    return {"items": ranked[offset:offset + limit], "total": len(ranked),
            "offset": offset, "limit": limit, "complete": True}


def _item(kind: str, entity_id: str, title: str, *, workspace_id: str = "",
          subtitle: str = "", updated_at: float = 0.0, match_kind: str = "",
          snippet: str = "", target: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "entity_kind": kind,
        "entity_id": entity_id,
        "title": title[:200],
        "workspace_id": workspace_id or None,
        "subtitle": subtitle[:200],
        "updated_at": updated_at,
        "match_kind": match_kind,
        "snippet": (snippet[:240] or None),
        "target": target,
    }


def _collect_chat(student_id: str, query_norm: str, in_scope_ws: set[str],
                  workspace_id: str) -> list[dict[str, Any]]:
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core.session import list_sessions
    out = []
    for s in list_sessions():
        if (s.get("student_id") or DEFAULT_STUDENT_ID) != student_id:
            continue
        ws = str(s.get("workspace_id") or "")
        if workspace_id and ws != workspace_id:
            continue
        sid = str(s.get("session_id") or "")
        title = str(s.get("title") or "")
        tier = _match_tier(query_norm, sid, title, [], "",
                           in_scope=ws in in_scope_ws)
        if tier is None:
            continue
        out.append(_item(
            "chat", sid, title or "未命名对话", workspace_id=ws,
            subtitle="聊天辅导", updated_at=float(s.get("updated_at") or 0.0),
            match_kind="exact" if tier == 0 else "title" if tier < 3 else "summary",
            target={"kind": "chat_session", "session_id": sid}))
    return out


def _collect_notes(student_id: str, query_norm: str, include_content: bool,
                   workspace_id: str) -> list[dict[str, Any]]:
    from app import notes as notes_store
    vault = notes_store.load_vault(student_id)
    out = []
    for note in vault.notes:
        nid = str(note.get("id") or "")
        title = str(note.get("title") or "")
        tags = [str(t) for t in (note.get("tags") or [])]
        summary = str(note.get("summary") or "")
        tier = _match_tier(query_norm, nid, title, tags, summary, in_scope=True)
        content = ""
        if tier is None and include_content:
            # 正文检索：仅用户明确要求时，且只搜本人笔记（§20.4）。
            body = str(note.get("content") or "")
            pos = _norm(body).find(query_norm)
            if pos >= 0:
                tier = 3
                content = body[max(0, pos - 40):pos + 200]
        if tier is None:
            continue
        out.append(_item(
            "note", nid, title or "未命名笔记",
            subtitle="笔记", updated_at=float(note.get("updated_at") or 0.0),
            match_kind="exact" if tier == 0 else "title" if tier < 3 else "content",
            snippet=content or (summary if tier == 3 else ""),
            target={"kind": "note", "note_id": nid}))
    return out


def _collect_lessons(student_id: str, query_norm: str, workspace_id: str) -> list[dict[str, Any]]:
    from app.classroom import storage as cstore
    out = []
    for w in readers.owned_workspaces(student_id):
        ws = w["workspace_id"]
        if workspace_id and ws != workspace_id:
            continue
        # 索引的 lessons 是 {lesson_id: entry} 字典（classroom_store 真实形状）。
        lessons = cstore.read_index(student_id, ws).get("lessons") or {}
        entries = (lessons.values() if isinstance(lessons, dict)
                   else lessons)
        for les in entries:
            if not isinstance(les, dict):
                continue
            lid = str(les.get("lesson_id") or "")
            title = str(les.get("title") or "")
            tier = _match_tier(query_norm, lid, title, [], "", in_scope=True)
            if tier is None:
                continue
            # updated_at 为 ISO 字符串；解析失败按 0 排序（不编造时间）。
            updated = les.get("updated_at")
            if isinstance(updated, str):
                try:
                    from datetime import datetime
                    updated = datetime.fromisoformat(updated).timestamp()
                except ValueError:
                    updated = 0.0
            out.append(_item(
                "lesson", lid, title, workspace_id=ws, subtitle="备课上课",
                updated_at=float(updated or 0.0),
                match_kind="exact" if tier == 0 else "title",
                target={"kind": "lesson", "workspace_id": ws,
                        "lesson_id": lid}))
    return out


def _collect_files(student_id: str, query_norm: str, workspace_id: str) -> list[dict[str, Any]]:
    from app.core.library import load_library
    lib = load_library(student_id)
    out = []
    for f in lib.files:
        fid = str(f.get("id") or "")
        name = str(f.get("filename") or "")
        # 去扩展名的文件名作为别名：用户说「微积分讲义」时文件
        # 「微积分讲义.pdf」可精确命中（扩展名阻断包含匹配）。
        stem = name.rsplit(".", 1)[0] if "." in name else name
        aliases = [stem] if stem and stem != name else []
        tier = _match_tier(query_norm, fid, name, aliases, "", in_scope=True)
        if tier is None:
            continue
        folder = str(f.get("folder_id") or "")
        out.append(_item(
            "file", fid, name, subtitle="资料文件",
            updated_at=float(f.get("uploaded_at") or 0.0),
            match_kind="exact" if tier == 0 else "title",
            target={"kind": "file", "file_id": fid,
                    **({"folder_id": folder} if folder else {})}))
    return out


def _collect_textbooks(student_id: str, query_norm: str) -> list[dict[str, Any]]:
    from app.core import textbook as tb_store
    from app.core.library import load_library
    out = []
    for sid in (student_id, tb_store.PUBLIC_STUDENT_ID):
        lib = load_library(sid)
        existing = {f["id"] for f in lib.files}
        for tb in tb_store.load_textbooks(sid):
            if tb.get("kind") == "group":
                if not any(fid in existing for fid in (tb.get("file_ids") or [])):
                    continue
            elif tb.get("file_id") not in existing:
                continue
            tid = str(tb.get("id") or "")
            title = str(tb.get("title") or "")
            tier = _match_tier(query_norm, tid, title, [], "", in_scope=True)
            if tier is None:
                continue
            out.append(_item(
                "textbook", tid, title,
                subtitle="公用教材" if sid == tb_store.PUBLIC_STUDENT_ID else "我的教材",
                updated_at=float(tb.get("updated_at") or 0.0),
                match_kind="exact" if tier == 0 else "title",
                target={"kind": "textbook", "textbook_id": tid}))
    return out


def _collect_concepts(student_id: str, query_norm: str,
                      workspace_id: str) -> list[dict[str, Any]]:
    """概念检索：当前范围（或全部本人工作区）已评价概念（标题/键）。"""
    wids = [workspace_id] if workspace_id else [
        w["workspace_id"] for w in readers.owned_workspaces(student_id)]
    out = []
    seen: set[str] = set()
    for wid in wids[:6]:
        try:
            from app.agents.student_model.evaluation import projections
            from app.agents.student_model.evaluation.scope import (
                get_scope_resolver)
            scope = get_scope_resolver().resolve(student_id, wid)
            for view in projections.concept_views(student_id, scope):
                key = str(getattr(view, "concept_key", "") or "")
                label = str(getattr(view, "label", "") or key)
                if not key or key in seen:
                    continue
                tier = _match_tier(query_norm, key, label, [], "",
                                   in_scope=True)
                if tier is None:
                    continue
                seen.add(key)
                out.append(_item(
                    "concept", key, label, workspace_id=wid, subtitle="知识图谱概念",
                    match_kind="exact" if tier == 0 else "title",
                    target={"kind": "concept", "concept_id": key,
                            "workspace_id": wid}))
        except Exception:
            continue
    return out


def _collect_tasks_and_goals(student_id: str, query_norm: str,
                             workspace_id: str) -> list[dict[str, Any]]:
    from app.agents.learning_orchestration.store import load_state
    state = load_state(student_id)
    out = []
    for goal in state.goals:
        gid = str(goal.id or "")
        tier = _match_tier(query_norm, gid, goal.title,
                           [goal.description], "", in_scope=True)
        if tier is None:
            continue
        out.append(_item(
            "goal", gid, goal.title or "学习目标",
            workspace_id=goal.workspace_id, subtitle="学习目标",
            updated_at=float(goal.updated_at or 0.0),
            match_kind="exact" if tier == 0 else "title",
            target={"kind": "goal", "goal_id": gid}))
    for task in state.daily_tasks:
        tid = str(task.id or "")
        title = task.title or task.concept_name or ""
        if workspace_id and task.workspace_id and task.workspace_id != workspace_id:
            continue
        tier = _match_tier(query_norm, tid, title, [], "", in_scope=True)
        if tier is None:
            continue
        out.append(_item(
            "task", tid, title or "学习任务", workspace_id=task.workspace_id,
            subtitle=f"学习任务 · {task.day}",
            updated_at=float(task.updated_at or 0.0) if hasattr(task, "updated_at") else 0.0,
            match_kind="exact" if tier == 0 else "title",
            target={"kind": "task", "task_id": tid}))
    return out


def _collect_archive(student_id: str, query_norm: str) -> list[dict[str, Any]]:
    from app.core.trash import list_items
    out = []
    for item in list_items(student_id):
        iid = str(item.get("id") or "")
        title = str(item.get("title") or "")
        rtype = str(item.get("resource_type") or "")
        tier = _match_tier(query_norm, iid, title, [rtype], "", in_scope=True)
        if tier is None:
            continue
        out.append(_item(
            "archive", iid, title, subtitle="归档",
            updated_at=float(item.get("deleted_at") or 0.0),
            match_kind="exact" if tier == 0 else "title",
            target={"kind": "archive_item", "item_id": iid,
                    "resource_type": rtype}))
    return out


def search_site_entities(
    student_id: str, *, q: str, kinds: list[str] | None = None,
    workspace_id: str = "", offset: int = 0, limit: int = 5,
    include_content: bool = False,
) -> dict[str, Any]:
    """§20.4 全站检索：权限过滤在排序前；返回分页与完整性标记。"""
    q = str(q or "").strip()
    if not q:
        return {"items": [], "total": 0, "offset": max(0, offset),
                "limit": max(1, min(20, limit)), "complete": True}
    wanted = [k for k in (kinds or list(SEARCH_KINDS)) if k in SEARCH_KINDS]
    if not wanted:
        wanted = list(SEARCH_KINDS)
    limit = max(1, min(20, limit))
    offset = max(0, offset)
    query_norm = _norm(q)
    in_scope_ws = {workspace_id} if workspace_id else {
        w["workspace_id"] for w in readers.owned_workspaces(student_id)}

    cache_key = f"{student_id}|{query_norm}|{','.join(wanted)}|{workspace_id}|{include_content}"
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        ranked = cached[1]
    else:
        candidates: list[dict[str, Any]] = []
        errors: list[str] = []

        def _tasks_only() -> list[dict[str, Any]]:
            return [it for it in _collect_tasks_and_goals(
                student_id, query_norm, workspace_id)
                if it["entity_kind"] == "task"]

        def _goals_only() -> list[dict[str, Any]]:
            return [it for it in _collect_tasks_and_goals(
                student_id, query_norm, workspace_id)
                if it["entity_kind"] == "goal"]

        collectors = {
            "chat": lambda: _collect_chat(student_id, query_norm, in_scope_ws,
                                          workspace_id),
            "note": lambda: _collect_notes(student_id, query_norm,
                                           include_content, workspace_id),
            "lesson": lambda: _collect_lessons(student_id, query_norm,
                                               workspace_id),
            "file": lambda: _collect_files(student_id, query_norm, workspace_id),
            "textbook": lambda: _collect_textbooks(student_id, query_norm),
            "concept": lambda: _collect_concepts(student_id, query_norm,
                                                 workspace_id),
            "task": _tasks_only,
            "goal": _goals_only,
            "archive": lambda: _collect_archive(student_id, query_norm),
        }
        for kind in wanted:
            if kind not in collectors:
                continue
            try:
                candidates.extend(collectors[kind]())
            except Exception:
                errors.append(kind)
        # 排序：档位升序 → updated_at 降序 → ID 升序（稳定 tie-break）。
        ranked = sorted(
            candidates,
            key=lambda it: (
                {"exact": 0, "title": 1, "summary": 2, "content": 3}.get(
                    it["match_kind"], 4),
                -float(it["updated_at"] or 0.0),
                it["entity_id"],
            ))
        if len(_cache) > 128:
            _cache.clear()
        _cache[cache_key] = (now, ranked)

    total = len(ranked)
    # task/goal 共用收集器可能重复计入不同 kind 查询；按 (kind,id) 去重。
    seen: set[tuple[str, str]] = set()
    unique = [it for it in ranked
              if (it["entity_kind"], it["entity_id"]) not in seen
              and not seen.add((it["entity_kind"], it["entity_id"]))]
    total = len(unique)
    items = unique[offset:offset + limit]
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "complete": True}
