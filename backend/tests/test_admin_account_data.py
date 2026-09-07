"""管理员账号数据治理回归：占用统计、聊天数据清理（all / uploads_only）、
账号彻底删除（级联清一切、不可恢复）。

覆盖面：
- GET /admin/users 携带每账号 storage 分桶与 summary 汇总。
- POST /admin/users/{id}/clear-chat scope=all：会话/转写/追踪/上传/工作区/
  资料库/聊天类回收站条目全清；账号、笔记、学习档案、图谱保留。
- POST ... scope=uploads_only：仅上传文件被删，会话文本与文件夹结构保留。
- DELETE /admin/users/{id}：账号记录 + 名下全部数据消失；他人数据不动；
  重复删除 404；管理员目标 400；非管理员 403 / 匿名 401。
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


class AccountDataFixture(unittest.TestCase):
    """隔离全部存储根 + 引导 admin/普通账号。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="acct_data_test_")
        self.root = Path(self.tmp.name)
        from app.agents.knowledge import store as graph_store
        from app.agents.memory import prompt_memory
        from app.agents.memory import store as memory_store
        from app.agents.student_model import store as sm_store
        from app.core import context, learning_records, library, notes, quiz_recent
        from app.core import session, textbook, trash, workspace
        from app.core.config import settings
        from app.identity import config as id_config
        from app.identity import store as id_store
        self._mods = dict(session=session, context=context, library=library,
                          workspace=workspace, trash=trash, notes=notes,
                          sm_store=sm_store, graph_store=graph_store,
                          settings=settings)
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self.patches = [
            patch.object(trash, "_TRASH_DIR", self.root / "chat_history" / "trash"),
            patch.object(trash, "_GLOBAL_POLICY", self.root / "chat_history" / "trash" / "policy.json"),
            patch.object(session, "_SESSIONS_DIR", self.root / "chat_history"),
            patch.object(context, "_TRANSCRIPT_DIR", self.root / "chat_history"),
            patch.object(settings, "trace_dir", str(self.root / "backend" / "traces")),
            patch.object(library, "_LIBRARY_DIR", self.root / "chat_history" / "library"),
            patch.object(textbook, "_LIBRARY_DIR", self.root / "chat_history" / "library"),
            patch.object(workspace, "_WORKSPACES_DIR", self.root / "chat_history" / "workspaces"),
            patch.object(graph_store, "_KG_DIR", self.root / "knowledge"),
            patch.object(graph_store, "_CUSTOM_DIR", self.root / "knowledge" / "custom"),
            patch.object(prompt_memory, "_STUDENTS_DIR", self.root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH", self.root / "students" / "prompt_policy.json"),
            patch.object(memory_store, "_STUDENTS_DIR", self.root / "students"),
            patch.object(learning_records, "_STUDENTS_DIR", self.root / "students"),
            patch.object(quiz_recent, "_STUDENTS_DIR", self.root / "students"),
            patch.object(sm_store, "_STUDENTS_DIR", self.root / "students"),
            patch.object(notes, "_NOTES_DIR", self.root / "notes"),
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE", self.root / "users" / "accounts.json"),
        ]
        for p in self.patches:
            p.start()
        (self.root / "users").mkdir(parents=True, exist_ok=True)

        self.client = TestClient(create_app())
        from app.identity.security import create_token, hash_password
        self.admin = id_store.create_user(
            email="admin@example.com", username="",
            password_hash=hash_password("secret123"), role="admin")
        self.user = id_store.create_user(
            email="u1@example.com", username="",
            password_hash=hash_password("secret123"))
        self.admin_h = {"Authorization": f"Bearer {create_token(self.admin.id)}"}
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ[self._env_old] = self._env_old
        self.tmp.cleanup()

    # --- 造数 ---

    def make_session(self, sid: str, owner: str):
        session = self._mods["session"]
        context = self._mods["context"]
        settings = self._mods["settings"]
        s = session.TutorSession(
            session_id=sid, student_id=owner, title="力学",
            messages=[{"role": "user", "content": "解释惯性"},
                      {"role": "assistant", "content": "好的"}],
            trace_ids=["t1" + sid])
        s.knowledge.add_file("fid_" + sid, "图.png", "OCR 文本",
                             raw=b"img-bytes", orig_ext=".png")
        session.save_session(s)
        context.append_transcript(sid, 1, [{"role": "user", "content": "解释惯性"}])
        trace_dir = Path(settings.trace_dir)
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / f"trace_t1{sid}.jsonl").write_text('{"kind":"finish"}\n', encoding="utf-8")
        return s

    def make_library(self, owner: str, n: int = 2):
        library = self._mods["library"]
        lib = library.load_library(owner)
        folder = lib.create_folder("教材")
        for i in range(n):
            lib.add_file(folder["id"], f"书{i}.pdf", f"教材内容 {i}",
                         raw=f"raw{i}".encode(), orig_ext=".pdf")
        library.save_library(lib)
        return lib

    def make_workspace(self, owner: str, ws_id: str):
        workspace = self._mods["workspace"]
        ws = workspace.Workspace(workspace_id=ws_id, student_id=owner, name="复习区")
        workspace.save_workspace(ws)
        upload_dir = workspace.workspace_upload_dir(ws_id)
        (upload_dir / "legacy.txt").write_text("legacy upload", encoding="utf-8")
        return ws

    def make_side_data(self, owner: str):
        """笔记 / 学习档案 / 知识图谱：clear-chat 不应触碰。"""
        notes = self._mods["notes"]
        sm_store = self._mods["sm_store"]
        graph_store = self._mods["graph_store"]
        ndir = notes._NOTES_DIR / owner / "notes"
        ndir.mkdir(parents=True, exist_ok=True)
        (ndir / "n1.md").write_text("# 笔记", encoding="utf-8")
        sm_store._STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        (sm_store._STUDENTS_DIR / f"{owner}.json").write_text("{}", encoding="utf-8")
        cdir = graph_store._CUSTOM_DIR / owner
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "topic1.json").write_text("{}", encoding="utf-8")

    def seed_full(self, owner: str):
        self.make_session("chat_" + owner.replace("usr_", "") + "_a", owner)
        self.make_library(owner)
        self.make_workspace(owner, "ws_" + owner.replace("usr_", ""))
        self.make_side_data(owner)
        trash = self._mods["trash"]
        # 一个真实的会话回收站条目（含 bundle 载荷）。
        s2 = self.make_session("chat_" + owner.replace("usr_", "") + "_b", owner)
        trash.archive_session(owner, s2.session_id)


