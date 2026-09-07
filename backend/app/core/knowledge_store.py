"""Session-scoped knowledge store: holds parsed chunks + a BM25 index.

Files are uploaded, parsed to text, chunked, and indexed. The knowledge_search
tool retrieves top-k chunks via BM25. Full text is also persisted to disk so
it survives server restarts; the in-memory index is rebuilt on demand.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import settings
from .file_parser import SUPPORTED_EXTS
from .retriever import BM25Index, Chunk, chunk_text


class KnowledgeStore:
    """Holds chunks and a BM25 index for one session's uploaded materials."""

    # Small stores bypass BM25 scoring entirely: BM25 needs lexical overlap,
    # so a tiny file (a one-page note) scores 0 for any paraphrased query and
    # the agent wrongly concludes the material has nothing relevant (then
    # hallucinates the file's content). A handful of chunks is cheap to
    # return in full, so small stores stay fully visible to the LLM.
    SMALL_STORE_MAX_CHUNKS = 8

    def __init__(self, upload_dir: Path | None = None) -> None:
        self.upload_dir = upload_dir or (Path(settings.trace_dir).parent / "uploads")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: list[Chunk] = []
        self.files: list[dict[str, Any]] = []   # metadata: {id, filename, char_count, chunk_count}
        self._index: BM25Index | None = None

    def add_file(self, file_id: str, filename: str, text: str,
                 raw: bytes | None = None, orig_ext: str = "",
                 metadata: dict[str, Any] | None = None,
                 *, structured: bool = False) -> dict[str, Any]:
        """Chunk text and add to the index. Persists raw text to disk.

        When ``raw`` + ``orig_ext`` are given, the original binary is kept
        alongside the extracted text (<file_id>.orig<orig_ext>) so the file
        can be re-downloaded byte-identically; the metadata records
        ``orig_ext`` ("" = no original, download falls back to the extracted
        text). ``structured=True`` forces the V2 structure-aware chunker
        (page boundaries / heading hard boundaries / protected figure-table-
        formula blocks) and stamps ``chunk_schema`` so reload keeps V2 —
        教材类引用绝不允许退回 V1 定长暴力分块。"""
        (self.upload_dir / f"{file_id}.txt").write_text(text, encoding="utf-8")
        has_orig = bool(raw) and bool(orig_ext)
        if has_orig:
            # ".orig" infix: never collides with the extracted-text .txt.
            (self.upload_dir / f"{file_id}.orig{orig_ext}").write_bytes(raw)
        if structured and text.strip():
            from .structured_chunker import CHUNK_SCHEMA_VERSION, chunk_text_v2
            new_chunks = chunk_text_v2(text, source=filename, file_id=file_id)
            schema = CHUNK_SCHEMA_VERSION
        else:
            new_chunks = chunk_text(text, source=filename, file_id=file_id)
            schema = ""
        self.chunks.extend(new_chunks)
        meta = {"id": file_id, "filename": filename,
                "char_count": len(text), "chunk_count": len(new_chunks),
                "orig_ext": orig_ext if has_orig else "",
                "chunk_schema": schema}
        if metadata:
            meta.update({str(k): v for k, v in metadata.items()
                         if str(k) not in {"id", "filename", "char_count",
                                          "chunk_count", "orig_ext",
                                          "chunk_schema"}})
        self.files.append(meta)
        self._index = None  # invalidate; rebuilt lazily on next search
        return meta

    def has_knowledge(self) -> bool:
        return len(self.chunks) > 0

    def remove_file(self, file_id: str) -> bool:
        """Remove a file: its metadata, chunks, and persisted txt on disk.

        Chunks carry the owning file_id (chunk_text tags them at add_file /
        rebuild time), so removal filters on file_id directly — unlike the
        old filename match, two files sharing a filename no longer interfere.
        Legacy chunks without a file_id (in-memory only, from before this
        change) fall back to the filename match.
        Returns True if the file was found and removed.
        """
        meta = next((f for f in self.files if f.get("id") == file_id), None)
        if meta is None:
            return False
        filename = meta.get("filename", "")
        self.files = [f for f in self.files if f.get("id") != file_id]

        def _belongs(c: Chunk) -> bool:
            return c.file_id == file_id if c.file_id else c.source == filename

        self.chunks = [c for c in self.chunks if not _belongs(c)]
        fp = self.upload_dir / f"{file_id}.txt"
        if fp.exists():
            fp.unlink()
        orig_ext = meta.get("orig_ext") or ""
        if orig_ext:
            op = self.upload_dir / f"{file_id}.orig{orig_ext}"
            if op.exists():
                op.unlink()
        self._index = None  # invalidate; rebuilt lazily on next search
        return True

    def _ensure_index(self) -> BM25Index:
        if self._index is None:
            self._index = BM25Index(self.chunks)
        return self._index

    def search(self, query: str, top_k: int = 4,
               file_ids: set[str] | None = None) -> list[dict[str, Any]]:
        if not self.chunks:
            return []
        pool = [c for c in self.chunks
                if file_ids is None or c.file_id in file_ids]
        if not pool:
            return []
        if len(pool) <= max(top_k, self.SMALL_STORE_MAX_CHUNKS):
            return [{"source": c.source, "filename": c.source,
                     "file_id": c.file_id, "chunk_id": c.chunk_id,
                     "index": c.index, "text": c.text, "score": 1.0,
                     "page": c.page,
                     "printed_page": c.metadata.get("printed_page"),
                     "noise_flags": list(c.metadata.get("noise_flags", [])),
                     "block_types": list(c.metadata.get("block_types", [])),
                     "section_path": list(c.metadata.get("section_path", [])),
                     "lesson": c.metadata.get("lesson"),
                     "is_lesson": bool(c.metadata.get("is_lesson"))} for c in pool]
        index = self._ensure_index() if file_ids is None else BM25Index(pool)
        results = index.search(query, top_k=top_k)
        return [{"source": c.source, "filename": c.source,
                 "file_id": c.file_id, "chunk_id": c.chunk_id,
                 "index": c.index, "text": c.text, "score": round(s, 3),
                 "page": c.page,
                 "printed_page": c.metadata.get("printed_page"),
                 "noise_flags": list(c.metadata.get("noise_flags", [])),
                 "block_types": list(c.metadata.get("block_types", [])),
                 "section_path": list(c.metadata.get("section_path", [])),
                 "lesson": c.metadata.get("lesson"),
                 "is_lesson": bool(c.metadata.get("is_lesson"))} for c, s in results]

    def file_list(self) -> list[dict[str, Any]]:
        return list(self.files)

    def to_dict(self) -> dict[str, Any]:
        return {"files": self.file_list(), "total_chunks": len(self.chunks)}
