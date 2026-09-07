"""Workspace (gongzuo xuexi qu): shared knowledge + public memory across sessions.

A Workspace groups multiple chat sessions under a named folder. It provides:

  - Source-selected knowledge: external library sources are explicitly selected
    textbooks (``selected_file_ids``); files uploaded directly into the
    workspace are workspace-owned shared materials (``workspace_file_ids``).
    knowledge_search merges session-level + these two authorized source sets —
    nothing else in the owner's library is visible to the session.
  - Public memory: a structured summary (same format as session compaction)
    that accumulates across all sessions in the workspace. Injected as a
    separate system block before the session's own history -- does NOT
    consume the session's compaction budget. Updated after each turn and
    when a session is moved into the workspace.

Persistence: chat_history/workspaces/ws_<timestamp>_<slug>.json, mirroring
session.py's file layout and path-traversal guards. Pre-library workspaces
(legacy ``knowledge_files``) are migrated into their exclusive library folder
lazily on load (see _migrate_legacy).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock
from .config import settings
from .knowledge_store import KnowledgeStore

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_WORKSPACES_DIR = _PROJECT_ROOT / "chat_history" / "workspaces"
_MAX_SLUG = 30
_FILENAME_PREFIX = "ws_"


def _slugify(text: str) -> str:
    text = (text or "").strip()
    kept = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    kept = re.sub(r"\s+", "_", kept).strip("_")
    return kept[:_MAX_SLUG] or "workspace"


def _resolve(ws_id: str, ext: str = ".json") -> Path:
    """Resolve workspace id to a Path under _WORKSPACES_DIR (traversal guard)."""
    bare = Path(ws_id).name
    if bare.endswith(ext):
        bare = bare[: -len(ext)]
    return _WORKSPACES_DIR / f"{bare}{ext}"


def new_workspace_id(name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_FILENAME_PREFIX}{ts}_{_slugify(name)}"


def workspace_upload_dir(ws_id: str) -> Path:
    """Per-workspace upload directory for shared knowledge files."""
    d = _WORKSPACES_DIR / "uploads" / Path(ws_id).name
    d.mkdir(parents=True, exist_ok=True)
    return d


class Workspace:
    """A named folder grouping sessions with shared knowledge + public memory."""

    def __init__(
        self,
        workspace_id: str = "",
        name: str = "gongzuo xuexi qu",
        session_ids: list[str] | None = None,
        knowledge: KnowledgeStore | None = None,
        public_memory: str = "",
        public_memory_updated_at: float = 0.0,
        created_at: float | None = None,
        updated_at: float | None = None,
        student_id: str = "",
        library_folder_id: str = "",
        selected_folder_ids: list[str] | None = None,
        selected_file_ids: list[str] | None = None,
        workspace_file_ids: list[str] | None = None,
        memory_boundary_sessions: list[str] | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.student_id = student_id  # M0: 归属命名空间（"" = M0 前遗留，归游客）
        self.name = name
        self.session_ids = session_ids or []
        # Shared knowledge store uses the workspace's own upload dir.
        # LEGACY: pre-library workspaces kept shared files here; load_workspace
        # migrates them into the workspace's exclusive library folder, after
        # which this store stays empty.
        self.knowledge = knowledge or KnowledgeStore(
            upload_dir=workspace_upload_dir(workspace_id) if workspace_id else None
        )
        self.public_memory = public_memory
        self.public_memory_updated_at = public_memory_updated_at
        # M7 library sources: the workspace reads exactly these library
        # folders/files (its exclusive folder is in selected_folder_ids by
        # default). Nothing else in the owner's library is visible.
        self.library_folder_id = library_folder_id
        self.selected_folder_ids = selected_folder_ids or []
        self.selected_file_ids = selected_file_ids or []
        # Files uploaded through /workspaces/{id}/upload.  They live in the
        # workspace-bound Library folder but are a distinct authorization class
        # from selected textbooks: all sessions in this workspace may read them,
        # while no other workspace/session may do so implicitly.
        self.workspace_file_ids = workspace_file_ids or []
        # Session ids whose new-conversation boundary compression has already
        # run. This makes boundary hooks idempotent across upload/chat entrypoints.
        self.memory_boundary_sessions = memory_boundary_sessions or []
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_persistable(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "student_id": self.student_id,
            "name": self.name,
            "session_ids": self.session_ids,
            "knowledge_files": self.knowledge.file_list(),
            "library_folder_id": self.library_folder_id,
            "selected_folder_ids": self.selected_folder_ids,
            "selected_file_ids": self.selected_file_ids,
            "workspace_file_ids": self.workspace_file_ids,
            "memory_boundary_sessions": self.memory_boundary_sessions[-100:],
            "public_memory": self.public_memory,
            "public_memory_updated_at": self.public_memory_updated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Workspace":
        ws = cls(
            workspace_id=d.get("workspace_id", ""),
            name=d.get("name", "gongzuo xuexi qu"),
            session_ids=d.get("session_ids", []),
            public_memory=d.get("public_memory", ""),
            public_memory_updated_at=d.get("public_memory_updated_at", 0.0),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            student_id=d.get("student_id", ""),
            library_folder_id=d.get("library_folder_id", ""),
            selected_folder_ids=d.get("selected_folder_ids", []),
            selected_file_ids=d.get("selected_file_ids", []),
            workspace_file_ids=d.get("workspace_file_ids", []),
            memory_boundary_sessions=d.get("memory_boundary_sessions", []),
        )
        # Rebuild knowledge store from persisted file metadata.
        upload_dir = workspace_upload_dir(ws.workspace_id) if ws.workspace_id else None
        ws.knowledge = KnowledgeStore(upload_dir=upload_dir)
        for f in d.get("knowledge_files", []):
            ws.knowledge.files.append(f)
            fp = ws.knowledge.upload_dir / f"{f['id']}.txt"
            if fp.exists():
                from .retriever import chunk_text
                text = fp.read_text(encoding="utf-8")
                ws.knowledge.chunks.extend(
                    chunk_text(text, source=f["filename"], file_id=f["id"]))
        return ws


# --- CRUD ---

def save_workspace(ws: Workspace) -> str:
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    ws.updated_at = time.time()
    if not ws.workspace_id:
        ws.workspace_id = new_workspace_id(ws.name)
    if not ws.name.strip():
        ws.name = "gongzuo xuexi qu"
    path = _resolve(ws.workspace_id)
    with file_lock(path):
        atomic_write_text(path, json.dumps(ws.to_persistable(), ensure_ascii=False, indent=2))
    return ws.workspace_id


def load_workspace(ws_id: str) -> Workspace | None:
    path = _resolve(ws_id)
    if not path.exists():
        return None
    ws = Workspace.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if ws.knowledge.files:
        _migrate_legacy(ws)
    return ws


def workspace_for_session(session: Any) -> Workspace | None:
    """Resolve a session's workspace only when identity ownership matches."""
    ws_id = str(getattr(session, "workspace_id", "") or "")
    if not ws_id:
        return None
    ws = load_workspace(ws_id)
    if ws is None:
        return None
    sid = str(getattr(session, "student_id", "") or "")
    if sid and _owner_of(ws) != sid:
        return None
    return ws