class TestAdminUsersStorage(AccountDataFixture):
    def test_list_users_reports_storage_buckets(self):
        self.seed_full(self.user.id)
        r = self.client.get("/api/v1/admin/users", headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["summary"]["count"], 2)
        by_id = {u["id"]: u for u in body["users"]}
        st = by_id[self.user.id]["storage"]
        self.assertGreater(st["total_bytes"], 0)
        # seed_full 归档了第二个会话（活跃副本已移入回收站），活跃数为 1。
        self.assertEqual(st["session_count"], 1)
        self.assertGreaterEqual(st["file_count"], 3)  # 1 会话上传 + 2 资料库文件
        self.assertGreater(st["notes_bytes"], 0)
        self.assertGreater(st["students_bytes"], 0)
        self.assertGreater(st["knowledge_bytes"], 0)
        self.assertGreater(st["trash_bytes"], 0)
        self.assertGreater(st["uploads_bytes"], 0)
        # 汇总 = 各账号之和
        self.assertEqual(body["summary"]["total_bytes"],
                         sum(u["storage"]["total_bytes"] for u in body["users"]))

    def test_storage_attributes_only_owned_sessions(self):
        self.make_session("chat_mine", self.user.id)
        self.make_session("chat_admins", self.admin.id)
        from app.core.account_data import scan_storage
        st = scan_storage([self.user.id, self.admin.id])
        self.assertEqual(st[self.user.id]["session_count"], 1)
        self.assertEqual(st[self.admin.id]["session_count"], 1)
        # 各自的上传文件归属正确
        self.assertGreater(st[self.user.id]["uploads_bytes"], 0)
        self.assertGreater(st[self.admin.id]["uploads_bytes"], 0)


