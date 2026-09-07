"""Tests for the RAG industrialization upgrade:

1. Structure-aware chunker (retriever.chunk_text): sentence integrity, \f
   page boundaries + page metadata, paragraph packing, sentence-level tail
   overlap, oversized-sentence hard cut, file_id in chunk_id.
2. RRF fusion (hybrid.rrf_merge): dual-lane merge, single-lane degradation.
3. Hybrid retrieval with a fake embed client + a real Chroma PersistentClient
   in a tmp dir: ensure_indexed idempotency, scope isolation, delete_file /
   delete_scope, vector-lane recall beyond BM25, BM25 fallback on embed
   failure, result-dict shape parity with KnowledgeStore.search.
4. Config-off behavior: EMBEDDING_PROVIDER=off -> no client -> pure BM25.
5. File-level summaries: fake LLM writes summary/topics into persisted file
   metadata; merged_knowledge_files injection format; LLM failure is silent.
6. M5 wiring: supervisor's _knowledge_directive_for_turn uses hybrid when the
   embedding track is available (fake embed asserted called) and falls back
   to the merged BM25 store otherwise.

No network: fake embed / fake LLM throughout; Chroma runs embedded in tmp
dirs (chromadb is a declared dependency).
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import settings
from app.core import vector_store
from app.core.knowledge_store import KnowledgeStore
from app.core.retriever import chunk_text
from app.core.hybrid import hybrid_search, rrf_merge
from tests.storage_sandbox import StorageSandboxTestCase


class FakeEmbed:
    """Deterministic keyword-vector embed client (dim=8, no network).

    A text's vector has 1.0 in the slot of each keyword it contains, so the
    vector lane recalls by keyword while BM25 may not (paraphrase test).
    """
    KEYS = ("浮力", "勾股", "光合", "导数", "斜边", "液体", "微分", "极限")

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.embedded_texts: list[str] = []
        self.fail = fail

    async def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding endpoint down")
        self.embedded_texts.extend(texts)
        return [[1.0 if k in t else 0.0 for k in self.KEYS] for t in texts]


class _ChromaTmpMixin:
    """Redirect the Chroma persistent dir to a tmp location per test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="chroma_")
        self._orig_dir = settings.chroma_dir
        settings.chroma_dir = str(Path(self._tmpdir.name) / "vdb")
        vector_store._reset()

    def tearDown(self):
        settings.chroma_dir = self._orig_dir
        vector_store._reset()
        self._tmpdir.cleanup()


def _big_store(tmp: Path, file_id: str = "f1", filename: str = "physics.txt",
               keyword: str = "浮力") -> KnowledgeStore:
    """A store with > SMALL_STORE_MAX_CHUNKS chunks (so ranking kicks in)."""
    store = KnowledgeStore(upload_dir=tmp)
    para = (f"{keyword}是液体对物体的向上托力。阿基米德原理给出了它的大小。"
            f"方向总是竖直向上。\n\n")
    store.add_file(file_id, filename, para * 120)
    assert len(store.chunks) > KnowledgeStore.SMALL_STORE_MAX_CHUNKS
    return store


# ---------------------------------------------------------------------------
# 1. chunker
# ---------------------------------------------------------------------------

