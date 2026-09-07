"""M0 identity x workspaces: /workspaces endpoints are per-user.

Regression: workspaces (shared knowledge files + public memory) were global —
a freshly registered account saw the guest's shared materials (e.g. an
uploaded PDF) instead of a clean slate. Pins the fixed behavior:
  - listing is filtered by the resolved identity (legacy unstamped
    workspaces belong to the guest),
  - create stamps the caller's student_id,
  - by-id endpoints 404 on foreign workspaces (no existence leak),
  - move_session rejects sessions owned by another identity.
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
from app.core import workspace as ws_mod  # noqa: E402
from app.core import library as library_mod  # noqa: E402
from app.core import session as session_mod  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402


def _write_workspace(dirpath: Path, ws_id: str, student_id: str | None) -> None:
    d: dict = {"workspace_id": ws_id, "name": ws_id, "session_ids": [],
               "knowledge_files": []}
    if student_id is not None:
        d["student_id"] = student_id
    (dirpath / f"{ws_id}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")


class TestWorkspaceIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "users").mkdir()
        (root / "sessions").mkdir()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self._patches = [
            patch.object(ws_mod, "_WORKSPACES_DIR", root / "workspaces"),
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"),
            # AUTH_MODE=1 refuses to boot with the dev default JWT secret.
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(create_app())
        self.user = id_store.create_user(
            email="grace@example.com", username="",
            password_hash=hash_password("secret123"))
        self.token = create_token(self.user.id)
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.ws_root = root / "workspaces"
        self.ws_root.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _ids(self, headers: dict | None = None) -> list[str]:
        r = self.client.get("/api/v1/workspaces", headers=headers or {})
        self.assertEqual(r.status_code, 200)
        return sorted(w["workspace_id"] for w in r.json()["workspaces"])

    def test_guest_sees_default_and_legacy_only(self):
        _write_workspace(self.ws_root, "ws_guest", "student_default")
        _write_workspace(self.ws_root, "ws_legacy", None)
        _write_workspace(self.ws_root, "ws_other", "usr_someoneelse")
        self.assertEqual(self._ids(), ["ws_guest", "ws_legacy"])

    def test_new_account_starts_with_empty_workspaces(self):
        _write_workspace(self.ws_root, "ws_guest", "student_default")
        _write_workspace(self.ws_root, "ws_legacy", None)
        self.assertEqual(self._ids(self.headers), [])

    def test_create_stamps_caller_identity(self):
        r = self.client.post("/api/v1/workspaces", json={"name": "我的工作区"},
                             headers=self.headers)
        self.assertEqual(r.status_code, 200)
        wid = r.json()["workspace_id"]
        self.assertEqual(self._ids(self.headers), [wid])
        self.assertEqual(self._ids(), [])  # invisible to the guest

    def test_foreign_workspace_by_id_404(self):
        _write_workspace(self.ws_root, "ws_guest", "student_default")
        for method, url in [
            ("GET", "/api/v1/workspaces/ws_guest"),
            ("PATCH", "/api/v1/workspaces/ws_guest"),
            ("DELETE", "/api/v1/workspaces/ws_guest"),
        ]:
            r = self.client.request(method, url, headers=self.headers,
                                    json={"name": "x"} if method == "PATCH" else None)
            self.assertEqual(r.status_code, 404, f"{method} {url}")
        # The owner's own view still works.
        r = self.client.get("/api/v1/workspaces/ws_guest")
        self.assertEqual(r.status_code, 200)

    def test_move_session_rejects_foreign_session(self):
        r = self.client.post("/api/v1/workspaces", json={"name": "mine"},
                             headers=self.headers)
        wid = r.json()["workspace_id"]
        # A guest-owned session must not be moved into the user's workspace.
        (self._tmp.name and Path(self._tmp.name) / "sessions" / "s_guest.json").write_text(
            json.dumps({"session_id": "s_guest", "student_id": "student_default",
                        "messages": []}), encoding="utf-8")
        r = self.client.post(f"/api/v1/workspaces/{wid}/sessions",
                             json={"session_id": "s_guest"}, headers=self.headers)
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
