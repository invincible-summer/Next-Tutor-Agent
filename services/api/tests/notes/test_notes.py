"""Tests for the M-Notes vault (per-student notes knowledge base):

1. Vault seeding + folder/note CRUD + template scaffolding.
2. Optimistic concurrency (409 with latest content) + revision snapshots
   (user/agent authors) + restore.
3. Wiki-links: resolve / backlinks / rename rewrites links across the vault
   (with a revision snapshot for the rewritten note).
4. Search, tag counts, link graph (ghost nodes for unresolved links).
5. Export: single .md with YAML frontmatter + zip bundle.
6. Suggestions queue: add / apply (replace & append) / dismiss / double-apply.
7. Trash lifecycle: archive -> 404 -> restore round-trip (revisions included).
8. M0 isolation: two authenticated users get fully separate vaults; forged
   student_id in the URL/body never reaches another user's data.
9. Path traversal note ids resolve to 404 (never escape the vault dir).

Fake clients only, no network. Data dirs are redirected to temp dirs.
"""
import os
import sys
from tests.support.storage_sandbox import patch_all_storage_roots, reset_shared_caches, authenticated_client
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app import notes as notes_mod  # noqa: E402
from app.core import textbook as textbook_mod  # noqa: E402
from app.core import trash as trash_mod  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402


class _TmpDirs:
    """Redirect notes/trash/orchestration persistence to a temp location."""

    def __init__(self, test: unittest.TestCase, with_users: bool = False):
        self._tmp = tempfile.TemporaryDirectory(prefix="notes_")
        root = Path(self._tmp.name)
        self.root = root
        (root / "users").mkdir(exist_ok=True)
        self._sandbox = patch_all_storage_roots(root)
        from app.agents.learning_orchestration import store as orch_store
        self._patches = [
            patch.object(notes_mod, "_NOTES_DIR", root / "notes"),
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
            patch.object(orch_store, "_STUDENTS_DIR", root / "students"),
            patch.object(textbook_mod, "_LIBRARY_DIR",
                         root / "chat_history" / "library"),
        ]
        if with_users:
            (root / "users").mkdir(exist_ok=True)
            self._patches.append(
                patch.object(id_store, "_ACCOUNTS_FILE",
                             root / "users" / "accounts.json"))
        for p in self._patches:
            p.start()

    def cleanup(self):
        for p in reversed(self._patches):
            p.stop()
        for p in reversed(self._sandbox):
            p.stop()
        reset_shared_caches()
        self._tmp.cleanup()


