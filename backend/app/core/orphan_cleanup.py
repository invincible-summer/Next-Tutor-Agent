"""孤儿运行时数据扫描与清理（管理台"数据清理"后端）。

孤儿数据 = 按 owner 命名/归属、但 owner 不在 users/accounts.json 里的运行时
数据：单元测试直写生产目录的合成 ID（rs_*、hook_reg_* 等）与自助注销/历史
删除账号的遗物（usr_*）。另含两类"无主文件"：失去会话的转写、不再被任何
保留会话或回收站载荷引用的 trace / 会话上传。

保护集（永不清）：
- 注册账号 ID（路由层由 id_store.list_users() 生成）；
- 共享命名空间 public / student_default；
- 全局策略文件（students/prompt_memory_policy.json、trash/policy.json、
  knowledge/custom/ 下的非目录杂项如 legacy 标记）。

路径一律在调用时从各归属模块读取（session_mod._SESSIONS_DIR 等），测试
patch 兄弟模块常量即可重定向全部 IO；所有名字拼接都过 Path(...).name 防路径
穿越。清理幂等，可重复执行。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .account_data import _dir_size, _read_json, _safe

# 永久受保护的共享命名空间（公用教材库 / 游客）。
SHARED_NAMESPACES = frozenset({"public", "student_default"})
# students/ 根下的全局策略文件（无 owner 前缀）。
_PROTECTED_STUDENT_FILES = frozenset({"prompt_memory_policy.json"})

CATEGORIES = ("students", "sessions", "transcripts", "traces", "uploads",
              "workspaces", "library", "trash", "notes", "knowledge")

_SAMPLE_LIMIT = 6


def _norm_ids(protected_ids) -> set[str]:
    ids = {_safe(i) for i in protected_ids if i}
    ids.discard("")
    return ids | set(SHARED_NAMESPACES)


def _collect_trace_ids(node: Any, out: set[str]) -> None:
    """递归收集 JSON 里 trace_ids 数组的成员（会话与回收站载荷均此形态）。"""
    if isinstance(node, dict):
        ids = node.get("trace_ids")
        if isinstance(ids, list):
            out.update(_safe(str(t)) for t in ids if t)
        for v in node.values():
            _collect_trace_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_trace_ids(v, out)


def _collect_orphans(protected_ids) -> dict[str, list[Path]]:
    """单次遍历，返回每类待清理路径（文件=unlink / 目录=rmtree）。"""
    from app.agents.knowledge import store as kgs_mod
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.agents.student_model import store as sm_store
    from app.core import context as context_mod
    from app.core import library as lib_mod
    from app.core import notes as notes_mod
    from app.core import session as session_mod
    from app.core import trash as trash_mod
    from app.core import workspace as ws_mod
    from app.core.config import settings

    protected = _norm_ids(protected_ids)
    out: dict[str, list[Path]] = {c: [] for c in CATEGORIES}

    # --- students/<owner>.*：owner = 首个 "." 前的前缀 ---
    if sm_store._STUDENTS_DIR.is_dir():
        for p in sorted(sm_store._STUDENTS_DIR.iterdir()):
            if not p.is_file() or p.name in _PROTECTED_STUDENT_FILES:
                continue
            owner = p.name.split(".", 1)[0]
            if owner and owner not in protected:
                out["students"].append(p)

    # --- 会话：按文件内 student_id 归属；保留会话登记其 trace/上传引用 ---
    kept_session_ids: set[str] = set()
    referenced_traces: set[str] = set()
    kept_fids: set[str] = set()
    if session_mod._SESSIONS_DIR.is_dir():
        for p in sorted(session_mod._SESSIONS_DIR.glob("*.json")):
            d = _read_json(p)
            if d is None:      # 损坏/未知 JSON 一律跳过，保守不动
                continue
            owner = _safe(str(d.get("student_id") or DEFAULT_STUDENT_ID))
            if owner in protected:
                sid = _safe(str(d.get("session_id") or p.stem))
                kept_session_ids.add(sid)
                referenced_traces.update(
                    _safe(str(t)) for t in d.get("trace_ids") or [] if t)
                for meta in d.get("knowledge_files") or []:
                    fid = _safe(str((meta or {}).get("id", "")))
                    if fid:
                        kept_fids.add(fid)
                kept_fids.update(_safe(str(f)) for f in
                                 d.get("pending_material_file_ids") or [])
            else:
                out["sessions"].append(p)

    # --- 转写：session_id 不在任何保留会话里即为孤儿 ---
    if context_mod._TRANSCRIPT_DIR.is_dir():
        for p in sorted(context_mod._TRANSCRIPT_DIR.glob("*.transcript.jsonl")):
            sid = p.name[: -len(".transcript.jsonl")]
            if sid and sid not in kept_session_ids:
                out["transcripts"].append(p)

    # --- trace：保留会话与受保护 owner 的回收站载荷引用之外的都算孤儿 ---
    items_root = trash_mod._TRASH_DIR / "items"
    if items_root.is_dir():
        for owner_dir in items_root.iterdir():
            if not owner_dir.is_dir() or owner_dir.name not in protected:
                continue
            for jf in owner_dir.rglob("*.json"):
                _collect_trace_ids(_read_json(jf), referenced_traces)
    traces_dir = Path(settings.trace_dir)
    if traces_dir.is_dir():
        for p in sorted(traces_dir.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            if name.startswith("trace_") and name.endswith(".jsonl"):
                if name[len("trace_"):-len(".jsonl")] not in referenced_traces:
                    out["traces"].append(p)
            elif name.startswith("tool_spill_"):
                out["traces"].append(p)

    # --- 会话上传：保留会话/工作区引用之外的文件（向量另做 best-effort）---
    uploads_dir = Path(settings.trace_dir).parent / "uploads"
    if ws_mod._WORKSPACES_DIR.is_dir():
        for p in sorted(ws_mod._WORKSPACES_DIR.glob("*.json")):
            d = _read_json(p)
            if d is None:
                continue
            owner = _safe(str(d.get("student_id") or DEFAULT_STUDENT_ID))
            if owner not in protected:
                out["workspaces"].append(p)
            else:
                for meta in d.get("knowledge_files") or []:
                    fid = _safe(str((meta or {}).get("id", "")))
                    if fid:
                        kept_fids.add(fid)
                for key in ("workspace_file_ids", "selected_file_ids"):
                    kept_fids.update(_safe(str(f)) for f in d.get(key) or [])
    if uploads_dir.is_dir():
        for p in sorted(uploads_dir.iterdir()):
            if not p.is_file():
                continue
            fid = p.name.split(".", 1)[0]
            if fid and fid not in kept_fids:
                out["uploads"].append(p)

    # --- 资料库：孤儿索引/数据目录 + public 命名空间无索引的数据残件 ---
    lib_dir = lib_mod._LIBRARY_DIR
    if lib_dir.is_dir():
        for p in sorted(lib_dir.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            if name.endswith(".textbooks.json"):
                key = name[: -len(".textbooks.json")]
            elif name.endswith(".json"):
                key = name[: -len(".json")]
            elif ".bak" in name:
                key = name.split(".bak", 1)[0]
            else:
                key = ""
            if key and key not in protected:
                out["library"].append(p)
        data_root = lib_dir / "data"
        if data_root.is_dir():
            for d in data_root.iterdir():
                if d.is_dir() and d.name not in protected:
                    out["library"].append(d)
            # public 只有管理员写入，不存在学生上传的并发竞态，可安全比对索引。
            pub_idx = _read_json(lib_dir / "public.json")
            if pub_idx is not None:
                known = {_safe(str((f or {}).get("id", "")))
                         for f in pub_idx.get("files") or []}
                pub_data = data_root / "public"
                if pub_data.is_dir():
                    for p in sorted(pub_data.iterdir()):
                        if p.is_file():
                            fid = p.name.split(".", 1)[0]
                            if fid and fid not in known:
                                out["library"].append(p)

    # --- 回收站：孤儿 owner 目录 + 空目录（注册账号的空壳也一并清） ---
    if items_root.is_dir():
        for d in sorted(items_root.iterdir()):
            if not d.is_dir():
                continue
            if d.name not in protected:
                out["trash"].append(d)
                continue
            try:
                next(d.iterdir())
            except StopIteration:
                out["trash"].append(d)
            except OSError:
                pass
    prefs = trash_mod._TRASH_DIR / "preferences"
    if prefs.is_dir():
        for p in sorted(prefs.glob("*.json")):
            if p.name[: -len(".json")] not in protected:
                out["trash"].append(p)

    # --- 笔记 / 知识图谱：目录名不在保护集即为孤儿 ---
    if notes_mod._NOTES_DIR.is_dir():
        for d in sorted(notes_mod._NOTES_DIR.iterdir()):
            if d.is_dir() and d.name not in protected:
                out["notes"].append(d)
    if kgs_mod._CUSTOM_DIR.is_dir():
        for d in sorted(kgs_mod._CUSTOM_DIR.iterdir()):
            if d.is_dir() and d.name not in protected:
                out["knowledge"].append(d)
    return out


def _category_stats(paths: list[Path]) -> dict[str, Any]:
    return {
        "items": len(paths),
        "bytes": sum(_dir_size(p) for p in paths),
        "samples": [p.name for p in paths[:_SAMPLE_LIMIT]],
    }


def scan_orphans(protected_ids) -> dict[str, Any]:
    """扫描孤儿数据，返回逐类别 items/bytes/samples 与总计。只读，无副作用。"""
    found = _collect_orphans(protected_ids)
    categories = {c: _category_stats(ps) for c, ps in found.items()}
    return {
        "protected_ids": sorted(_norm_ids(protected_ids)),
        "categories": categories,
        "total_items": sum(s["items"] for s in categories.values()),
        "total_bytes": sum(s["bytes"] for s in categories.values()),
    }


def _best_effort_vector_cleanup(fids: set[str]) -> None:
    from . import vector_store
    for fid in fids:
        try:
            vector_store.delete_file(fid)
            vector_store.delete_scope(f"file:{fid}")
            vector_store.delete_scope(f"folder:{fid}")
        except Exception:
            pass


def purge_orphans(protected_ids, categories=None, dry_run=False) -> dict[str, Any]:
    """删除全部（或指定类别）孤儿数据，返回逐类别删除数与释放字节。

    dry_run=True 只统计不删除。categories 须为 CATEGORIES 子集。幂等。
    """
    if categories is None:
        categories = list(CATEGORIES)
    unknown = set(categories) - set(CATEGORIES)
    if unknown:
        raise ValueError(f"unknown categories: {sorted(unknown)}")
    found = _collect_orphans(protected_ids)
    vector_fids: set[str] = set()
    report_categories: dict[str, dict[str, Any]] = {}
    deleted_total, freed_total = 0, 0
    for cat in CATEGORIES:
        paths = found.get(cat, []) if cat in categories else []
        stats = _category_stats(paths)
        if cat in ("uploads", "library"):
            vector_fids.update(p.name.split(".", 1)[0]
                               for p in paths if p.is_file())
        deleted, freed = 0, 0
        for p in paths:
            freed += _dir_size(p)
            if dry_run:
                deleted += 1
                continue
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                pass
        report_categories[cat] = {"deleted": deleted, "bytes": freed,
                                  "total": stats["items"]}
        deleted_total += deleted
        freed_total += freed
    if vector_fids and not dry_run:
        vector_fids.discard("")
        _best_effort_vector_cleanup(vector_fids)
    return {
        "status": "dry_run" if dry_run else "purged",
        "dry_run": bool(dry_run),
        "categories": report_categories,
        "total_deleted": deleted_total,
        "total_bytes": freed_total,
    }
