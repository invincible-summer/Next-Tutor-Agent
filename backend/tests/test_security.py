"""阶段B 安全回归测试：

  1. JWT 默认密钥 + AUTH_MODE=1：app 工厂拒绝启动；自定义密钥/游客模式放行。
  2. 会话归属：GET/DELETE/PATCH /chat/sessions/{id} 对他人会话 404
     （不泄露存在性）；无戳遗留会话归游客可见。
  3. POST /chat/stream 加载他人会话被拒，且绝不覆盖其 student_id 戳。
  4. POST /chat/upload 拒绝向他人会话挂载文件。
  5. /trace/{run_id}：无身份戳的 trace 在 AUTH_MODE=1 下要求登录。
  6. 简易限流：/auth/login、/auth/register 超限返回 429，X-Forwarded-For
     首段区分客户端桶。

mock/隔离方式与 test_assessment_identity.py / test_delete_account.py 一致：
临时目录接管持久化路径 + patch 非默认 JWT 密钥（AUTH_MODE=1 启动要求）。
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
from app.core import ratelimit  # noqa: E402
from app.core import session as session_mod  # noqa: E402
from app.core import trash as trash_mod  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402

_TEST_SECRET = "test-secret-not-default"


def _write_session(dirpath: Path, session_id: str, student_id: str | None) -> None:
    d: dict = {"session_id": session_id, "title": session_id, "messages": []}
    if student_id is not None:
        d["student_id"] = student_id
    (dirpath / f"{session_id}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")


class _AuthModeEnv:
    """Context helper: set AUTH_MODE and restore it afterwards."""

    def __init__(self, value: str | None) -> None:
        self._value = value
        self._old = os.environ.get("AUTH_MODE")

    def __enter__(self):
        if self._value is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._value
        return self

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._old


class TestStartupSecretGuard(unittest.TestCase):
    """默认密钥 + AUTH_MODE=1 -> create_app 抛 RuntimeError。"""

    def test_default_secret_auth_mode_1_refuses_startup(self):
        with _AuthModeEnv("1"), \
                patch.object(id_config, "AUTH_JWT_SECRET", id_config._DEFAULT_SECRET):
            with self.assertRaises(RuntimeError) as cm:
                create_app()
            self.assertIn("AUTH_JWT_SECRET", str(cm.exception))

    def test_custom_secret_auth_mode_1_boots(self):
        with _AuthModeEnv("1"), \
                patch.object(id_config, "AUTH_JWT_SECRET", _TEST_SECRET):
            self.assertIsNotNone(create_app())

    def test_default_secret_guest_mode_boots_with_warning(self):
        with _AuthModeEnv("0"), \
                patch.object(id_config, "AUTH_JWT_SECRET", id_config._DEFAULT_SECRET):
            self.assertIsNotNone(create_app())


class TestSessionOwnership(unittest.TestCase):
    """会话归属守卫：他人会话 404；遗留无戳会话归游客。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "users").mkdir()
        self._env = _AuthModeEnv("1")
        self._env.__enter__()
        self._patches = [
            patch.object(session_mod, "_SESSIONS_DIR", root),
            patch.object(id_config, "AUTH_JWT_SECRET", _TEST_SECRET),
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
            # 会话删除走软删除归档（trash），不 patch 会把 items/<uid>/ 写进
            # 生产回收站目录。
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
            patch.object(trash_mod, "_GLOBAL_POLICY", root / "trash" / "policy.json"),
        ]
        for p in self._patches:
            p.start()
        ratelimit.reset_rate_limits()
        self.client = TestClient(create_app())
        self.root = root
        self.user_a = id_store.create_user(
            email="a@example.com", username="",
            password_hash=hash_password("secret123"))
        self.user_b = id_store.create_user(
            email="b@example.com", username="",
            password_hash=hash_password("secret123"))
        self.headers_a = {"Authorization": f"Bearer {create_token(self.user_a.id)}"}
        self.headers_b = {"Authorization": f"Bearer {create_token(self.user_b.id)}"}

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._env.__exit__()
        ratelimit.reset_rate_limits()
        self._tmp.cleanup()

    # --- GET /chat/sessions/{id} ------------------------------------------

    def test_guest_get_foreign_session_404(self):
        """无 token（游客）访问他人已打戳会话 -> 404，不泄露存在性。"""
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.get("/api/v1/chat/sessions/s_a")
        self.assertEqual(r.status_code, 404)

    def test_other_user_get_session_404_owner_200(self):
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.get("/api/v1/chat/sessions/s_a", headers=self.headers_b)
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/api/v1/chat/sessions/s_a", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)

    def test_legacy_unstamped_session_visible_to_guest(self):
        _write_session(self.root, "s_legacy", None)
        r = self.client.get("/api/v1/chat/sessions/s_legacy")
        self.assertEqual(r.status_code, 200)

    # --- DELETE / PATCH ----------------------------------------------------

    def test_delete_foreign_session_404_and_untouched(self):
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.delete("/api/v1/chat/sessions/s_a", headers=self.headers_b)
        self.assertEqual(r.status_code, 404)
        self.assertTrue((self.root / "s_a.json").exists())
        r = self.client.delete("/api/v1/chat/sessions/s_a", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)

    def test_rename_foreign_session_404_and_untouched(self):
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.patch("/api/v1/chat/sessions/s_a",
                              json={"title": "劫持标题"}, headers=self.headers_b)
        self.assertEqual(r.status_code, 404)
        d = json.loads((self.root / "s_a.json").read_text(encoding="utf-8"))
        self.assertEqual(d.get("title"), "s_a")

    # --- POST /chat/stream -------------------------------------------------

    def test_stream_foreign_session_rejected_stamp_untouched(self):
        """stream 加载他人会话 -> 404，且 student_id 戳不被调用者覆盖。"""
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.post("/api/v1/chat/stream",
                             json={"message": "你好", "session_id": "s_a"},
                             headers=self.headers_b)
        self.assertEqual(r.status_code, 404)
        d = json.loads((self.root / "s_a.json").read_text(encoding="utf-8"))
        self.assertEqual(d.get("student_id"), self.user_a.id)

    # --- POST /chat/upload -------------------------------------------------

    def test_upload_to_foreign_session_404(self):
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.post(
            "/api/v1/chat/upload?session_id=s_a",
            files=[("files", ("note.txt", b"hello world", "text/plain"))],
            headers=self.headers_b)
        self.assertEqual(r.status_code, 404)

    # --- /trace/{run_id} ---------------------------------------------------

    def test_trace_requires_login_when_unclaimed(self):
        """无会话认领的 trace（无身份戳）：AUTH_MODE=1 下匿名 401，登录可看。"""
        trace_dir = self.root / "traces"
        trace_dir.mkdir()
        (trace_dir / "trace_run1.jsonl").write_text(
            '{"ts": 0, "run_id": "run1", "kind": "finish"}\n', encoding="utf-8")
        with patch("app.api.v1.trace.trace_dir_path", lambda: trace_dir):
            r = self.client.get("/api/v1/trace/run1")
            self.assertEqual(r.status_code, 401)
            r = self.client.get("/api/v1/trace/run1", headers=self.headers_a)
            self.assertEqual(r.status_code, 200)


