"""P2 教材库批量操作 API 测试：POST /textbooks/bulk/rebuild + bulk/cancel。

验收：
- bulk/rebuild 逐本幂等发起：building / idempotent_reuse / missing / forbidden
  分级回报（单项失败不拖垮整批）；mode 非法与空 ids 400；ids 去重。
- 权限：公用教材仅管理员可批量重建（非管理员逐项 forbidden，自有项照常）。
- bulk/cancel 只对活动态（building/ocr_waiting/ocr_paused）执行合作式取消；
  空闲教材 skipped 且不置 stale 取消标记。
- 单本 rebuild_graph / cancel 端点语义回归不变（helper 抽取无行为漂移）。

隔离：继承 StorageSandboxTestCase（AGENTS.md 测试规范）——全部存储根 +
AUTH_MODE=1 + 身份账号文件由基类重定向进 TemporaryDirectory；公共命名空间
（public.textbooks.json）随 textbook._LIBRARY_DIR 一并进沙箱。
"""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class TestTextbookBulkAPI(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        # 不触发真实后台构建：patch _spawn_build 为 no-op，保证确定性
        #（重建派发经 _spawn_refresh 单独按用例 mock）。
        from app.api.v1 import textbook as tb_api
        self._tb_api = tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        # 上传限流 10/min，多个用例累计会触发 429——每用例重置桶。
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self.client = TestClient(create_app())
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="bulk@example.com", username="",
            password_hash=hash_password("secret123"))
        self.tok = create_token(self.user.id)
        self.h = {"Authorization": f"Bearer {self.tok}"}

    def tearDown(self):
        self._spawn_patch.stop()
        super().tearDown()

    def _upload(self, filename="math.txt", content="教材内容" * 5):
        return self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": (filename, io.BytesIO(content.encode("utf-8")), "text/plain")},
            headers=self.h)

    def _upload_ids(self, n):
        ids = []
        for i in range(n):
            r = self._upload(filename=f"book{i}.txt")
            ids.append(r.json()["results"][0]["id"])
        return ids

    # --- bulk/rebuild ---

    def test_bulk_rebuild_starts_each(self):
        ids = self._upload_ids(2)
        with patch.object(self._tb_api, "_spawn_refresh") as spawn:
            r = self.client.post("/api/v1/textbooks/bulk/rebuild",
                                 json={"ids": ids, "mode": "rag_graph"},
                                 headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["mode"], "rag_graph")
        self.assertEqual(body["count"], 2)
        by_id = {x["textbook_id"]: x for x in body["results"]}
        self.assertEqual(set(by_id), set(ids))
        for x in body["results"]:
            self.assertEqual(x["status"], "building")
            self.assertFalse(x["idempotent_reuse"])
            self.assertTrue(x["uses_existing_text"])
            self.assertFalse(x["ocr_requested"])
        self.assertEqual(spawn.call_count, 2)
        # 记录进入 building（清取消标记 + 进度复位）
        from app.core import textbook as tb_store
        for i in ids:
            rec = tb_store.find_textbook(self.user.id, i)
            self.assertEqual(rec["status"], "building")
            self.assertFalse(rec.get("parse_cancel_requested"))

    def test_bulk_rebuild_dedupes_and_reports_missing(self):
        real = self._upload_ids(1)[0]
        with patch.object(self._tb_api, "_spawn_refresh") as spawn:
            r = self.client.post("/api/v1/textbooks/bulk/rebuild",
                                 json={"ids": [real, real, "tb_missing"]},
                                 headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        results = {x["textbook_id"]: x["status"] for x in r.json()["results"]}
        self.assertEqual(results, {real: "building", "tb_missing": "missing"})
        self.assertEqual(spawn.call_count, 1)  # 去重后只发一次

    def test_bulk_rebuild_idempotent_reuse(self):
        class PendingTask:
            def done(self):
                return False

            def cancel(self):
                return None

        ids = self._upload_ids(2)
        task = PendingTask()
        self._tb_api.tb_store.register_refresh_task(self.user.id, ids[0], task)
        try:
            with patch.object(self._tb_api, "_spawn_refresh") as spawn:
                r = self.client.post("/api/v1/textbooks/bulk/rebuild",
                                     json={"ids": ids}, headers=self.h)
            self.assertEqual(r.status_code, 200, r.text)
            by_id = {x["textbook_id"]: x for x in r.json()["results"]}
            self.assertTrue(by_id[ids[0]]["idempotent_reuse"])
            self.assertFalse(by_id[ids[1]]["idempotent_reuse"])
            self.assertEqual(spawn.call_count, 1)  # 在建的不再重复派发
        finally:
            self._tb_api.tb_store.finish_refresh_task(self.user.id, ids[0], task)

    def test_bulk_rebuild_public_forbidden_for_non_admin(self):
        from app.core import textbook as tb_store
        pub = tb_store.create_textbook(
            tb_store.PUBLIC_STUDENT_ID, file_id="f_pub", title="公用教材",
            scope="public")
        own = self._upload_ids(1)[0]
        with patch.object(self._tb_api, "_spawn_refresh") as spawn:
            r = self.client.post("/api/v1/textbooks/bulk/rebuild",
                                 json={"ids": [pub["id"], own]},
                                 headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        by_id = {x["textbook_id"]: x for x in r.json()["results"]}
        self.assertEqual(by_id[pub["id"]]["status"], "forbidden")
        self.assertEqual(by_id[own]["status"], "building")
        self.assertEqual(spawn.call_count, 1)
        # 公用记录未被改动（仍为 create_textbook 的 building 初值）
        self.assertEqual(
            tb_store.find_textbook(tb_store.PUBLIC_STUDENT_ID, pub["id"])["status"],
            "building")

    def test_bulk_rebuild_admin_can_rebuild_public(self):
        from app.core import textbook as tb_store
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        admin = id_store.create_user(
            email="admin@example.com", username="",
            password_hash=hash_password("secret123"), role="admin")
        admin_h = {"Authorization": f"Bearer {create_token(admin.id)}"}
        pub = tb_store.create_textbook(
            tb_store.PUBLIC_STUDENT_ID, file_id="f_pub2", title="公用教材2",
            scope="public")
        with patch.object(self._tb_api, "_spawn_refresh") as spawn:
            r = self.client.post("/api/v1/textbooks/bulk/rebuild",
                                 json={"ids": [pub["id"]]},
                                 headers=admin_h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["results"][0]["status"], "building")
        self.assertEqual(spawn.call_count, 1)

    def test_bulk_rebuild_validations(self):
        self.assertEqual(
            self.client.post("/api/v1/textbooks/bulk/rebuild",
                             json={"ids": []}, headers=self.h).status_code, 400)
        self.assertEqual(
            self.client.post("/api/v1/textbooks/bulk/rebuild",
                             json={"ids": ["x"], "mode": "bogus"},
                             headers=self.h).status_code, 400)

    # --- bulk/cancel ---

    def test_bulk_cancel_only_active(self):
        from app.core import textbook as tb_store
        ids = self._upload_ids(2)
        # 一本保持 building（上传初值），另一本置 ready（空闲）
        tb_store.update_textbook(self.user.id, ids[1], status="ready")
        r = self.client.post("/api/v1/textbooks/bulk/cancel",
                             json={"ids": ids}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        by_id = {x["textbook_id"]: x for x in r.json()["results"]}
        self.assertEqual(by_id[ids[0]]["status"], "cancelled")
        self.assertEqual(by_id[ids[1]]["status"], "skipped")
        self.assertEqual(by_id[ids[1]]["record_status"], "ready")
        # 空闲教材不留 stale 取消标记；活动教材被结算
        recs = {i: tb_store.find_textbook(self.user.id, i) for i in ids}
        self.assertFalse(recs[ids[1]].get("parse_cancel_requested"))
        self.assertNotEqual(recs[ids[0]]["status"], "building")

    def test_bulk_cancel_reports_missing_and_forbidden(self):
        from app.core import textbook as tb_store
        pub = tb_store.create_textbook(
            tb_store.PUBLIC_STUDENT_ID, file_id="f_pub3", title="公用教材3",
            scope="public")
        r = self.client.post("/api/v1/textbooks/bulk/cancel",
                             json={"ids": [pub["id"], "tb_missing"]},
                             headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        by_id = {x["textbook_id"]: x for x in r.json()["results"]}
        self.assertEqual(by_id[pub["id"]]["status"], "forbidden")
        self.assertEqual(by_id["tb_missing"]["status"], "missing")

    def test_bulk_cancel_empty_ids_400(self):
        self.assertEqual(
            self.client.post("/api/v1/textbooks/bulk/cancel",
                             json={"ids": []}, headers=self.h).status_code, 400)

    # --- 单本端点回归（helper 抽取无行为漂移） ---

    def test_single_rebuild_and_cancel_unchanged(self):
        tb_id = self._upload_ids(1)[0]
        with patch.object(self._tb_api, "_spawn_refresh") as spawn:
            r = self.client.post(f"/api/v1/textbooks/{tb_id}/rebuild_graph",
                                 json={"mode": "graph_only"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        payload = r.json()
        self.assertEqual(payload["status"], "building")
        self.assertEqual(payload["mode"], "graph_only")
        self.assertEqual(spawn.call_count, 1)

        c = self.client.post(f"/api/v1/textbooks/{tb_id}/cancel", headers=self.h)
        self.assertEqual(c.status_code, 200, c.text)
        self.assertEqual(c.json()["status"], "cancelled")
        self.assertIn("record_status", c.json())


if __name__ == "__main__":
    unittest.main()
