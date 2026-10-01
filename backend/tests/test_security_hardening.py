"""安全加固回归（本轮审计修复项）：

  1. 开发部署的 JWT 密钥自动生成（.runtime/auth_jwt_secret 持久化、幂等）。
  2. ensure_admin_account 抢注防护：密码不匹配的既有账号绝不提权。
  3. trace 读路径 run_id 消毒（与写入端 _safe 一致）。
  4. KnowledgeStore / Library 的 file_id / orig_ext 白名单。
  5. 助手语音 job_id 白名单（非法 id 一律 404 语义）。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class TestGeneratedDevSecret(unittest.TestCase):
    """未显式配置 AUTH_JWT_SECRET 的本地部署：生成本机随机密钥并持久化。"""

    def _secret_file(self) -> Path:
        import shutil, tempfile
        d = tempfile.mkdtemp(prefix="edu-secret-test-")
        self.addCleanup(shutil.rmtree, d, True)
        return Path(d) / "auth_jwt_secret"

    def test_secret_generated_once_and_reused(self):
        from app.identity import config as id_config
        with patch.object(id_config, "_GENERATED_SECRET_FILE",
                          self._secret_file()):
            first = id_config._load_or_generate_secret()
            self.assertTrue(first)
            self.assertNotEqual(first, id_config._DEFAULT_SECRET)
            self.assertTrue(id_config._GENERATED_SECRET_FILE.is_file())
            # 二次调用读同一文件（进程重启语义）：值稳定。
            self.assertEqual(id_config._load_or_generate_secret(), first)

    def test_existing_secret_file_is_honored(self):
        from app.identity import config as id_config
        target = self._secret_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pre-configured-dev-secret\n", encoding="utf-8")
        with patch.object(id_config, "_GENERATED_SECRET_FILE", target):
            self.assertEqual(id_config._load_or_generate_secret(),
                             "pre-configured-dev-secret")


class TestAdminBootstrapSquatProtection(StorageSandboxTestCase):
    """ADMIN_EMAIL 被抢注（role=student）时：密码不匹配绝不提权。"""

    def setUp(self) -> None:
        super().setUp()
        from app.identity import store as id_store
        from app.identity.security import hash_password
        self._store = id_store
        self.user = id_store.create_user(
            email="boot@example.com", username="",
            password_hash=hash_password("user-chosen-pw"))

    def test_wrong_password_never_promotes(self):
        from app.identity.store import ensure_admin_account, get_by_email
        with patch.dict(os.environ, {"ADMIN_EMAIL": "boot@example.com",
                                     "ADMIN_PASSWORD": "attacker-guess"}):
            ensure_admin_account()
        self.assertEqual(get_by_email("boot@example.com").role, "student")

    def test_matching_password_promotes(self):
        from app.identity.store import ensure_admin_account, get_by_email
        with patch.dict(os.environ, {"ADMIN_EMAIL": "boot@example.com",
                                     "ADMIN_PASSWORD": "user-chosen-pw"}):
            ensure_admin_account()
        self.assertEqual(get_by_email("boot@example.com").role, "admin")


class TestTracePathSanitization(unittest.TestCase):
    def test_run_id_traversal_is_stripped(self):
        from app.api.v1 import trace as trace_api
        from app.core.config import trace_dir_path
        for evil in ("../evil", "..\\evil", "a/../b", ".."):
            p = trace_api._trace_path(evil)
            self.assertEqual(p.parent, trace_dir_path())
        self.assertEqual(trace_api._trace_path("run1").name,
                         "trace_run1.jsonl")


class TestKnowledgeStoreIdGuards(StorageSandboxTestCase):
    def test_evil_file_id_rejected(self):
        from app.core.knowledge_store import KnowledgeStore
        ks = KnowledgeStore(memory_only=True)
        for evil in ("../../evil", "a/b", "..", "x\\y", ""):
            with self.assertRaises(ValueError):
                ks.add_file(evil, "f.txt", "content")
        with self.assertRaises(ValueError):
            ks.add_file("ok123", "f.pdf", "content",
                        raw=b"x", orig_ext="/../../evil")
        # 合法 id（服务端 uuid hex 形态）不受影响。
        meta = ks.add_file("ok123", "f.txt", "content")
        self.assertEqual(meta["id"], "ok123")

    def test_evil_remove_returns_false(self):
        from app.core.knowledge_store import KnowledgeStore
        ks = KnowledgeStore(memory_only=True)
        self.assertFalse(ks.remove_file("../../evil"))
        self.assertFalse(ks.remove_file("nope"))


class TestLibraryIdGuards(StorageSandboxTestCase):
    def test_evil_file_id_rejected(self):
        from app.core.library import Library
        lib = Library("usr_libguard")
        for evil in ("../../evil", "a/b", ".."):
            with self.assertRaises(ValueError):
                lib.add_file("", "f.txt", "content", file_id=evil)
        meta = lib.add_file("", "f.txt", "content", file_id="abc123def456")
        self.assertEqual(meta["id"], "abc123def456")
        self.assertTrue(lib.remove_file("abc123def456"))


class TestVoiceJobIdGuard(unittest.TestCase):
    def test_illegal_job_ids_read_as_missing(self):
        from app.agents.site_assistant import voice
        # 非法 id（路径片段）在拼路径前即被拒绝，读作"任务不存在"。
        self.assertIsNone(voice.load_job("usr_x", "../../evil"))
        self.assertIsNone(voice.load_job("usr_x", "a/b"))
        with self.assertRaises(ValueError):
            voice._job_path("usr_x", "..")
        # 合法格式但不存在 → 同样 None（无差异泄露）。
        self.assertIsNone(voice.load_job("usr_x", "aj_0123456789abcdef"))


if __name__ == "__main__":
    unittest.main()
