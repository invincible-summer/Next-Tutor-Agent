"""Local embedding interface, model-isolated Chroma, and vector pack regressions.

The local provider is model-agnostic: these tests mock the inference runtime,
so no real local embedding model is required or downloaded.
"""
from __future__ import annotations
import asyncio
import hashlib
import sys
import types
from unittest import mock
import numpy as np

from app.core.config import settings
from app.core import vector_store
from app.core.embedding import (LocalEmbeddingClient, get_embedding_client,
                                model_fingerprint, reset_embedding_client)
from app.core.library import Library, load_library, save_library
from app.core.public_vector_artifact import (build_public_vector_pack,
                                             import_public_vector_pack,
                                             verify_public_vector_pack)
from app.core.retriever import chunk_text
from app.core.textbook import PUBLIC_STUDENT_ID
from tests.storage_sandbox import StorageSandboxTestCase


class NormalizedFakeEmbed:
    normalize_embeddings = True

    def __init__(self, model="fake-local-embed", dimension=4):
        self.model = self.model_identifier = model
        self.dimension = dimension
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            values = np.asarray([digest[i] + 1 for i in range(self.dimension)], dtype=np.float32)
            values /= np.linalg.norm(values)
            rows.append(values.tolist())
        return rows


class TestEmbeddingProviders(StorageSandboxTestCase):
    def tearDown(self):
        reset_embedding_client()
        super().tearDown()

    def test_provider_selection(self):
        with mock.patch.object(settings, "embedding_provider", "off"):
            reset_embedding_client(); self.assertIsNone(get_embedding_client())
        # No default local model ships with the repository: the local lane
        # stays disabled until an operator names their own model explicitly.
        with mock.patch.object(settings, "embedding_provider", "local"):
            with mock.patch.object(settings, "embedding_model", ""):
                with mock.patch.object(settings, "embedding_model_path", ""):
                    reset_embedding_client(); self.assertIsNone(get_embedding_client())
        with mock.patch.object(settings, "embedding_provider", "local"):
            with mock.patch.object(settings, "embedding_model", "operator-supplied-model"):
                reset_embedding_client(); self.assertIsInstance(get_embedding_client(), LocalEmbeddingClient)
        with mock.patch.object(settings, "embedding_provider", "openai"), \
             mock.patch.object(settings, "embedding_api_key", ""):
            reset_embedding_client(); self.assertIsNone(get_embedding_client())

    def test_local_path_priority_offline_and_normalized(self):
        model_dir = self.root / "shared-cache" / "local-model"
        model_dir.mkdir(parents=True)
        captured = {}

        class FakeSentenceTransformer:
            def __init__(self, source, **kwargs):
                captured["source"], captured["kwargs"] = source, kwargs
            def get_sentence_embedding_dimension(self): return 3
            def encode(self, texts, **kwargs):
                captured["encode"] = kwargs
                return np.asarray([[1., 0., 0.] for _ in texts], dtype=np.float32)

        module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
        with mock.patch.dict(sys.modules, {"sentence_transformers": module}), \
             mock.patch.object(settings, "embedding_model_path", str(model_dir)), \
             mock.patch.object(settings, "embedding_model", "remote-name"):
            vectors = asyncio.run(LocalEmbeddingClient().embed(["甲", "乙"]))
        self.assertEqual(captured["source"], str(model_dir))
        self.assertTrue(captured["kwargs"]["local_files_only"])
        self.assertTrue(captured["encode"]["normalize_embeddings"])
        self.assertEqual(vectors[0], [1., 0., 0.])

    def test_missing_path_fails_offline(self):
        with mock.patch.object(settings, "embedding_model_path", str(self.root / "missing")):
            with self.assertRaises(FileNotFoundError):
                asyncio.run(LocalEmbeddingClient().embed(["x"]))


class TestModelIsolatedVectorStore(StorageSandboxTestCase):
    def test_models_dimensions_are_isolated(self):
        chunks = chunk_text("浮力与液体压强。" * 20, "physics.txt", "f1")
        first, second = NormalizedFakeEmbed("model-a", 4), NormalizedFakeEmbed("model-b", 6)
        self.assertTrue(asyncio.run(vector_store.ensure_indexed("file:f1", chunks, first)))
        self.assertTrue(asyncio.run(vector_store.ensure_indexed("file:f1", chunks, second)))
        fp1, fp2 = vector_store.collection_fingerprint(first, 4, chunk_schema="legacy-v1"), vector_store.collection_fingerprint(second, 6, chunk_schema="legacy-v1")
        self.assertNotEqual(fp1, fp2)
        names = {getattr(c, "name", str(c)) for c in vector_store._get_client().list_collections()}
        self.assertIn(vector_store.collection_name(fp1), names)
        self.assertIn(vector_store.collection_name(fp2), names)

    def test_content_change_reembeds_same_id(self):
        embed = NormalizedFakeEmbed()
        first = chunk_text("旧内容。" * 20, "a.txt", "same")
        second = chunk_text("新内容。" * 20, "a.txt", "same")
        self.assertEqual(first[0].chunk_id, second[0].chunk_id)
        asyncio.run(vector_store.ensure_indexed("file:same", first, embed)); calls = embed.calls
        asyncio.run(vector_store.ensure_indexed("file:same", second, embed))
        self.assertGreater(embed.calls, calls)


