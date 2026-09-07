"""Model-isolated Chroma index for the optional RAG vector lane.

Every embedding model/dimension/chunk-schema/revision combination receives a
separate ``chunks_<fingerprint>`` collection.  Runtime databases are never
portable public assets: public vectors are imported from verified NPZ packs.
All async indexing/query I/O is dispatched to the dedicated RAG CPU worker;
failures are non-fatal and callers retain BM25 retrieval.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import settings
from .rag_index import RAG_INDEX_VERSION
from .retriever import Chunk
from .structured_chunker import active_chunk_schema

log = logging.getLogger(__name__)

_COLLECTION_PREFIX = "chunks_"
_GET_BATCH = 256
_UPSERT_BATCH = 128

_client: Any | None = None
_collections: dict[str, Any] = {}
_last_fingerprints_by_dim: dict[int, set[str]] = {}
_known_dimension_by_model: dict[str, int] = {}
_disabled = False
_warned = False


def _warn_once(msg: str, *args: Any) -> None:
    global _warned
    if not _warned:
        log.warning("RAG vector track disabled: " + msg, *args)
        _warned = True


def _disable(msg: str, *args: Any) -> None:
    global _disabled
    _disabled = True
    _warn_once(msg, *args)


def _get_client() -> Any | None:
    global _client
    if _disabled:
        return None
    if _client is not None:
        return _client
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        _client = chromadb.PersistentClient(
            path=settings.chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        return _client
    except Exception as exc:
        _disable("chroma init failed (%s)", exc)
        return None


def collection_fingerprint(embed_client: Any, dimension: int, *,
                           chunk_schema: str | None = None,
                           vector_revision: str = RAG_INDEX_VERSION) -> str:
    from .embedding import model_fingerprint
    return model_fingerprint(
        embed_client, dimension,
        chunk_schema=chunk_schema or active_chunk_schema(),
        vector_revision=vector_revision,
    )


def collection_name(fingerprint: str) -> str:
    return f"{_COLLECTION_PREFIX}{fingerprint}"


def _aliases_path() -> Path:
    return Path(settings.chroma_dir) / "active_collections.json"


def active_collection_name(fingerprint: str) -> str:
    try:
        data = json.loads(_aliases_path().read_text(encoding="utf-8"))
        value = str(data.get(fingerprint) or "") if isinstance(data, dict) else ""
        if value.startswith(_COLLECTION_PREFIX):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return collection_name(fingerprint)


def set_active_collection(fingerprint: str, physical_name: str) -> None:
    if not physical_name.startswith(_COLLECTION_PREFIX):
        raise ValueError("invalid Chroma collection alias")
    from .atomic import atomic_write_text
    path = _aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError, TypeError):
        data = {}
    data[fingerprint] = physical_name
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _get_collection_by_fingerprint(fingerprint: str, *, create: bool = True,
                                   physical_name: str | None = None,
                                   metadata: dict[str, Any] | None = None) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    try:
        name = physical_name or active_collection_name(fingerprint)
        if name in _collections:
            return _collections[name]
        if create:
            collection_meta = {"hnsw:space": "cosine", "model_fingerprint": fingerprint}
            collection_meta.update(metadata or {})
            col = client.get_or_create_collection(name=name, metadata=collection_meta)
        else:
            col = client.get_collection(name=name)
        _collections[name] = col
        return col
    except Exception as exc:
        if create:
            _disable("chroma collection init failed (%s)", exc)
        return None


def _reset() -> None:
    """Drop cached state (tests redirect chroma_dir between cases)."""
    global _client, _collections, _last_fingerprints_by_dim, _known_dimension_by_model, _disabled, _warned
    _client = None
    _collections = {}
    _last_fingerprints_by_dim = {}
    _known_dimension_by_model = {}
    _disabled = False
    _warned = False


def _content_sha(chunk: Chunk) -> str:
    import hashlib
    # Canonicalize from final packed text. Some legacy structured chunks carry
    # a pre-pack metadata hash, which must never suppress a changed-vector upsert.
    return hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()


def _metadata(scope: str, chunk: Chunk, *, embed_client: Any,
              dimension: int, fingerprint: str, chunk_schema: str) -> dict[str, Any]:
    meta = chunk.metadata or {}
    return {
        "scope": scope,
        "file_id": chunk.file_id,
        "chunk_id": chunk.chunk_id,
        "source": chunk.source,
        "index": int(chunk.index),
        "page": chunk.page if chunk.page is not None else -1,
        "embedding_model": str(getattr(embed_client, "model_identifier", None)
                               or getattr(embed_client, "model", None)
                               or embed_client.__class__.__name__),
        "embedding_dim": int(dimension),
        "model_fingerprint": fingerprint,
        "vector_revision": RAG_INDEX_VERSION,
        "content_sha256": _content_sha(chunk),
        "chunk_schema": chunk_schema,
    }


def _validate_vectors(vectors: list[list[float]], expected: int) -> int:
    if len(vectors) != expected or not vectors:
        raise ValueError("embedding vector count mismatch")
    dim = len(vectors[0])
    if dim <= 0 or any(len(v) != dim for v in vectors):
        raise ValueError("embedding dimensions are empty or inconsistent")
    return dim


def _existing_metadata(fingerprint: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    col = _get_collection_by_fingerprint(fingerprint)
    if col is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), _GET_BATCH):
        got = col.get(ids=ids[i:i + _GET_BATCH], include=["metadatas"])
        got_ids = got.get("ids") or []
        metas = got.get("metadatas") or []
        out.update({str(cid): dict(meta or {}) for cid, meta in zip(got_ids, metas)})
    return out


def _upsert(fingerprint: str, ids: list[str], vectors: list[list[float]],
            metadatas: list[dict[str, Any]]) -> None:
    col = _get_collection_by_fingerprint(fingerprint)
    if col is None:
        raise RuntimeError("vector collection unavailable")
    col.upsert(ids=ids, embeddings=vectors, metadatas=metadatas)


def _stale_ids(fingerprint: str, scope: str, live: set[str]) -> list[str]:
    col = _get_collection_by_fingerprint(fingerprint)
    if col is None:
        return []
    stale: list[str] = []
    offset = 0
    while True:
        got = col.get(where={"scope": scope}, include=[], limit=_GET_BATCH, offset=offset)
        page = got.get("ids") or []
        if not page:
            break
        stale.extend(str(cid) for cid in page if str(cid) not in live)
        if len(page) < _GET_BATCH:
            break
        offset += len(page)
    return stale


async def ensure_indexed(scope: str, chunks: list[Chunk], embed_client: Any) -> bool:
    """Upsert missing/changed chunks and prune stale vectors in exactly one scope."""
    try:
        eligible = [c for c in chunks if not (c.metadata or {}).get("garble_excluded")]
        if embed_client is None or not eligible:
            return False

        # One small probe determines dimension on the first use of a model. The
        # descriptor cache makes subsequent idempotent calls perform zero embeds.
        model_key = str(getattr(embed_client, "model_identifier", None)
                        or getattr(embed_client, "model", None)
                        or embed_client.__class__.__name__)
        dimension = (getattr(embed_client, "dimension", None)
                     or _known_dimension_by_model.get(model_key))
        probe_vectors: list[list[float]] = []
        if not dimension:
            probe_vectors = await embed_client.embed([
                __import__("app.core.retriever", fromlist=["retrievable_text"])
                .retrievable_text(eligible[0])
            ])
            dimension = _validate_vectors(probe_vectors, 1)
            _known_dimension_by_model[model_key] = dimension
        dimension = int(dimension)
        schemas = {str((c.metadata or {}).get("chunk_schema") or "legacy-v1")
                   for c in eligible}
        chunk_schema = next(iter(schemas)) if len(schemas) == 1 else active_chunk_schema()
        fingerprint = collection_fingerprint(
            embed_client, dimension, chunk_schema=chunk_schema)
        _last_fingerprints_by_dim.setdefault(dimension, set()).add(fingerprint)

        from .embedding import run_cpu
        ids = [c.chunk_id for c in eligible]
        existing = await run_cpu(_existing_metadata, fingerprint, ids)
        changed = [c for c in eligible if (
            c.chunk_id not in existing
            or existing[c.chunk_id].get("scope") != scope
            or existing[c.chunk_id].get("content_sha256") != _content_sha(c)
            or int(existing[c.chunk_id].get("embedding_dim") or -1) != dimension
        )]
        # Probe is only reusable when the first chunk really needs an upsert.
        probe_id = eligible[0].chunk_id
        from .retriever import retrievable_text
        for i in range(0, len(changed), _UPSERT_BATCH):
            batch = changed[i:i + _UPSERT_BATCH]
            texts = [retrievable_text(c) for c in batch]
            if probe_vectors and batch and batch[0].chunk_id == probe_id:
                rest = await embed_client.embed(texts[1:]) if len(texts) > 1 else []
                vectors = probe_vectors + rest
            else:
                vectors = await embed_client.embed(texts)
            got_dim = _validate_vectors(vectors, len(batch))
            if got_dim != dimension:
                raise ValueError("embedding dimension changed during indexing")
            metas = [_metadata(scope, c, embed_client=embed_client,
                               dimension=dimension, fingerprint=fingerprint,
                               chunk_schema=chunk_schema)
                     for c in batch]
            await run_cpu(_upsert, fingerprint, [c.chunk_id for c in batch], vectors, metas)

        live = set(ids)
        try:
            stale = await run_cpu(_stale_ids, fingerprint, scope, live)
            for i in range(0, len(stale), _GET_BATCH):
                await run_cpu(_delete_ids, fingerprint, stale[i:i + _GET_BATCH])
        except Exception:
            pass
        return True
    except Exception as exc:
        # Do not permanently disable local embeddings for a transient model or
        # data error. Chroma init/corruption itself marks the store disabled.
        _warn_once("ensure_indexed failed (%s)", exc)
        return False


def _delete_ids(fingerprint: str, ids: list[str]) -> None:
    col = _get_collection_by_fingerprint(fingerprint, create=False)
    if col is not None and ids:
        col.delete(ids=ids)


def _query_sync(query_vec: list[float], scopes: list[str], top_k: int,
                fingerprint: str) -> dict[str, float]:
    col = _get_collection_by_fingerprint(fingerprint, create=False)
    if col is None:
        return {}
    result = col.query(
        query_embeddings=[query_vec], n_results=max(1, top_k),
        where={"scope": {"$in": scopes}},
    )
    ids = (result.get("ids") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    return dict(zip(ids, distances))


async def query(query_vec: list[float], scopes: list[str], top_k: int,
                embed_client: Any | None = None,
                chunk_schemas: list[str] | None = None) -> dict[str, float]:
    """Search visible scopes across the relevant schema-isolated collections."""
    try:
        if not query_vec or not scopes:
            return {}
        dimension = len(query_vec)
        if embed_client is not None:
            schemas = chunk_schemas or [active_chunk_schema(), "legacy-v1"]
            fingerprints = [collection_fingerprint(
                embed_client, dimension, chunk_schema=schema)
                for schema in dict.fromkeys(schemas)]
        else:
            fingerprints = list(_last_fingerprints_by_dim.get(dimension, set()))
        if not fingerprints:
            return {}
        from .embedding import run_cpu
        merged: dict[str, float] = {}
        for fingerprint in fingerprints:
            hits = await run_cpu(_query_sync, query_vec, scopes, top_k, fingerprint)
            for chunk_id, distance in hits.items():
                if chunk_id not in merged or float(distance) < merged[chunk_id]:
                    merged[chunk_id] = float(distance)
        ordered = sorted(merged.items(), key=lambda item: item[1])[:max(1, top_k)]
        return dict(ordered)
    except Exception as exc:
        _warn_once("vector query failed (%s)", exc)
        return {}


def _all_collections() -> list[Any]:
    client = _get_client()
    if client is None:
        return []
    out: list[Any] = []
    try:
        for item in client.list_collections():
            name = item if isinstance(item, str) else getattr(item, "name", "")
            if str(name).startswith(_COLLECTION_PREFIX):
                try:
                    out.append(client.get_collection(name=str(name)))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def delete_file(file_id: str, *, scope: str | None = None) -> bool:
    """Remove a file across all model collections, optionally within one scope."""
    try:
        deleted = False
        for col in _all_collections():
            where: dict[str, Any]
            if scope:
                where = {"$and": [{"file_id": file_id}, {"scope": scope}]}
            else:
                where = {"file_id": file_id}
            col.delete(where=where)
            deleted = True
        return deleted
    except Exception as exc:
        _warn_once("vector delete_file failed (%s)", exc)
        return False


def delete_scope(scope: str) -> bool:
    """Remove one visibility scope across all model collections."""
    try:
        deleted = False
        for col in _all_collections():
            col.delete(where={"scope": scope})
            deleted = True
        return deleted
    except Exception as exc:
        _warn_once("vector delete_scope failed (%s)", exc)
        return False


def import_records(*, fingerprint: str, ids: list[str], vectors: list[list[float]],
                   metadatas: list[dict[str, Any]]) -> bool:
    """Synchronous validated upsert used only by the public artifact importer."""
    if not ids:
        return True
    dimension = _validate_vectors(vectors, len(ids))
    if len(metadatas) != len(ids):
        raise ValueError("public vector metadata count mismatch")
    if any(int(m.get("embedding_dim") or -1) != dimension for m in metadatas):
        raise ValueError("public vector metadata dimension mismatch")
    if any(str(m.get("model_fingerprint") or "") != fingerprint for m in metadatas):
        raise ValueError("public vector model fingerprint mismatch")
    col = _get_collection_by_fingerprint(fingerprint)
    if col is None:
        return False
    for i in range(0, len(ids), _UPSERT_BATCH):
        _upsert(fingerprint, ids[i:i + _UPSERT_BATCH], vectors[i:i + _UPSERT_BATCH],
                metadatas[i:i + _UPSERT_BATCH])
    _last_fingerprints_by_dim.setdefault(dimension, set()).add(fingerprint)
    return True


def collection_count(fingerprint: str, *, scope: str | None = None) -> int:
    col = _get_collection_by_fingerprint(fingerprint, create=False)
    if col is None:
        return 0
    if scope is None:
        return int(col.count())
    return len(col.get(where={"scope": scope}, include=[]).get("ids") or [])


def delete_collection(fingerprint: str) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        name = active_collection_name(fingerprint)
        client.delete_collection(name)
        _collections.pop(name, None)
        return True
    except Exception:
        return False
