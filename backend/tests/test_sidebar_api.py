"""Tests for the P2/P3 data-layer performance endpoints:

1. GET /api/v1/sidebar — composite snapshot (sessions + workspaces + details)
   in ONE request, with a weak ETag (If-None-Match -> 304) and M0 isolation.
2. GET /api/v1/chat/sessions/{id}?tail=N — progressive transcript loading:
   last-N messages + message_total; default stays full for compatibility.
3. Lazy library chunks — load_library no longer re-chunks every file (the
   root cause of multi-second list endpoints); chunks appear via chunks_for()
   and are reused from the process cache.

Fake clients only, no network. Data dirs are redirected to temp dirs.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core import library as library_mod  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.core import session as session_mod  # noqa: E402
from app.core.library import Library, load_library  # noqa: E402
from app.core.session import TutorSession, save_session  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402


class _TmpDirs:
    """Redirect library/workspace/session/user persistence to temp dirs."""

    def __init__(self, test: unittest.TestCase, with_users: bool = False):
        self._tmp = tempfile.TemporaryDirectory(prefix="sidebar_")
        root = Path(self._tmp.name)
        self.root = root
        from app.agents.memory import prompt_memory
        from app.core import trash as trash_mod
        self._patches = [
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(ws_mod, "_WORKSPACES_DIR", root / "workspaces"),
            patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"),
            # 历史遗漏：会话删除走软删除归档（trash）且画像写入 prompt_memory，
            # 不 patch 会直落生产目录。
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
            patch.object(trash_mod, "_GLOBAL_POLICY", root / "trash" / "policy.json"),
            patch.object(prompt_memory, "_STUDENTS_DIR", root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH",
                         root / "students" / "prompt_memory_policy.json"),
        ]
        if with_users:
            (root / "users").mkdir()
            self._patches.append(
                patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"))
        for p in self._patches:
            p.start()

    def cleanup(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()


class TestSidebarApi(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs = _TmpDirs(self)
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._dirs.cleanup()

    def _make_session(self, sid: str, n_messages: int, student_id: str = "student_default"):
        s = TutorSession(session_id=sid, title=sid)
        s.student_id = student_id
        s.messages = [{"role": "user" if i % 2 == 0 else "assistant",
                       "content": f"m{i}"} for i in range(n_messages)]
        save_session(s)

    # --- composite snapshot + ETag ---

    def test_snapshot_combines_sessions_workspaces_details(self):
        self._make_session("chat_a", 4)
        r = self.client.post("/api/v1/workspaces", json={"name": "物理专区"})
        wid = r.json()["workspace_id"]

        r = self.client.get("/api/v1/sidebar")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([s["session_id"] for s in body["sessions"]], ["chat_a"])
        self.assertEqual(len(body["workspaces"]), 1)
        self.assertEqual(body["workspaces"][0]["workspace_id"], wid)
        self.assertIn("file_count", body["workspaces"][0])
        self.assertIn(wid, body["details"])
        self.assertEqual(body["details"][wid]["name"], "物理专区")

    def test_etag_revalidation_304_then_changes(self):
        self._make_session("chat_a", 2)
        r1 = self.client.get("/api/v1/sidebar")
        etag = r1.headers.get("ETag")
        self.assertTrue(etag)

        r2 = self.client.get("/api/v1/sidebar", headers={"If-None-Match": etag})
        self.assertEqual(r2.status_code, 304)

        # Data changes -> new content + a different validator.
        self._make_session("chat_b", 2)
        r3 = self.client.get("/api/v1/sidebar", headers={"If-None-Match": etag})
        self.assertEqual(r3.status_code, 200)
        self.assertIn("chat_b", [s["session_id"] for s in r3.json()["sessions"]])

    def test_identity_isolation(self):
        self._dirs.cleanup()
        self._dirs = _TmpDirs(self, with_users=True)
        self.client = TestClient(create_app())
        alice_user = id_store.create_user(email="alice@t.local", username="alice",
                                          password_hash=hash_password("pw"))
        bob_user = id_store.create_user(email="bob@t.local", username="bob",
                                        password_hash=hash_password("pw"))
        alice = {"Authorization": "Bearer " + create_token(alice_user.id)}
        bob = {"Authorization": "Bearer " + create_token(bob_user.id)}

        # Sessions are keyed by the REAL generated user id (usr_*), not email.
        self._make_session("chat_alice", 2, student_id=alice_user.id)
        r = self.client.get("/api/v1/sidebar", headers=alice)
        self.assertEqual([s["session_id"] for s in r.json()["sessions"]], ["chat_alice"])
        r = self.client.get("/api/v1/sidebar", headers=bob)
        self.assertEqual(r.json()["sessions"], [])
        self.assertEqual(r.json()["workspaces"], [])

    # --- progressive transcript loading ---

    def test_session_tail_progressive(self):
        self._make_session("chat_long", 50)
        full = self.client.get("/api/v1/chat/sessions/chat_long")
        self.assertEqual(len(full.json()["messages"]), 50)
        self.assertEqual(full.json()["message_total"], 50)

        tail = self.client.get("/api/v1/chat/sessions/chat_long?tail=10")
        body = tail.json()
        self.assertEqual(tail.status_code, 200)
        self.assertEqual(len(body["messages"]), 10)
        self.assertEqual(body["message_total"], 50)
        # The tail keeps the most RECENT messages in order.
        self.assertEqual([m["content"] for m in body["messages"]],
                         [f"m{i}" for i in range(40, 50)])

        # tail larger than the transcript behaves like full.
        big = self.client.get("/api/v1/chat/sessions/chat_long?tail=500")
        self.assertEqual(len(big.json()["messages"]), 50)


class TestLazyChunks(unittest.TestCase):
    """from_dict must not re-chunk; chunks arrive lazily via chunks_for()."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="lazychunks_")
        root = Path(self._tmp.name)
        self._patch = patch.object(library_mod, "_LIBRARY_DIR", root / "library")
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_load_is_metadata_only_and_chunks_are_lazy(self):
        lib = Library(student_id="student_default")
        meta = lib.add_file("notes.txt", "浮力是流体对物体的托力。" * 40, "notes.txt")
        lib.save_library() if hasattr(lib, "save_library") else None
        from app.core.library import save_library
        save_library(lib)

        reloaded = load_library("student_default")
        self.assertEqual(len(reloaded.files), 1)
        # No eager chunking on load (the multi-second endpoint regression).
        self.assertEqual(reloaded.chunks_by_file, {})

        chunks = reloaded.chunks_for(meta["id"])
        self.assertGreater(len(chunks), 0)
        # Cached on the instance: a second access is free.
        self.assertIs(reloaded.chunks_for(meta["id"]), chunks)

    def test_process_cache_reuses_chunks_across_loads(self):
        lib = Library(student_id="student_default")
        meta = lib.add_file("notes.txt", "惯性是物体保持运动状态的属性。" * 40, "notes.txt")
        from app.core.library import save_library
        save_library(lib)

        first = load_library("student_default").chunks_for(meta["id"])
        second = load_library("student_default").chunks_for(meta["id"])
        # Same mtime -> the process-level cache returns the SAME chunk list.
        self.assertIs(first, second)

        # remove_file invalidates the cache entry (no stale chunks survive).
        lib2 = load_library("student_default")
        lib2.remove_file(meta["id"])
        save_library(lib2)
        self.assertNotIn((lib2.student_id, meta["id"]), library_mod._chunk_cache)


if __name__ == "__main__":
    unittest.main()
