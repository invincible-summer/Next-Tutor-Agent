"""P8 质量报告端点 + quality_ocr 重建模式 API 测试（存储沙箱内）。

- GET /textbooks/{id}/quality：只读，逐卷 verdict 统计；乱码卷 corrupt-heavy
  且 recommended_mode=quality_ocr，好卷 rag_graph。
- POST /textbooks/{id}/rebuild_graph mode=quality_ocr：受理新模式
  （ocr_requested=true），非法 mode 仍 400。
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class TextbookQualityAPITest(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        from app.api.v1 import textbook as tb_api
        from app.core.ratelimit import reset_rate_limits
        # 不触发后台构建/刷新（端点行为与受理语义是断言对象）。
        self._build_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._build_patch.start()
        self._refresh_patch = patch.object(tb_api, "_spawn_refresh", lambda *a, **k: False)
        self._refresh_patch.start()
        reset_rate_limits()
        self.client = TestClient(create_app())
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        user = id_store.create_user(email="q@example.com", username="",
                                    password_hash=hash_password("secret123"))
        self.h = {"Authorization": f"Bearer {create_token(user.id)}"}

    def tearDown(self):
        self._refresh_patch.stop()
        self._build_patch.stop()
        super().tearDown()

    def _upload(self, filename, content):
        return self.client.post(
            "/api/v1/textbooks/upload",
            files={"files": (filename, io.BytesIO(content.encode("utf-8")),
                             "text/plain")},
            headers=self.h, data={"group": "质量测试组"})

    def test_quality_report_flags_corrupt_volume(self):
        garble = "\f".join(["ａ１１ｘ１＋ａ１２ｘ２＝ｂ１ꎬ" * 6 + "正常中文" * 10
                            for _ in range(6)])
        resp = self._upload("线代.txt", garble)
        self.assertEqual(resp.status_code, 200)
        tb_id = resp.json()["results"][0]["group_id"]
        report = self.client.get(f"/api/v1/textbooks/{tb_id}/quality",
                                 headers=self.h)
        self.assertEqual(report.status_code, 200)
        body = report.json()
        self.assertEqual(len(body["volumes"]), 1)
        vol = body["volumes"][0]
        self.assertGreater(vol["text_quality"]["corrupt"], 0)
        self.assertGreaterEqual(body["corrupt_ratio"], 0.10)
        self.assertEqual(body["recommended_mode"], "quality_ocr")

    def test_quality_report_clean_volume(self):
        clean = "\f".join(["卷积神经网络具有局部连接和权重共享特性。" * 20
                           for _ in range(4)])
        resp = self._upload("深度学习.txt", clean)
        tb_id = resp.json()["results"][0]["group_id"]
        body = self.client.get(f"/api/v1/textbooks/{tb_id}/quality",
                               headers=self.h).json()
        self.assertEqual(body["page_verdicts"]["corrupt"], 0)
        self.assertEqual(body["recommended_mode"], "rag_graph")

    def test_rebuild_graph_accepts_quality_ocr_mode(self):
        resp = self._upload("教材.txt", "正常教材内容。" * 20)
        tb_id = resp.json()["results"][0]["group_id"]
        ok = self.client.post(f"/api/v1/textbooks/{tb_id}/rebuild_graph",
                              json={"mode": "quality_ocr"}, headers=self.h)
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["ocr_requested"])
        self.assertEqual(ok.json()["mode"], "quality_ocr")

    def test_rebuild_graph_rejects_unknown_mode(self):
        resp = self._upload("教材.txt", "正常教材内容。" * 20)
        tb_id = resp.json()["results"][0]["group_id"]
        bad = self.client.post(f"/api/v1/textbooks/{tb_id}/rebuild_graph",
                               json={"mode": "turbo"}, headers=self.h)
        self.assertEqual(bad.status_code, 400)

    def test_quality_missing_textbook_404(self):
        resp = self.client.get("/api/v1/textbooks/tb_missing/quality",
                               headers=self.h)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