class TestRateLimit(unittest.TestCase):
    """固定窗口限流：超限 429；X-Forwarded-For 首段分桶。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "users").mkdir()
        self._patches = [
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
        ]
        for p in self._patches:
            p.start()
        ratelimit.reset_rate_limits()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        ratelimit.reset_rate_limits()
        self._tmp.cleanup()

    def test_login_rate_limited_429(self):
        body = {"email": "nobody@example.com", "password": "wrong"}
        for _ in range(10):
            r = self.client.post("/api/v1/auth/login", json=body)
            self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/v1/auth/login", json=body)
        self.assertEqual(r.status_code, 429)

    def test_register_rate_limited_429(self):
        body = {"email": "dup@example.com", "password": "secret123"}
        codes = [self.client.post("/api/v1/auth/register", json=body).status_code
                 for _ in range(6)]
        self.assertEqual(codes[-1], 429)
        self.assertTrue(all(c in (200, 409) for c in codes[:-1]))

    def test_x_forwarded_for_separates_buckets(self):
        body = {"email": "nobody@example.com", "password": "wrong"}
        for _ in range(10):
            self.client.post("/api/v1/auth/login", json=body)
        # 默认来源已超限；带不同 XFF 首段的请求走独立桶，不被误伤。
        r = self.client.post("/api/v1/auth/login", json=body,
                             headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