def _owner_of(ws: Workspace) -> str:
    from ..agents.student_model.store import DEFAULT_STUDENT_ID
    return ws.student_id or DEFAULT_STUDENT_ID


def _migrate_legacy(ws: Workspace) -> None:
    """Move a pre-library workspace's shared files into its exclusive folder.

    Legacy workspaces kept shared files in ws.knowledge (uploads/<ws_id>/).
    They are moved — metadata + extracted text, preserving file ids so chunk
    ids stay stable — into a library folder bound to this workspace, which is
    then selected by default. The old "workspace:<id>" vector scope is dropped;
    the hybrid track re-indexes under "folder:<id>" on first query (backfill).
    Never raises: on any failure the legacy files stay put and readable.
    """
    try:
        from .library import load_library, save_library
        owner = _owner_of(ws)
        lib = load_library(owner)
        folder = lib.workspace_folder(ws.workspace_id) or lib.create_folder(
            ws.name, workspace_id=ws.workspace_id)
        for f in list(ws.knowledge.files):
            src = ws.knowledge.upload_dir / f"{f['id']}.txt"
            text = src.read_text(encoding="utf-8") if src.exists() else ""
            meta = lib.add_file(folder["id"], f.get("filename", ""), text,
                                file_id=f["id"])
            for k in ("summary", "topics"):
                if f.get(k):
                    meta[k] = f[k]
        save_library(lib)
        ws.library_folder_id = folder["id"]
        if folder["id"] not in ws.selected_folder_ids:
            ws.selected_folder_ids.append(folder["id"])
        ws.knowledge.files = []
        ws.knowledge.chunks = []
        save_workspace(ws)
        import shutil
        shutil.rmtree(workspace_upload_dir(ws.workspace_id), ignore_errors=True)
        try:
            from . import vector_store
            vector_store.delete_scope(f"workspace:{ws.workspace_id}")
        except Exception:
            pass
    except Exception:
        pass  # legacy overlay stays functional; migration retries next load


