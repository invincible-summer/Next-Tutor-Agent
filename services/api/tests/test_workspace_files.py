"""Tests for workspace shared-file fixes:

1. KnowledgeStore.remove_file: metadata / chunks / on-disk txt are cleared,
   other files untouched, search no longer hits the deleted content.
2. /chat/upload workspace binding: uploading with workspace_id on a fresh
   session eagerly assigns the session id and binds it to the workspace, so
   the session can read workspace shared files from turn 1 (previously the
   orphan-session bug).
3. update_workspace_memory race: an upload landing during the LLM call must
   not be clobbered by the stale pre-LLM snapshot (read-modify-write fix).

Fake LLM only, no network. Data dirs are redirected to temp dirs.
"""
import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from fastapi import UploadFile

import app.core.session as session_mod
import app.core.workspace as workspace_mod
import app.core.library as library_mod
import app.core.textbook as tb_mod
from app.core.knowledge_store import KnowledgeStore
from app.core.library import load_library, save_library
from app.core.session import load_session
from app.core.workspace import (Workspace, ensure_library_folder, load_workspace,
                                readable_files, save_workspace)
from app.core.workspace_memory import update_workspace_memory
from app.api.v1.chat import upload_files


class _TmpDirsMixin:
    """Redirect session/workspace/library persistence dirs to a temp location."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ws_fix_")
        root = Path(self._tmpdir.name)
        from app.agents.memory import prompt_memory
        self._orig_sessions = session_mod._SESSIONS_DIR
        self._orig_workspaces = workspace_mod._WORKSPACES_DIR
        self._orig_library = library_mod._LIBRARY_DIR
        self._orig_tb = tb_mod._LIBRARY_DIR
        self._orig_pm = prompt_memory._STUDENTS_DIR
        self._orig_policy = prompt_memory._POLICY_PATH
        session_mod._SESSIONS_DIR = root / "chat_history"
        workspace_mod._WORKSPACES_DIR = root / "chat_history" / "workspaces"
        library_mod._LIBRARY_DIR = root / "chat_history" / "library"
        tb_mod._LIBRARY_DIR = root / "chat_history" / "library"
        # 漏 patch 的写路径曾把 student_default.prompt_memory.json 直写生产目录。
        prompt_memory._STUDENTS_DIR = root / "students"
        prompt_memory._POLICY_PATH = root / "students" / "prompt_memory_policy.json"

    def tearDown(self):
        session_mod._SESSIONS_DIR = self._orig_sessions
        workspace_mod._WORKSPACES_DIR = self._orig_workspaces
        library_mod._LIBRARY_DIR = self._orig_library
        tb_mod._LIBRARY_DIR = self._orig_tb
        from app.agents.memory import prompt_memory
        prompt_memory._STUDENTS_DIR = self._orig_pm
        prompt_memory._POLICY_PATH = self._orig_policy
        self._tmpdir.cleanup()


class TestRemoveFile(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ks_rm_")
        self.store = KnowledgeStore(upload_dir=Path(self._tmpdir.name))
        self.store.add_file("f1", "note_a.txt", "浮力等于排开液体的重力 阿基米德")
        self.store.add_file("f2", "note_b.txt", "勾股定理 直角三角形 斜边平方")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_remove_clears_meta_chunks_and_disk(self):
        self.assertTrue(self.store.remove_file("f1"))
        # metadata gone, other file untouched
        self.assertEqual([f["id"] for f in self.store.file_list()], ["f2"])
        # chunks of f1 gone, f2 chunks intact
        self.assertTrue(self.store.chunks)
        self.assertTrue(all(c.source == "note_b.txt" for c in self.store.chunks))
        # on-disk txt removed for f1, kept for f2
        up = Path(self._tmpdir.name)
        self.assertFalse((up / "f1.txt").exists())
        self.assertTrue((up / "f2.txt").exists())
        # search no longer hits deleted content, still hits the other file
        texts = [r["text"] for r in self.store.search("阿基米德 浮力")]
        self.assertFalse(any("阿基米德" in t for t in texts))
        hits = self.store.search("勾股定理")
        self.assertTrue(any("勾股定理" in r["text"] for r in hits))

    def test_remove_unknown_id_returns_false(self):
        self.assertFalse(self.store.remove_file("nope"))
        self.assertEqual(len(self.store.file_list()), 2)

    def test_index_invalidated(self):
        self.store.search("浮力")  # builds lazily... small store path skips it
        self.store._ensure_index()
        self.assertIsNotNone(self.store._index)
        self.store.remove_file("f1")
        self.assertIsNone(self.store._index)


class TestUploadWorkspaceBinding(_TmpDirsMixin, unittest.TestCase):
    def test_upload_binds_new_session_to_workspace(self):
        ws = Workspace(name="物理学习区")
        wid = save_workspace(ws)
        up = UploadFile(io.BytesIO("浮力是液体对物体的向上托力".encode("utf-8")),
                        filename="physics.txt")
        resp = asyncio.run(upload_files(session_id=None, grade="高中",
                                        workspace_id=wid, files=[up],
                                        student_id="student_default"))
        sid = resp.session_id
        self.assertTrue(sid)
        # session persisted with the workspace binding
        sess = load_session(sid)
        self.assertIsNotNone(sess)
        self.assertEqual(sess.workspace_id, wid)
        # workspace now lists the session
        ws2 = load_workspace(wid)
        self.assertIn(sid, ws2.session_ids)
        # uploaded file landed in the session store
        self.assertEqual(resp.results[0].filename, "physics.txt")
        # cleanup: session upload artifacts live in the default uploads dir
        for suffix in (".txt", ".orig.txt"):
            try:
                (sess.knowledge.upload_dir / f"{resp.results[0].id}{suffix}").unlink()
            except OSError:
                pass

    def test_upload_without_workspace_unchanged(self):
        up = UploadFile(io.BytesIO("some text".encode("utf-8")), filename="a.txt")
        resp = asyncio.run(upload_files(session_id=None, grade="高中",
                                        workspace_id=None, files=[up],
                                        student_id="student_default"))
        sess = load_session(resp.session_id)
        self.assertEqual(sess.workspace_id, "")
        for suffix in (".txt", ".orig.txt"):
            try:
                (sess.knowledge.upload_dir / f"{resp.results[0].id}{suffix}").unlink()
            except OSError:
                pass


class _ConcurrentUploadLLM:
    """Fake LLM that simulates a shared-file upload landing mid-call."""

    def __init__(self, ws_id: str):
        self.ws_id = ws_id

    async def complete(self, messages, temperature=None, max_tokens=None):
        # P6-C3：共享来源只保留教材——并发落库的文件须注册为教材并选入才可读。
        ws = load_workspace(self.ws_id)
        ensure_library_folder(ws)
        lib = load_library("student_default")
        lib.add_file("", "race.txt", "并发上传的共享资料", file_id="racefile")
        save_library(lib)
        tb_mod.create_textbook("student_default", file_id="racefile", title="race.txt")
        ws.selected_file_ids.append("racefile")
        save_workspace(ws)
        return "更新后的公共记忆", {}


class TestWorkspaceMemoryRace(_TmpDirsMixin, unittest.TestCase):
    def test_concurrent_upload_not_clobbered(self):
        ws = Workspace(name="竞态区")
        wid = save_workspace(ws)
        llm = _ConcurrentUploadLLM(wid)
        asyncio.run(update_workspace_memory(
            wid, "讲一下浮力", "浮力是向上的托力", llm=llm))
        ws2 = load_workspace(wid)
        # public memory was updated...
        self.assertEqual(ws2.public_memory, "更新后的公共记忆")
        self.assertGreater(ws2.public_memory_updated_at, 0)
        # ...and the upload that happened during the LLM call survived
        ids = [f["id"] for f in readable_files(ws2)]
        self.assertIn("racefile", ids)


if __name__ == "__main__":
    unittest.main()
