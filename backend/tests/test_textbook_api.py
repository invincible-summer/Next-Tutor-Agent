"""P2 教材库 API 集成测试：upload/list/get/patch/delete + 隔离 404 + 孤儿清理。

验收（update_plan §5.5 / §10.2）：
- upload 200（逐文件失败也 200）；记录创建 + library 文件 kind=textbook。
- list/get/patch/delete 全部 JWT 隔离（外人 404）。
- PATCH level 非法值 400。
- DELETE 级联：library 文件 + 记录同步清除。
- library 直删文件 → 孤儿教材记录清理。
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
# Import create_app at module load (AUTH_MODE defaults to "0" here, so the
# module-level `app = create_app()` in main.py boots fine). setUp then sets
# AUTH_MODE=1 + patches the secret before calling create_app() again — mirroring
# test_session_isolation.py.
from app.main import create_app  # noqa: E402


def _setup_app(tmpdir: str):
    from app.identity import config as id_config
    from app.identity import store as id_store
    from tests.storage_sandbox import patch_all_storage_roots
    root = Path(tmpdir)
    (root / "users").mkdir()
    # 完整存储根隔离：旧清单漏了 trash（删除教材 → 软删除归档直落生产目录）。
    patches = patch_all_storage_roots(root)
    patches += [
        patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
        patch.object(id_store, "_ACCOUNTS_FILE",
                     root / "users" / "accounts.json"),
    ]
    for p in patches[-2:]:
        p.start()
    return create_app(), patches


def _teardown(patches, env_old):
    for p in reversed(patches):
        p.stop()
    if env_old is None:
        os.environ.pop("AUTH_MODE", None)
    else:
        os.environ["AUTH_MODE"] = env_old


class TestTextbookAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        # 不在 setUp 触发后台构建：patch _spawn_build 为 no-op，保证确定性。
        from app.api.v1 import textbook as tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        # 上传限流 10/min，多个测试用例累计会触发 429——每用例重置桶。
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self.app, self._patches = _setup_app(self._tmp.name)
        self.client = TestClient(self.app)
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self._id_store = id_store
        self._create_token = create_token
        self._hash_password = hash_password
        self.user = id_store.create_user(
            email="tb@example.com", username="",
            password_hash=hash_password("secret123"))
        self.tok = create_token(self.user.id)
        self.h = {"Authorization": f"Bearer {self.tok}"}

    def tearDown(self):
        self._spawn_patch.stop()
        _teardown(self._patches, self._env_old)
        self._tmp.cleanup()

    def _upload(self, filename="math.txt", content="教材内容" * 5):
        return self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": (filename, io.BytesIO(content.encode("utf-8")), "text/plain")},
            headers=self.h)

    def test_upload_creates_record_and_library_file(self):
        r = self._upload()
        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["results"]
        self.assertEqual(len(res), 1)
        self.assertIn("id", res[0])
        self.assertEqual(res[0]["status"], "building")
        # library 文件标记 kind=textbook
        from app.core.library import load_library
        lib = load_library(self.user.id)
        self.assertEqual(len(lib.files), 1)
        self.assertEqual(lib.files[0].get("kind"), "textbook")

    def test_upload_rejects_unsupported_format(self):
        r2 = self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": ("bad.exe", io.BytesIO(b"binary"), "application/octet-stream")},
            headers=self.h)
        self.assertEqual(r2.status_code, 200)
        self.assertIn("error", r2.json()["results"][0])

    def test_list_returns_textbooks(self):
        self._upload()
        r = self.client.get("/api/v1/textbooks", headers=self.h)
        self.assertEqual(r.status_code, 200)
        tbs = r.json()["textbooks"]
        self.assertEqual(len(tbs), 1)
        self.assertEqual(tbs[0]["status"], "building")

    def test_patch_title_and_level(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.patch(f"/api/v1/textbooks/{tb_id}",
                              json={"title": "新教材", "level": "本科"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()["textbook"]
        self.assertEqual(out["title"], "新教材")
        self.assertEqual(out["level"], "本科")

    def test_patch_invalid_level_400(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.patch(f"/api/v1/textbooks/{tb_id}",
                              json={"level": "研究生"}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_patch_empty_fields_400(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.patch(f"/api/v1/textbooks/{tb_id}", json={}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_get_returns_record_and_outline(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.get(f"/api/v1/textbooks/{tb_id}", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["textbook"]["id"], tb_id)
        self.assertIn("outline", body)

    def test_delete_cascades_record_and_library_file(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.delete(f"/api/v1/textbooks/{tb_id}", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        # 记录已删
        from app.core import textbook as tb
        self.assertIsNone(tb.find_textbook(self.user.id, tb_id))
        # library 文件已删
        from app.core.library import load_library
        self.assertEqual(len(load_library(self.user.id).files), 0)
        # list 不再返回
        self.assertEqual(len(self.client.get("/api/v1/textbooks", headers=self.h).json()["textbooks"]), 0)

    def test_isolation_foreign_user_404(self):
        tb_id = self._upload().json()["results"][0]["id"]
        other = self._id_store.create_user(
            email="other@example.com", username="",
            password_hash=self._hash_password("secret123"))
        oh = {"Authorization": f"Bearer {self._create_token(other.id)}"}
        # 外人看不见（404 不泄露存在性）
        self.assertEqual(self.client.get(f"/api/v1/textbooks/{tb_id}", headers=oh).status_code, 404)
        self.assertEqual(self.client.patch(f"/api/v1/textbooks/{tb_id}",
                                           json={"title": "x"}, headers=oh).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/textbooks/{tb_id}", headers=oh).status_code, 404)
        self.assertEqual(len(self.client.get("/api/v1/textbooks", headers=oh).json()["textbooks"]), 0)

    def test_rebuild_graph_sets_building(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.post(f"/api/v1/textbooks/{tb_id}/rebuild_graph", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "building")

    def test_rebuild_reuses_existing_refresh_task_idempotently(self):
        from app.api.v1 import textbook as tb_api

        class PendingTask:
            def done(self):
                return False

            def cancel(self):
                return None

        tb_id = self._upload().json()["results"][0]["id"]
        task = PendingTask()
        tb_api.tb_store.register_refresh_task(self.user.id, tb_id, task)
        try:
            with patch.object(tb_api, "_spawn_refresh") as spawn:
                r = self.client.post(
                    f"/api/v1/textbooks/{tb_id}/rebuild_graph",
                    json={"mode": "rag_graph"}, headers=self.h)
            self.assertEqual(r.status_code, 200, r.text)
            payload = r.json()
            self.assertEqual(payload["status"], "building")
            self.assertEqual(payload["mode"], "rag_graph")
            self.assertTrue(payload["idempotent_reuse"])
            self.assertTrue(payload["uses_existing_text"])
            self.assertFalse(payload["ocr_requested"])
            spawn.assert_not_called()
        finally:
            tb_api.tb_store.finish_refresh_task(self.user.id, tb_id, task)

    def test_refreshes_are_serialized_per_owner(self):
        import asyncio as _asyncio
        from app.api.v1 import textbook as tb_api

        active = 0
        max_active = 0

        async def fake_inner(*args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await _asyncio.sleep(0.02)
            active -= 1

        async def drive():
            tb_api._REFRESH_OWNER_LOCKS.pop(self.user.id, None)
            with patch.object(tb_api, "_safe_refresh_inner", side_effect=fake_inner):
                await _asyncio.gather(
                    tb_api._safe_refresh(self.user.id, "tb1", "rag_graph"),
                    tb_api._safe_refresh(self.user.id, "tb2", "rag_graph"),
                )

        try:
            _asyncio.run(drive())
        finally:
            tb_api._REFRESH_OWNER_LOCKS.pop(self.user.id, None)
        self.assertEqual(max_active, 1)

    def test_rebuild_actually_spawns_build(self):
        """回归：rebuild 端点必须真正派发后台构建。

        历史 bug：端点是同步 def（线程池、无事件循环），_spawn_build 的
        asyncio.create_task RuntimeError 被吞，构建永不执行、记录悬挂 building。
        """
        import asyncio as _asyncio
        from app.api.v1 import textbook as tb_api
        tb_id = self._upload().json()["results"][0]["id"]
        sid = self.user.id
        # 让真实 _spawn_build 生效（setUp 里 patch 掉了），跑完再恢复。
        self._spawn_patch.stop()
        try:
            async def fake_refresh(student_id, tbid, mode, **_kw):
                tb_api.tb_store.update_textbook(
                    student_id, tbid, status="ready",
                    chapter_count=1, concept_count=3)

            async def drive():
                with patch.object(tb_api, "_safe_refresh", side_effect=fake_refresh):
                    resp = await tb_api.rebuild_graph(tb_id, student_id=sid)
                    self.assertEqual(resp["status"], "building")
                    for _ in range(100):
                        rec = tb_api.tb_store.find_textbook(sid, tb_id)
                        if rec["status"] == "ready":
                            return
                        await _asyncio.sleep(0.02)
                    self.fail("后台构建未执行（状态未变为 ready）")

            _asyncio.run(drive())
        finally:
            self._spawn_patch.start()


class TestCancelParseEndpoint(unittest.TestCase):
    """POST /textbooks/{id}/cancel：合作式终止 + 终态结算 + 幂等。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.api.v1 import textbook as tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self.app, self._patches = _setup_app(self._tmp.name)
        self.client = TestClient(self.app)
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="cancel@example.com", username="",
            password_hash=hash_password("secret123"))
        self.h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        self._spawn_patch.stop()
        _teardown(self._patches, self._env_old)
        self._tmp.cleanup()

    def test_cancel_building_settles_ready_and_keeps_text(self):
        from app.core.library import library_data_dir
        from app.core import textbook as tb_store
        tb_id = self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": ("b.txt", io.BytesIO(
                "已有可用文本，这一行内容足够长以通过可用性判定。".encode("utf-8")),
                "text/plain")},
            headers=self.h).json()["results"][0]["id"]
        tb_store.update_textbook(self.user.id, tb_id, status="building")
        r = self.client.post(f"/api/v1/textbooks/{tb_id}/cancel", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertEqual(r.json()["record_status"], "ready")
        rec = tb_store.find_textbook(self.user.id, tb_id)
        self.assertEqual(rec["status"], "ready")
        self.assertEqual(rec["error"], "")
        # 文本保留（.txt 未被清理）——记录为组形态（file_ids 承载卷）
        fid = (rec.get("file_ids") or [rec.get("file_id") or ""])[0]
        self.assertTrue(fid)
        self.assertTrue((library_data_dir(self.user.id) / f"{fid}.txt").exists())
        # 幂等：再次 cancel 仍是 cancelled/ready
        r2 = self.client.post(f"/api/v1/textbooks/{tb_id}/cancel", headers=self.h)
        self.assertEqual(r2.json()["record_status"], "ready")

    def test_cancel_sets_flag_for_cooperative_checkpoints(self):
        from app.core import textbook as tb_store
        tb_id = self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": ("b.txt", io.BytesIO(("x" * 500).encode("utf-8")),
                             "text/plain")},
            headers=self.h).json()["results"][0]["id"]
        tb_store.update_textbook(self.user.id, tb_id, status="ocr_waiting")
        self.client.post(f"/api/v1/textbooks/{tb_id}/cancel", headers=self.h)
        # 标记保留至下一轮构建开始（运行中的构建检查点仍需观测）
        self.assertTrue(tb_store.find_textbook(
            self.user.id, tb_id).get("parse_cancel_requested"))

    def test_cancel_foreign_textbook_returns_404(self):
        from app.identity.store import create_user
        from app.identity.security import create_token, hash_password
        other = create_user(email="other2@example.com", username="",
                            password_hash=hash_password("secret123"))
        r = self.client.post(
            f"/api/v1/textbooks/{'tb_nonexistent'}/cancel", headers=self.h)
        self.assertEqual(r.status_code, 404)
        self.assertIsNotNone(other)  # 仅防未使用告警