class TestClearChat(AccountDataFixture):
    def test_clear_all_removes_chat_but_keeps_account_and_side_data(self):
        self.seed_full(self.user.id)
        self.make_session("chat_admin_keep", self.admin.id)
        session = self._mods["session"]
        workspace = self._mods["workspace"]
        library = self._mods["library"]
        notes = self._mods["notes"]
        sm_store = self._mods["sm_store"]
        graph_store = self._mods["graph_store"]
        trash = self._mods["trash"]
        settings = self._mods["settings"]
        uploads_dir = Path(settings.trace_dir).parent / "uploads"
        user_prefix = "chat_" + self.user.id.replace("usr_", "")

        r = self.client.post(f"/api/v1/admin/users/{self.user.id}/clear-chat",
                             json={"scope": "all"}, headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "cleared")
        self.assertGreater(body["report"]["freed_bytes"], 0)
        self.assertEqual(body["report"]["sessions"], 1)  # _b 已在回收站，活跃仅 _a
        self.assertEqual(body["report"]["workspaces"], 1)
        self.assertEqual(body["report"]["library_files"], 2)

        # 会话 / 转写 / 追踪 / 上传：全没了
        self.assertFalse((session._resolve(user_prefix + "_a")).exists())
        self.assertFalse((session._resolve(user_prefix + "_b")).exists())
        self.assertFalse((Path(session._SESSIONS_DIR) / f"{user_prefix}_a.transcript.jsonl").exists())
        self.assertFalse((Path(settings.trace_dir) / f"trace_t1{user_prefix}_a.jsonl").exists())
        self.assertFalse((uploads_dir / f"fid_{user_prefix}_a.txt").exists())
        self.assertFalse((uploads_dir / f"fid_{user_prefix}_a.orig.png").exists())
        # 资料库：索引与数据目录整体清除
        key = library._key(self.user.id)
        self.assertFalse((library._LIBRARY_DIR / f"{key}.json").exists())
        self.assertFalse((library._LIBRARY_DIR / "data" / key).exists())
        # 工作区及其 legacy 上传目录
        ws_id = "ws_" + self.user.id.replace("usr_", "")
        self.assertFalse((workspace._WORKSPACES_DIR / f"{ws_id}.json").exists())
        # 直接拼路径断言（workspace_upload_dir 会顺手 mkdir）
        self.assertFalse((workspace._WORKSPACES_DIR / "uploads" / ws_id).exists())
        # 聊天类回收站条目（bundle）被永久清除
        items_dir = trash._TRASH_DIR / "items" / self.user.id
        self.assertTrue(not items_dir.exists() or not any(items_dir.iterdir()))
        # 账号与侧数据保留
        from app.identity import store as id_store
        self.assertIsNotNone(id_store.get_by_id(self.user.id))
        self.assertTrue((notes._NOTES_DIR / self.user.id / "notes" / "n1.md").exists())
        self.assertTrue((sm_store._STUDENTS_DIR / f"{self.user.id}.json").exists())
        self.assertTrue((graph_store._CUSTOM_DIR / self.user.id / "topic1.json").exists())
        # 他人数据不动
        self.assertTrue(session._resolve("chat_admin_keep").exists())
        self.assertTrue((uploads_dir / "fid_chat_admin_keep.txt").exists())
        # 清理后占用归零（聊天侧）
        st = self._storage(self.user.id)
        self.assertEqual(st["chat_bytes"], 0)
        self.assertEqual(st["uploads_bytes"], 0)
        self.assertEqual(st["trash_bytes"], 0)
        self.assertGreater(st["notes_bytes"], 0)

    def test_clear_uploads_only_keeps_sessions_and_folder_structure(self):
        session = self._mods["session"]
        library = self._mods["library"]
        settings = self._mods["settings"]
        s = self.make_session("chat_uploads_only", self.user.id)
        self.make_library(self.user.id)
        self.make_side_data(self.user.id)
        uploads_dir = Path(settings.trace_dir).parent / "uploads"

        r = self.client.post(f"/api/v1/admin/users/{self.user.id}/clear-chat",
                             json={"scope": "uploads_only"}, headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["report"]["scope"], "uploads_only")
        # 会话保留、消息文本在、文件元数据已剥离
        spath = session._resolve(s.session_id)
        self.assertTrue(spath.exists())
        d = json.loads(spath.read_text(encoding="utf-8"))
        self.assertEqual(d["messages"][0]["content"], "解释惯性")
        self.assertEqual(d["knowledge_files"], [])
        # 上传文件被删
        self.assertFalse((uploads_dir / f"fid_{s.session_id}.txt").exists())
        self.assertFalse((uploads_dir / f"fid_{s.session_id}.orig.png").exists())
        # 转写保留
        self.assertTrue((Path(session._SESSIONS_DIR) / f"{s.session_id}.transcript.jsonl").exists())
        # 资料库：文件清空但索引与文件夹结构保留
        lib = library.load_library(self.user.id)
        self.assertEqual(lib.files, [])
        self.assertEqual(len(lib.folders), 1)
        key = library._key(self.user.id)
        self.assertTrue((library._LIBRARY_DIR / f"{key}.json").exists())
        self.assertFalse(any((library._LIBRARY_DIR / "data" / key).iterdir()))
        # 侧数据不动
        self.assertGreater(self._storage(self.user.id)["notes_bytes"], 0)

    def test_clear_chat_requires_valid_scope(self):
        r = self.client.post(f"/api/v1/admin/users/{self.user.id}/clear-chat",
                             json={"scope": "everything"}, headers=self.admin_h)
        self.assertEqual(r.status_code, 422)

    def _storage(self, uid: str) -> dict:
        r = self.client.get("/api/v1/admin/users", headers=self.admin_h)
        by_id = {u["id"]: u for u in r.json()["users"]}
        return by_id[uid]["storage"]


