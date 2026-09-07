"""Unified recycle-bin lifecycle for user-owned learning resources.

Items are immutable recovery bundles under ``chat_history/trash/<owner>/<id>``.
Archiving snapshots all recoverable data before removing the active copy;
restoring performs targeted index merges and never overwrites unrelated data.
Permanent purge removes the bundle and performs idempotent residual cleanup.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TRASH_DIR = _PROJECT_ROOT / "chat_history" / "trash"
_GLOBAL_POLICY = _TRASH_DIR / "policy.json"
_DEFAULT_POLICY = {
    "default_days": 7,
    "user_max_days": 30,
    "forced_max_days": 30,
    "mode": "auto",  # auto | manual
    "cleanup_interval_seconds": 3600,
}
_TYPES = {"session", "library_file", "library_folder", "textbook", "textbook_volume",
          "workspace", "knowledge_graph", "notes_note"}


def _safe(value: str) -> str:
    return Path(str(value or "")).name


def _owner_dir(owner_id: str) -> Path:
    return _TRASH_DIR / "items" / _safe(owner_id)


def _item_dir(owner_id: str, item_id: str) -> Path:
    return _owner_dir(owner_id) / _safe(item_id)


def _rmdir_if_empty(owner_id: str) -> None:
    """条目清空后顺手移除 items/<owner>/ 空目录，不留下无主空壳。"""
    try:
        _owner_dir(owner_id).rmdir()
    except OSError:
        pass


def _manifest_path(owner_id: str, item_id: str) -> Path:
    return _item_dir(owner_id, item_id) / "manifest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _restore_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".restore.tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def get_global_policy() -> dict[str, Any]:
    raw = _read_json(_GLOBAL_POLICY, {})
    out = dict(_DEFAULT_POLICY)
    if isinstance(raw, dict):
        out.update(raw)
    out["default_days"] = max(1, min(30, int(out.get("default_days", 7))))
    out["user_max_days"] = max(1, min(30, int(out.get("user_max_days", 30))))
    out["forced_max_days"] = max(1, min(365, int(out.get("forced_max_days", 30))))
    out["mode"] = "manual" if out.get("mode") == "manual" else "auto"
    out["cleanup_interval_seconds"] = max(300, int(out.get("cleanup_interval_seconds", 3600)))
    return out


def set_global_policy(**fields: Any) -> dict[str, Any]:
    policy = get_global_policy()
    for key in _DEFAULT_POLICY:
        if key in fields and fields[key] is not None:
            policy[key] = fields[key]
    # Normalize through the same reader rules without depending on a write/read race.
    policy["default_days"] = max(1, min(30, int(policy["default_days"])))
    policy["user_max_days"] = max(1, min(30, int(policy["user_max_days"])))
    policy["forced_max_days"] = max(1, min(365, int(policy["forced_max_days"])))
    policy["mode"] = "manual" if policy.get("mode") == "manual" else "auto"
    policy["cleanup_interval_seconds"] = max(300, int(policy["cleanup_interval_seconds"]))
    _write_json(_GLOBAL_POLICY, policy)
    return policy


def _preference_path(owner_id: str) -> Path:
    return _TRASH_DIR / "preferences" / f"{_safe(owner_id)}.json"


def get_user_policy(owner_id: str) -> dict[str, Any]:
    global_policy = get_global_policy()
    pref = _read_json(_preference_path(owner_id), {})
    requested = int((pref or {}).get("retention_days", global_policy["default_days"]))
    maximum = min(global_policy["user_max_days"], global_policy["forced_max_days"])
    return {
        **global_policy,
        "retention_days": max(1, min(maximum, requested)),
        "can_keep_manually": global_policy["mode"] == "manual",
    }


def set_user_policy(owner_id: str, retention_days: int) -> dict[str, Any]:
    policy = get_global_policy()
    maximum = min(policy["user_max_days"], policy["forced_max_days"])
    days = max(1, min(maximum, int(retention_days)))
    _write_json(_preference_path(owner_id), {"retention_days": days, "updated_at": _now_iso()})
    return get_user_policy(owner_id)


def _new_bundle(owner_id: str, resource_type: str, original_id: str, title: str,
                metadata: dict[str, Any] | None = None) -> tuple[str, Path, dict[str, Any]]:
    if resource_type not in _TYPES:
        raise ValueError("unsupported trash resource type")
    item_id = "trash_" + uuid.uuid4().hex[:16]
    staging = _owner_dir(owner_id) / ("." + item_id + ".staging")
    staging.mkdir(parents=True, exist_ok=False)
    policy = get_user_policy(owner_id)
    deleted_at = time.time()
    expires_at = None if policy["mode"] == "manual" else deleted_at + policy["retention_days"] * 86400
    manifest = {
        "id": item_id,
        "owner_id": _safe(owner_id),
        "resource_type": resource_type,
        "original_id": _safe(original_id),
        "title": str(title or "未命名")[:240],
        "deleted_at": deleted_at,
        "deleted_at_iso": _now_iso(),
        "expires_at": expires_at,
        "retention_days": policy["retention_days"],
        "metadata": dict(metadata or {}),
        "version": 1,
    }
    return item_id, staging, manifest


def _commit_bundle(owner_id: str, item_id: str, staging: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    _write_json(staging / "manifest.json", manifest)
    target = _item_dir(owner_id, item_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, target)
    return manifest


def _abort_bundle(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)


def _public_manifest(data: dict[str, Any], bundle: Path | None = None) -> dict[str, Any]:
    out = {k: data.get(k) for k in (
        "id", "resource_type", "original_id", "title", "deleted_at",
        "deleted_at_iso", "expires_at", "retention_days", "metadata", "version")}
    # A logical, project-relative location gives users an auditable answer to
    # "where was this archived?" without exposing the host's absolute path.
    # It is also stable across deployment roots.
    owner = _safe(str(data.get("owner_id") or ""))
    item_id = _safe(str(data.get("id") or ""))
    if owner and item_id:
        out["archive_location"] = f"chat_history/trash/items/{owner}/{item_id}"
    if bundle and bundle.exists():
        try:
            out["size_bytes"] = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
        except Exception:
            out["size_bytes"] = 0
    return out


def list_items(owner_id: str, resource_type: str = "") -> list[dict[str, Any]]:
    root = _owner_dir(owner_id)
    if not root.is_dir():
        return []
    out = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        data = _read_json(directory / "manifest.json", {})
        if not isinstance(data, dict) or data.get("owner_id") != _safe(owner_id):
            continue
        if resource_type and data.get("resource_type") != resource_type:
            continue
        out.append(_public_manifest(data, directory))
    return sorted(out, key=lambda x: float(x.get("deleted_at") or 0), reverse=True)


def get_item(owner_id: str, item_id: str) -> dict[str, Any] | None:
    path = _manifest_path(owner_id, item_id)
    data = _read_json(path, None)
    if not isinstance(data, dict) or data.get("owner_id") != _safe(owner_id):
        return None
    return data


def _all_owned_workspaces(owner_id: str):
    from . import workspace as ws_store
    for path in ws_store._WORKSPACES_DIR.glob("*.json"):
        ws = ws_store.load_workspace(path.stem)
        if ws is not None and ws_store._owner_of(ws) == owner_id:
            yield ws


def _workspace_refs(owner_id: str, *, file_ids: Iterable[str] = (), folder_ids: Iterable[str] = ()) -> list[str]:
    file_set, folder_set = set(file_ids), set(folder_ids)
    refs: list[str] = []
    for ws in _all_owned_workspaces(owner_id):
        if (file_set.intersection(ws.selected_file_ids)
                or file_set.intersection(ws.workspace_file_ids)
                or folder_set.intersection(ws.selected_folder_ids)):
            refs.append(ws.workspace_id)
    return refs


def _remove_workspace_refs(owner_id: str, *, file_ids: Iterable[str] = (), folder_ids: Iterable[str] = ()) -> None:
    from .workspace import save_workspace
    file_set, folder_set = set(file_ids), set(folder_ids)
    for ws in _all_owned_workspaces(owner_id):
        before = (list(ws.selected_file_ids), list(ws.selected_folder_ids),
                  list(ws.workspace_file_ids))
        ws.selected_file_ids = [x for x in ws.selected_file_ids if x not in file_set]
        ws.selected_folder_ids = [x for x in ws.selected_folder_ids if x not in folder_set]
        ws.workspace_file_ids = [x for x in ws.workspace_file_ids if x not in file_set]
        if before != (ws.selected_file_ids, ws.selected_folder_ids,
                      ws.workspace_file_ids):
            save_workspace(ws)


def _restore_workspace_refs(owner_id: str, workspace_ids: list[str], *,
                            file_ids: Iterable[str] = (), folder_ids: Iterable[str] = ()) -> None:
    from .workspace import load_workspace, save_workspace, _owner_of
    for ws_id in workspace_ids:
        ws = load_workspace(_safe(ws_id))
        if ws is None or _owner_of(ws) != owner_id:
            continue
        for fid in file_ids:
            if fid not in ws.selected_file_ids:
                ws.selected_file_ids.append(fid)
        for folder_id in folder_ids:
            if folder_id not in ws.selected_folder_ids:
                ws.selected_folder_ids.append(folder_id)
        save_workspace(ws)


def _library_artifacts(owner_id: str, records: list[dict[str, Any]], dest: Path) -> None:
    from .library import library_data_dir
    data = library_data_dir(owner_id)
    for record in records:
        fid = _safe(record.get("id", ""))
        if not fid:
            continue
        _copy_if_exists(data / f"{fid}.txt", dest / f"{fid}.txt")
        ext = str(record.get("orig_ext") or "")
        if ext:
            _copy_if_exists(data / f"{fid}.orig{ext}", dest / f"{fid}.orig{ext}")


def _restore_library_records(owner_id: str, records: list[dict[str, Any]], src: Path) -> None:
    from .library import load_library, save_library, library_data_dir
    lib = load_library(owner_id)
    for record in records:
        fid = _safe(record.get("id", ""))
        if not fid or lib.find_file(fid):
            raise FileExistsError(f"资料 {fid} 已存在")
    data = library_data_dir(owner_id)
    for record in records:
        fid = _safe(record["id"])
        _restore_file(src / f"{fid}.txt", data / f"{fid}.txt")
        ext = str(record.get("orig_ext") or "")
        if ext:
            _restore_file(src / f"{fid}.orig{ext}", data / f"{fid}.orig{ext}")
        lib.files.append(dict(record))
    save_library(lib)


def _assert_library_records_available(owner_id: str,
                                      records: list[dict[str, Any]]) -> None:
    from .library import load_library
    lib = load_library(owner_id)
    conflicts = [_safe(r.get("id", "")) for r in records
                 if _safe(r.get("id", "")) and lib.find_file(_safe(r.get("id", "")))]
    if conflicts:
        raise FileExistsError(f"资料 {conflicts[0]} 已存在")


def _delete_library_records(owner_id: str, file_ids: list[str]) -> None:
    from .library import load_library, save_library
    from . import vector_store
    lib = load_library(owner_id)
    for fid in file_ids:
        if lib.find_file(fid):
            lib.remove_file(fid)
        try:
            vector_store.delete_file(fid)
        except Exception:
            pass
    save_library(lib)


def _session_snapshot(owner_id: str, session_id: str, dest: Path) -> dict[str, Any]:
    from . import session as session_store
    from .context import transcript_path
    from .config import trace_dir_path
    session = session_store.load_session(session_id)
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    if session is None or (session.student_id or DEFAULT_STUDENT_ID) != owner_id:
        raise FileNotFoundError("会话不存在")
    data = session.to_persistable()
    _write_json(dest / "session.json", data)
    for meta in data.get("knowledge_files") or []:
        fid = _safe(meta.get("id", ""))
        _copy_if_exists(session.knowledge.upload_dir / f"{fid}.txt", dest / "files" / f"{fid}.txt")
        ext = str(meta.get("orig_ext") or "")
        if ext:
            _copy_if_exists(session.knowledge.upload_dir / f"{fid}.orig{ext}", dest / "files" / f"{fid}.orig{ext}")
    _copy_if_exists(transcript_path(session_id), dest / "transcript.jsonl")
    for trace_id in data.get("trace_ids") or []:
        _copy_if_exists(trace_dir_path() / f"trace_{_safe(trace_id)}.jsonl",
                        dest / "traces" / f"trace_{_safe(trace_id)}.jsonl")
    return data


def _delete_session_active(owner_id: str, session_id: str, data: dict[str, Any]) -> None:
    from . import session as session_store
    from .context import transcript_path
    from .config import trace_dir_path
    from .workspace import load_workspace, save_workspace, _owner_of
    session_store.delete_session(session_id)
    tp = transcript_path(session_id)
    if tp.exists():
        tp.unlink()
    for trace_id in data.get("trace_ids") or []:
        path = trace_dir_path() / f"trace_{_safe(trace_id)}.jsonl"
        if path.exists():
            path.unlink()
    ws_id = str(data.get("workspace_id") or "")
    ws = load_workspace(ws_id) if ws_id else None
    if ws is not None and _owner_of(ws) == owner_id and session_id in ws.session_ids:
        ws.session_ids = [x for x in ws.session_ids if x != session_id]
        save_workspace(ws)
    try:
        from .quiz_recent import mark_session_source_deleted
        mark_session_source_deleted(owner_id, session_id)
    except Exception:
        pass
    try:
        from .learning_records import mark_source_deleted
        mark_source_deleted(owner_id, session_id)
    except Exception:
        pass


def _restore_session_payload(owner_id: str, payload: Path,
                             workspace_ids: list[str] | None = None) -> str:
    from . import session as session_store
    from .context import transcript_path
    from .config import trace_dir_path
    from .workspace import load_workspace, save_workspace, _owner_of
    data = _read_json(payload / "session.json", {})
    sid = _safe(data.get("session_id", ""))
    if not sid or session_store.load_session(sid) is not None:
        raise FileExistsError("同 ID 会话已存在")
    target_ws = ""
    # Never silently reattach a restored chat. The caller must explicitly
    # choose a currently existing workspace. Workspace-bundle restoration
    # binds its member sessions after recreating the workspace itself.
    candidates = list(workspace_ids or [])
    for ws_id in candidates:
        ws = load_workspace(_safe(ws_id))
        if ws is not None and _owner_of(ws) == owner_id:
            target_ws = ws.workspace_id
            if sid not in ws.session_ids:
                ws.session_ids.append(sid)
                save_workspace(ws)
            break
    data["workspace_id"] = target_ws
    _write_json(session_store._resolve(sid), data)
    upload_dir = session_store.KnowledgeStore().upload_dir
    for meta in data.get("knowledge_files") or []:
        fid = _safe(meta.get("id", ""))
        _restore_file(payload / "files" / f"{fid}.txt", upload_dir / f"{fid}.txt")
        ext = str(meta.get("orig_ext") or "")
        if ext:
            _restore_file(payload / "files" / f"{fid}.orig{ext}", upload_dir / f"{fid}.orig{ext}")
    _restore_file(payload / "transcript.jsonl", transcript_path(sid))
    for src in (payload / "traces").glob("trace_*.jsonl") if (payload / "traces").exists() else []:
        _restore_file(src, trace_dir_path() / src.name)
    try:
        from .quiz_recent import mark_session_source_active
        mark_session_source_active(owner_id, sid)
    except Exception:
        pass
    try:
        from .learning_records import mark_source_active
        mark_source_active(owner_id, sid)
    except Exception:
        pass
    return sid


def _restore_session(owner_id: str, bundle: Path,
                     workspace_ids: list[str] | None = None) -> str:
    return _restore_session_payload(owner_id, bundle / "payload", workspace_ids)


def archive_session(owner_id: str, session_id: str, *, forget_prompt_memory: bool = False) -> dict[str, Any]:
    item_id, staging, manifest = _new_bundle(owner_id, "session", session_id, session_id)
    try:
        data = _session_snapshot(owner_id, session_id, staging / "payload")
        manifest["title"] = str(data.get("title") or session_id)
        manifest["metadata"].update({
            "workspace_id": data.get("workspace_id", ""),
            "round_count": sum(1 for m in data.get("messages") or [] if m.get("role") == "assistant"),
            "forget_prompt_memory": bool(forget_prompt_memory),
        })
        try:
            from app.agents.memory.prompt_memory import session_forget_status
            manifest["metadata"]["memory_forget_status"] = session_forget_status(
                owner_id, session_id)
        except Exception:
            manifest["metadata"]["memory_forget_status"] = "unavailable"
        _commit_bundle(owner_id, item_id, staging, manifest)
        _delete_session_active(owner_id, session_id, data)
        if forget_prompt_memory:
            try:
                from app.agents.memory.prompt_memory import forget_session_contribution
                manifest["metadata"]["memory_forget_result"] = forget_session_contribution(owner_id, session_id)
                _write_json(_manifest_path(owner_id, item_id), manifest)
            except Exception:
                manifest["metadata"]["memory_forget_result"] = "unavailable"
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def archive_library_file(owner_id: str, file_id: str) -> dict[str, Any]:
    from .library import load_library
    lib = load_library(owner_id)
    record = lib.find_file(file_id)
    if record is None:
        raise FileNotFoundError("资料不存在")
    folder = lib.find_folder(str(record.get("folder_id") or ""))
    owned_workspace_id = str((folder or {}).get("workspace_id") or "")
    item_id, staging, manifest = _new_bundle(
        owner_id, "library_file", file_id, record.get("filename", "资料"))
    try:
        _write_json(staging / "payload" / "records.json", [record])
        _library_artifacts(owner_id, [record], staging / "payload" / "files")
        refs = _workspace_refs(owner_id, file_ids=[file_id])
        manifest["metadata"].update({"workspace_ids": refs, "file_count": 1,
                                     "owned_workspace_id": owned_workspace_id})
        _commit_bundle(owner_id, item_id, staging, manifest)
        _remove_workspace_refs(owner_id, file_ids=[file_id])
        _delete_library_records(owner_id, [file_id])
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def archive_library_folder(owner_id: str, folder_id: str) -> dict[str, Any]:
    from .library import load_library, save_library
    lib = load_library(owner_id)
    folder = lib.find_folder(folder_id)
    if folder is None:
        raise FileNotFoundError("文件夹不存在")
    if folder.get("workspace_id"):
        raise ValueError("工作区专属文件夹需随工作区归档")
    records = [f for f in lib.files if f.get("folder_id") == folder_id]
    item_id, staging, manifest = _new_bundle(
        owner_id, "library_folder", folder_id, folder.get("name", "文件夹"))
    try:
        _write_json(staging / "payload" / "folder.json", folder)
        _write_json(staging / "payload" / "records.json", records)
        _library_artifacts(owner_id, records, staging / "payload" / "files")
        refs = _workspace_refs(owner_id, folder_ids=[folder_id])
        manifest["metadata"].update({"workspace_ids": refs, "file_count": len(records)})
        _commit_bundle(owner_id, item_id, staging, manifest)
        _remove_workspace_refs(owner_id, folder_ids=[folder_id], file_ids=[f["id"] for f in records])
        for record in records:
            lib.remove_file(record["id"])
            try:
                from . import vector_store
                vector_store.delete_file(record["id"])
            except Exception:
                pass
        lib.folders = [f for f in lib.folders if f.get("id") != folder_id]
        save_library(lib)
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def _graph_snapshot(owner_id: str, topic_key: str, dest: Path) -> None:
    from app.agents.knowledge import store as kg_store
    graph = kg_store.load_custom_graph(owner_id, topic_key)
    chunks = kg_store.load_concept_chunks(owner_id, topic_key)
    if graph:
        _write_json(dest / "graph.json", graph)
    if chunks:
        _write_json(dest / "chunks.json", chunks)
    specs = kg_store.volume_specs_dir(owner_id, topic_key)
    if specs and specs.is_dir():
        shutil.copytree(specs, dest / "volume_specs", dirs_exist_ok=True)


def _restore_volume_specs(owner_id: str, topic_key: str, payload: Path) -> None:
    from app.agents.knowledge import store as kg_store
    source = payload / "volume_specs"
    target = kg_store.volume_specs_dir(owner_id, topic_key)
    if source.is_dir() and target is not None:
        shutil.copytree(source, target, dirs_exist_ok=True)


def _invalidate_knowledge_cache(owner_id: str) -> None:
    """Drop merged M5 views affected by a custom-graph lifecycle change."""
    try:
        from app.agents.knowledge.manager import get_knowledge_service
        get_knowledge_service().invalidate_custom_cache(owner_id)
    except Exception:
        # Persistent state is authoritative; graph_for() also guards itself
        # with filesystem stamps if cache invalidation is unavailable.
        pass


def _purge_graph_active(owner_id: str, topic_key: str, *, include_volume_specs: bool = True) -> None:
    from app.agents.knowledge import store as kg_store
    kg_store.purge_custom_graph(owner_id, topic_key, include_legacy_archives=True,
                                include_volume_specs=include_volume_specs)
    _invalidate_knowledge_cache(owner_id)


def archive_textbook(owner_id: str, textbook_id: str) -> dict[str, Any]:
    from . import textbook as tb_store
    tb_store.cancel_refresh_task(owner_id, textbook_id)
    from .library import load_library
    try:
        from .textbook_ocr import cancel_textbook_ocr
        cancel_textbook_ocr(owner_id, textbook_id)
    except Exception:
        pass
    tb = tb_store.find_textbook(owner_id, textbook_id)
    if tb is None:
        raise FileNotFoundError("教材不存在")
    file_ids = list(tb.get("file_ids") or [tb.get("file_id")])
    file_ids = [_safe(x) for x in file_ids if x]
    lib = load_library(owner_id)
    records = [lib.find_file(fid) for fid in file_ids]
    records = [r for r in records if r is not None]
    item_id, staging, manifest = _new_bundle(
        owner_id, "textbook", textbook_id, tb.get("title", "教材"))
    try:
        _write_json(staging / "payload" / "textbook.json", tb)
        _write_json(staging / "payload" / "records.json", records)
        _library_artifacts(owner_id, records, staging / "payload" / "files")
        _graph_snapshot(owner_id, str(tb.get("topic_key") or ""), staging / "payload")
        refs = _workspace_refs(owner_id, file_ids=file_ids)
        manifest["metadata"].update({
            "workspace_ids": refs, "file_ids": file_ids,
            "topic_key": tb.get("topic_key", ""), "file_count": len(records),
        })
        _commit_bundle(owner_id, item_id, staging, manifest)
        _remove_workspace_refs(owner_id, file_ids=file_ids)
        _delete_library_records(owner_id, file_ids)
        _purge_graph_active(owner_id, str(tb.get("topic_key") or ""))
        tb_store.remove_textbook(owner_id, textbook_id)
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def archive_note(owner_id: str, note_id: str) -> dict[str, Any]:
    """归档一篇笔记：快照元数据 + 正文 + 修订历史，随后移除活动副本。

    温故复习卡与 pending 建议随归档一并摘除（恢复时重建）。"""
    from . import notes as notes_store
    vault = notes_store.load_vault(owner_id)
    meta = vault.find_note(note_id)
    if meta is None:
        raise FileNotFoundError("笔记不存在")
    content = vault.read_note(note_id)
    item_id, staging, manifest = _new_bundle(
        owner_id, "notes_note", note_id, meta.get("title", "笔记"))
    try:
        _write_json(staging / "payload" / "note.json", meta)
        _write_text(staging / "payload" / "content.md", content)
        rdir = notes_store._revisions_dir(owner_id, note_id)
        if rdir.is_dir():
            shutil.copytree(rdir, staging / "payload" / "revisions",
                            dirs_exist_ok=True)
        manifest["metadata"].update({
            "folder_id": meta.get("folder_id", ""),
            "template_id": meta.get("template_id", ""),
            "review_enabled": bool((meta.get("review") or {}).get("enabled")),
            "word_count": meta.get("word_count", 0),
        })
        _commit_bundle(owner_id, item_id, staging, manifest)
        vault.remove_note(note_id)
        notes_store.save_vault(vault)
        if manifest["metadata"]["review_enabled"]:
            try:
                from app.agents.learning_orchestration import manager as m9
                m9.get_orchestration_service().remove_review_card(
                    owner_id, concept_id=f"note:{note_id}")
            except Exception:
                pass
        # 归档即清理该笔记的专属智能体状态（remove_note 内统一处理）；
        # 旧建议队列已随协作模式一并移除（2026-09 每笔记专属智能体重构）。
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def archive_textbook_volume(owner_id: str, textbook_id: str,
                            file_id: str) -> dict[str, Any]:
    """Archive one volume while retaining the rest of its textbook group."""
    from . import textbook as tb_store
    tb_store.cancel_refresh_task(owner_id, textbook_id)
    from .library import load_library
    try:
        from .textbook_ocr import cancel_textbook_ocr
        cancel_textbook_ocr(owner_id, textbook_id)
    except Exception:
        pass
    tb = tb_store.find_textbook(owner_id, textbook_id)
    if tb is None or tb.get("kind") != "group" or file_id not in (tb.get("file_ids") or []):
        raise FileNotFoundError("教材卷不存在")
    lib = load_library(owner_id)
    record = lib.find_file(file_id)
    if record is None:
        raise FileNotFoundError("教材卷文件不存在")
    item_id, staging, manifest = _new_bundle(
        owner_id, "textbook_volume", file_id, record.get("filename", "教材卷"),
        {"textbook_id": textbook_id, "topic_key": tb.get("topic_key", ""),
         "workspace_ids": _workspace_refs(owner_id, file_ids=[file_id])})
    try:
        _write_json(staging / "payload" / "textbook.json", tb)
        _write_json(staging / "payload" / "records.json", [record])
        _library_artifacts(owner_id, [record], staging / "payload" / "files")
        _graph_snapshot(owner_id, str(tb.get("topic_key") or ""), staging / "payload")
        manifest["metadata"].update({"textbook_id": textbook_id, "file_id": file_id,
                                     "topic_key": tb.get("topic_key", "")})
        _commit_bundle(owner_id, item_id, staging, manifest)
        _remove_workspace_refs(owner_id, file_ids=[file_id])
        _delete_library_records(owner_id, [file_id])
        # The old group graph may still mention the removed volume until the
        # asynchronous rebuild publishes a replacement; never expose that
        # stale graph as the active source in the meantime.
        _purge_graph_active(owner_id, str(tb.get("topic_key") or ""),
                            include_volume_specs=False)
        from app.agents.knowledge import store as kg_store
        kg_store.delete_volume_spec(owner_id, str(tb.get("topic_key") or ""), file_id)
        tb_store.remove_group_file(owner_id, textbook_id, file_id)
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def archive_knowledge_graph(owner_id: str, topic_key: str) -> dict[str, Any]:
    from app.agents.knowledge import store as kg_store
    graph = kg_store.load_custom_graph(owner_id, topic_key)
    if graph is None:
        raise FileNotFoundError("知识谱系不存在")
    item_id, staging, manifest = _new_bundle(
        owner_id, "knowledge_graph", topic_key, graph.get("topic", topic_key))
    try:
        _graph_snapshot(owner_id, topic_key, staging / "payload")
        manifest["metadata"].update({"topic_key": topic_key})
        _commit_bundle(owner_id, item_id, staging, manifest)
        _purge_graph_active(owner_id, topic_key)
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def archive_workspace(owner_id: str, workspace_id: str) -> dict[str, Any]:
    from . import workspace as ws_store
    from .library import load_library, save_library
    ws = ws_store.load_workspace(workspace_id)
    if ws is None or ws_store._owner_of(ws) != owner_id:
        raise FileNotFoundError("工作学习区不存在")
    lib = load_library(owner_id)
    folder = lib.find_folder(ws.library_folder_id) if ws.library_folder_id else None
    records = [f for f in lib.files if f.get("folder_id") == ws.library_folder_id] if folder else []
    item_id, staging, manifest = _new_bundle(
        owner_id, "workspace", workspace_id, ws.name)
    try:
        _write_json(staging / "payload" / "workspace.json", ws.to_persistable())
        if folder:
            _write_json(staging / "payload" / "folder.json", folder)
        _write_json(staging / "payload" / "records.json", records)
        _library_artifacts(owner_id, records, staging / "payload" / "files")
        session_ids: list[str] = []
        for sid in list(ws.session_ids):
            try:
                _session_snapshot(owner_id, sid, staging / "payload" / "sessions" / _safe(sid))
                session_ids.append(sid)
            except FileNotFoundError:
                continue
        manifest["metadata"].update({
            "session_count": len(session_ids), "file_count": len(records),
            "has_public_memory": bool(ws.public_memory),
        })
        _commit_bundle(owner_id, item_id, staging, manifest)
        for sid in session_ids:
            data = _read_json(_item_dir(owner_id, item_id) / "payload" / "sessions" / _safe(sid) / "session.json", {})
            _delete_session_active(owner_id, sid, data)
        for record in records:
            lib.remove_file(record["id"])
            try:
                from . import vector_store
                vector_store.delete_file(record["id"])
            except Exception:
                pass
        if folder:
            lib.folders = [f for f in lib.folders if f.get("id") != folder["id"]]
        save_library(lib)
        path = ws_store._resolve(workspace_id)
        if path.exists():
            path.unlink()
        shutil.rmtree(ws_store._WORKSPACES_DIR / "uploads" / _safe(workspace_id), ignore_errors=True)
        try:
            from . import vector_store
            vector_store.delete_scope(f"workspace:{workspace_id}")
        except Exception:
            pass
        return _public_manifest(manifest, _item_dir(owner_id, item_id))
    except Exception:
        _abort_bundle(staging)
        raise


def restore_item(owner_id: str, item_id: str, *, workspace_ids: list[str] | None = None) -> dict[str, Any]:
    manifest = get_item(owner_id, item_id)
    if manifest is None:
        raise FileNotFoundError("归档不存在")
    bundle = _item_dir(owner_id, item_id)
    payload = bundle / "payload"
    kind = manifest["resource_type"]
    restored_id = manifest["original_id"]
    chosen = list(workspace_ids or [])
    resume_textbook_id = ""
    if kind == "session":
        restored_id = _restore_session(owner_id, bundle, chosen)
    elif kind in {"library_file", "library_folder"}:
        records = _read_json(payload / "records.json", []) or []
        _assert_library_records_available(owner_id, records)
        if kind == "library_folder":
            from .library import load_library, save_library
            folder = _read_json(payload / "folder.json", {})
            lib = load_library(owner_id)
            if lib.find_folder(folder.get("id", "")):
                raise FileExistsError("同 ID 文件夹已存在")
            lib.folders.append(folder)
            save_library(lib)
        if kind == "library_file":
            owned_ws = str((manifest.get("metadata") or {}).get("owned_workspace_id") or "")
            if owned_ws and owned_ws not in chosen:
                records = [{**r, "folder_id": ""} for r in records]
        _restore_library_records(owner_id, records, payload / "files")
        _restore_workspace_refs(owner_id, chosen, file_ids=[r["id"] for r in records],
                                folder_ids=[manifest["original_id"]] if kind == "library_folder" else [])
        if kind == "library_file":
            owned_ws = str((manifest.get("metadata") or {}).get("owned_workspace_id") or "")
            if owned_ws and owned_ws in chosen:
                from .workspace import load_workspace, save_workspace, _owner_of
                ws = load_workspace(owned_ws)
                if ws is not None and _owner_of(ws) == owner_id:
                    for record in records:
                        if record["id"] not in ws.workspace_file_ids:
                            ws.workspace_file_ids.append(record["id"])
                    save_workspace(ws)
    elif kind == "textbook":
        from . import textbook as tb_store
        if tb_store.find_textbook(owner_id, restored_id):
            raise FileExistsError("同 ID 教材已存在")
        records = _read_json(payload / "records.json", []) or []
        _assert_library_records_available(owner_id, records)
        from app.agents.knowledge import store as kg_store
        tb = _read_json(payload / "textbook.json", {})
        if kg_store.load_custom_graph(owner_id, str(tb.get("topic_key") or "")):
            raise FileExistsError("同 topic_key 知识谱系已存在")
        _restore_library_records(owner_id, records, payload / "files")
        rows = tb_store._load_raw(owner_id)
        rows.append(tb)
        tb_store._save(owner_id, rows)
        graph = _read_json(payload / "graph.json", None)
        chunks = _read_json(payload / "chunks.json", None)
        if graph:
            kg_store.save_custom_graph(owner_id, tb.get("topic_key", ""), graph)
        if chunks:
            kg_store.save_concept_chunks(owner_id, tb.get("topic_key", ""), chunks)
        _restore_volume_specs(owner_id, str(tb.get("topic_key") or ""), payload)
        _invalidate_knowledge_cache(owner_id)
        _restore_workspace_refs(owner_id, chosen, file_ids=[r["id"] for r in records])
        resume_textbook_id = str(tb.get("id") or restored_id)
    elif kind == "textbook_volume":
        from . import textbook as tb_store
        from app.agents.knowledge import store as kg_store
        tb = _read_json(payload / "textbook.json", {})
        group_id = str((manifest.get("metadata") or {}).get("textbook_id") or tb.get("id") or "")
        current = tb_store.find_textbook(owner_id, group_id)
        if current is None:
            raise FileNotFoundError("原教材组已不存在，请恢复教材组归档")
        records = _read_json(payload / "records.json", []) or []
        _assert_library_records_available(owner_id, records)
        _restore_library_records(owner_id, records, payload / "files")
        rows = tb_store._load_raw(owner_id)
        target = next((x for x in rows if x.get("id") == group_id), None)
        if target is None:
            raise FileNotFoundError("原教材组已不存在，请恢复教材组归档")
        restored_fid = str((manifest.get("metadata") or {}).get("file_id") or restored_id)
        current_ids = list(target.get("file_ids") or [])
        old_ids = list(tb.get("file_ids") or [])
        if restored_fid not in current_ids:
            old_index = old_ids.index(restored_fid) if restored_fid in old_ids else len(current_ids)
            insert_at = min(old_index, len(current_ids))
            current_ids.insert(insert_at, restored_fid)
            target["file_ids"] = current_ids
            target["updated_at"] = time.time()
        archived_volume_state = ((((tb.get("ocr_state") or {}).get("volumes") or {})
                                  .get(restored_fid)) or {})
        if archived_volume_state:
            root = dict(target.get("ocr_state") or {})
            states = dict(root.get("volumes") or {})
            states[restored_fid] = archived_volume_state
            root["volumes"] = states
            root["version"] = int(root.get("version") or 1)
            target["ocr_state"] = root
            if archived_volume_state.get("status") == "waiting":
                target["status"] = "ocr_waiting"
        tb_store._save(owner_id, rows)
        graph = _read_json(payload / "graph.json", None)
        chunks = _read_json(payload / "chunks.json", None)
        topic_key = str((manifest.get("metadata") or {}).get("topic_key") or tb.get("topic_key") or "")
        if graph:
            kg_store.save_custom_graph(owner_id, topic_key, graph)
        if chunks:
            kg_store.save_concept_chunks(owner_id, topic_key, chunks)
        _restore_volume_specs(owner_id, topic_key, payload)
        _invalidate_knowledge_cache(owner_id)
        _restore_workspace_refs(owner_id, chosen, file_ids=[r["id"] for r in records])
        resume_textbook_id = group_id
    elif kind == "knowledge_graph":
        from app.agents.knowledge import store as kg_store
        graph = _read_json(payload / "graph.json", None)
        if kg_store.load_custom_graph(owner_id, restored_id):
            raise FileExistsError("同 ID 知识谱系已存在")
        if graph:
            kg_store.save_custom_graph(owner_id, restored_id, graph)
        chunks = _read_json(payload / "chunks.json", None)
        if chunks:
            kg_store.save_concept_chunks(owner_id, restored_id, chunks)
        _invalidate_knowledge_cache(owner_id)
    elif kind == "notes_note":
        from . import notes as notes_store
        vault = notes_store.load_vault(owner_id)
        if vault.find_note(restored_id):
            raise FileExistsError("同 ID 笔记已存在")
        meta = _read_json(payload / "note.json", {}) or {}
        content = ""
        try:
            content = (payload / "content.md").read_text(encoding="utf-8")
        except Exception:
            content = ""
        vault.notes.append(meta)
        vault._write_content(restored_id, content)
        src_revisions = payload / "revisions"
        if src_revisions.is_dir():
            shutil.copytree(src_revisions,
                            notes_store._revisions_dir(owner_id, restored_id),
                            dirs_exist_ok=True)
        notes_store.save_vault(vault)
        if (meta.get("review") or {}).get("enabled"):
            try:
                from app.agents.learning_orchestration import manager as m9
                m9.get_orchestration_service().upsert_review_card(
                    owner_id, concept_id=f"note:{restored_id}",
                    concept_name=str(meta.get("title") or restored_id))
            except Exception:
                pass
    elif kind == "workspace":
        from . import workspace as ws_store
        from .library import load_library, save_library
        if ws_store.load_workspace(restored_id):
            raise FileExistsError("同 ID 工作区已存在")
        folder = _read_json(payload / "folder.json", None)
        records = _read_json(payload / "records.json", []) or []
        _assert_library_records_available(owner_id, records)
        sessions_dir = payload / "sessions"
        from . import session as session_store
        if sessions_dir.exists():
            for session_bundle in sessions_dir.iterdir():
                data = _read_json(session_bundle / "session.json", {}) if session_bundle.is_dir() else {}
                sid = _safe(data.get("session_id", ""))
                if sid and session_store.load_session(sid) is not None:
                    raise FileExistsError(f"同 ID 会话 {sid} 已存在")
        if folder:
            lib = load_library(owner_id)
            if lib.find_folder(folder.get("id", "")):
                raise FileExistsError("工作区资料文件夹已存在")
            lib.folders.append(folder)
            save_library(lib)
            _restore_library_records(owner_id, records, payload / "files")
        ws_data = _read_json(payload / "workspace.json", {})
        session_ids = []
        if sessions_dir.exists():
            for session_bundle in sorted(sessions_dir.iterdir()):
                if session_bundle.is_dir():
                    session_ids.append(_restore_session_payload(owner_id, session_bundle, []))
        ws_data["session_ids"] = session_ids
        _write_json(ws_store._resolve(restored_id), ws_data)
        # Sessions were restored before the workspace existed; now bind them.
        for sid in session_ids:
            session = session_store.load_session(sid)
            if session:
                session.workspace_id = restored_id
                session_store.save_session(session)
    else:
        raise ValueError("不支持的归档类型")
    if resume_textbook_id:
        try:
            from .textbook_ocr import schedule_textbook_resume
            from . import textbook as tb_store
            rec = tb_store.find_textbook(owner_id, resume_textbook_id) or {}
            if rec.get("status") == "ocr_waiting":
                states = ((rec.get("ocr_state") or {}).get("volumes") or {})
                next_at = min((float(v.get("next_retry_at") or time.time())
                               for v in states.values() if v.get("status") == "waiting"),
                              default=time.time())
                schedule_textbook_resume(owner_id, resume_textbook_id, next_at)
        except Exception:
            pass
    shutil.rmtree(bundle)
    _rmdir_if_empty(owner_id)
    return {"status": "restored", "item_id": item_id, "resource_type": kind,
            "original_id": restored_id,
            "textbook_id": resume_textbook_id or None}


def _residual_purge(owner_id: str, manifest: dict[str, Any]) -> None:
    """Best-effort idempotent cleanup for stale active artifacts."""
    kind = manifest.get("resource_type")
    original_id = _safe(manifest.get("original_id", ""))
    metadata = manifest.get("metadata") or {}
    if kind == "session":
        data = _read_json(_item_dir(owner_id, manifest["id"]) / "payload" / "session.json", {})
        _delete_session_active(owner_id, original_id, data)
    elif kind == "textbook":
        from . import textbook as tb_store
        _delete_library_records(owner_id, list(metadata.get("file_ids") or []))
        _purge_graph_active(owner_id, str(metadata.get("topic_key") or ""))
        tb_store.remove_textbook(owner_id, original_id)
    elif kind == "knowledge_graph":
        _purge_graph_active(owner_id, original_id)
    elif kind == "notes_note":
        from . import notes as notes_store
        vault = notes_store.load_vault(owner_id)
        if vault.remove_note(original_id):
            notes_store.save_vault(vault)
        try:
            from app.agents.learning_orchestration import manager as m9
            m9.get_orchestration_service().remove_review_card(
                owner_id, concept_id=f"note:{original_id}")
        except Exception:
            pass
    elif kind == "textbook_volume":
        from . import textbook as tb_store
        _delete_library_records(owner_id, [original_id])
        group_id = str(metadata.get("textbook_id") or "")
        if group_id:
            tb_store.remove_group_file(owner_id, group_id, original_id)
    elif kind == "library_file":
        _delete_library_records(owner_id, [original_id])
        _remove_workspace_refs(owner_id, file_ids=[original_id])
    elif kind == "library_folder":
        from .library import load_library, save_library
        lib = load_library(owner_id)
        records = [f for f in lib.files if f.get("folder_id") == original_id]
        _delete_library_records(owner_id, [f["id"] for f in records])
        lib = load_library(owner_id)
        lib.folders = [f for f in lib.folders if f.get("id") != original_id]
        save_library(lib)
        _remove_workspace_refs(owner_id, folder_ids=[original_id])
    elif kind == "workspace":
        from . import workspace as ws_store
        payload = _item_dir(owner_id, manifest["id"]) / "payload"
        sessions_dir = payload / "sessions"
        if sessions_dir.exists():
            for session_dir in sessions_dir.iterdir():
                if session_dir.is_dir():
                    data = _read_json(session_dir / "session.json", {})
                    sid = _safe(data.get("session_id", ""))
                    if sid:
                        _delete_session_active(owner_id, sid, data)
        ws = ws_store.load_workspace(original_id)
        if ws is not None and ws_store._owner_of(ws) == owner_id:
            ws_store.delete_workspace(original_id)
        try:
            from . import vector_store
            vector_store.delete_scope(f"workspace:{original_id}")
        except Exception:
            pass


def _archived_session_ids(owner_id: str, manifest: dict[str, Any]) -> list[str]:
    """Session identities whose attributable memory must follow a purge."""
    kind = manifest.get("resource_type")
    if kind == "session":
        sid = _safe(str(manifest.get("original_id") or ""))
        return [sid] if sid else []
    if kind != "workspace":
        return []
    sessions_dir = _item_dir(owner_id, str(manifest.get("id") or "")) / "payload" / "sessions"
    if not sessions_dir.is_dir():
        return []
    out: list[str] = []
    for session_dir in sessions_dir.iterdir():
        data = _read_json(session_dir / "session.json", {}) if session_dir.is_dir() else {}
        sid = _safe(str(data.get("session_id") or ""))
        if sid and sid not in out:
            out.append(sid)
    return out


def _purge_session_memory(owner_id: str, session_ids: list[str]) -> dict[str, int]:
    """Erase individually attributable chat memory during permanent purge.

    Bounded prompt contributions and legacy episodic rows carry a session id
    and can therefore be deleted exactly. Contributions already folded into
    the aggregate core profile cannot be safely decomposed and are reported as
    ``compacted_unavailable``. Independent learning-result records are outside
    this helper by product contract.
    """
    counts: dict[str, int] = {}
    for sid in session_ids:
        try:
            from app.agents.memory.prompt_memory import forget_session_contribution
            result = forget_session_contribution(owner_id, sid)
            counts[result] = counts.get(result, 0) + 1
        except Exception:
            counts["unavailable"] = counts.get("unavailable", 0) + 1
        try:
            from app.agents.memory.store import remove_episodes_for_session
            remove_episodes_for_session(owner_id, sid)
        except Exception:
            pass
    return counts


def _detach_learning_source_ids(owner_id: str, session_ids: list[str]) -> dict[str, int]:
    """Remove erased chat identifiers while retaining independent outcomes."""
    counts = {"learning_records": 0, "quiz_recent": 0}
    for sid in session_ids:
        try:
            from .learning_records import detach_source_session
            counts["learning_records"] += detach_source_session(owner_id, sid)
        except Exception:
            pass
        try:
            from .quiz_recent import detach_session_source
            counts["quiz_recent"] += detach_session_source(owner_id, sid)
        except Exception:
            pass
    return counts


def purge_item(owner_id: str, item_id: str) -> dict[str, Any]:
    manifest = get_item(owner_id, item_id)
    if manifest is None:
        raise FileNotFoundError("归档不存在")
    session_ids = _archived_session_ids(owner_id, manifest)
    memory_forget = _purge_session_memory(owner_id, session_ids)
    _residual_purge(owner_id, manifest)
    source_detached = _detach_learning_source_ids(owner_id, session_ids)
    shutil.rmtree(_item_dir(owner_id, item_id), ignore_errors=False)
    _rmdir_if_empty(owner_id)
    return {"status": "purged", "item_id": item_id,
            "memory_forget": memory_forget,
            "source_attribution_detached": source_detached}


def empty_trash(owner_id: str) -> dict[str, Any]:
    purged, failed = 0, []
    for item in list_items(owner_id):
        try:
            purge_item(owner_id, item["id"])
            purged += 1
        except Exception:
            failed.append(item["id"])
    return {"status": "ok", "purged": purged, "failed": failed}


def cleanup_expired(now: float | None = None) -> dict[str, Any]:
    policy = get_global_policy()
    if policy["mode"] == "manual":
        return {"status": "manual", "purged": 0, "failed": []}
    current = time.time() if now is None else float(now)
    purged, failed = 0, []
    root = _TRASH_DIR / "items"
    if not root.is_dir():
        return {"status": "ok", "purged": 0, "failed": []}
    for owner_dir in root.iterdir():
        if not owner_dir.is_dir():
            continue
        for item in list_items(owner_dir.name):
            deleted_at = float(item.get("deleted_at") or current)
            forced_expiry = deleted_at + policy["forced_max_days"] * 86400
            stored_expiry = item.get("expires_at")
            effective_expiry = min(float(stored_expiry), forced_expiry) \
                if stored_expiry is not None else forced_expiry
            if effective_expiry > current:
                continue
            try:
                purge_item(owner_dir.name, item["id"])
                purged += 1
            except Exception:
                failed.append(item["id"])
        _rmdir_if_empty(owner_dir.name)
    return {"status": "ok", "purged": purged, "failed": failed}
