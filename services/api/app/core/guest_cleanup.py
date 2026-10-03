"""Delete only attributable guest remnants; public and account data stay protected."""
from __future__ import annotations

import shutil
import json
from pathlib import Path

from .account_data import _dir_size, _read_json, _safe
from .orphan_cleanup import CATEGORIES
from . import guest_runtime


def _references(node, files: set[str], traces: set[str], sessions: set[str] | None = None) -> None:
    if isinstance(node, dict):
        if sessions is not None:
            for key in ("session_id", "source_session_ref"):
                if isinstance(node.get(key), str) and node[key]:
                    sessions.add(_safe(node[key]))
        traces.update(_safe(str(t)) for t in node.get("trace_ids") or [] if t)
        for key in ("pending_material_file_ids", "workspace_file_ids", "selected_file_ids", "file_ids"):
            files.update(_safe(str(f)) for f in node.get(key) or [] if f)
        if node.get("file_id"):
            files.add(_safe(str(node["file_id"])))
        for f in [*(node.get("knowledge_files") or []), *(node.get("files") or [])]:
            if isinstance(f, dict) and f.get("id"):
                files.add(_safe(str(f["id"])))
        for value in node.values():
            _references(value, files, traces, sessions)
    elif isinstance(node, list):
        for value in node:
            _references(value, files, traces, sessions)


def collect() -> tuple[dict[str, list[Path]], set[str], set[str], set[str]]:
    from app.agents.student_model import store as sm
    from app.agents.knowledge import store as knowledge
    from app.identity import store as identity, avatars
    from . import session, context, workspace, library, trash
    from app.classroom import storage as classroom_store
    from app.agents.site_assistant import store as assistant_store
    from app import notes
    from .config import settings
    protected = {u.id for u in identity.list_users()} | {"public"}

    def guest(owner: str) -> bool:
        return owner not in protected and (owner == "student_default" or guest_runtime.is_guest(owner))

    found = {c: [] for c in CATEGORIES}
    owners = {"student_default"} if guest("student_default") else set()
    guest_files: set[str] = set()
    guest_traces: set[str] = set()
    kept_files: set[str] = set()
    kept_traces: set[str] = set()
    kept_sessions: set[str] = set()
    guest_sessions: set[str] = set()
    scopes: set[str] = set()
    for category, root, id_key in (("sessions", session._SESSIONS_DIR, "session_id"),
                                    ("workspaces", workspace._WORKSPACES_DIR, "workspace_id")):
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            data = _read_json(path)
            if data is None or not data.get(id_key):
                continue
            owner = str(data.get("student_id") or "student_default")
            owned = guest(owner)
            _references(data, guest_files if owned else kept_files,
                        guest_traces if owned else kept_traces,
                        guest_sessions if owned else kept_sessions)
            resource_id = _safe(str(data[id_key]))
            if owned:
                owners.add(owner)
                found[category].append(path)
                scopes.add(("session:" if category == "sessions" else "workspace:") + resource_id)
                if category == "sessions":
                    guest_sessions.add(resource_id)
                else:
                    uploads = root / "uploads" / resource_id
                    if uploads.exists():
                        found[category].append(uploads)
            elif category == "sessions":
                kept_sessions.add(resource_id)
    if library._LIBRARY_DIR.is_dir():
        for path in library._LIBRARY_DIR.iterdir():
            if not path.is_file():
                continue
            owner = path.name.split(".", 1)[0]
            data = _read_json(path)
            if guest(owner):
                owners.add(owner)
                found["library"].append(path)
            if data is not None:
                for meta in data.get("files") or []:
                    if isinstance(meta, dict) and meta.get("id"):
                        (guest_files if guest(owner) else kept_files).add(_safe(str(meta["id"])))
                if guest(owner):
                    scopes.update("folder:" + _safe(str(f["id"]))
                                  for f in data.get("folders") or [] if isinstance(f, dict) and f.get("id"))
    items = trash._TRASH_DIR / "items"
    if items.is_dir():
        for owner_dir in items.iterdir():
            if not owner_dir.is_dir():
                continue
            owned = guest(owner_dir.name)
            for path in owner_dir.rglob("*.json"):
                _references(_read_json(path), guest_files if owned else kept_files,
                            guest_traces if owned else kept_traces,
                            guest_sessions if owned else kept_sessions)
    for category, root in (("library", library._LIBRARY_DIR / "data"),
                           ("trash", items), ("notes", notes._NOTES_DIR),
                           ("knowledge", knowledge._CUSTOM_DIR),
                           ("classroom", classroom_store._CLASSROOM_DIR),
                           ("assistant", assistant_store._ASSISTANT_DIR),
                           ("avatars", avatars._AVATARS_DIR)):
        if root.is_dir():
            for path in root.iterdir():
                if guest(path.name):
                    owners.add(path.name)
                    found[category].append(path)
    if sm._STUDENTS_DIR.is_dir():
        for path in sm._STUDENTS_DIR.iterdir():
            owner = path.name.split(".", 1)[0]
            if path.is_file():
                # Journals can reference chats whose original session was deleted.
                try:
                    for line in path.read_text(encoding="utf-8").splitlines() if path.suffix == ".jsonl" else [path.read_text(encoding="utf-8")]:
                        try:
                            _references(json.loads(line), guest_files if guest(owner) else kept_files,
                                        guest_traces if guest(owner) else kept_traces,
                                        guest_sessions if guest(owner) else kept_sessions)
                        except (ValueError, TypeError):
                            pass
                except OSError:
                    pass
            if guest(owner):
                owners.add(owner)
                found["students"].append(path)
    prefs = trash._TRASH_DIR / "preferences"
    if prefs.is_dir():
        found["trash"].extend(p for p in prefs.glob("*.json") if guest(p.stem))
    for sid in guest_sessions - kept_sessions:
        path = context._TRANSCRIPT_DIR / f"{sid}.transcript.jsonl"
        if path.exists():
            found["transcripts"].append(path)
    for tid in guest_traces - kept_traces:
        path = Path(settings.trace_dir) / f"trace_{tid}.jsonl"
        if path.exists():
            found["traces"].append(path)
    uploads = Path(settings.trace_dir).parent / "uploads"
    removable_files = guest_files - kept_files
    if uploads.is_dir():
        found["uploads"].extend(p for p in uploads.iterdir()
                                 if p.is_file() and p.name.split(".", 1)[0] in removable_files)
    return found, owners, removable_files, scopes