class TestPurgeAccount(AccountDataFixture):
    def test_purge_deletes_account_and_everything(self):
        self.seed_full(self.user.id)
        self.make_session("chat_admin_keep", self.admin.id)
        self.make_side_data(self.admin.id)
        session = self._mods["session"]
        notes = self._mods["notes"]
        sm_store = self._mods["sm_store"]
        graph_store = self._mods["graph_store"]
        trash = self._mods["trash"]

        r = self.client.delete(f"/api/v1/admin/users/{self.user.id}", headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "purged")
        self.assertGreater(body["report"]["freed_bytes"], 0)

        from app.identity import store as id_store
        self.assertIsNone(id_store.get_by_id(self.user.id))
        # 名下所有根目录数据消失
        self.assertFalse((notes._NOTES_DIR / self.user.id).exists())
        self.assertFalse((sm_store._STUDENTS_DIR / f"{self.user.id}.json").exists())
        self.assertFalse((graph_store._CUSTOM_DIR / self.user.id).exists())
        self.assertFalse((trash._TRASH_DIR / "items" / self.user.id).exists())
        user_prefix = "chat_" + self.user.id.replace("usr_", "")
        self.assertFalse(session._resolve(user_prefix + "_a").exists())
        # 重复删除 404
        r = self.client.delete(f"/api/v1/admin/users/{self.user.id}", headers=self.admin_h)
        self.assertEqual(r.status_code, 404)
        # 管理员自己的数据原封不动
        self.assertTrue(session._resolve("chat_admin_keep").exists())
        self.assertTrue((notes._NOTES_DIR / self.admin.id).exists())
        # 列表只剩管理员，汇总同步
        r = self.client.get("/api/v1/admin/users", headers=self.admin_h)
        body = r.json()
        self.assertEqual([u["email"] for u in body["users"]], ["admin@example.com"])
        self.assertEqual(body["summary"]["count"], 1)

    def test_permissions_and_admin_guard(self):
        # 非管理员 403；匿名 401
        self.assertEqual(self.client.post(
            f"/api/v1/admin/users/{self.user.id}/clear-chat",
            json={"scope": "all"}, headers=self.user_h).status_code, 403)
        self.assertEqual(self.client.post(
            f"/api/v1/admin/users/{self.user.id}/clear-chat",
            json={"scope": "all"}).status_code, 401)
        self.assertEqual(self.client.delete(
            f"/api/v1/admin/users/{self.user.id}", headers=self.user_h).status_code, 403)
        # 管理员账号不可删（含自己）
        self.assertEqual(self.client.delete(
            f"/api/v1/admin/users/{self.admin.id}", headers=self.admin_h).status_code, 400)
        # 未知账号 404
        self.assertEqual(self.client.delete(
            "/api/v1/admin/users/usr_nope", headers=self.admin_h).status_code, 404)


if __name__ == "__main__":
    unittest.main()