def ensure_library_folder(ws: Workspace) -> str:
    """Find-or-create the workspace's exclusive library folder.

    The folder carries workspace_id (auto-named after the workspace, deleted
    with it). Only guarantees EXISTENCE — selection is the caller's choice
    (create/migration/upload select it explicitly; the user may later
    unselect it in workspace settings). Returns the folder id."""
    from .library import load_library, save_library
    owner = _owner_of(ws)
    lib = load_library(owner)
    folder = (lib.find_folder(ws.library_folder_id)
              or lib.workspace_folder(ws.workspace_id))
    if folder is None:
        folder = lib.create_folder(ws.name, workspace_id=ws.workspace_id)
        save_library(lib)
    if ws.library_folder_id != folder["id"]:
        ws.library_folder_id = folder["id"]
        save_workspace(ws)
    return folder["id"]


def resolve_textbook_file(student_id: str, file_id: str) -> tuple[dict[str, Any] | None, str]:
    """P6-C3：仅教材可被选用——file_id 是已注册教材（自有或公用）时返回
    (library 文件 meta, 所属命名空间)，否则 (None, "")。

    公用教材落在保留命名空间 ``public``；选择链路（工作区/会话引用）经此
    统一解析，非教材文件（散文件/文件夹）一律不参与对话检索。
    """
    from .textbook import PUBLIC_STUDENT_ID, textbook_for_file
    from .library import load_library
    if textbook_for_file(student_id, file_id) is not None:
        lib = load_library(student_id)
        meta = lib.find_file(file_id)
        if meta is not None:
            return meta, student_id
    if textbook_for_file(PUBLIC_STUDENT_ID, file_id) is not None:
        lib = load_library(PUBLIC_STUDENT_ID)
        meta = lib.find_file(file_id)
        if meta is not None:
            return meta, PUBLIC_STUDENT_ID
    return None, ""


def valid_textbook_file_ids(student_id: str) -> set[str]:
    """批量版 resolve_textbook_file：返回该学生可选的全部教材 file_id 集合
    （自有 + 公用，且 library 文件仍存在）。列表端点对 N 个 file_id 逐个调
    resolve_textbook_file 会各重载一次 store——这里每命名空间只加载一次。
    判定语义与 resolve_textbook_file 完全一致。"""
    from .textbook import PUBLIC_STUDENT_ID, load_textbooks
    from .library import load_library
    ids: set[str] = set()
    for sid in (student_id, PUBLIC_STUDENT_ID):
        existing = {f["id"] for f in load_library(sid).files}
        for tb in load_textbooks(sid):
            if tb.get("kind") == "group":
                fids = tb.get("file_ids") or []
            else:
                fids = [tb["file_id"]] if tb.get("file_id") else []
            ids.update(f for f in fids if f in existing)
    return ids


