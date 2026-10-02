"""Session-scoped library references (POST /chat/sessions/{sid}/attach_library):

1. Attach copies extracted text + original bytes into the SESSION store under
   fresh session-scoped ids — the library itself is never modified.
2. Copies persist through save/load (restart rebuild path).
3. Isolation: another session's store stays empty (references never cross
   conversations); only workspace-shared memory + the library are cross-chat.
4. Missing file / duplicate filename are reported per-file in `errors`.
5. Original binary round-trips byte-identically (download survives even if the
   library original is later deleted).

Endpoint functions are called directly (no network). Data dirs are redirected
to temp dirs; session upload artifacts in the default uploads dir are cleaned
up after each test.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

import app.core.library as library_mod  # noqa: E402
import app.core.session as session_mod  # noqa: E402
import app.core.textbook as tb_mod  # noqa: E402
from app.api.v1.chat import AttachLibraryRequest, attach_library_files  # noqa: E402
from app.core.library import load_library, save_library  # noqa: E402
from app.core.session import TutorSession, load_session, save_session  # noqa: E402

STUDENT = "student_default"


class _TmpDirsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="attach_")
        root = Path(self._tmpdir.name)
        from app.agents.memory import prompt_memory
        from app.core import context as context_mod
        from app.core.config import settings
        self._orig_sessions = session_mod._SESSIONS_DIR
        self._orig_library = library_mod._LIBRARY_DIR
        self._orig_tb = tb_mod._LIBRARY_DIR
        self._orig_pm = prompt_memory._STUDENTS_DIR
        self._orig_policy = prompt_memory._POLICY_PATH
        self._orig_transcript = context_mod._TRANSCRIPT_DIR
        self._orig_trace = settings.trace_dir
        session_mod._SESSIONS_DIR = root / "chat_history"
        library_mod._LIBRARY_DIR = root / "chat_history" / "library"
        tb_mod._LIBRARY_DIR = root / "chat_history" / "library"
        # 漏 patch 的写路径曾把 student_default.prompt_memory.json 等直写生产目录。
        prompt_memory._STUDENTS_DIR = root / "students"
        prompt_memory._POLICY_PATH = root / "students" / "prompt_memory_policy.json"
        context_mod._TRANSCRIPT_DIR = root / "chat_history"
        settings.trace_dir = str(root / "traces")

    def tearDown(self):
        session_mod._SESSIONS_DIR = self._orig_sessions
        library_mod._LIBRARY_DIR = self._orig_library
        tb_mod._LIBRARY_DIR = self._orig_tb
        from app.agents.memory import prompt_memory
        from app.core import context as context_mod
        from app.core.config import settings
        prompt_memory._STUDENTS_DIR = self._orig_pm
        prompt_memory._POLICY_PATH = self._orig_policy
        context_mod._TRANSCRIPT_DIR = self._orig_transcript
        settings.trace_dir = self._orig_trace
        self._tmpdir.cleanup()


def _add_lib_file(fid: str, name: str, text: str,
                  raw: bytes | None = None, ext: str = "") -> dict:
    """落库一个文件并注册为教材（P6-C3：只有教材可被会话引用）。"""
    lib = load_library(STUDENT)
    meta = lib.add_file("", name, text, raw=raw, orig_ext=ext, file_id=fid)
    meta["kind"] = "textbook"
    save_library(lib)
    tb_mod.create_textbook(STUDENT, file_id=fid, title=name)
    return meta


def _attach(sid: str, ids: list[str]):
    return asyncio.run(attach_library_files(
        sid, AttachLibraryRequest(file_ids=ids), student_id=STUDENT))


def _cleanup_uploads(sess) -> None:
    """Session upload artifacts live in the default uploads dir — remove them."""
    for f in sess.knowledge.files:
        fid = f["id"]
        for suffix in (".txt", f.get("orig_ext") and f".orig{f['orig_ext']}" or ".orig"):
            try:
                (sess.knowledge.upload_dir / f"{fid}{suffix}").unlink()
            except OSError:
                pass


class TestAttachLibrary(_TmpDirsMixin, unittest.TestCase):
    def test_attach_copies_into_session_and_persists(self):
        _add_lib_file("libf1", "物理笔记.txt", "浮力等于排开液体的重力 阿基米德")
        _add_lib_file("libf2", "数学笔记.txt", "勾股定理 斜边平方")
        resp = _attach("new", ["libf1", "libf2"])
        self.assertEqual(resp["errors"], [])
        self.assertEqual(len(resp["results"]), 2)
        sid = resp["session_id"]
        self.assertTrue(sid and sid != "new")

        sess = load_session(sid)
        self.assertIsNotNone(sess)
        names = {f["filename"] for f in sess.knowledge.files}
        self.assertEqual(names, {"物理笔记.txt", "数学笔记.txt"})
        # ids are fresh session-scoped ids, not the library ids
        sess_ids = {f["id"] for f in sess.knowledge.files}
        self.assertTrue(sess_ids)
        self.assertNotIn("libf1", sess_ids)
        self.assertNotIn("libf2", sess_ids)
        # chunks landed in the session store and are retrievable
        self.assertGreaterEqual(len(sess.knowledge.chunks), 2)
        hits = sess.knowledge.search("浮力")
        self.assertTrue(any("浮力" in h["text"] for h in hits))
        # the library itself is untouched
        self.assertEqual(len(load_library(STUDENT).files), 2)
        # copies survive a save/load cycle (the restart rebuild path)
        sess2 = load_session(sid)
        self.assertEqual({f["filename"] for f in sess2.knowledge.files}, names)
        _cleanup_uploads(sess)

    def test_attach_never_crosses_sessions(self):
        _add_lib_file("libf1", "物理笔记.txt", "浮力等于排开液体的重力")
        resp = _attach("new", ["libf1"])
        sess = load_session(resp["session_id"])
        # a second, independent session sees nothing of the reference
        other = TutorSession(session_id="other_session")
        other.student_id = STUDENT
        save_session(other)
        reloaded = load_session("other_session")
        self.assertEqual(reloaded.knowledge.files, [])
        self.assertEqual(reloaded.knowledge.chunks, [])
        _cleanup_uploads(sess)

    def test_missing_and_duplicate_reported_per_file(self):
        _add_lib_file("libf1", "笔记.txt", "一些内容")
        resp = _attach("new", ["libf1", "ghost"])
        self.assertEqual(len(resp["results"]), 1)
        self.assertEqual(len(resp["errors"]), 1)
        self.assertEqual(resp["errors"][0]["file_id"], "ghost")
        # 同一库文件再次挂载（内容未变）→ 幂等提示，不重复复制快照
        resp2 = _attach(resp["session_id"], ["libf1"])
        self.assertEqual(resp2["results"], [])
        self.assertEqual(len(resp2["errors"]), 1)
        self.assertIn("已在会话中引用", resp2["errors"][0]["error"])
        sess = load_session(resp["session_id"])
        self.assertEqual(len(sess.knowledge.files), 1)
        _cleanup_uploads(sess)

    def test_reattach_after_library_text_change_refreshes_snapshot(self):
        """库文本更新（OCR 续跑/重建 RAG）后重新挂载 → 快照原地替换为最新文本。"""
        _add_lib_file("libf2", "语文必修上.txt", "旧文本 沁园春")
        resp = _attach("new", ["libf2"])
        sid = resp["session_id"]
        sess = load_session(sid)
        self.assertEqual(len(sess.knowledge.files), 1)
        old_id = sess.knowledge.files[0]["id"]
        # 模拟库侧 .txt 更新（如 rebuild 复用 OCR 后收割并入了图表标记）
        lib = load_library(STUDENT)
        data_dir = library_mod.library_data_dir(STUDENT)
        (data_dir / "libf2.txt").write_text("新文本 我与小城（节选）", encoding="utf-8")
        save_library(lib)
        resp2 = _attach(sid, ["libf2"])
        self.assertEqual(resp2["errors"], [])
        self.assertEqual(len(resp2["results"]), 1)
        sess2 = load_session(sid)
        self.assertEqual(len(sess2.knowledge.files), 1)
        new_meta = sess2.knowledge.files[0]
        self.assertNotEqual(new_meta["id"], old_id)
        self.assertEqual(new_meta["library_file_id"], "libf2")
        self.assertTrue(any("我与小城" in c.text for c in sess2.knowledge.chunks))
        _cleanup_uploads(sess2)

    def test_original_bytes_copied_byte_identically(self):
        raw = b"%PDF-1.4 fake-binary \x00\x01\x02"
        _add_lib_file("libpdf", "课件.pdf", "提取出的文本", raw=raw, ext=".pdf")
        resp = _attach("new", ["libpdf"])
        self.assertEqual(resp["errors"], [])
        self.assertTrue(resp["results"][0]["has_original"])
        sess = load_session(resp["session_id"])
        fid = resp["results"][0]["id"]
        self.assertEqual(
            (sess.knowledge.upload_dir / f"{fid}.orig.pdf").read_bytes(), raw)
        _cleanup_uploads(sess)

    def test_plain_library_file_rejected(self):
        """P6-C3：未注册为教材的普通资料库文件不可被会话引用。"""
        lib = load_library(STUDENT)
        lib.add_file("", "普通文件.txt", "散件内容", file_id="plain1")
        save_library(lib)
        resp = _attach("new", ["plain1"])
        self.assertEqual(resp["results"], [])
        self.assertEqual(len(resp["errors"]), 1)
        self.assertIn("教材", resp["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