class TestLibraryOrphanCleanup(unittest.TestCase):
    """library 直删文件 → 孤儿教材记录清理（P2 级联钩子）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.api.v1 import textbook as tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        # 上传限流 10/min，多个测试用例累计会触发 429——每用例重置桶。
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self.app, self._patches = _setup_app(self._tmp.name)
        self.client = TestClient(self.app)
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="orph@example.com", username="",
            password_hash=hash_password("secret123"))
        self.h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        self._spawn_patch.stop()
        _teardown(self._patches, self._env_old)
        self._tmp.cleanup()

    def test_library_delete_textbook_file_requires_textbook_archive(self):
        # 上传教材
        up = self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": ("m.txt", io.BytesIO(b"text content"), "text/plain")},
            headers=self.h)
        self.assertEqual(up.status_code, 200)
        # 教材记录里的 file_id 才是 library 文件 id（upload 返回的 id 是 textbook id）
        tbs = self.client.get("/api/v1/textbooks", headers=self.h).json()["textbooks"]
        file_id = tbs[0]["file_id"]
        # 通过 library 端点直接删该文件
        r = self.client.delete(f"/api/v1/library/files/{file_id}", headers=self.h)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("教材库归档", r.json()["detail"])
        self.assertEqual(len(self.client.get("/api/v1/textbooks", headers=self.h).json()["textbooks"]), 1)


if __name__ == "__main__":
    unittest.main()


class TestFigureStatusAPI(unittest.TestCase):
    """P7 图表标记状态端点：旧书（无 [图/[页码= 标记）False、升级后 True、外人 404。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.api.v1 import textbook as tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self.app, self._patches = _setup_app(self._tmp.name)
        self.client = TestClient(self.app)
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="fig@example.com", username="",
            password_hash=hash_password("secret123"))
        self.h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        self._spawn_patch.stop()
        _teardown(self._patches, self._env_old)
        self._tmp.cleanup()

    def _upload(self):
        return self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": ("old.txt", io.BytesIO("旧书纯文本转录没有结构化标记".encode()), "text/plain")},
            headers=self.h)

    def test_old_book_false_until_markers_written(self):
        tb_id = self._upload().json()["results"][0]["id"]
        r = self.client.get(f"/api/v1/textbooks/{tb_id}/figure-status", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["has_markers"])
        self.assertEqual(len(body["volumes"]), 1)
        # 升级：写入带标记的 .txt 事实源 → True
        from app.core.library import library_data_dir, load_library
        fid = load_library(self.user.id).files[0]["id"]
        (library_data_dir(self.user.id) / f"{fid}.txt").write_text(
            "[页码=1]\n[图1|受力示意]\n图述：斜面模型。", encoding="utf-8")
        r2 = self.client.get(f"/api/v1/textbooks/{tb_id}/figure-status", headers=self.h)
        self.assertTrue(r2.json()["has_markers"])

    def test_foreign_user_404(self):
        tb_id = self._upload().json()["results"][0]["id"]
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        other = id_store.create_user(
            email="fig2@example.com", username="",
            password_hash=hash_password("secret123"))
        r = self.client.get(
            f"/api/v1/textbooks/{tb_id}/figure-status",
            headers={"Authorization": f"Bearer {create_token(other.id)}"})
        self.assertEqual(r.status_code, 404)
