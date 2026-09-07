"""Per-student material library (ziliao ku): folders + files, private by default.

The library is the M7 resource center's backing store. Files uploaded here are
NOT visible to any conversation until a workspace explicitly selects the file
or its folder (see Workspace.selected_folder_ids / selected_file_ids). Each
workspace owns one exclusive folder (``workspace_id`` set on the folder) that
receives workspace-level uploads and is auto-selected for that workspace.

Persistence (mirrors session.py / workspace.py conventions):
  - Index:  chat_history/library/<student_key>.json  {"folders": [...], "files": [...]}
  - Data:   chat_history/library/data/<student_key>/<file_id>.txt   (extracted text)
            chat_history/library/data/<student_key>/<file_id><ext>  (original binary)

The extracted text is what gets chunked for retrieval; the original binary is
kept purely for re-download. Legacy files (pre-download feature) carry no
original and are not downloadable (the API answers 404).

Vector scope convention (core/vector_store.py): chunks of a folder file are
indexed under "folder:<folder_id>", unfiled (root) files under "file:<file_id>"
— file_scope() is the single rule every index/move/lookup path uses.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock
from .knowledge_store import KnowledgeStore
from .retriever import Chunk, chunk_text

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LIBRARY_DIR = _PROJECT_ROOT / "chat_history" / "library"
_MAX_FOLDER_NAME = 60


def _default_student_id() -> str:
    # Lazy import: agents.student_model.store pulls core.config; importing it
    # at module level would risk a cycle through agents/__init__.
    from ..agents.student_model.store import DEFAULT_STUDENT_ID
    return DEFAULT_STUDENT_ID


def _key(student_id: str) -> str:
    """Filesystem-safe student key (traversal guard); "" maps to the guest."""
    bare = Path(student_id or "").name
    return bare or _default_student_id()


def _index_path(student_id: str) -> Path:
    return _LIBRARY_DIR / f"{_key(student_id)}.json"


def library_data_dir(student_id: str) -> Path:
    d = _LIBRARY_DIR / "data" / _key(student_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_scope(f: dict[str, Any]) -> str:
    """Vector-index scope for one file: folder-scoped when filed, else file-scoped."""
    folder_id = f.get("folder_id") or ""
    return f"folder:{folder_id}" if folder_id else f"file:{f.get('id', '')}"


# 进程级 chunk 缓存：(namespace, file_id) -> (txt mtime, chunks)。
# 切块是 CPU 密集操作（V2 尤甚）；文本未变时跨 Library 实例复用。
# dict 读写 GIL 原子，竞态最坏是重复切块一次。
_chunk_cache: dict[tuple[str, str], tuple[float, list[Chunk]]] = {}


def _cached_chunks(namespace: str, meta: dict[str, Any]) -> list[Chunk]:
    fp = library_data_dir(namespace) / f"{meta['id']}.txt"
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        return []
    key = (namespace, meta["id"])
    hit = _chunk_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    text = fp.read_text(encoding="utf-8")
    from .structured_chunker import chunks_from_meta
    chunks = chunks_from_meta(text, source=meta.get("filename", ""),
                              file_id=meta["id"], meta=meta)
    _chunk_cache[key] = (mtime, chunks)
    return chunks


def _invalidate_chunk_cache(namespace: str, file_id: str) -> None:
    _chunk_cache.pop((namespace, file_id), None)


class Library:
    """One student's folders + files with lazily rebuilt retrieval chunks."""

    def __init__(self, student_id: str) -> None:
        self.student_id = student_id
        self.folders: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = []
        # file_id -> chunks。写路径（上传/重建）直接写入；读路径经 chunks_for()
        # 惰性加载——from_dict 不再急切重切块（曾是列表/详情端点秒级延迟与
        # CPU 饱和的根因：每次 load_library 都对全库全文重新 chunk）。
        self.chunks_by_file: dict[str, list[Chunk]] = {}

    # --- lookups ---

    def find_folder(self, folder_id: str) -> dict[str, Any] | None:
        return next((f for f in self.folders if f.get("id") == folder_id), None)

    def find_file(self, file_id: str) -> dict[str, Any] | None:
        return next((f for f in self.files if f.get("id") == file_id), None)

    def folder_file_count(self, folder_id: str) -> int:
        return sum(1 for f in self.files if f.get("folder_id") == folder_id)

    def workspace_folder(self, workspace_id: str) -> dict[str, Any] | None:
        return next((f for f in self.folders if f.get("workspace_id") == workspace_id), None)

    # --- folders ---

    def create_folder(self, name: str, workspace_id: str = "") -> dict[str, Any]:
        folder = {
            "id": "f" + uuid.uuid4().hex[:8],
            "name": (name or "").strip()[:_MAX_FOLDER_NAME] or "未命名文件夹",
            "workspace_id": workspace_id,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.folders.append(folder)
        return folder

    def rename_folder(self, folder_id: str, name: str,
                      allow_workspace_bound: bool = False) -> bool:
        folder = self.find_folder(folder_id)
        if folder is None:
            return False
        if folder.get("workspace_id") and not allow_workspace_bound:
            return False  # exclusive folders follow their workspace's name
        folder["name"] = (name or "").strip()[:_MAX_FOLDER_NAME] or folder["name"]
        folder["updated_at"] = time.time()
        return True

    def delete_folder(self, folder_id: str,
                      allow_workspace_bound: bool = False) -> list[str] | None:
        """Delete a folder and its files (disk artifacts included).

        Returns the removed file_ids (for vector cleanup); None when the folder
        is missing or is workspace-bound without explicit allowance.
        """
        folder = self.find_folder(folder_id)
        if folder is None:
            return None
        if folder.get("workspace_id") and not allow_workspace_bound:
            return None
        removed = [f["id"] for f in self.files if f.get("folder_id") == folder_id]
        for fid in removed:
            self.remove_file(fid)
        self.folders = [f for f in self.folders if f.get("id") != folder_id]
        return removed

    # --- files ---

    def add_file(self, folder_id: str, filename: str, text: str,
                 raw: bytes | None = None, orig_ext: str = "",
                 file_id: str | None = None) -> dict[str, Any]:
        """Add a file: persist extracted text + original binary, build chunks.

        ``file_id`` is injectable so legacy migration can preserve ids (chunk
        ids stay stable across the move). A requested id that is already taken
        falls back to a fresh uuid — duplicate ids across folders break
        find/remove semantics and React keys."""
        fid = file_id or uuid.uuid4().hex[:12]
        if self.find_file(fid) is not None:
            fid = uuid.uuid4().hex[:12]
        data = library_data_dir(self.student_id)
        (data / f"{fid}.txt").write_text(text, encoding="utf-8")
        has_orig = bool(raw) and bool(orig_ext)
        if has_orig:
            # ".orig" infix: never collides with the extracted-text <fid>.txt
            # (a .txt upload's original and its text share the extension).
            (data / f"{fid}.orig{orig_ext}").write_bytes(raw)
        chunks = chunk_text(text, source=filename, file_id=fid)
        self.chunks_by_file[fid] = chunks
        # 播种进程级缓存：刚写的文本+刚切的块直接复用，后续加载零切块。
        try:
            _chunk_cache[(self.student_id, fid)] = (
                (data / f"{fid}.txt").stat().st_mtime, chunks)
        except OSError:
            pass
        now = time.time()
        meta: dict[str, Any] = {
            "id": fid,
            "filename": filename,
            "original_filename": filename,
            "folder_id": folder_id or "",
            "char_count": len(text),
            "chunk_count": len(chunks),
            "orig_ext": orig_ext if has_orig else "",
            "created_at": now,
            "updated_at": now,
        }
        self.files.append(meta)
        return meta

    def rename_file(self, file_id: str, filename: str) -> bool:
        """Rename the display/download filename without touching text/chunks."""
        f = self.find_file(file_id)
        if f is None:
            return False
        name = (filename or "").strip()[:240]
        if not name:
            return False
        f["filename"] = name
        f["updated_at"] = time.time()
        return True

    def move_file(self, file_id: str, folder_id: str) -> bool:
        f = self.find_file(file_id)
        if f is None:
            return False
        if folder_id and self.find_folder(folder_id) is None:
            return False
        f["folder_id"] = folder_id or ""
        return True

    def remove_file(self, file_id: str) -> bool:
        f = self.find_file(file_id)
        if f is None:
            return False
        self.files = [x for x in self.files if x.get("id") != file_id]
        self.chunks_by_file.pop(file_id, None)
        _invalidate_chunk_cache(self.student_id, file_id)
        data = library_data_dir(self.student_id)
        orig_ext = f.get("orig_ext") or ""
        for suffix in (".txt", f".orig{orig_ext}" if orig_ext else ""):
            if not suffix:
                continue
            fp = data / f"{file_id}{suffix}"
            if fp.exists():
                fp.unlink()
        return True

    # --- retrieval views (the workspace source-selection boundary) ---

    def _selected_file_metas(self, folder_ids: list[str],
                             file_ids: list[str]) -> list[dict[str, Any]]:
        """Files visible under a selection: whole folders + individual files.

        Individually selected files whose folder is also selected are de-duped
        (they are already covered by the folder store)."""
        folders = set(folder_ids or [])
        out: list[dict[str, Any]] = []
        covered: set[str] = set()
        for f in self.files:
            if f.get("folder_id") in folders:
                out.append(f)
                covered.add(f["id"])
        for fid in file_ids or []:
            if fid in covered:
                continue
            f = self.find_file(fid)
            if f is not None:
                out.append(f)
                covered.add(fid)
        return out

    def files_for_selection(self, folder_ids: list[str],
                            file_ids: list[str]) -> list[dict[str, Any]]:
        return self._selected_file_metas(folder_ids, file_ids)

    def stores_for_selection(self, folder_ids: list[str],
                             file_ids: list[str]) -> list[tuple[str, KnowledgeStore]]:
        """[(scope, store)] for hybrid retrieval, mirroring scoped_knowledge_stores.

        One store per selected folder (scope "folder:<id>") plus one store per
        individually selected loose file (scope "file:<id>"). Empty stores are
        skipped; stores are lightweight KnowledgeStore shells (chunks only)."""
        out: list[tuple[str, KnowledgeStore]] = []
        folders = set(folder_ids or [])
        data = library_data_dir(self.student_id)
        for fid in folder_ids or []:
            if self.find_folder(fid) is None:
                continue
            chunks = [c for f in self.files if f.get("folder_id") == fid
                      for c in self.chunks_for(f["id"])]
            if chunks:
                store = KnowledgeStore(upload_dir=data)
                store.chunks = chunks
                out.append((f"folder:{fid}", store))
        for meta in self._selected_file_metas([], file_ids):
            if meta.get("folder_id") in folders:
                continue  # already covered by its folder store
            chunks = self.chunks_for(meta["id"])
            if chunks:
                store = KnowledgeStore(upload_dir=data)
                store.chunks = chunks
                out.append((f"file:{meta['id']}", store))
        return out

    def chunks_for_files(self, file_ids: list[str]) -> list[Chunk]:
        return [c for fid in file_ids for c in self.chunks_for(fid)]

    def chunks_for(self, file_id: str) -> list[Chunk]:
        """按文件惰性取 chunks：内存中已有（写路径刚生成）优先，否则读盘
        切块并落进程级缓存（文本未变直接复用）。文件缺失返回 []。"""
        got = self.chunks_by_file.get(file_id)
        if got is not None:
            return got
        meta = self.find_file(file_id)
        if meta is None:
            return []
        chunks = _cached_chunks(self.student_id, meta)
        self.chunks_by_file[file_id] = chunks
        return chunks

    # --- serialization ---

    def to_dict(self) -> dict[str, Any]:
        return {"student_id": self.student_id,
                "folders": self.folders, "files": self.files}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Library":
        # 只还原元数据；chunks 经 chunks_for() 按需惰性重建（进程级缓存）。
        # 急切全量重切块会让每个调用 load_library 的端点付出整库 CPU 成本
        # （公共教材库 13 卷 V2 切块实测 ~7s/次）。
        lib = cls(student_id=d.get("student_id", ""))
        lib.folders = list(d.get("folders", []))
        lib.files = list(d.get("files", []))
        return lib


# --- CRUD ---

def load_library(student_id: str) -> Library:
    """Load the student's library; an empty one when nothing is persisted yet."""
    sid = student_id or _default_student_id()
    path = _index_path(sid)
    if not path.exists():
        return Library(student_id=sid)
    try:
        return Library.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return Library(student_id=sid)  # corrupt index: degrade to empty, never raise


def save_library(lib: Library) -> None:
    _LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    lib.student_id = lib.student_id or _default_student_id()
    path = _index_path(lib.student_id)
    with file_lock(path):
        atomic_write_text(path, json.dumps(lib.to_dict(), ensure_ascii=False, indent=2))
