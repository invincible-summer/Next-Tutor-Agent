"""使用文档页（/docs）测试：全员读 + 管理员写 + 防御读 + 容量上限。

- GET /docs/content 公开（无认证头可读，默认引导文案兜底）；
- PUT /docs/content 非管理员 403、管理员 200 并持久化（updated_by 记录）；
- usage_docs.read_docs 损坏文件 → 引导默认；write_docs 超限 ValueError。
存储经 StorageSandbox 重定向（chat_history/settings/usage_docs.json）。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from tests.storage_sandbox import patch_all_storage_roots


def _setup(tmpdir: str):
    from app.identity import config as id_config
    from app.identity import store as id_store
    root = Path(tmpdir)
    (root / "users").mkdir()
    patches = patch_all_storage_roots(root)
    patches += [
        patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
        patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
    ]
    for p in patches[-2:]:
        p.start()
    return patches


class TestUsageDocsAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self._patches = _setup(self._tmp.name)
        self.client = TestClient(create_app())
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.admin = id_store.create_user(
            email="admin@example.com", username="管理员",
            password_hash=hash_password("secret123"), role="admin")
        self.user = id_store.create_user(
            email="u1@example.com", username="",
            password_hash=hash_password("secret123"))
        self.admin_h = {"Authorization": f"Bearer {create_token(self.admin.id)}"}
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        from tests.storage_sandbox import reset_shared_caches
        reset_shared_caches()
        self._tmp.cleanup()

    def test_get_is_public_and_bootstraps_default(self):
        r = self.client.get("/api/v1/docs/content")  # 无认证头
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("使用文档", data["markdown"])

    def test_put_requires_admin(self):
        body = {"markdown": "# 新文档"}
        self.assertNotEqual(
            self.client.put("/api/v1/docs/content", json=body).status_code, 200)
        self.assertEqual(
            self.client.put("/api/v1/docs/content", json=body,
                            headers=self.user_h).status_code, 403)

    def test_admin_put_persists_and_reads_back(self):
        r = self.client.put("/api/v1/docs/content",
                            json={"markdown": "# 使用指南 v2\n\n- 条目一"},
                            headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["updated_at"], 0)
        self.assertEqual(r.json()["updated_by"], "管理员")
        # 全员（含未登录）读回新内容
        r2 = self.client.get("/api/v1/docs/content")
        self.assertIn("使用指南 v2", r2.json()["markdown"])
        self.assertIn("updated_by", r2.json())

    def test_put_rejects_oversized_document(self):
        r = self.client.put("/api/v1/docs/content",
                            json={"markdown": "x" * 200_001},
                            headers=self.admin_h)
        self.assertEqual(r.status_code, 400)


class TestUsageDocsStorage(unittest.TestCase):
    """usage_docs 存储原语：防御读 + 尺寸上限（hermetic 临时目录）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        from app.core import usage_docs
        self.usage_docs = usage_docs
        self._patch = patch.object(usage_docs, "_DOCS_FILE",
                                   Path(self._tmp.name) / "usage_docs.json")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_missing_file_bootstraps(self):
        data = self.usage_docs.read_docs()
        self.assertIn("使用文档", data["markdown"])
        self.assertEqual(data["updated_at"], 0.0)

    def test_corrupt_file_bootstraps(self):
        self.usage_docs._DOCS_FILE.write_text("{not json", encoding="utf-8")
        data = self.usage_docs.read_docs()
        self.assertIn("使用文档", data["markdown"])

    def test_roundtrip_and_oversize(self):
        payload = self.usage_docs.write_docs("# 标题", updated_by="admin1")
        self.assertEqual(payload["markdown"], "# 标题")
        back = self.usage_docs.read_docs()
        self.assertEqual(back["markdown"], "# 标题")
        self.assertEqual(back["updated_by"], "admin1")
        with self.assertRaises(ValueError):
            self.usage_docs.write_docs("y" * 200_001)


if __name__ == "__main__":
    unittest.main()