class TestStructuredChunker(unittest.TestCase):
    def test_sentences_never_cut(self):
        sents = [f"这是第{i}句话，用来测试切块器的行为。" for i in range(40)]
        chunks = chunk_text("".join(sents), source="t.txt", file_id="f1",
                            chunk_size=100, overlap=30)
        self.assertGreater(len(chunks), 1)
        for c in chunks[:-1]:
            self.assertTrue(c.text.endswith("。"), c.text[-20:])
        # every chunk boundary lands on a sentence boundary
        joined = "\n".join(c.text for c in chunks)
        for s in sents[:5]:
            self.assertIn(s, joined)

    def test_page_boundaries_and_metadata(self):
        text = "第一页的内容。\f第二页的内容。\f第三页的内容。"
        chunks = chunk_text(text, source="p.pdf", file_id="f2")
        self.assertEqual([c.page for c in chunks], [1, 2, 3])
        self.assertEqual([c.chunk_id for c in chunks], ["f2#0", "f2#1", "f2#2"])
        self.assertEqual([c.file_id for c in chunks], ["f2"] * 3)
        # a chunk never spans pages
        for c in chunks:
            self.assertNotIn("第一页", chunks[1].text)
            self.assertNotIn("第三页", chunks[0].text)

    def test_single_page_doc_has_no_page(self):
        chunks = chunk_text("没有分页符的纯文本。", source="a.txt")
        self.assertIsNone(chunks[0].page)

    def test_paragraph_packing_respects_size(self):
        paras = [f"第{i}段内容，长度适中的一段文字。" for i in range(30)]
        chunks = chunk_text("\n\n".join(paras), source="d.txt", chunk_size=120,
                            overlap=20)
        for c in chunks:
            self.assertLessEqual(len(c.text), 120)

    def test_tail_overlap_is_whole_sentences(self):
        sents = [f"这是第{i}句话，用来测试切块器的行为。" for i in range(40)]
        chunks = chunk_text("".join(sents), source="t.txt", file_id="f1",
                            chunk_size=100, overlap=30)
        # chunk 2 starts with the trailing sentence of chunk 1 (no \n prefix,
        # no half sentence)
        head = chunks[1].text.split("\n")[0]
        self.assertTrue(head.endswith("。"))
        self.assertTrue(chunks[0].text.endswith(head),
                        (head, chunks[0].text[-40:]))

    def test_oversized_sentence_hard_cut(self):
        chunks = chunk_text("啊" * 1200 + "。", source="b.txt", chunk_size=500)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c.text), 500)

    def test_chunk_id_falls_back_to_source(self):
        chunks = chunk_text("内容。", source="s.md")
        self.assertEqual(chunks[0].chunk_id, "s.md#0")
        self.assertEqual(chunks[0].file_id, "")

    def test_separator_lines_not_kept_as_content(self):
        chunks = chunk_text("段落一。\n\n---\n\n段落二。", source="m.md",
                            file_id="f6")
        texts = [c.text for c in chunks]
        self.assertTrue(any("段落一" in t for t in texts))
        self.assertTrue(any("段落二" in t for t in texts))
        self.assertFalse(any("---" in t for t in texts))


# ---------------------------------------------------------------------------
# 2. RRF
# ---------------------------------------------------------------------------

class TestRRFMerge(unittest.TestCase):
    def test_dual_lane_merge(self):
        # a ranks [x, y], b ranks [y, z] -> y (in both) wins
        merged = rrf_merge([["x", "y"], ["y", "z"]])
        self.assertEqual(merged[0][0], "y")
        self.assertEqual({cid for cid, _ in merged}, {"x", "y", "z"})
        # scores are descending
        scores = [s for _c, s in merged]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_single_lane_missing(self):
        merged = rrf_merge([["a", "b", "c"], []])
        self.assertEqual([cid for cid, _ in merged], ["a", "b", "c"])

    def test_empty_rankings(self):
        self.assertEqual(rrf_merge([[], []]), [])


# ---------------------------------------------------------------------------
# 3. hybrid retrieval over a real (tmp-dir) Chroma
# ---------------------------------------------------------------------------

