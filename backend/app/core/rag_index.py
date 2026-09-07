"""Versioned, no-re-OCR RAG index rebuilds for persisted textbook text."""
from __future__ import annotations

import hashlib
import time
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from .library import library_data_dir, load_library, save_library
from .structured_chunker import (HARD_TOKEN_LIMIT, active_chunk_schema,
                                 chunk_text_for_rag, estimate_model_tokens)

RAG_INDEX_VERSION = "rag-v2"
_RAG_OWNER_LOCKS: dict[str, threading.RLock] = {}
_RAG_OWNER_LOCKS_GUARD = threading.Lock()


@contextmanager
def _owner_rag_lock(owner_id: str) -> Iterator[None]:
    key = str(owner_id or "guest")
    with _RAG_OWNER_LOCKS_GUARD:
        lock = _RAG_OWNER_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_staged_chunks(text: str, chunks: list[Any]) -> dict[str, Any]:
    """Reject an invalid derived index before replacing the active snapshot."""
    if text.strip() and not chunks:
        raise ValueError("Structured RAG staging produced no chunks for non-empty text")
    ids: set[str] = set()
    page_count = max(1, len(text.split("\f")))
    max_tokens = 0
    for chunk in chunks:
        chunk_id = str(getattr(chunk, "chunk_id", "") or "")
        if not chunk_id or chunk_id in ids:
            raise ValueError("Structured RAG staging contains duplicate/empty chunk ids")
        ids.add(chunk_id)
        metadata = getattr(chunk, "metadata", {}) or {}
        expected_hash = hashlib.sha256(str(getattr(chunk, "text", "")).encode()).hexdigest()
        if metadata.get("content_sha256") != expected_hash:
            raise ValueError("Structured RAG staging content hash mismatch")
        tokens = int(metadata.get("token_estimate") or
                     estimate_model_tokens(str(getattr(chunk, "text", ""))))
        if tokens > HARD_TOKEN_LIMIT:
            raise ValueError(f"Structured RAG staging chunk exceeds hard limit: {tokens}")
        max_tokens = max(max_tokens, tokens)
        page = getattr(chunk, "page", None)
        if page is not None and not (1 <= int(page) <= page_count):
            raise ValueError("Structured RAG staging page mapping is out of range")
        start, end = metadata.get("source_start"), metadata.get("source_end")
        if start is not None and end is not None and int(start) > int(end):
            raise ValueError("Structured RAG staging source mapping is invalid")
    quality = {"status": "passed", "chunk_count": len(chunks),
               "max_token_estimate": max_tokens, "hard_token_limit": HARD_TOKEN_LIMIT}
    # P9 索引准入门：structured chunker 对乱码 chunk 已置 garble_excluded 且
    # 不生成 BM25 token（mojibake 绝不进入索引）；此处计数供质量报告观测。
    excluded = sum(1 for c in chunks
                   if (getattr(c, "metadata", {}) or {}).get("garble_excluded"))
    if excluded:
        quality["excluded_by_garble"] = excluded
    # 保真度检查（P8）：结构门槛只看 token/映射，乱码文本层（定制字体无
    # ToUnicode 映射）同样能通过——2026-08 取证发现部分卷以 mojibake
    # 形态拿到 staging_quality=passed。这里按页跑 text_quality 分级，坏卷
    # 标记 failed_garble 但仍发布（降级可检索），由质量报告端点/手动
    # quality_ocr 重建兜底，绝不自动烧 OCR API。
    from .text_quality import summarize_pages
    summary = summarize_pages(text.split("\f"))
    corrupt = int(summary["corrupt"])
    non_empty = summary["total"] - summary["empty"]
    if corrupt and non_empty and corrupt / non_empty >= 0.10:
        quality["status"] = "failed_garble"
    quality["text_quality"] = {k: (round(v, 6) if isinstance(v, float) else int(v))
                               for k, v in summary.items()}
    return quality


def _stage_file_index(lib: Any, owner_id: str, file_id: str,
                      *, force: bool) -> tuple[dict[str, Any], bool]:
    meta = lib.find_file(file_id)
    if meta is None:
        raise FileNotFoundError(file_id)
    text_path = library_data_dir(owner_id) / f"{file_id}.txt"
    text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
    content_hash = _hash_text(text)
    current = dict(meta.get("rag_index") or {})
    if (not force and current.get("content_sha256") == content_hash
            and current.get("version") == RAG_INDEX_VERSION
            and meta.get("chunk_schema") == active_chunk_schema()):
        return {"file_id": file_id, **current, "reused": True}, False
    chunks = chunk_text_for_rag(
        text, source=str(meta.get("filename") or ""), file_id=file_id)
    schema = active_chunk_schema()
    staging_quality = _validate_staged_chunks(text, chunks) \
        if schema.startswith("structured-v") else {
        "status": "legacy", "chunk_count": len(chunks)}
    lib.chunks_by_file[file_id] = chunks
    meta["char_count"] = len(text)
    meta["chunk_count"] = len(chunks)
    meta["chunk_schema"] = schema
    meta["rag_index"] = {
        "version": RAG_INDEX_VERSION,
        "chunk_schema": schema,
        "content_sha256": content_hash,
        "chunk_count": len(chunks),
        "bm25_revision": f"{RAG_INDEX_VERSION}:{content_hash[:16]}",
        "vector_revision": "pending",
        "status": "bm25_ready",
        "staging_quality": staging_quality,
        "updated_at": time.time(),
    }
    meta["updated_at"] = time.time()
    return {"file_id": file_id, **meta["rag_index"], "reused": False}, True