class TestPublicVectorArtifact(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        lib = Library(PUBLIC_STUDENT_ID)
        for file_id, filename, text in (
            ("pub001", "物理.txt", "浮力是液体对物体向上的作用力。\n" * 30),
            ("pub002", "数学.txt", "勾股定理描述直角三角形三边关系。\n" * 30),
        ):
            meta = lib.add_file("", filename, text, file_id=file_id)
            meta.update({"kind": "textbook", "chunk_schema": "structured-v2.2"})
            meta["rag_index"] = {"content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                 "status": "bm25_ready", "vector_revision": "pending"}
        save_library(lib)
        self.pack = self.root / "public-pack"
        self.embed = NormalizedFakeEmbed()

    def _build(self):
        return asyncio.run(build_public_vector_pack(self.pack, shard_size=1,
                                                     embed_client=self.embed))

    def test_build_and_verify(self):
        manifest = self._build()
        verified, arrays = verify_public_vector_pack(self.pack, check_sources=True)
        self.assertEqual(manifest["manifest_sha256"], verified["manifest_sha256"])
        self.assertEqual(manifest["chunk_count"], len(arrays["chunk_ids"]))
        self.assertGreater(len(manifest["shards"]), 1)
        self.assertEqual(set(arrays["file_ids"].tolist()), {"pub001", "pub002"})

    def test_missing_shard_is_rejected(self):
        self._build()
        old_manifest = (self.pack / "manifest.json").read_bytes()
        next(self.pack.glob("shard-*.npz")).unlink()
        with self.assertRaises(ValueError):
            verify_public_vector_pack(self.pack, check_sources=True)
        self.assertEqual((self.pack / "manifest.json").read_bytes(), old_manifest)

    def test_import_idempotent_preserves_private(self):
        manifest = self._build()
        private = chunk_text("private secret algebra" * 20, "private.txt", "priv1")
        for chunk in private:
            chunk.metadata["chunk_schema"] = "structured-v2.2"
        asyncio.run(vector_store.ensure_indexed("file:priv1", private, self.embed))
        self.assertEqual(import_public_vector_pack(self.pack, embed_client=self.embed)["status"], "imported")
        self.assertEqual(import_public_vector_pack(self.pack, embed_client=self.embed)["status"], "skipped")
        self.assertTrue(all((f.get("rag_index") or {}).get("status") == "ready"
                            for f in load_library(PUBLIC_STUDENT_ID).files))
        query = asyncio.run(self.embed.embed(["private secret algebra"]))[0]
        self.assertTrue(asyncio.run(vector_store.query(query, ["file:priv1"], 5,
                                                       embed_client=self.embed)))
        self.assertEqual(manifest["model_fingerprint"], model_fingerprint(
            self.embed, 4, chunk_schema=manifest["chunk_schema"],
            vector_revision=manifest["rag_version"]))

    def test_source_or_model_mismatch_rejected(self):
        self._build()
        data = self.root / "chat_history/library/data/public/pub001.txt"
        original = data.read_text(encoding="utf-8")
        data.write_text("tampered", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_public_vector_pack(self.pack, embed_client=self.embed)
        data.write_text(original, encoding="utf-8")
        with self.assertRaises(ValueError):
            import_public_vector_pack(self.pack,
                                      embed_client=NormalizedFakeEmbed("different", 4))

class TestTextbookVectorLifecycle(StorageSandboxTestCase):
    def _seed(self):
        from app.core import textbook as tb_store
        lib = Library("student-vectors")
        text = "导数表示函数的瞬时变化率。\n" * 40
        meta = lib.add_file("", "calculus.txt", text, file_id="tbfile1")
        meta.update({"kind": "textbook", "chunk_schema": "structured-v2.2"})
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        meta["rag_index"] = {"version": "rag-v2", "content_sha256": content_hash,
                             "bm25_revision": f"rag-v2:{content_hash[:16]}",
                             "status": "bm25_ready", "vector_revision": "pending"}
        save_library(lib)
        return tb_store.create_group("student-vectors", file_ids=["tbfile1"],
                                     title="微积分", subject="数学")

    def test_bm25_ready_transitions_to_ready_in_background(self):
        from app.core import embedding as embedding_mod
        from app.core import vector_jobs
        from app.core.rag_index import refresh_textbook_vectors
        textbook = self._seed()
        embed = NormalizedFakeEmbed()

        async def run():
            with mock.patch.object(embedding_mod, "get_embedding_client", return_value=embed):
                outcomes = await refresh_textbook_vectors("student-vectors", textbook)
                self.assertTrue(outcomes["tbfile1"])
                self.assertEqual(load_library("student-vectors").find_file(
                    "tbfile1")["rag_index"]["status"], "bm25_ready")
                for _ in range(200):
                    if not vector_jobs._TASKS:
                        break
                    await asyncio.sleep(0.01)
        asyncio.run(run())
        meta = load_library("student-vectors").find_file("tbfile1")
        self.assertEqual(meta["rag_index"]["status"], "ready")
        self.assertNotEqual(meta["rag_index"]["vector_revision"], "pending")

    def test_embedding_failure_keeps_bm25_ready(self):
        from app.core import embedding as embedding_mod
        from app.core import vector_jobs
        from app.core.rag_index import refresh_textbook_vectors
        textbook = self._seed()

        class Failing(NormalizedFakeEmbed):
            async def embed(self, texts):
                raise RuntimeError("model unavailable")

        async def run():
            with mock.patch.object(embedding_mod, "get_embedding_client", return_value=Failing()):
                await refresh_textbook_vectors("student-vectors", textbook)
                for _ in range(200):
                    if not vector_jobs._TASKS:
                        break
                    await asyncio.sleep(0.01)
        asyncio.run(run())
        meta = load_library("student-vectors").find_file("tbfile1")
        self.assertEqual(meta["rag_index"]["status"], "bm25_ready")
        self.assertEqual(meta["rag_index"]["vector_revision"], "unavailable")
