"""M0 self-service account deletion: DELETE /user/account.

契约：登录必需（无 token 401）、密码复核（错误 403 且数据不动）、成功后
账号记录与名下全部数据（会话/转写/trace/上传/工作区/资料库/回收站/笔记/
学习档案/知识图谱，含目录本身）不可恢复地清除、不残留空目录；JWT 随账号
记录死亡（后续 /auth/me -> 401）；他人数据不受影响。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.storage_sandbox import StorageSandboxTestCase


class DeleteAccountFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core import context, library, session, trash
        from app.core.config import settings
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        from app.main import create_app

        self.client = TestClient(create_app())
        self.id_store = id_store
        self.user = id_store.create_user(
            email="frank@example.com", username="",
            password_hash=hash_password("secret123"))
        self.other = id_store.create_user(
            email="peer@example.com", username="",
            password_hash=hash_password("secret123"))
        self.headers = {"Authorization": f"Bearer {create_token(self.user.id)}"}

        chat = self.root / "chat_history"
        traces = Path(settings.trace_dir)
        uploads = traces.parent / "uploads"
        lib = library._LIBRARY_DIR

        for owner in (self.user.id, self.other.id):
            s = session.TutorSession(
                session_id=f"sess_{owner[-4:]}", student_id=owner, title="力学",
                messages=[{"role": "user", "content": "解释惯性"}],
                trace_ids=[f"t_{owner[-4:]}"])
            s.knowledge.add_file(f"fu_{owner[-4:]}", "图.png", "OCR 文本",
                                 raw=b"img-bytes", orig_ext=".png")
            session.save_session(s)
            context.append_transcript(s.session_id, 1,
                                      [{"role": "user", "content": "解释惯性"}])
            (traces / f"trace_t_{owner[-4:]}.jsonl").write_text(
                "{}\n", encoding="utf-8")
            self.assertTrue((uploads / f"fu_{owner[-4:]}.txt").exists())
            (self.root / "students" / f"{owner}.json").write_text("{}", encoding="utf-8")
            (self.root / "students" / f"{owner}.prompt_memory.json").write_text(
                "{}", encoding="utf-8")
            (self.root / "notes" / owner / "notes").mkdir(parents=True, exist_ok=True)
            (self.root / "knowledge" / "custom" / owner).mkdir(parents=True,
                                                               exist_ok=True)
            (lib / f"{owner}.json").write_text(
                json.dumps({"files": [{"id": f"lf_{owner[-4:]}"}]}),
                encoding="utf-8")
            (lib / "data" / owner).mkdir(parents=True, exist_ok=True)
            (lib / "data" / owner / "book.txt").write_text("lib", encoding="utf-8")
            item = trash._TRASH_DIR / "items" / owner / "trash_1" / "manifest.json"
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_text('{"id": "trash_1", "resource_type": "session"}',
                            encoding="utf-8")

    def _delete(self, body: dict | None, headers: dict | None = None) -> int:
        r = self.client.request("DELETE", "/api/v1/user/account",
                                json=body, headers=headers or self.headers)
        return r.status_code


class TestDeleteAccount(DeleteAccountFixture):
    def test_unauthenticated_401(self):
        r = self.client.request("DELETE", "/api/v1/user/account",
                                json={"password": "secret123"})
        self.assertEqual(r.status_code, 401)

    def test_wrong_password_403_keeps_data(self):
        self.assertEqual(self._delete({"password": "wrongpass"}), 403)
        self.assertIsNotNone(self.id_store.get_by_email("frank@example.com"))
        self.assertTrue((self.root / "chat_history" /
                         f"sess_{self.user.id[-4:]}.json").exists())
        self.assertTrue((self.root / "students" / f"{self.user.id}.json").exists())

    def test_missing_password_422(self):
        self.assertEqual(self._delete({}), 422)

    def test_delete_purges_all_account_data(self):
        uid = self.user.id
        suffix = uid[-4:]
        self.assertEqual(self._delete({"password": "secret123"}), 200)

        # 账号记录消失，JWT 死亡，重复删除 401。
        self.assertIsNone(self.id_store.get_by_email("frank@example.com"))
        r = self.client.get("/api/v1/auth/me", headers=self.headers)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self._delete({"password": "secret123"}), 401)

        chat = self.root / "chat_history"
        from app.core.config import settings
        traces = Path(settings.trace_dir)
        uploads = traces.parent / "uploads"
        lib = chat / "library"

        # 名下全部数据清空，且不留空目录。
        self.assertFalse((chat / f"sess_{suffix}.json").exists())
        self.assertFalse((chat / f"sess_{suffix}.transcript.jsonl").exists())
        self.assertFalse((traces / f"trace_t_{suffix}.jsonl").exists())
        self.assertFalse((uploads / f"fu_{suffix}.txt").exists())
        self.assertFalse(list(uploads.glob(f"fu_{suffix}.orig*")))
        self.assertFalse((self.root / "students" / f"{uid}.json").exists())
        self.assertFalse((self.root / "students" / f"{uid}.prompt_memory.json").exists())
        self.assertFalse((self.root / "notes" / uid).exists())
        self.assertFalse((self.root / "knowledge" / "custom" / uid).exists())
        self.assertFalse((lib / f"{uid}.json").exists())
        self.assertFalse((lib / "data" / uid).exists())
        self.assertFalse((chat / "trash" / "items" / uid).exists())

        # 他人数据完好。
        peer = self.other.id
        self.assertTrue((chat / f"sess_{peer[-4:]}.json").exists())
        self.assertTrue((uploads / f"fu_{peer[-4:]}.txt").exists())
        self.assertTrue((self.root / "students" / f"{peer}.json").exists())
        self.assertTrue((self.root / "notes" / peer).exists())
        self.assertTrue((lib / f"{peer}.json").exists())
        self.assertTrue((chat / "trash" / "items" / peer / "trash_1").exists())


if __name__ == "__main__":
    unittest.main()
