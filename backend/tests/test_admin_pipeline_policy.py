"""Admin textbook-pipeline policy API regression tests.

GET/PUT /admin/textbook-pipeline：非 admin 403；admin 可读快照、更新并持久化；
非法值 422。策略文件与运行时均重定向到临时目录（不触生产存储根）。
"""
from __future__ import annotations
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


class TestAdminTextbookPipelinePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.identity import config as id_config
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        from tests.storage_sandbox import patch_all_storage_roots
        from app.core import textbook_pipeline
        self._pipeline = textbook_pipeline
        (root / "users").mkdir(parents=True, exist_ok=True)
        self._patches = patch_all_storage_roots(root)
        self._patches += [
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
            patch.object(textbook_pipeline, "_POLICY_FILE", root / "pipeline.json"),
        ]
        for p in self._patches[-3:]:
            p.start()
        self._old_runtime = textbook_pipeline._RUNTIME
        textbook_pipeline._RUNTIME = textbook_pipeline._Runtime()

        self.client = TestClient(create_app())
        self.admin = id_store.create_user(
            email="admin@example.com", username="",
            password_hash=hash_password("secret123"), role="admin")
        self.user = id_store.create_user(
            email="u1@example.com", username="",
            password_hash=hash_password("secret123"))
        self.admin_h = {"Authorization": f"Bearer {create_token(self.admin.id)}"}
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        self._pipeline._RUNTIME = self._old_runtime
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ[self._env_old] = self._env_old
        self.tmp.cleanup()

    def test_requires_admin(self):
        self.assertEqual(
            self.client.get("/api/v1/admin/textbook-pipeline",
                            headers=self.user_h).status_code, 403)
        self.assertEqual(
            self.client.put("/api/v1/admin/textbook-pipeline", json={},
                            headers=self.user_h).status_code, 403)

    def test_get_returns_snapshot(self):
        res = self.client.get("/api/v1/admin/textbook-pipeline",
                              headers=self.admin_h)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["scope"], "textbook_build_scheduling_only")
        self.assertIn(body["mode"], ("parallel", "legacy"))
        self.assertEqual(set(body["effective_limits"]), {"build", "volume", "llm"})

    def test_put_updates_and_persists(self):
        res = self.client.put("/api/v1/admin/textbook-pipeline", headers=self.admin_h,
                              json={"mode": "legacy", "build_concurrency": 3,
                                    "volume_concurrency": 2, "llm_concurrency": 6})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["mode"], "legacy")
        # legacy：配置值保留，生效值全部强制 1。
        self.assertEqual(body["effective_limits"], {"build": 1, "volume": 1, "llm": 1})
        persisted = self._pipeline._read_policy()
        self.assertEqual(persisted["mode"], "legacy")
        self.assertEqual(persisted["llm_concurrency"], 6)
        # 快照接口读到同一策略
        got = self.client.get("/api/v1/admin/textbook-pipeline",
                              headers=self.admin_h).json()
        self.assertEqual(got["mode"], "legacy")

    def test_put_rejects_out_of_range(self):
        res = self.client.put("/api/v1/admin/textbook-pipeline", headers=self.admin_h,
                              json={"mode": "parallel", "build_concurrency": 9,
                                    "volume_concurrency": 2, "llm_concurrency": 4})
        self.assertEqual(res.status_code, 422)
        res = self.client.put("/api/v1/admin/textbook-pipeline", headers=self.admin_h,
                              json={"mode": "turbo", "build_concurrency": 2,
                                    "volume_concurrency": 2, "llm_concurrency": 4})
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
