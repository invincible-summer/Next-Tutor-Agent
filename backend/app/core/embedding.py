"""Embedding providers for the optional RAG vector lane.

Providers are explicit: ``off`` (default), ``local`` (offline, bring-your-own
embedding model), or ``openai`` (OpenAI-compatible Embeddings API). The
``local`` provider is a generic interface: Edu_Agent bundles, pins, or
defaults to no specific local model — the operator supplies the model files
and their inference runtime explicitly via EMBEDDING_MODEL /
EMBEDDING_MODEL_PATH. The public ``embed(texts)`` coroutine is stable across
providers.  Local model loading and encoding run on Edu_Agent's single-slot
CPU executor, so FastAPI's event loop never performs ML work and a
missing/unconfigured model only makes the caller fall back to BM25.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

import httpx
from openai import AsyncOpenAI
from openai import RateLimitError, APITimeoutError, APIConnectionError, APIStatusError

from .config import settings

log = logging.getLogger(__name__)

_RETRY_MAX = 3
_RETRY_BASE_DELAY = 1.0
_T = TypeVar("_T")

# One active local ML/Chroma operation process-wide. Chroma 0.5 native
# bindings are most reliable when each call gets a fresh short-lived thread;
# the async semaphore preserves the hard single-slot resource contract.
_CPU_SLOT: asyncio.Semaphore | None = None


def _cpu_slot() -> asyncio.Semaphore:
    global _CPU_SLOT
    if _CPU_SLOT is None:
        _CPU_SLOT = asyncio.Semaphore(1)
    return _CPU_SLOT


async def run_cpu(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run blocking local-ML/Chroma work off-loop, at process concurrency one."""
    async with _cpu_slot():
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def deliver(result: Any = None, error: BaseException | None = None) -> None:
            if future.done():
                return
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

        def invoke() -> None:
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                try:
                    loop.call_soon_threadsafe(deliver, None, exc)
                except RuntimeError:
                    pass
            else:
                try:
                    loop.call_soon_threadsafe(deliver, result, None)
                except RuntimeError:
                    pass

        threading.Thread(target=invoke, name="edu-rag-cpu", daemon=True).start()
        # Some WSL/event-loop combinations do not wake the selector promptly
        # for call_soon_threadsafe alone. A short cooperative poll keeps request
        # latency bounded while the event loop remains free for SSE traffic.
        while not future.done():
            await asyncio.sleep(0.01)
        return future.result()


def _safe_model_label(value: str) -> str:
    value = str(value or "unknown").strip()
    try:
        p = Path(value).expanduser()
        if p.exists():
            return p.name or str(p.resolve())
    except OSError:
        pass
    return value