def workspace_owned_file_ids(ws: Workspace, lib: Any | None = None) -> list[str]:
    """Return authorized workspace-owned files, repairing pre-field records.

    ``workspace_file_ids`` was introduced after workspace uploads were already
    stored in the workspace-bound Library folder.  The folder binding is itself
    an ownership fact, so include its files as a compatibility repair and keep
    the explicit list as the persisted/current API representation.
    """
    try:
        if lib is None:
            from .library import load_library
            lib = load_library(_owner_of(ws))
        folder_id = ws.library_folder_id or ""
        discovered = [f.get("id", "") for f in lib.files
                      if folder_id and f.get("folder_id") == folder_id]
        return [fid for fid in dict.fromkeys(
            list(getattr(ws, "workspace_file_ids", []) or []) + discovered)
                if fid and lib.find_file(fid) is not None]
    except Exception:
        return list(getattr(ws, "workspace_file_ids", []) or [])


def readable_stores(ws: Workspace) -> list[tuple[str, KnowledgeStore]]:
    """[(scope, store)] the workspace may read.

    External Library sources remain selected textbooks only (P6-C3); files
    uploaded directly to this workspace are additionally readable as an
    explicit workspace-owned shared source.  Arbitrary folders/loose Library
    files remain invisible.
    The session's own store is NOT included (callers add it)."""
    try:
        from .library import load_library, library_data_dir
        owner = _owner_of(ws)
        out: list[tuple[str, KnowledgeStore]] = []
        libs: dict[str, Any] = {}

        def _lib(sid: str):
            if sid not in libs:
                libs[sid] = load_library(sid)
            return libs[sid]

        owner_lib = _lib(owner)
        workspace_chunks = [
            c for fid in workspace_owned_file_ids(ws, owner_lib)
            for c in owner_lib.chunks_for(fid)
        ]
        if workspace_chunks:
            store = KnowledgeStore(upload_dir=library_data_dir(owner))
            store.chunks = workspace_chunks
            # Upload indexing already uses the bound folder scope.
            scope = (f"folder:{ws.library_folder_id}" if ws.library_folder_id
                     else f"workspace:{ws.workspace_id}")
            out.append((scope, store))

        for fid in ws.selected_file_ids:
            meta, owner_sid = resolve_textbook_file(owner, fid)
            if meta is None:
                continue
            chunks = _lib(owner_sid).chunks_for(fid)
            if chunks:
                store = KnowledgeStore(upload_dir=library_data_dir(owner_sid))
                store.chunks = chunks
                out.append((f"file:{fid}", store))
        return out
    except Exception:
        return []


def readable_files(ws: Workspace) -> list[dict[str, Any]]:
    """File metadata the workspace may read (display + planner injection).

    Returns workspace-owned uploads first, followed by selected textbooks.
    Each item carries stable scope metadata for API/UI consumers.
    """
    try:
        from .library import load_library
        owner = _owner_of(ws)
        out: list[dict[str, Any]] = []
        owner_lib = load_library(owner)
        seen: set[str] = set()
        for fid in workspace_owned_file_ids(ws, owner_lib):
            meta = owner_lib.find_file(fid)
            if meta is not None:
                out.append({**meta, "source_scope": "workspace",
                            "source_visibility": "workspace_shared"})
                seen.add(fid)
        for fid in ws.selected_file_ids:
            if fid in seen:
                continue
            meta, owner_sid = resolve_textbook_file(owner, fid)
            if meta is not None:
                out.append({**meta, "source_scope": "workspace_textbook",
                            "source_visibility": ("public" if owner_sid == "public"
                                                  else "private")})
                seen.add(fid)
        return out
    except Exception:
        return []


def list_workspaces() -> list[dict[str, Any]]:
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(_WORKSPACES_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "workspace_id": d.get("workspace_id", p.stem),
                "student_id": d.get("student_id", ""),
                "name": d.get("name", "gongzuo xuexi qu"),
                "session_count": len(d.get("session_ids", [])),
                "file_count": len(d.get("knowledge_files", [])),
                "library_folder_id": d.get("library_folder_id", ""),
                "selected_folder_ids": d.get("selected_folder_ids", []),
                "selected_file_ids": d.get("selected_file_ids", []),
                "workspace_file_ids": d.get("workspace_file_ids", []),
                "has_memory": bool(d.get("public_memory", "").strip()),
                "updated_at": d.get("updated_at", 0),
            })
        except Exception:
            continue
    return out