def scan() -> dict:
    found, _, _, _ = collect()
    categories = {key: {"items": len(paths), "bytes": sum(_dir_size(p) for p in paths)}
                  for key, paths in found.items()}
    return {"active": guest_runtime.stats(), "categories": categories,
            "total_items": sum(c["items"] for c in categories.values()),
            "total_bytes": sum(c["bytes"] for c in categories.values())}


def purge() -> dict:
    active = guest_runtime.purge_all()
    found, owners, files, scopes = collect()
    categories = {}
    for key, paths in found.items():
        deleted = freed = failed = 0
        for path in paths:
            size = _dir_size(path)
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                if path.exists():
                    failed += 1
                else:
                    deleted += 1
                    freed += size
            except FileNotFoundError:
                if path.exists():
                    failed += 1
            except OSError:
                failed += 1
        categories[key] = {"deleted": deleted, "bytes": freed, "failed": failed}
    from app.agents.student_model import manager
    from app.agents.knowledge import manager as knowledge_manager
    from app.agents.student_model.evaluation import store as journal_store
    from . import library, vector_store
    for owner in owners:
        manager._CACHE.pop(owner, None)
        with journal_store._CACHE_LOCK:
            journal_store._JOURNALS.pop(owner, None)
        for key in list(library._chunk_cache):
            if key[0] == owner:
                library._chunk_cache.pop(key, None)
        if knowledge_manager._INSTANCE is not None:
            knowledge_manager._INSTANCE.invalidate_custom_cache(owner)
    vector_failures = 0
    try:
        for fid in files:
            vector_store.delete_file(fid)
            vector_store.delete_scope("file:" + fid)
        for scope in scopes:
            vector_store.delete_scope(scope)
    except Exception:
        vector_failures = 1
    failed = sum(c["failed"] for c in categories.values()) + vector_failures
    return {"status": "partial" if failed else "purged", "active": active,
            "categories": categories, "failed": failed,
            "total_deleted": sum(c["deleted"] for c in categories.values()),
            "total_bytes": sum(c["bytes"] for c in categories.values())}
