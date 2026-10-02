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

from __future__ import annotations


import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import asyncio

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient
from app.main import create_app
from app.core import workspace as ws_mod
from app.core import library as library_mod
from app.core import session as session_mod
from app.identity import config as id_config
from app.identity import store as id_store
from app.identity.security import create_token, hash_password
from tests.storage_sandbox import StorageSandboxTestCase
from app.core import workspace, workspace_memory


def _write_workspace(dirpath: Path, ws_id: str, student_id: str | None) -> None:
    d: dict = {"workspace_id": ws_id, "name": ws_id, "session_ids": [],
               "knowledge_files": []}
    if student_id is not None:
        d["student_id"] = student_id
    (dirpath / f"{ws_id}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")


class TestWorkspaceIsolation(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(create_app())
        self.user = id_store.create_user(
            email="grace@example.com", username="",
            password_hash=hash_password("secret123"))
        self.token = create_token(self.user.id)
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.ws_root = ws_mod._WORKSPACES_DIR
        self.ws_root.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        super().tearDown()

    def _ids(self, headers: dict | None = None) -> list[str]:
        r = self.client.get("/api/v1/workspaces", headers=headers or {})
        self.assertEqual(r.status_code, 200)
        return sorted(w["workspace_id"] for w in r.json()["workspaces"])

    def test_guest_sees_default_and_legacy_only(self):
        _write_workspace(self.ws_root, "ws_guest", "student_default")
        _write_workspace(self.ws_root, "ws_legacy", None)
        _write_workspace(self.ws_root, "ws_other", "usr_someoneelse")
        self.assertEqual(self.client.get("/api/v1/workspaces").status_code, 401)

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
        self.assertEqual(self.client.get("/api/v1/workspaces").status_code, 401)

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
        self.assertEqual(r.status_code, 401)

    def test_move_session_rejects_foreign_session(self):
        r = self.client.post("/api/v1/workspaces", json={"name": "mine"},
                             headers=self.headers)
        wid = r.json()["workspace_id"]
        # A guest-owned session must not be moved into the user's workspace.
        (session_mod._SESSIONS_DIR / "s_guest.json").write_text(
            json.dumps({"session_id": "s_guest", "student_id": "student_default",
                        "messages": []}), encoding="utf-8")
        r = self.client.post(f"/api/v1/workspaces/{wid}/sessions",
                             json={"session_id": "s_guest"}, headers=self.headers)
        self.assertEqual(r.status_code, 404)

# Related workspace memory boundary regressions.


class TestWorkspaceMemoryBoundary(StorageSandboxTestCase):
    def test_new_session_compacts_once_and_stays_workspace_local(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(workspace, "_WORKSPACES_DIR", Path(tmp) / "workspaces"):
            ws = workspace.Workspace(
                workspace_id="ws1", student_id="stu1", name="课程项目",
                public_memory="知识点：旧内容\n薄弱点：需要复习")
            workspace.save_workspace(ws)

            class LLM:
                calls = 0
                async def complete(self, messages, **kwargs):
                    self.calls += 1
                    return "知识点：压缩后内容\n薄弱点：需要复习", {}

            llm = LLM()
            first = asyncio.run(workspace_memory.compact_workspace_memory_on_new_session(
                "ws1", "chat1", llm=llm))
            second = asyncio.run(workspace_memory.compact_workspace_memory_on_new_session(
                "ws1", "chat1", llm=llm))
            self.assertEqual(first["status"], "compacted")
            self.assertEqual(second["status"], "already_done")
            self.assertEqual(llm.calls, 1)
            restored = workspace.load_workspace("ws1")
            self.assertEqual(restored.public_memory, "知识点：压缩后内容\n薄弱点：需要复习")
            self.assertEqual(restored.memory_boundary_sessions, ["chat1"])


if __name__ == "__main__":
    unittest.main()