def delete_workspace(ws_id: str) -> bool:
    path = _resolve(ws_id)
    if not path.exists():
        return False
    # Clear workspace_id on all member sessions before deleting.
    d = json.loads(path.read_text(encoding="utf-8"))
    try:
        from .session import load_session, save_session
        for sid in d.get("session_ids", []):
            session = load_session(sid)
            if session and session.workspace_id == ws_id:
                session.workspace_id = ""
                save_session(session)
    except Exception:
        pass  # never block deletion on session cleanup
    path.unlink()
    # Delete the workspace's exclusive library folder (files + disk artifacts),
    # then drop the removed files' vectors (best-effort; track may be off).
    removed_file_ids: list[str] = []
    try:
        from .library import load_library, save_library
        owner = d.get("student_id", "")
        from ..agents.student_model.store import DEFAULT_STUDENT_ID
        lib = load_library(owner or DEFAULT_STUDENT_ID)
        folder = lib.workspace_folder(ws_id)
        if folder is not None:
            removed_file_ids = lib.delete_folder(
                folder["id"], allow_workspace_bound=True) or []
            save_library(lib)
    except Exception:
        pass
    # Also clean up the legacy shared uploads dir (pre-library workspaces).
    upath = workspace_upload_dir(ws_id)
    if upath.exists():
        import shutil
        shutil.rmtree(upath, ignore_errors=True)
    # Drop the workspace's vectors (best-effort; the vector track may be off).
    try:
        from . import vector_store
        vector_store.delete_scope(f"workspace:{ws_id}")
        for fid in removed_file_ids:
            vector_store.delete_file(fid)
    except Exception:
        pass
    return True


def rename_workspace(ws_id: str, name: str) -> bool:
    ws = load_workspace(ws_id)
    if ws is None:
        return False
    ws.name = name.strip() or "gongzuo xuexi qu"
    save_workspace(ws)
    # The exclusive library folder follows the workspace's name.
    try:
        from .library import load_library, save_library
        lib = load_library(_owner_of(ws))
        folder_id = ws.library_folder_id or ""
        if folder_id and lib.rename_folder(folder_id, ws.name,
                                           allow_workspace_bound=True):
            save_library(lib)
    except Exception:
        pass
    return True


def add_session_to_workspace(ws_id: str, session_id: str) -> Workspace | None:
    """Move a session into a workspace. Returns the updated workspace or None.

    A session can only be in ONE workspace at a time. If it was previously in
    a different workspace, it is removed from the old one first.
    """
    # First, remove from any existing workspace.
    old_ws = get_workspace_for_session(session_id)
    if old_ws and old_ws.workspace_id != ws_id:
        if session_id in old_ws.session_ids:
            old_ws.session_ids.remove(session_id)
            save_workspace(old_ws)
    ws = load_workspace(ws_id)
    if ws is None:
        return None
    if session_id not in ws.session_ids:
        ws.session_ids.append(session_id)
        save_workspace(ws)
    return ws


def remove_session_from_workspace(ws_id: str, session_id: str) -> Workspace | None:
    """Remove a session from a workspace (move back to loose)."""
    ws = load_workspace(ws_id)
    if ws is None:
        return None
    if session_id in ws.session_ids:
        ws.session_ids.remove(session_id)
        save_workspace(ws)
    return ws


def get_workspace_for_session(session_id: str) -> Workspace | None:
    """Find which workspace (if any) a session belongs to."""
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    for p in _WORKSPACES_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if session_id in d.get("session_ids", []):
                return Workspace.from_dict(d)
        except Exception:
            continue
    return None
 

def _file_display_name(f: dict[str, Any]) -> str:
    """"文件名：摘要（标签）" for the planner/preamble injection.

    The LLM then knows what each uploaded file covers BEFORE deciding to
    knowledge_search. Files uploaded before the summary feature (or whose
    summary generation failed) degrade to the bare filename.
    """
    name = f.get("filename", "")
    summary = (f.get("summary") or "").strip()
    if not summary:
        return name
    topics = [str(t) for t in (f.get("topics") or []) if str(t).strip()][:5]
    tag = f"（{'、'.join(topics)}）" if topics else ""
    return f"{name}：{summary}{tag}"


