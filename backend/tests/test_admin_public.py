"""P6-B 测试：管理员引导/鉴权 + 公用教材库（上传/合并/写权限/图谱合并视图）。

- ensure_admin_account：env 引导创建/提升 admin；未配置 no-op。
- /admin/users：非 admin 403；admin 列表；注销账号（不能删 admin）。
- /textbooks/upload scope=public：非 admin 403；admin → 记录落 public 命名空间。
- 公用教材：所有账号 list/get 可见；非 admin PATCH/DELETE/rebuild 403；admin 可删（级联）。
- graph_for：公用图谱合并进其他用户视图（教材绑定图谱，公库共享）。
"""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def _setup(tmpdir: str):
    from app.identity import config as id_config
    from app.identity import store as id_store
    from tests.storage_sandbox import patch_all_storage_roots
    root = Path(tmpdir)
    (root / "users").mkdir()
    # 完整存储根隔离（tests/storage_sandbox.py 单一清单）：历史上本文件只
    # patch 了 library/knowledge/accounts，trash 与 students 的写路径直落
    # 生产目录（孤儿 trash bundle 与 usr_*.json 的来源之一）。
    patches = patch_all_storage_roots(root)
    patches += [
        patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
        patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
    ]
    for p in patches[-2:]:
        p.start()
    return patches


