"""孤儿数据扫描/清理回归（管理台"数据清理"后端）。

覆盖：
- scan_orphans 分类：注册账号 / public / student_default / 全局策略文件全部
  受保护；合成 ID、注销遗物、无引用 trace、失会话转写、无索引 public 数据
  残件、空回收站目录（含注册账号的空壳）全部命中。
- purge_orphans：只删孤儿且幂等；dry_run 零删除；categories 过滤与未知
  类别 ValueError。
- HTTP：GET /admin/orphan-data 与 POST /admin/orphan-data/purge 的
  401/403/200/400 语义。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.storage_sandbox import StorageSandboxTestCase


class OrphanCleanupFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core import context, library, session, trash
        from app.core.config import settings
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        from app.main import create_app

        self.client = TestClient(create_app())
        self.admin = id_store.create_user(
            email="admin@example.com", username="",
            password_hash=hash_password("secret123"), role="admin")
        self.user = id_store.create_user(
            email="u1@example.com", username="",
            password_hash=hash_password("secret123"))
        self.admin_h = {"Authorization": f"Bearer {create_token(self.admin.id)}"}
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

        chat = self.root / "chat_history"
        students = self.root / "students"
        traces = Path(settings.trace_dir)
        uploads = traces.parent / "uploads"
        lib = library._LIBRARY_DIR
        lib_data = lib / "data"
        items = trash._TRASH_DIR / "items"

        def session_file(sid: str, owner: str, trace_ids: list[str],
                         fids: list[str] | None = None) -> None:
            d = {"session_id": sid, "student_id": owner,
                 "trace_ids": trace_ids,
                 "knowledge_files": [{"id": f} for f in fids or []]}
            (session._SESSIONS_DIR / f"{sid}.json").write_text(
                json.dumps(d), encoding="utf-8")

        def transcript(sid: str) -> None:
            (context._TRANSCRIPT_DIR / f"{sid}.transcript.jsonl").write_text(
                '{"role":"user"}\n', encoding="utf-8")

        # --- 注册账号（全部保留） ---
        session_file("sess_keep", self.user.id, ["tk1"], ["fkeep"])
        transcript("sess_keep")
        (traces / "trace_tk1.jsonl").write_text("{}\n", encoding="utf-8")
        (uploads / "fkeep.txt").write_text("keep", encoding="utf-8")
        (uploads / "fkeep.orig.pdf").write_text("keep", encoding="utf-8")
        (students / f"{self.user.id}.json").write_text("{}", encoding="utf-8")
        (students / f"{self.user.id}.prompt_memory.json").write_text("{}", encoding="utf-8")
        (students / "prompt_memory_policy.json").write_text("{}", encoding="utf-8")
        (self.root / "notes" / self.user.id / "notes").mkdir(parents=True)
        (self.root / "knowledge" / "custom" / self.user.id).mkdir(parents=True)
        (lib / f"{self.user.id}.json").write_text(
            '{"files": [{"id": "lf1"}]}', encoding="utf-8")
        (lib_data / self.user.id).mkdir(parents=True, exist_ok=True)
        (lib_data / self.user.id / "lf1.txt").write_text("keep", encoding="utf-8")
        # 受保护 owner 的回收站条目，载荷里引用的 trace 必须保留。
        item = items / self.user.id / "trash_1" / "manifest.json"
        item.parent.mkdir(parents=True)
        item.write_text('{"id": "trash_1", "resource_type": "session"}',
                        encoding="utf-8")
        payload = (items / self.user.id / "trash_1" / "payload" / "session.json")
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text('{"trace_ids": ["ttrash"]}', encoding="utf-8")
        (traces / "trace_ttrash.jsonl").write_text("{}\n", encoding="utf-8")
        # 注册账号的空回收站目录（空壳也应清除）。
        (items / self.admin.id).mkdir(parents=True, exist_ok=True)

        # --- 共享命名空间（保留；public 无索引残件除外） ---
        session_file("sess_guest", "student_default", ["tguest"])
        transcript("sess_guest")
        (traces / "trace_tguest.jsonl").write_text("{}\n", encoding="utf-8")
        (lib / "public.json").write_text(
            '{"files": [{"id": "pf1"}]}', encoding="utf-8")
        (lib_data / "public").mkdir(parents=True, exist_ok=True)
        (lib_data / "public" / "pf1.txt").write_text("keep", encoding="utf-8")
        (lib_data / "public" / "pf_unindexed.txt").write_text("orphan",
                                                              encoding="utf-8")
        (self.root / "knowledge" / "custom" / "public").mkdir(parents=True,
                                                              exist_ok=True)

        # --- 孤儿（全部应被清） ---
        (students / "rs_abc.teaching.json").write_text("{}", encoding="utf-8")
        (students / "hook_reg_x.prompt_memory.json").write_text("{}", encoding="utf-8")
        session_file("sess_orph", "rs_abc", ["torph"], ["forg"])
        transcript("sess_orph")
        transcript("sess_stray")           # 会话早已不存在的转写
        (traces / "trace_torph.jsonl").write_text("{}\n", encoding="utf-8")
        (traces / "trace_tfree.jsonl").write_text("{}\n", encoding="utf-8")
        (traces / "tool_spill_knowledge_search_1.txt").write_text("{}", encoding="utf-8")
        (uploads / "forg.txt").write_text("orphan", encoding="utf-8")
        (chat / "workspaces" / "ws_orph.json").write_text(
            f'{{"workspace_id": "ws_orph", "student_id": "rs_abc"}}',
            encoding="utf-8")
        (lib / "usr_gone.json").write_text("{}", encoding="utf-8")
        (lib / "usr_gone.textbooks.json").write_text("{}", encoding="utf-8")
        (lib / "usr_gone.bak_123").write_text("{}", encoding="utf-8")
        (lib_data / "usr_gone" / "x.txt").mkdir(parents=True, exist_ok=True)
        gone_item = items / "usr_gone" / "trash_9" / "manifest.json"
        gone_item.parent.mkdir(parents=True)
        gone_item.write_text('{"id": "trash_9"}', encoding="utf-8")
        (self.root / "notes" / "usr_gone").mkdir(parents=True)
        (self.root / "knowledge" / "custom" / "usr_gone").mkdir(parents=True)

    @property
    def protected(self) -> set[str]:
        return {self.admin.id, self.user.id}


class TestScanOrphans(OrphanCleanupFixture):
    def test_classification(self):
        from app.core import orphan_cleanup
        report = orphan_cleanup.scan_orphans(self.protected)
        cats = report["categories"]
        self.assertEqual(cats["students"]["items"], 2)
        self.assertEqual(cats["sessions"]["items"], 1)
        self.assertEqual(cats["transcripts"]["items"], 2)
        self.assertEqual(cats["traces"]["items"], 3)   # torph + tfree + spill
        self.assertEqual(cats["uploads"]["items"], 1)  # forg.txt
        self.assertEqual(cats["workspaces"]["items"], 1)
        self.assertEqual(cats["library"]["items"], 5)  # 3 索引 + data 目录 + public 残件
        self.assertEqual(cats["trash"]["items"], 2)    # usr_gone + 空 admin 目录
        self.assertEqual(cats["notes"]["items"], 1)
        self.assertEqual(cats["knowledge"]["items"], 1)
        # 保护集包含共享命名空间
        prot = set(report["protected_ids"])
        self.assertTrue({"public", "student_default"} <= prot)
        self.assertTrue(self.protected <= prot)

    def test_referenced_traces_survive_scan(self):
        from app.core import orphan_cleanup
        found = orphan_cleanup._collect_orphans(self.protected)
        names = {p.name for p in found["traces"]}
        self.assertNotIn("trace_tk1.jsonl", names)      # 保留会话引用
        self.assertNotIn("trace_ttrash.jsonl", names)   # 受保护回收站载荷引用
        self.assertNotIn("trace_tguest.jsonl", names)   # 游客会话引用
        self.assertIn("trace_torph.jsonl", names)       # 孤儿会话的 trace


class TestPurgeOrphans(OrphanCleanupFixture):
    def test_dry_run_deletes_nothing(self):
        from app.core import orphan_cleanup
        report = orphan_cleanup.purge_orphans(self.protected, dry_run=True)
        self.assertEqual(report["status"], "dry_run")
        self.assertTrue((self.root / "students" / "rs_abc.teaching.json").exists())
        self.assertTrue((self.root / "chat_history" / "sess_orph.json").exists())

    def test_purge_removes_only_orphans(self):
        from app.core import orphan_cleanup
        report = orphan_cleanup.purge_orphans(self.protected)
        self.assertEqual(report["status"], "purged")
        cats = report["categories"]
        self.assertEqual(cats["students"]["deleted"], 2)
        self.assertEqual(cats["traces"]["deleted"], 3)

        students = self.root / "students"
        chat = self.root / "chat_history"
        from app.core.config import settings
        traces = Path(settings.trace_dir)
        uploads = traces.parent / "uploads"
        lib = chat / "library"
        items = chat / "trash" / "items"

        # 孤儿全没了（含空目录与 data 目录）。
        self.assertFalse((students / "rs_abc.teaching.json").exists())
        self.assertFalse((students / "hook_reg_x.prompt_memory.json").exists())
        self.assertFalse((chat / "sess_orph.json").exists())
        self.assertFalse((chat / "sess_orph.transcript.jsonl").exists())
        self.assertFalse((chat / "sess_stray.transcript.jsonl").exists())
        self.assertFalse((traces / "trace_torph.jsonl").exists())
        self.assertFalse((traces / "tool_spill_knowledge_search_1.txt").exists())
        self.assertFalse((uploads / "forg.txt").exists())
        self.assertFalse((chat / "workspaces" / "ws_orph.json").exists())
        self.assertFalse((lib / "usr_gone.json").exists())
        self.assertFalse((lib / "data" / "usr_gone").exists())
        self.assertFalse((lib / "data" / "public" / "pf_unindexed.txt").exists())
        self.assertFalse((items / "usr_gone").exists())
        self.assertFalse((items / self.admin.id).exists())  # 注册账号空壳目录
        self.assertFalse((self.root / "notes" / "usr_gone").exists())
        self.assertFalse((self.root / "knowledge" / "custom" / "usr_gone").exists())

        # 受保护数据全部完好。
        self.assertTrue((students / f"{self.user.id}.json").exists())
        self.assertTrue((students / "prompt_memory_policy.json").exists())
        self.assertTrue((chat / "sess_keep.json").exists())
        self.assertTrue((chat / "sess_keep.transcript.jsonl").exists())
        self.assertTrue((chat / "sess_guest.json").exists())
        self.assertTrue((traces / "trace_tk1.jsonl").exists())
        self.assertTrue((traces / "trace_ttrash.jsonl").exists())
        self.assertTrue((traces / "trace_tguest.jsonl").exists())
        self.assertTrue((uploads / "fkeep.txt").exists())
        self.assertTrue((uploads / "fkeep.orig.pdf").exists())
        self.assertTrue((lib / f"{self.user.id}.json").exists())
        self.assertTrue((lib / "data" / self.user.id / "lf1.txt").exists())
        self.assertTrue((lib / "data" / "public" / "pf1.txt").exists())
        self.assertTrue((items / self.user.id / "trash_1" / "manifest.json").exists())
        self.assertTrue((self.root / "notes" / self.user.id).exists())
        self.assertTrue((self.root / "knowledge" / "custom" / self.user.id).exists())

        # 幂等：再扫应为零。
        again = self.app_scan()
        self.assertEqual(again["total_items"], 0)

    def app_scan(self) -> dict:
        from app.core import orphan_cleanup
        return orphan_cleanup.scan_orphans(self.protected)

    def test_category_filter_and_unknown(self):
        from app.core import orphan_cleanup
        report = orphan_cleanup.purge_orphans(self.protected,
                                              categories=["students"])
        self.assertEqual(report["categories"]["students"]["deleted"], 2)
        self.assertEqual(report["categories"]["traces"]["deleted"], 0)
        self.assertTrue((self.root / "chat_history" / "sess_orph.json").exists())
        with self.assertRaises(ValueError):
            orphan_cleanup.purge_orphans(self.protected, categories=["nope"])


class TestOrphanEndpoints(OrphanCleanupFixture):
    def test_scan_requires_admin(self):
        r = self.client.get("/api/v1/admin/orphan-data")
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/api/v1/admin/orphan-data", headers=self.user_h)
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/api/v1/admin/orphan-data", headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["report"]["total_items"], 19)

    def test_purge_endpoint(self):
        r = self.client.post("/api/v1/admin/orphan-data/purge",
                             json={"dry_run": True}, headers=self.user_h)
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/api/v1/admin/orphan-data/purge",
                             json={"dry_run": True}, headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "dry_run")
        self.assertTrue((self.root / "students" / "rs_abc.teaching.json").exists())

        r = self.client.post("/api/v1/admin/orphan-data/purge",
                             json={}, headers=self.admin_h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "purged")
        self.assertFalse((self.root / "students" / "rs_abc.teaching.json").exists())

        r = self.client.post("/api/v1/admin/orphan-data/purge",
                             json={"categories": ["nope"]}, headers=self.admin_h)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
