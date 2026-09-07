"""Admin-grade per-account storage accounting and destructive cleanup.

一个账号的运行时数据分散在多个根目录（chat_history/ 的会话/转写/工作区/
资料库/回收站、students/ 学习档案、knowledge/custom/ 图谱、notes/ 笔记、
backend/uploads 会话上传与 backend/traces 调用追踪）。本模块把它们当作
一个账号的整体来统计与不可恢复地清理：

- scan_storage(user_ids)：单次遍历磁盘，返回每账号的分桶占用字节数。
- clear_chat_data(user_id, scope)：只清聊天侧数据。scope="all" 连会话一起
  删；scope="uploads_only" 保留会话文本，仅删上传的原始/提取文件。
- purge_account(user_id)：clear(all) + 笔记/学习档案/知识图谱/回收站残留，
  最后一步才删 accounts.json 里的账号记录。

路径一律在调用时从各归属模块读取（session_mod._SESSIONS_DIR 等），测试
patch 兄弟模块常量即可重定向全部 IO。清理目标只应是 accounts.json 里真实
存在的账号 id（路由层已校验）；public / student_default 共享命名空间天然
不会被这些函数指向。所有文件名拼接都过 _safe() 防路径穿越。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

# 回收站条目类型（core/trash.py _TYPES）按清理策略分组：
# 聊天/材料类条目随聊天清理一起永久清掉；笔记与图谱条目只在整账号
# 彻底删除时处理。
_CHAT_TRASH_TYPES = {"session", "library_file", "library_folder",
                     "textbook", "textbook_volume", "workspace"}
_FILE_TRASH_TYPES = {"library_file", "library_folder",
                     "textbook", "textbook_volume"}


def _safe(value: str) -> str:
    return Path(str(value or "")).name


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return _file_size(path)
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += _file_size(p)
    return total


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _empty_buckets() -> dict[str, Any]:
    return {
        "chat_bytes": 0,       # 会话/转写/追踪/工作区/资料库索引与数据
        "uploads_bytes": 0,    # backend/uploads 会话上传（按会话归属统计）
        "notes_bytes": 0,
        "students_bytes": 0,
        "knowledge_bytes": 0,
        "trash_bytes": 0,
        "session_count": 0,
        "file_count": 0,
    }


def _total_of(buckets: dict[str, Any]) -> int:
    return int(sum(buckets.get(k, 0) for k in
                   ("chat_bytes", "uploads_bytes", "notes_bytes",
                    "students_bytes", "knowledge_bytes", "trash_bytes")))


def scan_storage(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    """单次磁盘遍历，统计每个账号在各根目录下的占用。

    会话/工作区是平铺文件、归属靠文件内 student_id 戳，所以这两个根必须
    逐文件解析；其余根按目录名直接归属。返回 {uid: buckets}（含
    total_bytes），未知目录/解析失败一律跳过，绝不抛出。
    """
    from app.agents.knowledge import store as kgs_mod
    from app.agents.student_model import store as sm_store
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core import context as context_mod
    from app.core import library as lib_mod
    from app.core import notes as notes_mod
    from app.core import session as session_mod
    from app.core import trash as trash_mod
    from app.core import workspace as ws_mod
    from app.core.config import settings

    out: dict[str, dict[str, Any]] = {_safe(u): _empty_buckets() for u in user_ids}
    if not out:
        return out
    uploads_dir = Path(settings.trace_dir).parent / "uploads"
    traces_dir = Path(settings.trace_dir)

    # --- 会话：归属 + 转写/追踪/上传 归属统计 ---
    if session_mod._SESSIONS_DIR.is_dir():
        for p in session_mod._SESSIONS_DIR.glob("*.json"):
            d = _read_json(p)
            if d is None:
                continue
            owner = d.get("student_id") or DEFAULT_STUDENT_ID
            buckets = out.get(owner)
            if buckets is None:
                continue
            sid = _safe(str(d.get("session_id") or p.stem))
            buckets["chat_bytes"] += _file_size(p)
            buckets["session_count"] += 1
            buckets["chat_bytes"] += _file_size(context_mod.transcript_path(sid))
            for tid in d.get("trace_ids") or []:
                buckets["chat_bytes"] += _file_size(
                    traces_dir / f"trace_{_safe(str(tid))}.jsonl")
            fids: set[str] = set()
            for meta in d.get("knowledge_files") or []:
                fid = _safe(str((meta or {}).get("id", "")))
                if fid:
                    fids.add(fid)
            buckets["file_count"] += len(fids)
            for fid in fids | {_safe(str(f)) for f in
                               d.get("pending_material_file_ids") or []}:
                buckets["uploads_bytes"] += _file_size(uploads_dir / f"{fid}.txt")
                for fp in uploads_dir.glob(f"{fid}.orig*"):
                    buckets["uploads_bytes"] += _file_size(fp)

    # --- 工作区：JSON + legacy 共享上传目录 ---
    if ws_mod._WORKSPACES_DIR.is_dir():
        for p in ws_mod._WORKSPACES_DIR.glob("*.json"):
            d = _read_json(p)
            if d is None:
                continue
            owner = d.get("student_id") or DEFAULT_STUDENT_ID
            buckets = out.get(owner)
            if buckets is None:
                continue
            buckets["chat_bytes"] += _file_size(p)
            # 直接拼路径而非 workspace_upload_dir()：后者会 mkdir，统计须无副作用。
            buckets["chat_bytes"] += _dir_size(
                ws_mod._WORKSPACES_DIR / "uploads" / _safe(str(d.get("workspace_id") or p.stem)))

    # --- 资料库：索引文件 + 数据目录 ---
    for uid, buckets in out.items():
        key = lib_mod._key(uid)
        idx = lib_mod._LIBRARY_DIR / f"{key}.json"
        buckets["chat_bytes"] += _file_size(idx)
        buckets["chat_bytes"] += _file_size(lib_mod._LIBRARY_DIR / f"{key}.textbooks.json")
        for p in lib_mod._LIBRARY_DIR.glob(f"{key}.bak*"):
            buckets["chat_bytes"] += _file_size(p)
        buckets["chat_bytes"] += _dir_size(lib_mod._LIBRARY_DIR / "data" / key)
        index = _read_json(idx)
        if index is not None and isinstance(index.get("files"), list):
            buckets["file_count"] += len(index["files"])

    # --- 回收站 / 笔记 / 学习档案 / 知识图谱：目录直取 ---
    for uid, buckets in out.items():
        buckets["trash_bytes"] += _dir_size(trash_mod._TRASH_DIR / "items" / uid)
        buckets["notes_bytes"] += _dir_size(notes_mod._NOTES_DIR / notes_mod._key(uid))
        if sm_store._STUDENTS_DIR.is_dir():
            for p in sm_store._STUDENTS_DIR.glob(f"{uid}*"):
                buckets["students_bytes"] += _file_size(p)
        buckets["knowledge_bytes"] += _dir_size(kgs_mod._CUSTOM_DIR / kgs_mod._safe_name(uid))

    for buckets in out.values():
        buckets["total_bytes"] = _total_of(buckets)
    return out


def _owned_session_files(uid: str) -> list[tuple[Path, dict[str, Any]]]:
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core import session as session_mod
    if not session_mod._SESSIONS_DIR.is_dir():
        return []
    out = []
    for p in session_mod._SESSIONS_DIR.glob("*.json"):
        d = _read_json(p)
        if d is None:
            continue
        if (d.get("student_id") or DEFAULT_STUDENT_ID) == uid:
            out.append((p, d))
    return out


def _forget_chat_memory(uid: str, session_ids: list[str]) -> None:
    """逐会话精确遗忘可归属的聊天记忆（镜像 trash._purge_session_memory 与
    _detach_learning_source_ids 的行为；不可安全分解的聚合档案保留）。"""
    from app.agents.memory.prompt_memory import forget_session_contribution
    from app.agents.memory.store import remove_episodes_for_session
    from app.core.learning_records import detach_source_session
    from app.core.quiz_recent import detach_session_source
    for sid in session_ids:
        for forget in (lambda: forget_session_contribution(uid, sid),
                       lambda: remove_episodes_for_session(uid, sid),
                       lambda: detach_source_session(uid, sid),
                       lambda: detach_session_source(uid, sid)):
            try:
                forget()
            except Exception:
                pass


def _delete_upload_files(fids: set[str]) -> None:
    """删除 backend/uploads 里的会话上传（提取文本 + 原始二进制）+ 向量。"""
    from app.core.knowledge_store import KnowledgeStore
    uploads_dir = KnowledgeStore().upload_dir
    for fid in fids:
        try:
            (uploads_dir / f"{fid}.txt").unlink(missing_ok=True)
            for fp in uploads_dir.glob(f"{fid}.orig*"):
                fp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            from . import vector_store
            vector_store.delete_file(fid)
        except Exception:
            pass


def _rewrite_json(path: Path, mutate) -> None:
    d = _read_json(path)
    if d is None:
        return
    mutate(d)
    with file_lock(path):
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))


def _clear_sessions_all(uid: str) -> int:
    """删除该账号全部会话及其转写、追踪、上传与可归属记忆。"""
    from app.core import context as context_mod
    from app.core import session as session_mod
    from app.core.config import settings
    traces_dir = Path(settings.trace_dir)
    owned = _owned_session_files(uid)
    _forget_chat_memory(uid, [_safe(str(d.get("session_id") or p.stem))
                              for p, d in owned])
    for p, d in owned:
        sid = _safe(str(d.get("session_id") or p.stem))
        fids = {_safe(str((m or {}).get("id", ""))) for m in d.get("knowledge_files") or []}
        fids |= {_safe(str(f)) for f in d.get("pending_material_file_ids") or []}
        fids.discard("")
        session_mod.delete_session(sid)  # 会话 JSON + 其上传 + session: 向量
        _delete_upload_files(fids)      # 兜底：pending 未入 knowledge_files 的
        try:
            context_mod.transcript_path(sid).unlink(missing_ok=True)
        except OSError:
            pass
        for tid in d.get("trace_ids") or []:
            try:
                (traces_dir / f"trace_{_safe(str(tid))}.jsonl").unlink(missing_ok=True)
            except OSError:
                pass
    return len(owned)


def _clear_workspaces(uid: str) -> int:
    """删除该账号全部工作区（复用 delete_workspace 的级联：成员会话解绑、
    工作区专属资料夹、legacy 上传目录、向量）。单个工作区文件损坏时跳过
    该项继续，不让整个清理 500。"""
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core import workspace as ws_mod
    count = 0
    if ws_mod._WORKSPACES_DIR.is_dir():
        for p in sorted(ws_mod._WORKSPACES_DIR.glob("*.json")):
            d = _read_json(p)
            if d is None:
                continue
            if (d.get("student_id") or DEFAULT_STUDENT_ID) != uid:
                continue
            try:
                if ws_mod.delete_workspace(_safe(str(d.get("workspace_id") or p.stem))):
                    count += 1
            except Exception:
                # delete_workspace 内部有裸 json.loads 等路径；残留文件由
                # 下面的兜底 rmtree 清掉，保证占用最终被释放。
                try:
                    p.unlink(missing_ok=True)
                    shutil.rmtree(
                        ws_mod._WORKSPACES_DIR / "uploads" / _safe(str(d.get("workspace_id") or p.stem)),
                        ignore_errors=True)
                    count += 1
                except OSError:
                    pass
    return count


def _clear_library(uid: str, remove_folders: bool) -> int:
    """清空资料库文件。remove_folders=False 保留文件夹结构（索引重写为空
    文件列表）；True 连索引文件一起删（scope=all，聊天侧整体清除）。"""
    from app.core import library as lib_mod
    lib = lib_mod.load_library(uid)
    removed_ids = [str(f.get("id", "")) for f in list(lib.files)]
    folder_ids = [str(f.get("id", "")) for f in list(getattr(lib, "folders", []))]
    for fid in removed_ids:
        lib.remove_file(fid)  # 数据文件 + 进程级 chunk 缓存
    lib_mod.save_library(lib)
    try:
        from . import vector_store
        for fid in removed_ids:
            vector_store.delete_file(fid)
            vector_store.delete_scope(f"file:{fid}")
        if remove_folders:
            for fid in folder_ids:
                vector_store.delete_scope(f"folder:{fid}")
    except Exception:
        pass
    if remove_folders:
        key = lib_mod._key(uid)
        for p in [lib_mod._LIBRARY_DIR / f"{key}.json",
                  lib_mod._LIBRARY_DIR / f"{key}.textbooks.json"]:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        for p in lib_mod._LIBRARY_DIR.glob(f"{key}.bak*"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(lib_mod._LIBRARY_DIR / "data" / key, ignore_errors=True)
    return len(removed_ids)


def _purge_trash_types(uid: str, types: set[str]) -> int:
    from app.core import trash as trash_mod
    count = 0
    for item in trash_mod.list_items(uid):
        if item.get("resource_type") not in types:
            continue
        try:
            trash_mod.purge_item(uid, str(item.get("id", "")))
            count += 1
        except Exception:
            pass
    return count


def _strip_uploads_only(uid: str) -> tuple[int, int]:
    """仅删上传文件：剥离会话/工作区 JSON 里的文件元数据并删磁盘文件，
    会话消息文本、转写与追踪全部保留。"""
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core import session as session_mod
    from app.core import workspace as ws_mod

    sessions_touched = 0
    for p, _d in _owned_session_files(uid):
        fids: set[str] = set()
        def _mutate(d: dict[str, Any], fids: set[str] = fids) -> None:
            fids.update(_safe(str((m or {}).get("id", "")))
                        for m in d.get("knowledge_files") or [])
            fids.update(_safe(str(f)) for f in d.get("pending_material_file_ids") or [])
            d["knowledge_files"] = []
            d["pending_material_file_ids"] = []
        _rewrite_json(p, _mutate)
        fids.discard("")
        _delete_upload_files(fids)
        sessions_touched += 1

    workspaces_touched = 0
    if ws_mod._WORKSPACES_DIR.is_dir():
        for p in ws_mod._WORKSPACES_DIR.glob("*.json"):
            d = _read_json(p)
            if d is None or (d.get("student_id") or DEFAULT_STUDENT_ID) != uid:
                continue
            fids: set[str] = set()
            def _mutate_ws(d2: dict[str, Any], fids: set[str] = fids) -> None:
                fids.update(_safe(str((m or {}).get("id", "")))
                            for m in d2.get("knowledge_files") or [])
                d2["knowledge_files"] = []      # legacy 共享上传引用
                d2["workspace_file_ids"] = []   # 资料库文件引用（由 _clear_library 清）
                d2["selected_file_ids"] = []
            _rewrite_json(p, _mutate_ws)
            fids.discard("")
            _delete_upload_files(fids)
            ws_id = _safe(str(d.get("workspace_id") or p.stem))
            shutil.rmtree(ws_mod._WORKSPACES_DIR / "uploads" / ws_id, ignore_errors=True)
            workspaces_touched += 1
    return sessions_touched, workspaces_touched


def clear_chat_data(user_id: str, scope: str = "all") -> dict[str, Any]:
    """清理账号的聊天侧数据，返回释放报告。账号本身与其余数据保留。

    scope="all"：会话、转写、追踪、会话上传、工作区、资料库、聊天类回收站
    条目与可归属记忆。
    scope="uploads_only"：仅上传的原始/提取文件（backend/uploads、资料库数
    据、工作区上传），会话文本保留。
    """
    if scope not in ("all", "uploads_only"):
        raise ValueError(f"unknown scope: {scope}")
    uid = _safe(user_id)
    before = scan_storage([uid]).get(uid, _empty_buckets())
    report: dict[str, Any] = {"scope": scope}
    if scope == "all":
        report["sessions"] = _clear_sessions_all(uid)
        report["workspaces"] = _clear_workspaces(uid)
        report["library_files"] = _clear_library(uid, remove_folders=True)
        report["trash_items"] = _purge_trash_types(uid, _CHAT_TRASH_TYPES)
        # 聊天类条目清完后若 owner 目录已空则顺手移除（非聊天条目仍在时
        # rmdir 自然失败跳过）。
        from app.core import trash as trash_mod
        try:
            (trash_mod._TRASH_DIR / "items" / uid).rmdir()
        except OSError:
            pass
    else:
        sessions_touched, workspaces_touched = _strip_uploads_only(uid)
        report["sessions"] = sessions_touched
        report["workspaces"] = workspaces_touched
        report["library_files"] = _clear_library(uid, remove_folders=False)
        report["trash_items"] = _purge_trash_types(uid, _FILE_TRASH_TYPES)
    after = scan_storage([uid]).get(uid, _empty_buckets())
    report["freed_bytes"] = max(0, _total_of(before) - _total_of(after))
    return report


def purge_account(user_id: str) -> dict[str, Any]:
    """彻底删除账号：聊天侧全清 + 笔记/学习档案/知识图谱/回收站残留，
    最后删账号记录。不可恢复；调用方须确保目标不是管理员账号。"""
    from app.agents.knowledge import store as kgs_mod
    from app.agents.student_model import store as sm_store
    from app.core import notes as notes_mod
    from app.core import trash as trash_mod
    from app.identity import store as id_store

    uid = _safe(user_id)
    before = scan_storage([uid]).get(uid, _empty_buckets())
    report = clear_chat_data(uid, scope="all")

    # 回收站残留（笔记/图谱类条目）逐个正规 purge，再兜底删归属目录与偏好。
    for item in trash_mod.list_items(uid):
        try:
            trash_mod.purge_item(uid, str(item.get("id", "")))
        except Exception:
            pass
    shutil.rmtree(trash_mod._TRASH_DIR / "items" / uid, ignore_errors=True)
    try:
        (trash_mod._TRASH_DIR / "preferences" / f"{uid}.json").unlink(missing_ok=True)
    except OSError:
        pass

    shutil.rmtree(notes_mod._NOTES_DIR / notes_mod._key(uid), ignore_errors=True)
    if sm_store._STUDENTS_DIR.is_dir():
        for p in sm_store._STUDENTS_DIR.glob(f"{uid}*"):
            try:
                if p.is_file():
                    p.unlink(missing_ok=True)
            except OSError:
                pass
    shutil.rmtree(kgs_mod._CUSTOM_DIR / kgs_mod._safe_name(uid), ignore_errors=True)

    # 账号记录最后删：中途任何失败都会留下可重试的账号（purge 幂等）。
    id_store.delete_user(uid)
    report["freed_bytes"] = _total_of(before)
    return report
