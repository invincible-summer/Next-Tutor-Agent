"""M0 identity x chat history: /chat/sessions listing is per-user.

Regression: the sessions list endpoint returned every transcript on disk
regardless of caller, so a freshly registered account saw the shared guest
(student_default) history instead of a clean slate. These tests pin the
fixed behavior: the JWT keys the listing to the caller's own sessions, guests cannot access persisted history, including legacy sessions without a
student_id stamp (pre-M0).
"""

from __future__ import annotations


import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient
from app.main import create_app
from app.core import session as session_mod
from app.identity import config as id_config
from app.identity import store as id_store
from app.identity.security import create_token, hash_password
from tests.support.storage_sandbox import StorageSandboxTestCase
from app.core import guest_policy
import app.core.session as session_mod
from app.core.session import TutorSession, list_sessions, save_session
from app.core.config import settings
from app.core.session import delete_session


def _write_session(dirpath: Path, session_id: str, student_id: str | None) -> None:
    d: dict = {"session_id": session_id, "title": session_id, "messages": []}
    if student_id is not None:
        d["student_id"] = student_id
    (dirpath / f"{session_id}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")


class TestSessionIsolation(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        super().tearDown()

    def _ids(self, token: str | None) -> list[str]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = self.client.get("/api/v1/chat/sessions", headers=headers)
        self.assertEqual(r.status_code, 200)
        return sorted(s["session_id"] for s in r.json()["sessions"])

    def test_guest_cannot_read_default_and_legacy_history(self):
        _write_session(session_mod._SESSIONS_DIR, "s_guest", "student_default")
        _write_session(session_mod._SESSIONS_DIR, "s_legacy", None)  # pre-M0 stamp
        _write_session(session_mod._SESSIONS_DIR, "s_other", "usr_someoneelse")
        self.assertEqual(self.client.get("/api/v1/chat/sessions").status_code, 401)
        guest_policy.set_policy(True)
        token = self.client.post("/api/v1/guest/session").json()["token"]
        self.assertEqual(self.client.get("/api/v1/chat/sessions", headers={"X-Guest-Token": token}).json(), {"sessions": []})

    def test_registered_user_sees_only_own_sessions(self):
        user = id_store.create_user(
            email="carol@example.com", username="",
            password_hash=hash_password("secret123"))
        _write_session(session_mod._SESSIONS_DIR, "s_guest", "student_default")
        _write_session(session_mod._SESSIONS_DIR, "s_legacy", None)
        _write_session(session_mod._SESSIONS_DIR, "s_mine", user.id)
        self.assertEqual(self._ids(create_token(user.id)), ["s_mine"])

    def test_new_account_starts_with_empty_history(self):
        user = id_store.create_user(
            email="dave@example.com", username="",
            password_hash=hash_password("secret123"))
        _write_session(session_mod._SESSIONS_DIR, "s_guest", "student_default")
        self.assertEqual(self._ids(create_token(user.id)), [])

    def test_guest_mode_still_honors_jwt(self):
        # AUTH_MODE=0 也不放行未登录历史；有效 JWT 仍须绑定账号，
        # JWT 必须绑定用户自己的命名空间——登录不能是无效操作。
        os.environ["AUTH_MODE"] = "0"
        user = id_store.create_user(
            email="erin@example.com", username="",
            password_hash=hash_password("secret123"))
        _write_session(session_mod._SESSIONS_DIR, "s_guest", "student_default")
        _write_session(session_mod._SESSIONS_DIR, "s_mine", user.id)
        self.assertEqual(self.client.get("/api/v1/chat/sessions").status_code, 401)
        self.assertEqual(self._ids(create_token(user.id)), ["s_mine"])

# Related round count regressions.


class TestRoundCount(StorageSandboxTestCase):
    def _session(self, messages) -> TutorSession:
        s = TutorSession(session_id="rc_test")
        s.messages = messages
        return s

    def test_uploads_do_not_count_replies_do(self):
        s = self._session([
            {"role": "user", "content": "（上传了文件：笔记.pdf）"},
            {"role": "user", "content": "讲一下浮力"},
            {"role": "assistant", "content": "浮力是……"},
            {"role": "user", "content": "再举个例子"},
            {"role": "assistant", "content": "比如……"},
        ])
        self.assertEqual(s.round_count(), 2)
        self.assertIn("对话 2 轮", s.context_summary())

    def test_zero_rounds_before_first_reply(self):
        s = self._session([{"role": "user", "content": "（上传了文件：a.pdf）"}])
        self.assertEqual(s.round_count(), 0)

    def test_list_sessions_carries_round_count(self):
        tmp = tempfile.TemporaryDirectory(prefix="rc_")
        orig = session_mod._SESSIONS_DIR
        session_mod._SESSIONS_DIR = Path(tmp.name)
        try:
            s = TutorSession(session_id="rc_list", title="浮力")
            s.student_id = "student_default"
            s.messages = [
                {"role": "user", "content": "讲一下浮力"},
                {"role": "assistant", "content": "浮力是……"},
            ]
            save_session(s)
            items = list_sessions()
            item = next(i for i in items if i["session_id"] == "rc_list")
            self.assertEqual(item["round_count"], 1)
            self.assertEqual(item["message_count"], 2)
        finally:
            session_mod._SESSIONS_DIR = orig
            tmp.cleanup()


# Related session material cleanup regressions.


class TestSessionMaterialCleanup(StorageSandboxTestCase):
    def test_delete_removes_text_and_original(self):
        with tempfile.TemporaryDirectory(prefix="session_cleanup_") as td:
            root = Path(td)
            with patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"), \
                    patch.object(settings, "trace_dir", str(root / "traces")):
                session = TutorSession(session_id="s-clean")
                meta = session.knowledge.add_file(
                    "f-clean", "scan.png", "OCR content", raw=b"image", orig_ext=".png")
                save_session(session)
                text_path = session.knowledge.upload_dir / "f-clean.txt"
                orig_path = session.knowledge.upload_dir / "f-clean.orig.png"
                self.assertTrue(text_path.exists())
                self.assertTrue(orig_path.exists())
                self.assertTrue(delete_session("s-clean"))
                self.assertFalse(text_path.exists())
                self.assertFalse(orig_path.exists())


if __name__ == "__main__":
    unittest.main()