class TestNotesVaultApi(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs = _TmpDirs(self)
        self.client = authenticated_client(create_app(), "student_default")

    def tearDown(self) -> None:
        self._dirs.cleanup()

    # -- helpers ------------------------------------------------------------

    def _vault(self) -> dict:
        r = self.client.get("/api/v1/notes/vault")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _create(self, **body) -> dict:
        r = self.client.post("/api/v1/notes/notes", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["note"]

    # -- 1. seeding + CRUD ---------------------------------------------------

    def test_seed_folders_on_first_access(self):
        body = self._vault()
        names = {f["name"] for f in body["folders"]}
        self.assertEqual(names, {"错题修正", "知识点总结", "学习温故", "章节笔记"})
        self.assertEqual(body["stats"]["note_count"], 0)

    def test_template_scaffolding_and_default_folder(self):
        note = self._create(title="牛顿第二定律",
                            template_id="knowledge_summary")
        self.assertTrue(note["folder_id"])
        self.assertEqual(note["tags"], ["知识点"])
        detail = self.client.get(
            f"/api/v1/notes/notes/{note['id']}").json()
        self.assertIn("## 核心概念", detail["content"])

    def test_review_note_template_enables_m9_card(self):
        note = self._create(title="温故测试", template_id="review_note")
        self.assertTrue(note["review"]["enabled"])
        self.assertGreater(note["review"]["next_review_at"], 0)
        r = self.client.post(f"/api/v1/notes/notes/{note['id']}/review",
                             json={"quality": 3})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["review"]["repetitions"], 1)

    def test_folder_crud_moves_notes_to_unfiled(self):
        folder = self.client.post(
            "/api/v1/notes/folders", json={"name": "临时"}).json()["folder"]
        note = self._create(title="A", folder_id=folder["id"])
        r = self.client.delete(f"/api/v1/notes/folders/{folder['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["moved_to_unfiled"], 1)
        note = self.client.get(
            f"/api/v1/notes/notes/{note['id']}").json()["note"]
        self.assertEqual(note["folder_id"], "")

    # -- 2. concurrency + revisions -------------------------------------------

    def test_stale_revision_returns_409_with_latest(self):
        note = self._create(title="并发", content="v1")
        r = self.client.put(f"/api/v1/notes/notes/{note['id']}",
                            json={"title": "并发", "content": "v2",
                                  "base_revision": 1})
        self.assertEqual(r.status_code, 200)
        r = self.client.put(f"/api/v1/notes/notes/{note['id']}",
                            json={"title": "并发", "content": "stale",
                                  "base_revision": 1})
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertEqual(body["content"], "v2")
        self.assertEqual(body["note"]["revision"], 2)

    def test_revision_authors_and_restore(self):
        note = self._create(title="版本", content="v1")
        vault = notes_mod.load_vault("student_default")
        vault.write_note(note["id"], "v2-by-agent", author="agent",
                         summary="助手修改")
        notes_mod.save_vault(vault)
        r = self.client.get(f"/api/v1/notes/notes/{note['id']}/revisions")
        revisions = r.json()["revisions"]
        self.assertEqual([x["revision"] for x in revisions], [2, 1])
        authors = {x["revision"]: x["author"] for x in revisions}
        self.assertEqual(authors[2], "agent")
        self.assertEqual(authors[1], "user")
        r = self.client.get(
            f"/api/v1/notes/notes/{note['id']}/revisions/1")
        self.assertEqual(r.json()["content"], "v1")
        r = self.client.post(
            f"/api/v1/notes/notes/{note['id']}/revisions/1/restore")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.client.get(
                f"/api/v1/notes/notes/{note['id']}").json()["content"], "v1")

    # -- 3. wiki links -----------------------------------------------------------

    def test_backlinks_and_rename_rewrite(self):
        a = self._create(title="笔记A", content="参见 [[笔记B]] 和 [[笔记B|别名]]")
        b = self._create(title="笔记B")
        detail = self.client.get(f"/api/v1/notes/notes/{b['id']}").json()
        self.assertEqual(len(detail["backlinks"]), 1)
        self.assertEqual(detail["backlinks"][0]["id"], a["id"])
        r = self.client.patch(f"/api/v1/notes/notes/{b['id']}",
                              json={"title": "笔记B改"})
        self.assertEqual(r.json()["note"]["links_rewritten"], 2)
        content = self.client.get(
            f"/api/v1/notes/notes/{a['id']}").json()["content"]
        self.assertIn("[[笔记B改]]", content)
        self.assertIn("[[笔记B改|别名]]", content)
        # rewritten note got a revision snapshot (history stays consistent)
        revisions = self.client.get(
            f"/api/v1/notes/notes/{a['id']}/revisions").json()["revisions"]
        self.assertEqual([x["revision"] for x in revisions], [2, 1])

    def test_unresolved_links_and_graph_ghosts(self):
        self._create(title="孤岛", content="链接到 [[不存在]]")
        graph = self.client.get("/api/v1/notes/graph").json()
        ghosts = [n for n in graph["nodes"] if n["ghost"]]
        self.assertEqual(len(ghosts), 1)
        self.assertEqual(ghosts[0]["title"], "不存在")
        self.assertFalse(any(e["resolved"] for e in graph["edges"]))

    def test_graph_includes_textbook_nodes_from_source(self):
        tb = textbook_mod.create_textbook("student_default", file_id="file-1",
                                          title="示例物理必修一")
        note = self._create(title="力学笔记", content="正文")
        vault = notes_mod.load_vault("student_default")
        meta = vault.find_note(note["id"])
        meta.setdefault("source", {})["textbook_ids"] = [tb["id"], "tb_missing"]
        notes_mod.save_vault(vault)
        graph = self.client.get("/api/v1/notes/graph").json()
        tb_nodes = {n["id"]: n for n in graph["nodes"]
                    if n["kind"] == "textbook"}
        self.assertEqual(tb_nodes[f"textbook:{tb['id']}"]["title"], "示例物理必修一")
        self.assertEqual(tb_nodes[f"textbook:{tb['id']}"]["status"], "resolved")
        self.assertEqual(tb_nodes["textbook:tb_missing"]["status"], "missing")
        tb_edges = [e for e in graph["edges"] if e["kind"] == "textbook"]
        self.assertEqual({(e["source"], e["target"]) for e in tb_edges},
                         {(note["id"], f"textbook:{tb['id']}"),
                          (note["id"], "textbook:tb_missing")})

    # -- 4. search / tags ---------------------------------------------------------

    def test_search_priority_and_tag_counts(self):
        self._create(title="力学总结", content="普通内容", tags=["物理"])
        self._create(title="光学", content="这里提到力学一次")
        r = self.client.get("/api/v1/notes/search?q=力学")
        results = r.json()["results"]
        self.assertEqual(results[0]["title"], "力学总结")
        tags = self._vault()["tags"]
        self.assertEqual(tags, {"物理": 1})

    # -- 5. export ------------------------------------------------------------------

    def test_export_markdown_frontmatter(self):
        note = self._create(title="导出测试", content="正文", tags=["a", "b"])
        r = self.client.get(f"/api/v1/notes/notes/{note['id']}/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        text = r.text
        self.assertTrue(text.startswith("---\ntitle: \"导出测试\""))
        self.assertIn('tags: ["a", "b"]', text)
        self.assertIn("正文", text)

    def test_export_zip_contains_both_notes(self):
        self._create(title="Zipped One", content="a")
        self._create(title="Zipped Two", content="b")
        r = self.client.get("/api/v1/notes/export")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/zip"))
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        self.assertEqual(len(names), 2)
        self.assertTrue(all(n.endswith(".md") for n in names))

    # -- 6. per-note agent history（2026-09 重构：建议队列/线程已移除） ----------

    def test_agent_history_endpoints_and_mode_validation(self):
        note = self._create(title="智能体", content="x")
        base = f"/api/v1/notes/notes/{note['id']}/agent"
        view = self.client.get(base).json()
        self.assertEqual(view["mode"], "ask")
        self.assertEqual(view["messages"], [])
        self.assertIsNone(view["pending_plan"])
        self.client.patch(base, json={"mode": "authorize"})
        self.assertEqual(self.client.get(base).json()["mode"], "authorize")
        # 未知模式 422；旧值映射
        self.assertEqual(
            self.client.patch(base, json={"mode": "junk"}).status_code, 422)
        self.assertEqual(
            self.client.patch(base, json={"mode": "auto"}).json()["mode"],
            "authorize")
        # 旧线程/建议路由已下线
        self.assertEqual(
            self.client.get("/api/v1/notes/threads").status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/notes/suggestions").status_code, 404)
        # vault 统计不再包含建议计数
        stats = self.client.get("/api/v1/notes/vault").json()["stats"]
        self.assertNotIn("pending_suggestions", stats)

    # -- 7. trash lifecycle -----------------------------------------------------

    def test_archive_and_restore_roundtrip(self):
        vault = notes_mod.load_vault("student_default")
        vault.write_note = vault.write_note  # keep style checkers quiet
        note = self._create(title="待删", content="内容v1")
        self.client.put(f"/api/v1/notes/notes/{note['id']}",
                        json={"title": "待删", "content": "内容v2"})
        r = self.client.delete(f"/api/v1/notes/notes/{note['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "archived")
        self.assertEqual(
            self.client.get(
                f"/api/v1/notes/notes/{note['id']}").status_code, 404)
        items = self.client.get(
            "/api/v1/trash?resource_type=notes_note").json()["items"]
        self.assertEqual(len(items), 1)
        r = self.client.post(f"/api/v1/trash/{items[0]['id']}/restore", json={})
        self.assertEqual(r.status_code, 200, r.text)
        detail = self.client.get(f"/api/v1/notes/notes/{note['id']}").json()
        self.assertEqual(detail["content"], "内容v2")
        # revisions survived the round-trip
        revisions = self.client.get(
            f"/api/v1/notes/notes/{note['id']}/revisions").json()["revisions"]
        self.assertEqual(len(revisions), 2)

    # -- 8. isolation ---------------------------------------------------------------

    def test_two_users_isolated_vaults(self):
        from app.identity import config as id_config
        from app.identity.models import UserProfile
        env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        accounts_file = self._dirs.root / "accounts.json"
        with patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"), \
                patch.object(id_store, "_ACCOUNTS_FILE", accounts_file):
            try:
                ua = id_store.create_user(
                    email="a.notes@example.com", username="a",
                    password_hash=hash_password("secret123"),
                    profile=UserProfile(name="a"))
                ub = id_store.create_user(
                    email="b.notes@example.com", username="b",
                    password_hash=hash_password("secret123"),
                    profile=UserProfile(name="b"))
                headers_a = {"Authorization": f"Bearer {create_token(ua.id)}"}
                headers_b = {"Authorization": f"Bearer {create_token(ub.id)}"}
                r = self.client.post("/api/v1/notes/notes", json={
                    "title": "A的笔记", "content": "秘密"}, headers=headers_a)
                note_id = r.json()["note"]["id"]
                # B cannot read or mutate A's note (404, no existence leak)
                self.assertEqual(self.client.get(
                    f"/api/v1/notes/notes/{note_id}",
                    headers=headers_b).status_code, 404)
                self.assertEqual(self.client.put(
                    f"/api/v1/notes/notes/{note_id}",
                    json={"title": "hack", "content": "hack"},
                    headers=headers_b).status_code, 404)
                self.assertEqual(self.client.delete(
                    f"/api/v1/notes/notes/{note_id}",
                    headers=headers_b).status_code, 404)
                # B's vault is empty; A's vault has the note
                vault_b = self.client.get(
                    "/api/v1/notes/vault", headers=headers_b).json()
                self.assertEqual(vault_b["stats"]["note_count"], 0)
                vault_a = self.client.get(
                    "/api/v1/notes/vault", headers=headers_a).json()
                self.assertEqual(vault_a["stats"]["note_count"], 1)
                # A's note is not in B's search results
                results_b = self.client.get(
                    "/api/v1/notes/search?q=秘密", headers=headers_b).json()
                self.assertEqual(results_b["results"], [])
            finally:
                if env_old is None:
                    os.environ.pop("AUTH_MODE", None)
                else:
                    os.environ["AUTH_MODE"] = env_old

    # -- 9. traversal ----------------------------------------------------------------

    def test_path_traversal_note_id_is_404(self):
        self.assertEqual(
            self.client.get("/api/v1/notes/notes/..%2F..%2Fetc").status_code,
            404)
        self.assertEqual(
            self.client.get(
                "/api/v1/notes/notes/../../etc/passwd").status_code, 404)


    # -- 10. 助手附件上传 -------------------------------------------------------

    def test_upload_md_file_stores_text_and_manifest(self):
        r = self.client.post("/api/v1/notes/upload", files=[
            ("files", ("讲义.md", "牛顿第二定律 F=ma。".encode("utf-8"),
                       "text/markdown"))])
        self.assertEqual(r.status_code, 200, r.text)
        results = r.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertNotIn("error", results[0])
        self.assertIn("id", results[0])
        self.assertGreater(results[0]["char_count"], 0)
        # 文档不上 OCR 预览（图片才有）
        self.assertIsNone(results[0]["preview_text"])
        # 第二次上传进同一清单，重建 store 后 chunks 可检索
        self.client.post("/api/v1/notes/upload", files=[
            ("files", ("二.md", "动能定理。".encode("utf-8"), "text/markdown"))])
        store = notes_mod.load_uploads_store("student_default")
        self.assertEqual(len(store.files), 2)
        hits = store.search("牛顿", top_k=2)
        self.assertTrue(hits)

    def test_upload_rejects_unsupported_extension(self):
        r = self.client.post("/api/v1/notes/upload", files=[
            ("files", ("恶意.exe", b"MZ...", "application/octet-stream"))])
        self.assertEqual(r.status_code, 200, r.text)
        results = r.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertIn("error", results[0])

    # -- 10. nested folders / bulk / stable resources -----------------------

    def test_legacy_folder_migration_and_nested_delete_moves_to_parent(self):
        root = notes_mod._vault_dir("student_default")
        root.mkdir(parents=True, exist_ok=True)
        notes_mod.atomic_write_text(root / "vault.json", '{"folders":[{"id":"old","name":"旧目录"}],"notes":[],"custom_templates":[]}')
        migrated = notes_mod.load_vault("student_default")
        self.assertEqual(migrated.find_folder("old")["parent_id"], "")

        parent = self.client.post("/api/v1/notes/folders", json={"name": "父"}).json()["folder"]
        middle = self.client.post("/api/v1/notes/folders", json={"name": "中", "parent_id": parent["id"]}).json()["folder"]
        child = self.client.post("/api/v1/notes/folders", json={"name": "子", "parent_id": middle["id"]}).json()["folder"]
        note = self._create(title="嵌套", folder_id=middle["id"])
        removed = self.client.delete(f"/api/v1/notes/folders/{middle['id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()["moved_notes"], 1)
        self.assertEqual(removed.json()["moved_folders"], 1)
        vault = self._vault()
        self.assertEqual(next(f for f in vault["folders"] if f["id"] == child["id"])["parent_id"], parent["id"])
        self.assertEqual(next(n for n in vault["notes"] if n["id"] == note["id"])["folder_id"], parent["id"])

    def test_folder_cycle_rejected_and_bulk_operations_archive(self):
        a = self.client.post("/api/v1/notes/folders", json={"name": "A"}).json()["folder"]
        b = self.client.post("/api/v1/notes/folders", json={"name": "B", "parent_id": a["id"]}).json()["folder"]
        cycle = self.client.patch(f"/api/v1/notes/folders/{a['id']}", json={"parent_id": b["id"]})
        self.assertEqual(cycle.status_code, 400)
        n1 = self._create(title="批量1")
        n2 = self._create(title="批量2")
        moved = self.client.post("/api/v1/notes/bulk/move", json={"note_ids": [n1["id"], n2["id"]], "folder_id": b["id"]})
        self.assertEqual(set(moved.json()["moved"]), {n1["id"], n2["id"]})
        deleted = self.client.post("/api/v1/notes/bulk/delete", json={"note_ids": [n1["id"], n2["id"]]})
        self.assertEqual(len(deleted.json()["archived"]), 2)
        self.assertEqual(self.client.get(f"/api/v1/notes/notes/{n1['id']}").status_code, 404)

    def test_stable_note_and_thread_links_report_missing_or_deleted(self):
        target = self._create(title="目标")
        source = self._create(title="来源", content=f"note://{target['id']} conversation://notes/no-such-thread")
        links = self.client.get(f"/api/v1/notes/notes/{source['id']}").json()["links"]["resources"]
        self.assertEqual(links[0]["status"], "resolved")
        self.assertEqual(links[1]["status"], "missing")
        # 助手线程不进关系图
        graph = self.client.get("/api/v1/notes/graph").json()
        self.assertFalse(any(n.get("kind") == "notes_thread" for n in graph["nodes"]))

    def test_resource_links_stop_at_prose_punctuation(self):
        body = ("与老师的讨论：conversation://session/chat_a、"
                "conversation://session/chat_b（第一次想明白）。\n"
                "关联笔记 note://note_123。")
        links = notes_mod.parse_resource_links(body)
        self.assertEqual(
            [(l["type"], l["resource_id"]) for l in links],
            [("session", "chat_a"), ("session", "chat_b"), ("note", "note_123")])

    def test_unresolved_panel_lists_wikilink_ghosts_only(self):
        note = self._create(title="孤岛", content=(
            "链接到 [[不存在]]；来源 conversation://session/no-such-session（旁注）。"))
        stats = self._vault()["stats"]
        # 会话资源 ghost 不进「未解析链接」面板（那里点击会直接建笔记）
        self.assertEqual(stats["unresolved_links"], ["不存在"])
        resources = self.client.get(
            f"/api/v1/notes/notes/{note['id']}").json()["links"]["resources"]
        self.assertEqual(resources[0]["resource_id"], "no-such-session")
        self.assertEqual(resources[0]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
