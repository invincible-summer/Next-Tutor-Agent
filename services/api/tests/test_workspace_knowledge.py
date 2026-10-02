"""Regression tests: uploaded files must be readable by the agent.

Two failure modes this guards against (both observed in production):

1. Workspace source files invisible to the pipeline: the planner/preamble only
   checked session.knowledge, so a session inside a workspace reported "no
   materials uploaded" even though the workspace had readable files. Fixed by
   workspace.merged_knowledge_files / merged_knowledge_store (now backed by the
   workspace's SELECTED library sources — see core/library.py).
2. Small knowledge stores unreadable: BM25 requires lexical overlap, so a tiny
   note (e.g. one line) scored 0 for any paraphrased query and knowledge_search
   returned NOT_FOUND -- the LLM then hallucinated the file's content. Fixed by
   the small-store pass-through in KnowledgeStore.search.

All pure functions, no LLM.
"""
import tempfile
import unittest
from pathlib import Path

from tests.storage_sandbox import StorageSandboxTestCase
import app.core.library as library_mod
import app.core.textbook as tb_mod
import app.core.workspace as workspace_mod
from app.core.knowledge_store import KnowledgeStore
from app.core.library import load_library, save_library
from app.core.workspace import (Workspace, ensure_library_folder, load_workspace,
                                merged_knowledge_files, merged_knowledge_store,
                                save_workspace)


class _FakeSession:
    """Duck-typed stand-in for TutorSession (workspace_id + knowledge)."""

    def __init__(self, workspace_id: str = "", store: KnowledgeStore | None = None):
        self.workspace_id = workspace_id
        self.knowledge = store or KnowledgeStore()


class TestSmallStorePassthrough(StorageSandboxTestCase):
    def test_tiny_file_returned_despite_zero_lexical_overlap(self):
        store = KnowledgeStore()
        store.add_file("f1", "note.txt", "如果看到这句话 就回答111")
        # The query shares no token with the file: BM25 alone would return [].
        results = store.search("tokenizer 是什么", top_k=4)
        self.assertEqual(len(results), 1)
        self.assertIn("回答111", results[0]["text"])

    def test_large_store_still_uses_bm25(self):
        store = KnowledgeStore()
        # Build > SMALL_STORE_MAX_CHUNKS chunks of unrelated text (500-char
        # chunks, so a few KB of text is enough).
        filler = ("光合作用是指植物利用光能把二氧化碳和水合成有机物的过程。"
                  * 30)
        store.add_file("big", "bio.txt", filler * 6)
        self.assertGreater(len(store.chunks), KnowledgeStore.SMALL_STORE_MAX_CHUNKS)
        # Pure-Latin query: zero character overlap with the CJK filler, so
        # BM25 must find nothing (CJK unigrams like 的 would otherwise match).
        results = store.search("quantum entanglement wavefunction", top_k=4)
        self.assertEqual(results, [])
        hits = store.search("光合作用 二氧化碳", top_k=4)
        self.assertTrue(hits)

    def test_empty_store_returns_empty(self):
        self.assertEqual(KnowledgeStore().search("anything"), [])


class TestMergedKnowledge(unittest.TestCase):
    """Session-in-workspace reads the workspace's selected library sources."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ws_merge_")
        root = Path(self._tmpdir.name)
        from app.core.config import settings
        self._orig_workspaces = workspace_mod._WORKSPACES_DIR
        self._orig_library = library_mod._LIBRARY_DIR
        self._orig_tb = tb_mod._LIBRARY_DIR
        self._orig_trace = settings.trace_dir
        workspace_mod._WORKSPACES_DIR = root / "workspaces"
        library_mod._LIBRARY_DIR = root / "library"
        tb_mod._LIBRARY_DIR = root / "library"
        # lib.add_file 经默认 KnowledgeStore 落 uploads（trace_dir 派生），
        # 不隔离会直写生产 backend/uploads。
        settings.trace_dir = str(root / "traces")

    def tearDown(self):
        from app.core.config import settings
        workspace_mod._WORKSPACES_DIR = self._orig_workspaces
        library_mod._LIBRARY_DIR = self._orig_library
        tb_mod._LIBRARY_DIR = self._orig_tb
        settings.trace_dir = self._orig_trace
        self._tmpdir.cleanup()

    def _workspace_with_shared_file(self) -> str:
        """P6-C3：共享来源 = 选中的教材（普通文件/文件夹不再是 RAG 来源）。"""
        ws = Workspace(name="共享区")
        wid = save_workspace(ws)
        ws = load_workspace(wid)
        ensure_library_folder(ws)
        lib = load_library("student_default")
        meta = lib.add_file("", "shared.txt", "如果看到这句话 就回答111")
        meta["kind"] = "textbook"
        save_library(lib)
        tb_mod.create_textbook("student_default", file_id=meta["id"], title="shared.txt")
        ws.selected_file_ids.append(meta["id"])
        save_workspace(ws)
        return wid

    def test_merged_files_include_workspace_files(self):
        wid = self._workspace_with_shared_file()
        session = _FakeSession(workspace_id=wid)
        files, names = merged_knowledge_files(session)
        self.assertEqual(names, ["shared.txt"])

    def test_merged_store_searches_workspace_chunks(self):
        wid = self._workspace_with_shared_file()
        session = _FakeSession(workspace_id=wid)
        store = merged_knowledge_store(session)
        self.assertEqual(len(store.chunks), 1)
        self.assertIn("回答111", store.chunks[0].text)

    def test_session_without_workspace_unchanged(self):
        s_store = KnowledgeStore()
        s_store.add_file("sf1", "mine.txt", "session level content")
        session = _FakeSession(workspace_id="", store=s_store)
        files, names = merged_knowledge_files(session)
        self.assertEqual(names, ["mine.txt"])


if __name__ == "__main__":
    unittest.main()