class TestAdminAndPublicTextbooks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.api.v1 import textbook as tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self._patches = _setup(self._tmp.name)
        self.client = TestClient(create_app())
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self._id_store = id_store
        self.admin = id_store.create_user(
            email="admin@example.com", username="",
            password_hash=hash_password("secret123"), role="admin")
        self.user = id_store.create_user(
            email="u1@example.com", username="",
            password_hash=hash_password("secret123"))
        self.admin_h = {"Authorization": f"Bearer {create_token(self.admin.id)}"}
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        self._spawn_patch.stop()
        for p in reversed(self._patches):
            p.stop()
        from app.agents.student_model import manager as sm_manager
        from app.core import vector_store
        sm_manager._CACHE.clear()
        vector_store._reset()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _upload(self, headers, scope=None, level="本科"):
        data = {"level": level}
        if scope:
            data["scope"] = scope
        return self.client.post(
            "/api/v1/textbooks/upload", data=data,
            files={"files": ("phys.txt", io.BytesIO("教材内容 力学 热学".encode() * 10), "text/plain")},
            headers=headers)

    # --- admin 引导与鉴权 ---

    def test_ensure_admin_account_creates_and_promotes(self):
        from app.identity.store import ensure_admin_account, get_by_email
        with patch.dict(os.environ, {"ADMIN_EMAIL": "boot@example.com",
                                     "ADMIN_PASSWORD": "pw123456"}):
            ensure_admin_account()
            u = get_by_email("boot@example.com")
            self.assertIsNotNone(u)
            self.assertEqual(u.role, "admin")
            # 二次调用幂等（不重复创建）
            ensure_admin_account()
            self.assertEqual(get_by_email("boot@example.com").id, u.id)

    def test_admin_users_list_and_delete(self):
        r = self.client.get("/api/v1/admin/users", headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        emails = {u["email"] for u in r.json()["users"]}
        self.assertIn("admin@example.com", emails)
        self.assertIn("u1@example.com", emails)
        # 注销普通账号
        r = self.client.delete(f"/api/v1/admin/users/{self.user.id}", headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self._id_store.get_by_id(self.user.id))

    def test_login_accepts_single_label_domain_email(self):
        # 回归：env 引导的管理员邮箱 administrator@administrator 无 TLD，
        # LoginRequest 曾因 EmailStr 校验在查库前就 422。
        from app.identity.security import hash_password
        self._id_store.create_user(
            email="administrator@administrator", username="",
            password_hash=hash_password("pw123456"), role="admin")
        r = self.client.post("/api/v1/auth/login", json={
            "email": "administrator@administrator", "password": "pw123456"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["role"], "admin")
        # 错误密码仍是 401 而非 422
        r = self.client.post("/api/v1/auth/login", json={
            "email": "administrator@administrator", "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_user_prefs_roundtrip_and_ocr_parallel_resolution(self):
        """账户偏好兼容读写；教材 OCR 实际并发由管理员策略统一控制。"""
        from app.api.v1.textbook import _effective_ocr_parallel
        # 显式 false 覆盖实例默认（PDF_OCR_CONCURRENCY 默认 5 → 并行开）
        r = self.client.put("/api/v1/user/profile",
                            json={"prefs": {"ocr_parallel": False}},
                            headers=self.user_h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["profile"]["prefs"]["ocr_parallel"])
        # 持久化往返：GET 与 store 重载都读到
        r = self.client.get("/api/v1/user/profile", headers=self.user_h)
        self.assertFalse(r.json()["profile"]["prefs"]["ocr_parallel"])
        reloaded = self._id_store.get_by_id(self.user.id)
        self.assertTrue(_effective_ocr_parallel(reloaded))
        # 教材 OCR 并发统一由管理员运行时策略控制，个人旧偏好仅兼容保存。
        self.assertTrue(_effective_ocr_parallel(self.admin))
        self.assertTrue(_effective_ocr_parallel(None))

    def test_admin_endpoints_forbidden_for_non_admin(self):
        self.assertEqual(self.client.get("/api/v1/admin/users", headers=self.user_h).status_code, 403)
        self.assertEqual(
            self.client.delete(f"/api/v1/admin/users/{self.user.id}", headers=self.user_h).status_code, 403)
        # 未登录 401
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 401)

    def test_admin_cannot_delete_admin(self):
        r = self.client.delete(f"/api/v1/admin/users/{self.admin.id}", headers=self.admin_h)
        self.assertEqual(r.status_code, 400)

    # --- 公用教材库 ---

    def test_batch_upgrade_endpoint_removed(self):
        """「批量无 OCR 升级」管理模块已删除：切块惰性自动重建 + 图谱缓存定向
        失效已覆盖其全部功能，保留端点只会重复劳动。端点须 404。"""
        response = self.client.post("/api/v1/admin/public-textbooks/rag-upgrade",
                                    headers=self.admin_h)
        self.assertEqual(response.status_code, 404)

    def test_public_upload_requires_admin(self):
        r = self._upload(self.user_h, scope="public")
        self.assertEqual(r.status_code, 403)
        r = self._upload(self.admin_h, scope="public")
        self.assertEqual(r.status_code, 200, r.text)
        rec = r.json()["results"][0]
        self.assertIn("id", rec)

    def test_public_visible_to_all_and_write_admin_only(self):
        tb_id = self._upload(self.admin_h, scope="public").json()["results"][0]["id"]
        # 普通用户可见（list + get）
        r = self.client.get("/api/v1/textbooks", headers=self.user_h)
        items = {t["id"]: t for t in r.json()["textbooks"]}
        self.assertIn(tb_id, items)
        self.assertEqual(items[tb_id]["scope"], "public")
        self.assertEqual(self.client.get(f"/api/v1/textbooks/{tb_id}", headers=self.user_h).status_code, 200)
        # 普通用户不可写
        self.assertEqual(self.client.patch(f"/api/v1/textbooks/{tb_id}", json={"title": "x"},
                                           headers=self.user_h).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/textbooks/{tb_id}/rebuild_graph",
                                          headers=self.user_h).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/textbooks/{tb_id}", headers=self.user_h).status_code, 403)
        # admin 可删（级联：list 中消失）
        self.assertEqual(self.client.delete(f"/api/v1/textbooks/{tb_id}", headers=self.admin_h).status_code, 200)
        r = self.client.get("/api/v1/textbooks", headers=self.user_h)
        self.assertNotIn(tb_id, {t["id"] for t in r.json()["textbooks"]})

    def test_private_textbook_not_leaked(self):
        # A 的私有教材 B 不可见（404 语义）
        tb_id = self._upload(self.admin_h).json()["results"][0]["id"]
        self.assertEqual(self.client.get(f"/api/v1/textbooks/{tb_id}", headers=self.user_h).status_code, 404)

    def test_public_graph_merges_into_other_students_view(self):
        # 直接向 public 命名空间落图谱，普通用户 graph_for 应可见
        from app.agents.knowledge import manager as kn_manager
        from app.agents.knowledge import store as kgs
        from app.core.textbook import PUBLIC_STUDENT_ID
        kgs.save_custom_graph(PUBLIC_STUDENT_ID, "tb-pub", {
            "topic": "公用物理", "topic_key": "tb-pub", "subject": "物理",
            "level": "高中", "source": "textbook:pf",
            "nodes": [{"id": "custom.tb-pub.c1", "name": "公用概念",
                       "subject": "物理", "level": "高中", "kind": "concept"}],
            "edges": [], "contents": []})
        kn_manager._INSTANCE = None
        svc = kn_manager.get_knowledge_service()
        g = svc.graph_for("some_random_student")
        self.assertIn("custom.tb-pub.c1", g.nodes)
        # 缓存失效：删除公用图谱后其他用户视图更新
        kgs.delete_custom_graph(PUBLIC_STUDENT_ID, "tb-pub")
        g2 = svc.graph_for("some_random_student")
        self.assertNotIn("custom.tb-pub.c1", g2.nodes)


class TestGraphIsolation(unittest.TestCase):
    """P6-E 隔离回归：图谱结构/mastery 变色按账号隔离，仅公用教材共享。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self._patches = _setup(self._tmp.name)
        # students 目录也要隔离（mastery overlay 测试）
        from app.agents.student_model import store as sm_store
        from app.agents.knowledge import manager as kn_manager
        self._sm_patch = patch.object(sm_store, "_STUDENTS_DIR",
                                      Path(self._tmp.name) / "students")
        self._sm_patch.start()
        kn_manager._INSTANCE = None
        self.client = TestClient(create_app())
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self._sm_store = sm_store
        self._kn_manager = kn_manager
        self.user_a = id_store.create_user(
            email="a@example.com", username="",
            password_hash=hash_password("secret123"))
        self.user_b = id_store.create_user(
            email="b@example.com", username="",
            password_hash=hash_password("secret123"))
        self.ha = {"Authorization": f"Bearer {create_token(self.user_a.id)}"}
        self.hb = {"Authorization": f"Bearer {create_token(self.user_b.id)}"}

    def tearDown(self):
        self._sm_patch.stop()
        for p in reversed(self._patches):
            p.stop()
        self._kn_manager._INSTANCE = None
        from app.agents.student_model import manager as sm_manager
        from app.core import vector_store
        sm_manager._CACHE.clear()
        vector_store._reset()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _seed_graph(self, sid: str, key: str, cid: str, name: str):
        from app.agents.knowledge import store as kgs
        kgs.save_custom_graph(sid, key, {
            "topic": name, "topic_key": key, "subject": "物理", "level": "高中",
            "source": "textbook:x",
            "nodes": [{"id": cid, "name": name, "subject": "物理",
                       "level": "高中", "kind": "concept"}],
            "edges": [], "contents": []})
        self._kn_manager._INSTANCE = None

    def test_graph_structure_and_mastery_isolated(self):
        from app.core.textbook import PUBLIC_STUDENT_ID
        self._seed_graph(self.user_a.id, "tb-a", "custom.tb-a.c1", "A独有概念")
        self._seed_graph(self.user_b.id, "tb-b", "custom.tb-b.c1", "B独有概念")
        self._seed_graph(PUBLIC_STUDENT_ID, "tb-pub", "custom.tb-pub.c1", "公用概念")
        # A 的 mastery（公用概念 0.9；B 无记录）
        (Path(self._tmp.name) / "students").mkdir(exist_ok=True)
        import json as _json
        (Path(self._tmp.name) / "students" / f"{self.user_a.id}.json").write_text(
            _json.dumps({"profile": {}, "mastery": {
                "custom.tb-pub.c1": {"skill_id": "custom.tb-pub.c1", "p_known": 0.9,
                                      "attempts": 3, "correct": 3,
                                      "last_review": 1.0, "mistakes": [],
                                      "params": {}}}, "memory": {}},
                ensure_ascii=False), encoding="utf-8")

        ga = self.client.get("/api/v1/knowledge/graph", headers=self.ha).json()
        ids_a = {n["id"] for n in ga["nodes"]}
        self.assertIn("custom.tb-a.c1", ids_a)      # 自有可见
        self.assertIn("custom.tb-pub.c1", ids_a)    # 公用可见
        self.assertNotIn("custom.tb-b.c1", ids_a)   # 他人不可见
        pub_a = next(n for n in ga["nodes"] if n["id"] == "custom.tb-pub.c1")
        self.assertIsNotNone(pub_a["mastery"])      # A 有变色

        gb = self.client.get("/api/v1/knowledge/graph", headers=self.hb).json()
        ids_b = {n["id"] for n in gb["nodes"]}
        self.assertIn("custom.tb-b.c1", ids_b)
        self.assertIn("custom.tb-pub.c1", ids_b)
        self.assertNotIn("custom.tb-a.c1", ids_b)
        pub_b = next(n for n in gb["nodes"] if n["id"] == "custom.tb-pub.c1")
        self.assertIsNone(pub_b["mastery"])         # B 无变色——同一公用图谱，变色各自独立


if __name__ == "__main__":
    unittest.main()