def merged_knowledge_files(session: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (files, display_names) merging session-level + workspace-readable files.

    Used by the agent pipeline (snapshot / preamble / attachment context) so
    the planner knows which materials exist and plans a knowledge_search step.
    The workspace part is the workspace's SELECTED library sources only (see
    readable_files) — unselected library files stay invisible. display_names
    carry the file summary + topic tags when available (see _file_display_name).
    Duck-typed: accepts any object with ``workspace_id`` + ``knowledge``.
    """
    cached = getattr(session, "_turn_merged_knowledge_cache", None)
    if isinstance(cached, tuple) and len(cached) == 2:
        return cached
    files = list(session.knowledge.file_list())
    ws_id = getattr(session, "workspace_id", "")
    if ws_id:
        ws = workspace_for_session(session)
        if ws:
            existing_ids = {f.get("id") for f in files}
            for wf in readable_files(ws):
                if wf.get("id") not in existing_ids:
                    files.append(wf)
    names = [_file_display_name(f) for f in files]
    result = (files, names)
    if getattr(session, "_turn_material_cache_enabled", False):
        session._turn_merged_knowledge_cache = result
    return result


def material_sources(session: Any) -> list[dict[str, Any]]:
    """Stable read-only source manifest for chat details and sidebars.

    Session files are private copies (including library references); workspace
    entries are live overlay sources.  The manifest deliberately contains
    metadata only and never exposes extracted text or API credentials.
    """
    try:
        files, _ = merged_knowledge_files(session)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for f in files:
            fid = str(f.get("id", ""))
            if not fid or fid in seen:
                continue
            seen.add(fid)
            scope = f.get("source_scope") or "session"
            visibility = f.get("source_visibility") or "session_private"
            out.append({**f, "source_scope": scope,
                        "source_visibility": visibility,
                        "has_original": bool(f.get("orig_ext")),
                        "chunk_count": int(f.get("chunk_count") or 0)})
        return out
    except Exception:
        return []


def scoped_knowledge_stores(session: Any) -> list[tuple[str, KnowledgeStore]]:
    """Return [(scope, store)] for hybrid retrieval: the session store plus
    the workspace's selected library sources (when bound), each tagged with
    the vector index scope used by core/vector_store ("session:<id>" /
    "folder:<id>" / "file:<id>"). Empty stores are skipped. Never raises."""
    out: list[tuple[str, KnowledgeStore]] = []
    try:
        sk = getattr(session, "knowledge", None)
        sid = getattr(session, "session_id", "") or ""
        if sk is not None and getattr(sk, "chunks", None) and sid:
            out.append((f"session:{sid}", sk))
        ws_id = getattr(session, "workspace_id", "")
        if ws_id:
            ws = workspace_for_session(session)
            if ws:
                out.extend(readable_stores(ws))
    except Exception:
        pass
    return out


def merged_knowledge_store(session: Any) -> KnowledgeStore:
    """Return a single KnowledgeStore combining session + workspace-readable chunks.

    For callers that need to SEARCH (not just list files): M5's ContentResolver
    grounds concept content in the student's actual materials, reusing the same
    BM25 store as knowledge_search. Builds a fresh combined store from the live
    session store + the workspace's selected library sources, de-duping by
    (source, index). Never raises; returns an empty store on any failure.
    """
    try:
        combined = KnowledgeStore()
        seen: set[tuple[str, int]] = set()
        for store in _iter_stores(session):
            for c in getattr(store, "chunks", []) or []:
                key = (getattr(c, "source", ""), getattr(c, "index", -1))
                if key in seen:
                    continue
                seen.add(key)
                combined.chunks.append(c)
        return combined
    except Exception:
        return KnowledgeStore()


def _iter_stores(session: Any):
    """Yield the session knowledge store + the workspace's selected source stores."""
    sk = getattr(session, "knowledge", None)
    if sk is not None:
        yield sk
    ws = workspace_for_session(session)
    if ws:
        for _scope, store in readable_stores(ws):
            yield store