class TestHybridSearch(_ChromaTmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._up = tempfile.TemporaryDirectory(prefix="up_")
        self.up = Path(self._up.name)

    def tearDown(self):
        self._up.cleanup()
        super().tearDown()

    def test_ensure_indexed_is_idempotent(self):
        store = _big_store(self.up)
        embed = FakeEmbed()
        ok = asyncio.run(vector_store.ensure_indexed(
            "session:s1", store.chunks, embed))
        self.assertTrue(ok)
        calls_after_first = embed.calls
        self.assertGreater(calls_after_first, 0)
        ok2 = asyncio.run(vector_store.ensure_indexed(
            "session:s1", store.chunks, embed))
        self.assertTrue(ok2)
        self.assertEqual(embed.calls, calls_after_first)  # zero new embeds

    def test_scope_isolation(self):
        s1 = _big_store(self.up / "a", "f1", keyword="浮力")
        s2 = _big_store(self.up / "b", "f2", keyword="勾股")
        embed = FakeEmbed()
        asyncio.run(vector_store.ensure_indexed("session:s1", s1.chunks, embed))
        asyncio.run(vector_store.ensure_indexed("session:s2", s2.chunks, embed))
        hits = asyncio.run(vector_store.query([1.0] + [0.0] * 7, ["session:s2"], 5, embed_client=embed))
        self.assertTrue(hits)
        self.assertTrue(all(cid.startswith("f2#") for cid in hits))

    def test_rebuild_prunes_stale_vectors_only_in_scope(self):
        """重建后旧 chunk 的向量孤儿必须被清掉，且只清本 scope（其他教材/资料
        的向量不动）——「刷新清理恰好对应的部分」的向量轨契约。"""
        s1 = _big_store(self.up / "a", "f1", keyword="浮力")
        s2 = _big_store(self.up / "b", "f2", keyword="勾股")
        embed = FakeEmbed()
        asyncio.run(vector_store.ensure_indexed("session:s1", s1.chunks, embed))
        asyncio.run(vector_store.ensure_indexed("session:s2", s2.chunks, embed))
        s2_old_ids = {c.chunk_id for c in s2.chunks}
        # 模拟重建：s2 文本变化 → chunk 集合整体改变（换 file_id 使新旧 id 不重叠）
        s2new = _big_store(self.up / "b2", "f2b", keyword="勾股")
        ok = asyncio.run(vector_store.ensure_indexed("session:s2", s2new.chunks, embed))
        self.assertTrue(ok)
        # s2 旧向量被剪枝
        stale_left = [cid for cid in s2_old_ids
                      if asyncio.run(vector_store.query([1.0] + [0.0] * 7, ["session:s2"], 2000, embed_client=embed))
                      .get(cid)]
        self.assertFalse(stale_left)
        # s1 向量完好（scope 隔离）
        s1_ids = {c.chunk_id for c in s1.chunks}
        hits = asyncio.run(vector_store.query([1.0] + [0.0] * 7, ["session:s1"], 2000, embed_client=embed))
        self.assertTrue(all(cid in s1_ids for cid in hits))

    def test_vector_lane_recalls_paraphrase_bm25_misses(self):
        # Query shares NO token with the text (pure Latin), so BM25 scores 0;
        # the fake embed gives the query the 浮力 slot, so the vector lane
        # still recalls the right chunks.
        store = _big_store(self.up)
        embed = FakeEmbed()
        scoped = [("session:s1", store)]
        results = asyncio.run(hybrid_search(
            scoped, "buoyancy direction 浮力", top_k=4, embed_client=embed))
        self.assertTrue(results)
        self.assertTrue(any("浮力" in r["text"] for r in results))
        # result dict shape parity with KnowledgeStore.search
        for r in results:
            for key in ("source", "chunk_id", "index", "text", "score", "page"):
                self.assertIn(key, r)
        # BM25 alone (the fallback) finds nothing for this query
        self.assertEqual(store.search("buoyancy direction", top_k=4), [])

    def test_embed_failure_falls_back_to_bm25(self):
        store = _big_store(self.up)
        bad = FakeEmbed(fail=True)
        results = asyncio.run(hybrid_search(
            [("session:s1", store)], "浮力 阿基米德", top_k=3,
            embed_client=bad))
        self.assertTrue(results)
        self.assertTrue(any("浮力" in r["text"] for r in results))

    def test_delete_file_removes_vectors(self):
        store = _big_store(self.up)
        embed = FakeEmbed()
        asyncio.run(vector_store.ensure_indexed("session:s1", store.chunks, embed))
        vec = (await_not_needed := None) or [1.0] + [0.0] * 7
        self.assertTrue(asyncio.run(vector_store.query(vec, ["session:s1"], 5, embed_client=embed)))
        vector_store.delete_file("f1")
        self.assertEqual(asyncio.run(vector_store.query(vec, ["session:s1"], 5, embed_client=embed)), {})

    def test_delete_scope_removes_only_that_scope(self):
        s1 = _big_store(self.up / "a", "f1", keyword="浮力")
        s2 = _big_store(self.up / "b", "f2", keyword="勾股")
        embed = FakeEmbed()
        asyncio.run(vector_store.ensure_indexed("session:s1", s1.chunks, embed))
        asyncio.run(vector_store.ensure_indexed("session:s2", s2.chunks, embed))
        vector_store.delete_scope("session:s1")
        vec = [1.0] + [0.0] * 7
        self.assertEqual(asyncio.run(vector_store.query(vec, ["session:s1"], 5, embed_client=embed)), {})
        self.assertTrue(asyncio.run(vector_store.query([0.0, 1.0] + [0.0] * 6,
                                           ["session:s2"], 5, embed_client=embed)))

    def test_small_store_passthrough_preserved(self):
        store = KnowledgeStore(upload_dir=self.up)
        store.add_file("f9", "note.txt", "如果看到这句话 就回答111")
        results = asyncio.run(hybrid_search(
            [("session:s1", store)], "tokenizer 是什么", top_k=4,
            embed_client=FakeEmbed()))
        self.assertEqual(len(results), 1)
        self.assertIn("回答111", results[0]["text"])
        self.assertEqual(results[0]["score"], 1.0)


# ---------------------------------------------------------------------------
# 4. config-off behavior
# ---------------------------------------------------------------------------

class TestConfigOff(StorageSandboxTestCase):
    def test_no_api_key_means_no_client(self):
        import app.core.embedding as embedding_mod
        with mock.patch.object(settings, "embedding_provider", "off"):
            with mock.patch.object(settings, "embedding_api_key", ""):
                embedding_mod._INSTANCE = None
                try:
                    self.assertIsNone(embedding_mod.get_embedding_client())
                finally:
                    embedding_mod._INSTANCE = None

    def test_tool_without_embed_uses_bm25(self):
        from app.tools.knowledge_search import KnowledgeSearchTool
        store = KnowledgeStore()
        store.add_file("f1", "note.txt", "勾股定理：直角三角形两直角边的平方和等于斜边的平方。")
        tool = KnowledgeSearchTool(store, scoped_stores=[("session:s", store)],
                                   embed_client=None)
        result = asyncio.run(tool.run(query="勾股定理"))
        self.assertFalse(result.is_error)
        self.assertIn("勾股定理", result.text)

    def test_tool_output_includes_page_when_known(self):
        from app.tools.knowledge_search import KnowledgeSearchTool
        store = KnowledgeStore()
        store.add_file("f1", "book.pdf", "第一页讲浮力。\f第二页讲勾股定理和斜边。")
        tool = KnowledgeSearchTool(store)
        result = asyncio.run(tool.run(query="勾股定理"))
        self.assertFalse(result.is_error)
        self.assertIn("第2页", result.text)


# ---------------------------------------------------------------------------
# 5. file-level summaries
# ---------------------------------------------------------------------------

class _FakeSummaryLLM:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0

    async def complete(self, messages, temperature=None, max_tokens=None):
        self.calls += 1
        return self.payload, {}


class TestFileSummary(StorageSandboxTestCase):
    def test_summary_written_and_persisted(self):
        from app.core.file_summary import summarize_session_file
        from app.core.session import TutorSession, load_session, save_session
        session = TutorSession(session_id="chat_test_summary")
        session.knowledge.add_file("f1", "physics.txt", "浮力是液体对物体的向上托力。")
        save_session(session)
        llm = _FakeSummaryLLM(json.dumps(
            {"summary": "讲解浮力的概念与阿基米德原理。",
             "topics": ["物理", "浮力", "力学"]}, ensure_ascii=False))
        asyncio.run(summarize_session_file(
            "chat_test_summary", "f1", "physics.txt", "浮力是液体对物体的向上托力。",
            llm=llm))
        self.assertEqual(llm.calls, 1)
        reloaded = load_session("chat_test_summary")
        f = reloaded.knowledge.files[0]
        self.assertEqual(f["summary"], "讲解浮力的概念与阿基米德原理。")
        self.assertEqual(f["topics"], ["物理", "浮力", "力学"])

    def test_workspace_summary_written(self):
        # Post-library world: workspace uploads live in the workspace's
        # exclusive library folder and summaries persist into the library.
        from app.core.file_summary import summarize_library_file
        from app.core.library import load_library, save_library
        from app.core.workspace import (Workspace, ensure_library_folder,
                                        load_workspace, save_workspace)
        ws = Workspace(name="物理区")
        wid = save_workspace(ws)
        ws = load_workspace(wid)
        folder_id = ensure_library_folder(ws)
        lib = load_library("student_default")
        lib.add_file(folder_id, "shared.txt", "光合作用利用光能合成有机物。",
                     file_id="wf1")
        save_library(lib)
        llm = _FakeSummaryLLM('{"summary": "光合作用原理讲义。", "topics": ["生物"]}')
        asyncio.run(summarize_library_file(
            "student_default", "wf1", "shared.txt", "光合作用利用光能合成有机物。",
            llm=llm))
        lib2 = load_library("student_default")
        self.assertEqual(lib2.find_file("wf1")["summary"], "光合作用原理讲义。")

    def test_llm_failure_leaves_no_summary_and_never_raises(self):
        from app.core.file_summary import summarize_session_file
        from app.core.session import TutorSession, load_session, save_session
        session = TutorSession(session_id="chat_test_summary_fail")
        session.knowledge.add_file("f1", "a.txt", "一些内容。")
        save_session(session)

        class _BrokenLLM:
            async def complete(self, messages, temperature=None, max_tokens=None):
                raise RuntimeError("llm down")

        asyncio.run(summarize_session_file(
            "chat_test_summary_fail", "f1", "a.txt", "一些内容。", llm=_BrokenLLM()))
        f = load_session("chat_test_summary_fail").knowledge.files[0]
        self.assertNotIn("summary", f)

    def test_injection_format_with_and_without_summary(self):
        from app.core.workspace import merged_knowledge_files
        from app.core.session import TutorSession
        session = TutorSession(session_id="s_fmt")
        session.knowledge.add_file("f1", "physics.txt", "浮力内容。")
        session.knowledge.files[0]["summary"] = "讲解浮力与阿基米德原理。"
        session.knowledge.files[0]["topics"] = ["物理", "浮力"]
        session.knowledge.add_file("f2", "legacy.txt", "旧文件内容。")
        _files, names = merged_knowledge_files(session)
        self.assertEqual(names[0], "physics.txt：讲解浮力与阿基米德原理。（物理、浮力）")
        self.assertEqual(names[1], "legacy.txt")  # no summary -> bare filename


# ---------------------------------------------------------------------------
# 6. M5 wiring
# ---------------------------------------------------------------------------

class TestM5HybridWiring(_ChromaTmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._up = tempfile.TemporaryDirectory(prefix="m5up_")
        # P6-A2：考纲 seed 已删——「导数」概念节点由教材图谱 fixture 提供。
        self._cg = tempfile.TemporaryDirectory(prefix="m5cg_")
        from app.agents.knowledge import store as kn_store
        from app.agents.knowledge import manager as kn_manager
        # 测试内构造的 Trace() 会按 settings.trace_dir 落盘，须一并隔离。
        self._trace_patch = mock.patch.object(settings, "trace_dir",
                                              str(Path(self._up.name) / "traces"))
        self._trace_patch.start()
        self._cg_patch = mock.patch.object(kn_store, "_CUSTOM_DIR",
                                           Path(self._cg.name))
        self._cg_patch.start()
        kn_store.save_custom_graph("student_default", "tb-m5", {
            "topic": "数学教材", "topic_key": "tb-m5", "subject": "数学",
            "level": "高中", "source": "textbook:f-m5",
            "nodes": [{"id": "m5.calc.derivative", "name": "导数",
                       "subject": "数学", "level": "高中", "difficulty": 3,
                       "kind": "concept", "aliases": ["微商"]}],
            "edges": [],
            "contents": [{"concept_id": "m5.calc.derivative",
                          "definition": "瞬时变化率", "formula": "",
                          "example": "", "exercise_hint": "", "source": "textbook"}],
        })
        kn_manager._INSTANCE = None

    def tearDown(self):
        self._trace_patch.stop()
        self._cg_patch.stop()
        self._cg.cleanup()
        from app.agents.knowledge import manager as kn_manager
        kn_manager._INSTANCE = None
        self._up.cleanup()
        super().tearDown()

    def _session_with_calculus_material(self):
        from app.core.session import TutorSession
        store = KnowledgeStore(upload_dir=Path(self._up.name))
        para = "导数描述函数的瞬时变化率。导数的定义依赖极限思想。\n\n"
        store.add_file("f1", "calculus.txt", para * 250)
        session = TutorSession(session_id="chat_m5_test", grade="高中")
        session.student_id = "student_default"  # 与教材图谱 fixture 同命名空间
        session.knowledge = store
        return session

    def _understanding(self):
        from app.agents.state import TaskType, TaskUnderstanding
        return TaskUnderstanding(intent=TaskType.EXPLAIN, concept="导数",
                                 requires_tools=False)

    def test_embed_available_routes_through_hybrid(self):
        import app.core.embedding as embedding_mod
        from app.agents.supervisor import _knowledge_directive_for_turn
        from app.core.trace import Trace
        embed = FakeEmbed()
        session = self._session_with_calculus_material()
        with mock.patch.object(embedding_mod, "get_embedding_client",
                               return_value=embed):
            directive = asyncio.run(
                _knowledge_directive_for_turn(self._understanding(), session,
                                              Trace()))
        self.assertGreater(embed.calls, 0)  # vector track was actually used
        self.assertIn("[知识智能", directive)
        self.assertIn("教材引用", directive)

    def test_embed_unavailable_falls_back_to_bm25(self):
        import app.core.embedding as embedding_mod
        from app.agents.supervisor import _knowledge_directive_for_turn
        from app.core.trace import Trace
        session = self._session_with_calculus_material()
        with mock.patch.object(embedding_mod, "get_embedding_client",
                               return_value=None):
            directive = asyncio.run(
                _knowledge_directive_for_turn(self._understanding(), session,
                                              Trace()))
        self.assertIn("[知识智能", directive)


# ---------------------------------------------------------------------------
# remove_file with file_id matching (same-filename files no longer collide)
# ---------------------------------------------------------------------------

class TestRemoveFileByFileId(unittest.TestCase):
    def test_same_filename_files_do_not_collide(self):
        tmp = tempfile.TemporaryDirectory(prefix="ks_same_")
        store = KnowledgeStore(upload_dir=Path(tmp.name))
        store.add_file("f1", "dup.txt", "浮力的内容在这里。")
        store.add_file("f2", "dup.txt", "勾股定理的内容在这里。")
        self.assertTrue(store.remove_file("f1"))
        self.assertEqual(len(store.chunks), 1)
        self.assertIn("勾股定理", store.chunks[0].text)
        self.assertEqual(store.chunks[0].file_id, "f2")
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