def model_fingerprint(client: Any, dimension: int, *, chunk_schema: str,
                      vector_revision: str, normalized: bool | None = None) -> str:
    """Stable collection fingerprint; different models/dims/schemas never mix."""
    identifier = getattr(client, "model_identifier", None) or \
        getattr(client, "model", None) or client.__class__.__name__
    if normalized is None:
        normalized = bool(getattr(client, "normalize_embeddings", False))
    raw = "\n".join((
        _safe_model_label(str(identifier)), str(int(dimension)), str(chunk_schema),
        "normalized" if normalized else "not-normalized", str(vector_revision),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class OpenAIEmbeddingClient:
    """Batched, retried wrapper around an OpenAI-compatible Embeddings API."""

    normalize_embeddings = False

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 60.0):
        self.model = model or settings.embedding_model
        self.model_identifier = self.model
        effective_url = base_url or settings.embedding_base_url
        self.client = AsyncOpenAI(
            api_key=api_key or settings.embedding_api_key,
            base_url=effective_url,
            timeout=timeout,
            max_retries=0,
            http_client=httpx.AsyncClient(trust_env=False, timeout=timeout),
        )
        self._semaphore = asyncio.Semaphore(2)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        size = settings.embedding_batch_size
        for i in range(0, len(texts), size):
            vectors.extend(await self._embed_batch(texts[i:i + size]))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._semaphore:
                    resp = await self.client.embeddings.create(model=self.model, input=batch)
                items = sorted(resp.data, key=lambda d: d.index)
                return [list(d.embedding) for d in items]
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt >= _RETRY_MAX:
                    raise
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            except APIStatusError as exc:
                if exc.status_code == 429 and attempt < _RETRY_MAX:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                    continue
                raise


# Backwards-compatible public name used by older tests/importers.
EmbeddingClient = OpenAIEmbeddingClient


class LocalEmbeddingClient:
    """Lazy, offline-only provider for an operator-supplied embedding model.

    Model-agnostic: whatever SentenceTransformer-compatible source the
    operator configures loads here; no model is shipped, pinned, or defaulted
    by this repository.
    """

    normalize_embeddings = True

    def __init__(self, *, model: str | None = None, model_path: str | None = None,
                 cache_dir: str | None = None, device: str | None = None):
        configured_path = (model_path if model_path is not None
                           else settings.embedding_model_path).strip()
        configured_model = model or settings.embedding_model
        self.model_source = configured_path or configured_model
        self._explicit_model_path = bool(configured_path)
        # Fingerprints use the configured semantic model name, not an
        # absolute/cache path, so a verified pack remains portable across hosts.
        self.model_identifier = configured_model
        self.model = self.model_identifier
        self.cache_dir = (cache_dir if cache_dir is not None
                          else settings.embedding_cache_dir).strip() or None
        self.device = (device or settings.embedding_device or "cpu").strip().lower()
        # v1 is deliberately CPU-only; silently accepting cuda would violate
        # the deploy resource contract and can pull incompatible runtimes.
        if self.device != "cpu":
            raise ValueError("local embedding v1 supports EMBEDDING_DEVICE=cpu only")
        self._model: Any | None = None
        self._load_error: Exception | None = None
        self._load_lock: asyncio.Lock | None = None
        self.dimension: int | None = None

    def _load_sync(self) -> Any:
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            raise self._load_error
        try:
            # Enforce offline operation before importing transformers/huggingface.
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            threads = str(settings.embedding_max_threads)
            os.environ.setdefault("OMP_NUM_THREADS", threads)
            os.environ.setdefault("MKL_NUM_THREADS", threads)
            source = self.model_source
            if self._explicit_model_path and not Path(source).expanduser().is_dir():
                raise FileNotFoundError(f"EMBEDDING_MODEL_PATH does not exist: {source}")
            try:
                import torch
                torch.set_num_threads(settings.embedding_max_threads)
                try:
                    torch.set_num_interop_threads(1)
                except RuntimeError:
                    pass
            except ImportError:
                pass
            from sentence_transformers import SentenceTransformer
            kwargs: dict[str, Any] = {
                "device": "cpu", "cache_folder": self.cache_dir,
                "local_files_only": True,
            }
            try:
                loaded = SentenceTransformer(source, **kwargs)
            except TypeError:
                # Older sentence-transformers lacks local_files_only; the
                # process-wide offline flags above still prohibit downloads.
                kwargs.pop("local_files_only", None)
                loaded = SentenceTransformer(source, **kwargs)
            self._model = loaded
            get_dim = getattr(loaded, "get_embedding_dimension", None) or \
                getattr(loaded, "get_sentence_embedding_dimension")
            dim = get_dim()
            self.dimension = int(dim) if dim else None
            return loaded
        except Exception as exc:
            self._load_error = exc
            raise

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        async with self._load_lock:
            if self._model is None:
                await run_cpu(self._load_sync)
        return self._model

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load_sync()
        values = model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        if len(values) != len(texts):
            raise RuntimeError("local embedding returned an unexpected vector count")
        vectors = [[float(v) for v in row] for row in values]
        if vectors:
            dim = len(vectors[0])
            if not dim or any(len(row) != dim for row in vectors):
                raise RuntimeError("local embedding returned inconsistent dimensions")
            self.dimension = dim
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        await self._ensure_model()
        vectors: list[list[float]] = []
        size = settings.embedding_batch_size
        for i in range(0, len(texts), size):
            vectors.extend(await run_cpu(self._encode_sync, texts[i:i + size]))
        return vectors


_INSTANCE: Any | None = None
_INSTANCE_KEY: tuple[Any, ...] | None = None


def _provider_key() -> tuple[Any, ...]:
    return (settings.embedding_provider, settings.embedding_model,
            settings.embedding_model_path, settings.embedding_cache_dir,
            settings.embedding_device, settings.embedding_base_url,
            bool(settings.embedding_api_key))


def reset_embedding_client() -> None:
    """Drop the cached provider (tests/config reload); model memory is GC-managed."""
    global _INSTANCE, _INSTANCE_KEY
    _INSTANCE = None
    _INSTANCE_KEY = None


def get_embedding_client() -> Any | None:
    """Return the process-level configured provider, or None for BM25-only."""
    global _INSTANCE, _INSTANCE_KEY
    provider = settings.embedding_provider
    if provider == "off":
        return None
    key = _provider_key()
    if _INSTANCE is not None and _INSTANCE_KEY == key:
        return _INSTANCE
    try:
        if provider == "local":
            if not (settings.embedding_model or settings.embedding_model_path):
                log.warning(
                    "EMBEDDING_PROVIDER=local but neither EMBEDDING_MODEL nor "
                    "EMBEDDING_MODEL_PATH is set; this repository ships no "
                    "default local embedding model, so the vector track is disabled")
                return None
            client: Any = LocalEmbeddingClient()
        elif provider == "openai":
            if not settings.embedding_api_key:
                log.warning("EMBEDDING_PROVIDER=openai but EMBEDDING_API_KEY is empty; vector track disabled")
                return None
            client = OpenAIEmbeddingClient()
        else:
            return None
        _INSTANCE = client
        _INSTANCE_KEY = key
        return client
    except Exception as exc:
        log.warning("embedding client init failed; vector track disabled: %s", exc)
        _INSTANCE = None
        _INSTANCE_KEY = key
        return None