def rebuild_file_index(owner_id: str, file_id: str, *, force: bool = False) -> dict[str, Any]:
    """Rebuild one derived file index from its persisted `.txt` fact source."""
    with _owner_rag_lock(owner_id):
        lib = load_library(owner_id)
        result, changed = _stage_file_index(lib, owner_id, file_id, force=force)
        if changed:
            save_library(lib)
        return result


def rebuild_textbook_rag(owner_id: str, textbook: dict[str, Any],
                         *, force: bool = False) -> dict[str, Any]:
    """Stage all volumes in one owner-locked snapshot and publish atomically."""
    file_ids = list(textbook.get("file_ids") or [])
    if not file_ids and textbook.get("file_id"):
        file_ids = [str(textbook["file_id"])]
    with _owner_rag_lock(owner_id):
        lib = load_library(owner_id)
        entries: list[dict[str, Any]] = []
        changed = False
        for file_id in file_ids:
            entry, file_changed = _stage_file_index(
                lib, owner_id, str(file_id), force=force)
            entries.append(entry)
            changed = changed or file_changed
        if changed:
            save_library(lib)
    return {
        "version": RAG_INDEX_VERSION,
        "textbook_id": textbook.get("id"),
        "files": entries,
        "content_hashes": {e["file_id"]: e.get("content_sha256", "") for e in entries},
        "updated_at": time.time(),
    }


def file_text_hash(owner_id: str, file_id: str) -> str:
    path = library_data_dir(owner_id) / f"{file_id}.txt"
    return _hash_text(path.read_text(encoding="utf-8") if path.exists() else "")

async def refresh_textbook_vectors(owner_id: str, textbook: dict[str, Any]) -> dict[str, bool]:
    """Queue textbook vectors on the global single-slot ML worker.

    BM25 metadata is durable before jobs are scheduled. Completion callbacks
    publish both per-file and textbook-registry status; failures retain
    ``bm25_ready`` and never make the textbook unusable.
    """
    from .embedding import get_embedding_client
    from .library import file_scope
    from .vector_jobs import schedule_index
    lib = load_library(owner_id)
    embed = get_embedding_client()
    outcomes: dict[str, bool] = {}
    file_ids = [str(x) for x in (list(textbook.get("file_ids") or [])
                or ([textbook.get("file_id")] if textbook.get("file_id") else []))]
    jobs: list[tuple[str, str, list[Any]]] = []

    def publish(fid: str, ok: bool) -> None:
        with _owner_rag_lock(owner_id):
            current_lib = load_library(owner_id)
            meta = current_lib.find_file(fid)
            if meta is None:
                return
            idx = dict(meta.get("rag_index") or {})
            idx["vector_revision"] = (idx.get("bm25_revision", RAG_INDEX_VERSION)
                                      if ok else "unavailable")
            idx["status"] = "ready" if ok else "bm25_ready"
            idx["updated_at"] = time.time()
            meta["rag_index"] = idx
            save_library(current_lib)
        # Registry projection is outside the owner lock to avoid nested file
        # locks. Look up dynamically because an upload may register its textbook
        # shortly after the vector job was queued.
        try:
            from . import textbook as tb_store
            record = tb_store.textbook_for_file(owner_id, fid)
            if record is not None:
                tb_store.update_textbook(
                    owner_id, record["id"],
                    rag_index=summarize_textbook_rag(owner_id, record))
        except Exception:
            pass

    # Publish pending first, so a fast callback can never be overwritten by a
    # later stale save of the pre-job snapshot.
    changed = False
    for fid in file_ids:
        meta = lib.find_file(fid)
        chunks = lib.chunks_for(fid)
        if meta is None or not chunks:
            outcomes[fid] = False
            continue
        idx = dict(meta.get("rag_index") or {})
        idx["vector_revision"] = "pending" if embed is not None else "unavailable"
        idx["status"] = "bm25_ready"
        idx["updated_at"] = time.time()
        meta["rag_index"] = idx
        jobs.append((fid, file_scope(meta), chunks))
        changed = True
    if changed:
        save_library(lib)

    for fid, scope, chunks in jobs:
        if embed is None:
            outcomes[fid] = False
            publish(fid, False)
            continue
        scheduled = schedule_index(
            scope, chunks, embed, key=f"textbook:{owner_id}:{fid}",
            callback=lambda ok, file_id=fid: publish(file_id, bool(ok)),
        )
        outcomes[fid] = scheduled
    return outcomes

def summarize_textbook_rag(owner_id: str, textbook: dict[str, Any]) -> dict[str, Any]:
    """Project per-file persisted revisions into the textbook registry record."""
    lib = load_library(owner_id)
    file_ids = list(textbook.get("file_ids") or []) or ([textbook.get("file_id")] if textbook.get("file_id") else [])
    entries: list[dict[str, Any]] = []
    for fid in file_ids:
        meta = lib.find_file(str(fid)) or {}
        idx = dict(meta.get("rag_index") or {})
        entries.append({"file_id": str(fid), **idx})
    statuses = {str(e.get("status") or "") for e in entries}
    return {
        "version": RAG_INDEX_VERSION,
        "chunk_schema": (entries[0].get("chunk_schema") if entries
                         and len({e.get("chunk_schema") for e in entries}) == 1 else "mixed"),
        "textbook_id": textbook.get("id"),
        "files": entries,
        "content_hashes": {e["file_id"]: e.get("content_sha256", "") for e in entries},
        "chunk_count": sum(int(e.get("chunk_count") or 0) for e in entries),
        "status": ("ready" if statuses and statuses == {"ready"}
                   else "bm25_ready" if entries else "empty"),
        "updated_at": time.time(),
    }
