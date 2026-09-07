"""Build and import verifiable public-textbook vector artifacts.

Only the fixed ``public`` Library namespace is accepted.  NPZ packs are
portable, versioned Git assets; Chroma remains deployment-local.  Import first
validates every text/chunk/shard and constructs a complete staging collection,
then atomically switches the active collection alias so a failed import cannot
replace a working index.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .embedding import LocalEmbeddingClient, get_embedding_client, model_fingerprint
from .library import file_scope, library_data_dir, load_library, save_library
from .rag_index import RAG_INDEX_VERSION
from .retriever import Chunk, retrievable_text
from .structured_chunker import active_chunk_schema
from .textbook import PUBLIC_STUDENT_ID

ARTIFACT_SCHEMA = "edu-public-vectors-v1"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "public_vector_artifacts"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3], text=True,
            stderr=subprocess.DEVNULL, timeout=5).strip()
    except Exception:
        return "unknown"


def _manifest_digest(manifest: dict[str, Any]) -> str:
    clean = dict(manifest)
    clean.pop("manifest_sha256", None)
    return _sha_bytes(json.dumps(clean, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"))


@dataclass
class PublicSnapshot:
    files: list[dict[str, Any]]
    chunks: list[Chunk]
    scopes: list[str]


def load_public_snapshot() -> PublicSnapshot:
    """Rebuild chunks from public text only and validate stable source facts."""
    lib = load_library(PUBLIC_STUDENT_ID)
    data_dir = library_data_dir(PUBLIC_STUDENT_ID)
    files: list[dict[str, Any]] = []
    chunks: list[Chunk] = []
    scopes: list[str] = []
    seen: set[str] = set()
    if lib.student_id != PUBLIC_STUDENT_ID:
        raise ValueError("public artifact source namespace is not public")
    for meta in sorted(lib.files, key=lambda item: str(item.get("id") or "")):
        file_id = str(meta.get("id") or "")
        if not file_id or Path(file_id).name != file_id:
            raise ValueError("public library contains an invalid file id")
        text_path = data_dir / f"{file_id}.txt"
        if not text_path.is_file():
            raise FileNotFoundError(f"missing public textbook text: {file_id}")
        raw = text_path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"public textbook text is not UTF-8: {file_id}") from exc
        text_sha = _sha_bytes(raw)
        expected = str((meta.get("rag_index") or {}).get("content_sha256") or "")
        if expected and expected != text_sha:
            raise ValueError(f"public textbook text hash mismatch: {file_id}")
        file_chunks = lib.chunks_for(file_id)
        for chunk in file_chunks:
            if not chunk.chunk_id or chunk.chunk_id in seen:
                raise ValueError("public chunks contain duplicate/empty ids")
            seen.add(chunk.chunk_id)
        scope = file_scope(meta)
        files.append({
            "file_id": file_id,
            "filename": str(meta.get("filename") or ""),
            "scope": scope,
            "content_sha256": text_sha,
            "chunk_count": len(file_chunks),
            "chunk_ids_sha256": _sha_bytes(
                "\n".join(c.chunk_id for c in file_chunks).encode("utf-8")),
        })
        chunks.extend(file_chunks)
        scopes.extend([scope] * len(file_chunks))
    if not chunks:
        raise ValueError("public namespace has no chunks")
    return PublicSnapshot(files=files, chunks=chunks, scopes=scopes)


def _arrays_for(chunks: list[Chunk], scopes: list[str], vectors: Any) -> dict[str, Any]:
    import numpy as np
    return {
        "chunk_ids": np.asarray([c.chunk_id for c in chunks], dtype=np.str_),
        "scopes": np.asarray(scopes, dtype=np.str_),
        "file_ids": np.asarray([c.file_id for c in chunks], dtype=np.str_),
        "sources": np.asarray([c.source for c in chunks], dtype=np.str_),
        "indexes": np.asarray([int(c.index) for c in chunks], dtype=np.int32),
        "pages": np.asarray([c.page if c.page is not None else -1 for c in chunks], dtype=np.int32),
        "content_sha256": np.asarray([
            _sha_bytes(c.text.encode("utf-8")) for c in chunks
        ], dtype=np.str_),
        "vectors": np.asarray(vectors, dtype=np.float32),
    }


def _validate_matrix(matrix: Any, expected_rows: int, expected_dim: int | None = None) -> int:
    import numpy as np
    if matrix.ndim != 2 or matrix.shape[0] != expected_rows or matrix.shape[1] <= 0:
        raise ValueError("public vector matrix shape mismatch")
    if expected_dim is not None and int(matrix.shape[1]) != int(expected_dim):
        raise ValueError("public vector dimension mismatch")
    if not np.isfinite(matrix).all():
        raise ValueError("public vector matrix contains non-finite values")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms < 0.90) or np.any(norms > 1.10):
        raise ValueError("public vectors are not normalized")
    return int(matrix.shape[1])


def _sample_self_check(matrix: Any, *, samples: int = 8) -> None:
    import numpy as np
    if not len(matrix):
        raise ValueError("cannot query-check an empty vector pack")
    rng = random.Random(0)
    for index in rng.sample(range(len(matrix)), min(samples, len(matrix))):
        scores = matrix @ matrix[index]
        if not math.isfinite(float(scores[index])) or int(np.argmax(scores)) != index:
            # Exact duplicates can tie; accept when the self score is within
            # numerical tolerance of the maximum.
            if float(scores[index]) + 1e-5 < float(np.max(scores)):
                raise ValueError("public vector random query validation failed")


async def build_public_vector_pack(output_dir: Path = DEFAULT_OUTPUT_DIR, *,
                                   shard_size: int = 8192,
                                   embed_client: Any | None = None) -> dict[str, Any]:
    """Build to a temporary sibling, fully validate, then replace output."""
    if shard_size <= 0:
        raise ValueError("shard_size must be positive")
    snapshot = load_public_snapshot()
    client = embed_client or get_embedding_client()
    if client is None:
        raise RuntimeError("public vector build requires EMBEDDING_PROVIDER=local")
    if not isinstance(client, LocalEmbeddingClient) and embed_client is None:
        raise RuntimeError("public vector build refuses a non-local provider")

    output_dir = Path(output_dir)
    staging = output_dir.with_name(f".{output_dir.name}.staging-{os.getpid()}")
    backup = output_dir.with_name(f".{output_dir.name}.backup-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=False)
    shard_records: list[dict[str, Any]] = []
    dimension: int | None = None
    all_vectors: list[Any] = []
    try:
        for shard_index, start in enumerate(range(0, len(snapshot.chunks), shard_size)):
            end = min(len(snapshot.chunks), start + shard_size)
            chunk_batch = snapshot.chunks[start:end]
            vector_rows: list[list[float]] = []
            batch_size = settings.embedding_batch_size
            for offset in range(0, len(chunk_batch), batch_size):
                texts = [retrievable_text(c) for c in chunk_batch[offset:offset + batch_size]]
                vector_rows.extend(await client.embed(texts))
            arrays = _arrays_for(chunk_batch, snapshot.scopes[start:end], vector_rows)
            got_dim = _validate_matrix(arrays["vectors"], len(chunk_batch), dimension)
            dimension = got_dim if dimension is None else dimension
            all_vectors.append(arrays["vectors"])
            shard_name = f"shard-{shard_index:03d}.npz"
            shard_path = staging / shard_name
            import numpy as np
            np.savez_compressed(shard_path, **arrays)
            shard_records.append({
                "file": shard_name, "sha256": _sha_file(shard_path),
                "count": len(chunk_batch), "start": start, "end": end,
            })
        import numpy as np
        matrix = np.concatenate(all_vectors, axis=0)
        _validate_matrix(matrix, len(snapshot.chunks), dimension)
        _sample_self_check(matrix)
        schema = active_chunk_schema()
        fingerprint = model_fingerprint(
            client, int(dimension), chunk_schema=schema,
            vector_revision=RAG_INDEX_VERSION)
        manifest: dict[str, Any] = {
            "artifact_schema": ARTIFACT_SCHEMA,
            "namespace": PUBLIC_STUDENT_ID,
            "rag_version": RAG_INDEX_VERSION,
            "chunk_schema": schema,
            "embedding_model": str(getattr(client, "model_identifier", None)
                                   or getattr(client, "model", "")),
            "model_fingerprint": fingerprint,
            "embedding_dim": int(dimension),
            "normalized": True,
            "dtype": "float32",
            "chunk_count": len(snapshot.chunks),
            "files": snapshot.files,
            "shards": shard_records,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "build_commit": _git_commit(),
        }
        manifest["manifest_sha256"] = _manifest_digest(manifest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verified, arrays = verify_public_vector_pack(staging, check_sources=True)
        client_fingerprint = model_fingerprint(
            client, int(verified["embedding_dim"]),
            chunk_schema=str(verified["chunk_schema"]),
            vector_revision=str(verified["rag_version"]))
        if client_fingerprint != verified["model_fingerprint"]:
            raise ValueError("built artifact fingerprint does not match embedding provider")
        # Query the provider itself against a deterministic sample, not merely
        # stored-vector self similarity, to detect model/serialization mismatch.
        sample_indexes = random.Random(1).sample(
            range(len(snapshot.chunks)), min(4, len(snapshot.chunks)))
        sample_queries = await client.embed([
            retrievable_text(snapshot.chunks[index]) for index in sample_indexes])
        import numpy as np
        sample_matrix = np.asarray(sample_queries, dtype=np.float32)
        _validate_matrix(sample_matrix, len(sample_indexes), int(verified["embedding_dim"]))
        for row, expected_index in zip(sample_matrix, sample_indexes):
            scores = arrays["vectors"] @ row
            if float(scores[expected_index]) + 1e-5 < float(np.max(scores)):
                raise ValueError("public vector provider query validation failed")
        shutil.rmtree(backup, ignore_errors=True)
        if output_dir.exists():
            output_dir.rename(backup)
        staging.rename(output_dir)
        shutil.rmtree(backup, ignore_errors=True)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not output_dir.exists():
            backup.rename(output_dir)
        raise


def _load_manifest(pack_dir: Path) -> dict[str, Any]:
    path = Path(pack_dir) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("artifact_schema") != ARTIFACT_SCHEMA:
        raise ValueError("unsupported public vector artifact schema")
    if manifest.get("namespace") != PUBLIC_STUDENT_ID:
        raise ValueError("vector artifact is not the public namespace")
    if manifest.get("manifest_sha256") != _manifest_digest(manifest):
        raise ValueError("public vector manifest checksum mismatch")
    return manifest


def _load_pack_arrays(pack_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    names = ("chunk_ids", "scopes", "file_ids", "sources", "indexes", "pages",
             "content_sha256", "vectors")
    combined: dict[str, list[Any]] = {name: [] for name in names}
    total = 0
    for shard in manifest.get("shards") or []:
        shard_name = str(shard.get("file") or "")
        if not shard_name or Path(shard_name).name != shard_name:
            raise ValueError("invalid public vector shard name")
        path = Path(pack_dir) / shard_name
        if not path.is_file() or _sha_file(path) != shard.get("sha256"):
            raise ValueError(f"public vector shard missing/corrupt: {shard_name}")
        with np.load(path, allow_pickle=False) as data:
            if any(name not in data for name in names):
                raise ValueError(f"public vector shard fields missing: {shard_name}")
            count = int(len(data["chunk_ids"]))
            if count != int(shard.get("count") or -1):
                raise ValueError(f"public vector shard count mismatch: {shard_name}")
            for name in names:
                if len(data[name]) != count:
                    raise ValueError(f"public vector shard column mismatch: {shard_name}")
                combined[name].append(data[name].copy())
            total += count
    if total != int(manifest.get("chunk_count") or -1):
        raise ValueError("public vector manifest total count mismatch")
    return {name: np.concatenate(values, axis=0) for name, values in combined.items()}


def verify_public_vector_pack(pack_dir: Path = DEFAULT_OUTPUT_DIR, *,
                              check_sources: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_manifest(Path(pack_dir))
    arrays = _load_pack_arrays(Path(pack_dir), manifest)
    _validate_matrix(arrays["vectors"], int(manifest["chunk_count"]),
                     int(manifest["embedding_dim"]))
    ids = [str(value) for value in arrays["chunk_ids"].tolist()]
    if len(ids) != len(set(ids)):
        raise ValueError("public vector artifact contains duplicate chunk ids")
    if any(str(scope) == "" for scope in arrays["scopes"].tolist()):
        raise ValueError("public vector artifact contains an empty scope")
    if check_sources:
        snapshot = load_public_snapshot()
        source_ids = [c.chunk_id for c in snapshot.chunks]
        source_hashes = [_sha_bytes(c.text.encode("utf-8"))
                         for c in snapshot.chunks]
        if ids != source_ids or [str(x) for x in arrays["content_sha256"].tolist()] != source_hashes:
            raise ValueError("public vector artifact chunks do not match current sources")
        expected_files = {item["file_id"]: item for item in snapshot.files}
        manifest_files = {str(item.get("file_id")): item for item in manifest.get("files") or []}
        if manifest_files != expected_files:
            raise ValueError("public vector artifact file hashes/counts do not match sources")
    _sample_self_check(arrays["vectors"])
    return manifest, arrays


def _metadatas(manifest: dict[str, Any], arrays: dict[str, Any]) -> list[dict[str, Any]]:
    fingerprint = str(manifest["model_fingerprint"])
    model = str(manifest["embedding_model"])
    dimension = int(manifest["embedding_dim"])
    revision = str(manifest["rag_version"])
    schema = str(manifest["chunk_schema"])
    rows: list[dict[str, Any]] = []
    for i in range(int(manifest["chunk_count"])):
        rows.append({
            "scope": str(arrays["scopes"][i]), "file_id": str(arrays["file_ids"][i]),
            "chunk_id": str(arrays["chunk_ids"][i]), "source": str(arrays["sources"][i]),
            "index": int(arrays["indexes"][i]), "page": int(arrays["pages"][i]),
            "embedding_model": model, "embedding_dim": dimension,
            "model_fingerprint": fingerprint, "vector_revision": revision,
            "content_sha256": str(arrays["content_sha256"][i]), "chunk_schema": schema,
        })
    return rows


def import_public_vector_pack(pack_dir: Path = DEFAULT_OUTPUT_DIR, *,
                              embed_client: Any | None = None) -> dict[str, Any]:
    """Validate, stage with private vectors preserved, verify, atomically switch."""
    manifest, arrays = verify_public_vector_pack(pack_dir, check_sources=True)
    client = embed_client or get_embedding_client()
    if client is None:
        raise RuntimeError("public vector import requires the configured embedding provider")
    expected_fingerprint = model_fingerprint(
        client, int(manifest["embedding_dim"]),
        chunk_schema=str(manifest["chunk_schema"]),
        vector_revision=str(manifest["rag_version"]))
    if expected_fingerprint != manifest["model_fingerprint"]:
        raise ValueError("configured embedding model fingerprint does not match artifact")

    from . import vector_store
    fingerprint = str(manifest["model_fingerprint"])
    chroma = vector_store._get_client()
    if chroma is None:
        raise RuntimeError("Chroma is unavailable")
    old_name = vector_store.active_collection_name(fingerprint)
    try:
        old = chroma.get_collection(old_name)
        if str((old.metadata or {}).get("manifest_sha256") or "") == manifest["manifest_sha256"]:
            return {"status": "skipped", "manifest_sha256": manifest["manifest_sha256"],
                    "collection": old_name, "chunk_count": manifest["chunk_count"]}
    except Exception:
        old = None

    stage_name = f"{vector_store.collection_name(fingerprint)}_stage_{manifest['manifest_sha256'][:12]}"
    try:
        chroma.delete_collection(stage_name)
    except Exception:
        pass
    stage = chroma.create_collection(
        stage_name,
        metadata={"hnsw:space": "cosine", "model_fingerprint": fingerprint,
                  "manifest_sha256": manifest["manifest_sha256"],
                  "namespace": PUBLIC_STUDENT_ID},
    )
    public_file_ids = {str(item["file_id"]) for item in manifest["files"]}
    public_scopes = {str(item["scope"]) for item in manifest["files"]}
    try:
        # Preserve every non-public runtime vector before the alias switch.
        if old is not None:
            offset = 0
            while True:
                got = old.get(limit=512, offset=offset,
                              include=["embeddings", "metadatas"])
                got_ids = got.get("ids") or []
                if not got_ids:
                    break
                embeddings = got.get("embeddings")
                metas = got.get("metadatas") or []
                keep = [i for i, meta in enumerate(metas)
                        if not (str((meta or {}).get("file_id") or "") in public_file_ids
                                and str((meta or {}).get("scope") or "") in public_scopes)]
                if keep:
                    stage.upsert(
                        ids=[got_ids[i] for i in keep],
                        embeddings=[embeddings[i] for i in keep],
                        metadatas=[metas[i] for i in keep],
                    )
                if len(got_ids) < 512:
                    break
                offset += len(got_ids)
        ids = [str(value) for value in arrays["chunk_ids"].tolist()]
        vectors = arrays["vectors"].tolist()
        metas = _metadatas(manifest, arrays)
        for start in range(0, len(ids), 256):
            stage.upsert(ids=ids[start:start + 256], embeddings=vectors[start:start + 256],
                         metadatas=metas[start:start + 256])
        public_count = len(stage.get(
            where={"scope": {"$in": sorted(public_scopes)}}, include=[]).get("ids") or [])
        if public_count != int(manifest["chunk_count"]):
            raise RuntimeError("staged public vector count verification failed")
        probe = stage.query(query_embeddings=[vectors[0]], n_results=3,
                            where={"scope": {"$in": sorted(public_scopes)}})
        if ids[0] not in ((probe.get("ids") or [[]])[0]):
            raise RuntimeError("staged public vector query verification failed")
        vector_store.set_active_collection(fingerprint, stage_name)
        vector_store._collections.pop(old_name, None)
        if old is not None and old_name != stage_name:
            try:
                chroma.delete_collection(old_name)
            except Exception:
                pass
    except Exception:
        try:
            chroma.delete_collection(stage_name)
        except Exception:
            pass
        raise

    # Publish file-level revisions only after the new collection is queryable.
    lib = load_library(PUBLIC_STUDENT_ID)
    for meta in lib.files:
        if str(meta.get("id") or "") not in public_file_ids:
            continue
        idx = dict(meta.get("rag_index") or {})
        idx["vector_revision"] = str(manifest["manifest_sha256"])
        idx["embedding_model"] = str(manifest["embedding_model"])
        idx["embedding_dim"] = int(manifest["embedding_dim"])
        idx["model_fingerprint"] = fingerprint
        idx["status"] = "ready"
        idx["updated_at"] = time.time()
        meta["rag_index"] = idx
    save_library(lib)
    return {"status": "imported", "manifest_sha256": manifest["manifest_sha256"],
            "collection": stage_name, "chunk_count": manifest["chunk_count"]}
