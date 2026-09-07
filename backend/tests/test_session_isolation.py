"""M0 identity x chat history: /chat/sessions listing is per-user.

Regression: the sessions list endpoint returned every transcript on disk
regardless of caller, so a freshly registered account saw the shared guest
(student_default) history instead of a clean slate. These tests pin the
fixed behavior: the JWT keys the listing to the caller's own sessions, the
guest sees only student_default sessions, and legacy sessions without a
student_id stamp (pre-M0) belong to the guest.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core import session as session_mod  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402


def _write_session(dirpath: Path, session_id: str, student_id: str | None) -> None:
    d: dict = {"session_id": session_id, "title": session_id, "messages": []}
    if student_id is not None:
        d["student_id"] = student_id
    (dirpath / f"{session_id}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")


class TestSessionIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        (Path(self._tmp.name) / "users").mkdir()  # _save_raw 不会自建补丁路径父目录
        self._patches = [
            patch.object(session_mod, "_SESSIONS_DIR", Path(self._tmp.name)),
            # AUTH_MODE=1 refuses to boot with the dev default JWT secret.
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE",
                         Path(self._tmp.name) / "users" / "accounts.json"),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _ids(self, token: str | None) -> list[str]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = self.client.get("/api/v1/chat/sessions", headers=headers)
        self.assertEqual(r.status_code, 200)
        return sorted(s["session_id"] for s in r.json()["sessions"])

    def test_guest_sees_default_and_legacy_sessions_only(self):
        _write_session(Path(self._tmp.name), "s_guest", "student_default")
        _write_session(Path(self._tmp.name), "s_legacy", None)  # pre-M0 stamp
        _write_session(Path(self._tmp.name), "s_other", "usr_someoneelse")
        self.assertEqual(self._ids(None), ["s_guest", "s_legacy"])

    def test_registered_user_sees_only_own_sessions(self):
        user = id_store.create_user(
            email="carol@example.com", username="",
            password_hash=hash_password("secret123"))
        _write_session(Path(self._tmp.name), "s_guest", "student_default")
        _write_session(Path(self._tmp.name), "s_legacy", None)
        _write_session(Path(self._tmp.name), "s_mine", user.id)
        self.assertEqual(self._ids(create_token(user.id)), ["s_mine"])

    def test_new_account_starts_with_empty_history(self):
        user = id_store.create_user(
            email="dave@example.com", username="",
            password_hash=hash_password("secret123"))
        _write_session(Path(self._tmp.name), "s_guest", "student_default")
        self.assertEqual(self._ids(create_token(user.id)), [])

    def test_guest_mode_still_honors_jwt(self):
        # AUTH_MODE=0（游客宽容模式）：无需登录即可用，但一旦登录，
        # JWT 必须绑定用户自己的命名空间——登录不能是无效操作。
        os.environ["AUTH_MODE"] = "0"
        user = id_store.create_user(
            email="erin@example.com", username="",
            password_hash=hash_password("secret123"))
        _write_session(Path(self._tmp.name), "s_guest", "student_default")
        _write_session(Path(self._tmp.name), "s_mine", user.id)
        self.assertEqual(self._ids(None), ["s_guest"])
        self.assertEqual(self._ids(create_token(user.id)), ["s_mine"])


if __name__ == "__main__":
    unittest.main()
