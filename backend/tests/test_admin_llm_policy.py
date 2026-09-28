"""Admin LLM runtime-policy API regression tests.

GET/PUT /admin/llm-policy：非 admin 403；admin 可读快照（含边界）、更新并
持久化 + 进程内热生效；越界/非法枚举 422；输出预算不得超过窗口一半。
策略文件与运行时均重定向到临时目录（不触生产存储根）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient

from app.main import create_app


class TestAdminLLMPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.identity import config as id_config
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        from tests.storage_sandbox import patch_all_storage_roots
        from app.core import llm_policy
        self._policy = llm_policy
        (root / "users").mkdir(parents=True, exist_ok=True)
        self._patches = patch_all_storage_roots(root)
        self._patches += [
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
        ]
        for p in self._patches[-2:]:
            p.start()
        llm_policy.reset_policy_cache()

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
        for p in reversed(self._patches):
            p.stop()
        self._policy.reset_policy_cache()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ[self._env_old] = self._env_old
        self.tmp.cleanup()

    def test_requires_admin(self):
        self.assertEqual(
            self.client.get("/api/v1/admin/llm-policy",
                            headers=self.user_h).status_code, 403)
        self.assertEqual(
            self.client.put("/api/v1/admin/llm-policy", json={},
                            headers=self.user_h).status_code, 403)

    def test_get_returns_snapshot_with_bounds(self):
        res = self.client.get("/api/v1/admin/llm-policy",
                              headers=self.admin_h)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["scope"], "llm_runtime_params")
        for key in ("context_window", "max_output_tokens", "temperature",
                    "agent_max_steps", "llm_max_tokens", "quiz_verify_mode"):
            self.assertIn(key, body)
        self.assertEqual(body["min_context_window"], 8192)
        self.assertGreaterEqual(body["context_window"], body["min_context_window"])
        self.assertIn("critic", body["verify_modes"])

    def test_put_updates_persists_and_hot_applies(self):
        res = self.client.put("/api/v1/admin/llm-policy", headers=self.admin_h,
                              json={"context_window": 131072,
                                    "max_output_tokens": 16000,
                                    "temperature": 0.5,
                                    "agent_max_steps": 8,
                                    "llm_max_tokens": 6000,
                                    "quiz_verify_mode": "basic"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["context_window"], 131072)
        self.assertEqual(body["quiz_verify_mode"], "basic")
        # 持久化到策略文件
        persisted = self._policy._read_policy()
        self.assertEqual(persisted["context_window"], 131072)
        self.assertEqual(persisted["temperature"], 0.5)
        # 进程内热生效：getter 立即返回新值（无需重启）
        self.assertEqual(self._policy.context_window(), 131072)
        self.assertEqual(self._policy.temperature(), 0.5)
        self.assertEqual(self._policy.agent_max_steps(), 8)
        self.assertEqual(self._policy.llm_max_tokens(), 6000)
        self.assertEqual(self._policy.quiz_verify_mode(), "basic")

    def test_put_rejects_out_of_range_and_bad_enum(self):
        base = {"context_window": 131072, "max_output_tokens": 16000,
                "temperature": 0.5, "agent_max_steps": 8,
                "llm_max_tokens": 6000, "quiz_verify_mode": "critic"}
        for field, bad in (("context_window", 100), ("temperature", 5.0),
                           ("agent_max_steps", 0), ("llm_max_tokens", 10),
                           ("quiz_verify_mode", "loose")):
            payload = dict(base)
            payload[field] = bad
            res = self.client.put("/api/v1/admin/llm-policy",
                                  headers=self.admin_h, json=payload)
            self.assertEqual(res.status_code, 422, f"{field}={bad!r}")

    def test_output_budget_cannot_swallow_window(self):
        # 预算自洽：输出上限被钳到窗口一半以内。
        res = self.client.put("/api/v1/admin/llm-policy", headers=self.admin_h,
                              json={"context_window": 8192,
                                    "max_output_tokens": 200000,
                                    "temperature": 0.3,
                                    "agent_max_steps": 6,
                                    "llm_max_tokens": 4000,
                                    "quiz_verify_mode": "critic"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["max_output_tokens"], 4096)

    def test_bootstrap_reads_env_backed_settings_when_file_missing(self):
        # 策略文件缺失时以 env-backed settings 为初值（老部署 .env 兼容）。
        fake = SimpleNamespace(
            llm_context_window=32768, llm_max_output_tokens=8192,
            llm_temperature=0.2, agent_max_steps=4, llm_max_tokens=3000,
            quiz_verify_mode="off")
        with patch.object(self._policy, "settings", fake):
            self._policy.reset_policy_cache()
            self.assertEqual(self._policy.context_window(), 32768)
            self.assertEqual(self._policy.quiz_verify_mode(), "off")
            self.assertEqual(self._policy.temperature(), 0.2)
        self._policy.reset_policy_cache()


if __name__ == "__main__":
    unittest.main()
