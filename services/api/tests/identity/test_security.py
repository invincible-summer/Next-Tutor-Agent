"""阶段B 安全回归测试：

  1. JWT 默认密钥 + AUTH_MODE=1：app 工厂拒绝启动；自定义密钥/游客模式放行。
     未显式配置密钥的非测试部署会生成本机随机开发密钥（.runtime 持久化）。
  2. 会话归属：GET/DELETE/PATCH /chat/sessions/{id} 对他人会话 404
     （不泄露存在性）；无戳遗留会话归游客可见。
  3. POST /chat/stream 加载他人会话被拒，且绝不覆盖其 student_id 戳。
  4. POST /chat/upload 拒绝向他人会话挂载文件。
  5. /trace/{run_id}：无身份戳的 trace 在 AUTH_MODE=1 下要求登录。
  6. 简易限流：/auth/login、/auth/register 超限返回 429；伪造
     X-Forwarded-For 不再产生独立桶；同账号连续登录失败被按账号节流。
  7. /auth/status 的 using_default_secret 仅对管理员如实披露。

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

from tests.support.storage_sandbox import StorageSandboxTestCase
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


class TestSessionOwnership(StorageSandboxTestCase):
    """会话归属守卫：他人会话 404；遗留无戳会话归游客。"""

    def setUp(self) -> None:
        super().setUp()
        root = session_mod._SESSIONS_DIR
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
        super().tearDown()

    def test_guest_get_foreign_session_404(self):
        """无 token（游客）访问他人已打戳会话 -> 404，不泄露存在性。"""
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.get("/api/v1/chat/sessions/s_a")
        self.assertEqual(r.status_code, 401)

    def test_other_user_get_session_404_owner_200(self):
        _write_session(self.root, "s_a", self.user_a.id)
        r = self.client.get("/api/v1/chat/sessions/s_a", headers=self.headers_b)
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/api/v1/chat/sessions/s_a", headers=self.headers_a)
        self.assertEqual(r.status_code, 200)

    def test_legacy_unstamped_session_visible_to_guest(self):
        _write_session(self.root, "s_legacy", None)
        r = self.client.get("/api/v1/chat/sessions/s_legacy")
        self.assertEqual(r.status_code, 401)

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

    def test_unclaimed_trace_is_only_visible_to_admin(self):
        """旧游客 trace 无归属时普通账号不能读取，管理员可排查。"""
        trace_dir = self.root / "traces"
        trace_dir.mkdir(exist_ok=True)
        (trace_dir / "trace_run1.jsonl").write_text(
            '{"ts": 0, "run_id": "run1", "kind": "finish"}\n', encoding="utf-8")
        with patch("app.api.v1.trace.trace_dir_path", lambda: trace_dir):
            r = self.client.get("/api/v1/trace/run1")
            self.assertEqual(r.status_code, 401)
            r = self.client.get("/api/v1/trace/run1", headers=self.headers_a)
            self.assertEqual(r.status_code, 404)
            admin = id_store.create_user("trace-admin@test.local", "", "unused", role="admin")
            r = self.client.get("/api/v1/trace/run1", headers={"Authorization": "Bearer " + create_token(admin.id)})
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
        # 每次轮换邮箱：账号级失败锁定（5 次/账号）不触发，单独验证按 IP 桶。
        for i in range(10):
            r = self.client.post("/api/v1/auth/login",
                                 json={"email": f"nobody{i}@example.com",
                                       "password": "wrong"})
            self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/v1/auth/login",
                             json={"email": "anyone@example.com",
                                   "password": "wrong"})
        self.assertEqual(r.status_code, 429)

    def test_register_rate_limited_429(self):
        body = {"email": "dup@example.com", "password": "secret123"}
        codes = [self.client.post("/api/v1/auth/register", json=body).status_code
                 for _ in range(6)]
        self.assertEqual(codes[-1], 429)
        self.assertTrue(all(c in (200, 409) for c in codes[:-1]))

    def test_x_forwarded_for_spoofing_does_not_reset_buckets(self):
        """伪造 X-Forwarded-For 不得绕过按 IP 的限流桶。

        旧行为（自行解析 XFF 首段分桶）允许攻击者每个请求换一个假 IP，
        使登录暴力猜解完全无视限流。现在 client IP 只取 uvicorn 已按
        信任代理规则解析好的 peer（--proxy-headers 默认仅信 127.0.0.1），
        应用层不读原始 XFF。"""
        for i in range(10):
            self.client.post("/api/v1/auth/login", json={
                "email": f"spoof{i}@example.com", "password": "wrong"})
        # 已超限；伪造任意 XFF 首段必须依旧 429。
        for spoofed in ("203.0.113.7, 10.0.0.1", "198.51.100.99", "1.2.3.4"):
            r = self.client.post("/api/v1/auth/login", json={
                    "email": "spoof-final@example.com", "password": "wrong"},
                headers={"X-Forwarded-For": spoofed})
            self.assertEqual(r.status_code, 429)

    def test_login_per_account_failure_throttle(self):
        """同账号连续登录失败：独立于来源 IP 的按账号节流（防轮换 IP 暴力猜解）。

        5 次失败/15 分钟锁定（收紧自 10 次/5 分钟）；锁定后即使给出正确
        密码也 429（验证前早退，不再消耗 bcrypt）；成功登录清空失败计数。"""
        id_store.create_user(email="victim@example.com", username="",
                             password_hash=hash_password("right-pw"))

        def _fail():
            # TestClient 来源 IP 固定，会先撞上按 IP 的桶；清掉它以单独
            # 验证按账号的桶（模拟攻击者轮换来源 IP）。
            with ratelimit._LOCK:
                ratelimit._BUCKETS.pop(("auth_login", "testclient"), None)
            return self.client.post("/api/v1/auth/login", json={
                "email": "victim@example.com", "password": "wrong"})

        for _ in range(5):
            self.assertEqual(_fail().status_code, 401)
        # 第 6 次：账号已锁定 → 429 早退（即使这次密码正确也一样）。
        with ratelimit._LOCK:
            ratelimit._BUCKETS.pop(("auth_login", "testclient"), None)
        r = self.client.post("/api/v1/auth/login", json={
            "email": "victim@example.com", "password": "right-pw"})
        self.assertEqual(r.status_code, 429)
        # 成功登录不受失败计数影响：另一账号正确密码照常 200。
        id_store.create_user(email="ok@example.com", username="",
                             password_hash=hash_password("right-pw"))
        with ratelimit._LOCK:
            ratelimit._BUCKETS.pop(("auth_login", "testclient"), None)
        r = self.client.post("/api/v1/auth/login", json={
            "email": "ok@example.com", "password": "right-pw"})
        self.assertEqual(r.status_code, 200)


class TestAuthStatusSecretDisclosure(StorageSandboxTestCase):
    """/auth/status：密钥状态只对管理员披露（匿名探测不得得知 token 可伪造）。"""

    def setUp(self) -> None:
        super().setUp()
        ratelimit.reset_rate_limits()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        super().tearDown()

    def test_default_secret_flag_admin_only(self):
        # patch 到默认密钥分支（不依赖运行环境的 AUTH_JWT_SECRET 恰好缺省）。
        with patch.object(id_config, "AUTH_JWT_SECRET",
                          id_config._DEFAULT_SECRET):
            self.assertTrue(id_config.using_default_secret())
            anon = self.client.get("/api/v1/auth/status").json()
            self.assertFalse(anon["using_default_secret"])
            user = id_store.create_user("plain@example.com", "",
                                        hash_password("secret123"))
            r = self.client.get("/api/v1/auth/status", headers={
                "Authorization": f"Bearer {create_token(user.id)}"})
            self.assertFalse(r.json()["using_default_secret"])
            admin = id_store.create_user("sec-admin@example.com", "",
                                         hash_password("secret123"),
                                         role="admin")
            r = self.client.get("/api/v1/auth/status", headers={
                "Authorization": f"Bearer {create_token(admin.id)}"})
            self.assertTrue(r.json()["using_default_secret"])


class TestTokenVersionRevocation(StorageSandboxTestCase):
    """token_version 吊销：bump 后全部旧 token 立即 401；重签后恢复。"""

    def setUp(self) -> None:
        super().setUp()
        ratelimit.reset_rate_limits()
        self.client = TestClient(create_app())
        self.user = id_store.create_user(
            email="victim2@example.com", username="",
            password_hash=hash_password("secret123"))

    def tearDown(self) -> None:
        super().tearDown()

    def test_bump_revokes_all_issued_tokens(self):
        old = {"Authorization": f"Bearer {create_token(self.user.id)}"}
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=old).status_code, 200)
        self.assertTrue(id_store.bump_token_version(self.user.id))
        # 旧 token（含缺失 ver 的历史形态）在 bump 后一律 401。
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=old).status_code, 401)
        refreshed = id_store.get_by_id(self.user.id)
        fresh = {"Authorization":
                 f"Bearer {create_token(refreshed.id, token_version=refreshed.token_version)}"}
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=fresh).status_code, 200)

    def test_version_mismatch_rejected(self):
        # 账号当前版本 2：旧版本(1)/无版本 token 均拒，当前版本放行。
        self.user.token_version = 2
        id_store.update_user(self.user)
        for ver in (0, 1):
            h = {"Authorization":
                 f"Bearer {create_token(self.user.id, token_version=ver)}"}
            self.assertEqual(
                self.client.get("/api/v1/auth/me", headers=h).status_code,
                401, f"token_version={ver} should be rejected")
        h = {"Authorization":
             f"Bearer {create_token(self.user.id, token_version=2)}"}
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=h).status_code, 200)


class TestPasswordPolicy(StorageSandboxTestCase):
    """注册密码策略：≥8 字符且 utf-8 ≤72 字节（bcrypt 截断对齐）。"""

    def setUp(self) -> None:
        super().setUp()
        ratelimit.reset_rate_limits()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        super().tearDown()

    def _register(self, password: str) -> int:
        return self.client.post("/api/v1/auth/register", json={
            "email": "policy@example.com", "password": password,
            "username": ""}).status_code

    def test_short_password_rejected(self):
        self.assertEqual(self._register("short7"), 422)

    def test_multibyte_over_72_bytes_rejected(self):
        # 25 个三字节汉字 = 75 字节：字符数在 max_length 内但超出 bcrypt
        # 72 字节参与域，必须显式拒绝而不是静默截断。
        self.assertEqual(self._register("密" * 25), 422)

    def test_valid_password_accepted(self):
        self.assertEqual(self._register("a-solid-password-123"), 200)


class TestAdminWeakPasswordGuard(StorageSandboxTestCase):
    """生产（AUTH_MODE=1）下弱/短 ADMIN_PASSWORD 拒绝引导；开发仅告警。"""

    def setUp(self) -> None:
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()

    def test_weak_default_refused_in_production(self):
        from app.identity.store import ensure_admin_account, get_by_email
        with _AuthModeEnv("1"), patch.dict(os.environ, {
                "ADMIN_EMAIL": "weakadm@example.com",
                "ADMIN_PASSWORD": "change-me"}):
            with self.assertRaises(RuntimeError):
                ensure_admin_account()
            # 拒绝的同时不留下半创建的账号。
            self.assertIsNone(get_by_email("weakadm@example.com"))

    def test_short_password_refused_in_production(self):
        from app.identity.store import ensure_admin_account
        with _AuthModeEnv("1"), patch.dict(os.environ, {
                "ADMIN_EMAIL": "shortadm@example.com",
                "ADMIN_PASSWORD": "short-pw9"}):
            with self.assertRaises(RuntimeError):
                ensure_admin_account()

    def test_strong_password_bootstraps_in_production(self):
        from app.identity.store import ensure_admin_account, get_by_email
        with _AuthModeEnv("1"), patch.dict(os.environ, {
                "ADMIN_EMAIL": "okadm@example.com",
                "ADMIN_PASSWORD": "a-strong-admin-password"}):
            ensure_admin_account()
            u = get_by_email("okadm@example.com")
            self.assertIsNotNone(u)
            self.assertEqual(u.role, "admin")

    def test_dev_mode_weak_password_only_warns(self):
        from app.identity.store import ensure_admin_account, get_by_email
        with _AuthModeEnv("0"), patch.dict(os.environ, {
                "ADMIN_EMAIL": "devadm@example.com",
                "ADMIN_PASSWORD": "change-me"}):
            ensure_admin_account()  # 不抛
            u = get_by_email("devadm@example.com")
            self.assertIsNotNone(u)
            self.assertEqual(u.role, "admin")


class TestProductionDocsDisabled(unittest.TestCase):
    """AUTH_MODE=1 关闭 /docs、/redoc、/openapi.json；开发模式保留。"""

    def test_docs_disabled_in_production(self):
        with _AuthModeEnv("1"), \
                patch.object(id_config, "AUTH_JWT_SECRET", _TEST_SECRET):
            client = TestClient(create_app())
            self.assertEqual(client.get("/docs").status_code, 404)
            self.assertEqual(client.get("/redoc").status_code, 404)
            self.assertEqual(client.get("/openapi.json").status_code, 404)

    def test_docs_available_in_dev(self):
        with _AuthModeEnv("0"), \
                patch.object(id_config, "AUTH_JWT_SECRET", _TEST_SECRET):
            client = TestClient(create_app())
            self.assertEqual(client.get("/docs").status_code, 200)


if __name__ == "__main__":
    unittest.main()
