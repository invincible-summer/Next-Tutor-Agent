"""Tests for workspace source selection (P6-C3: 来源只保留教材).

1. Create/PATCH with selection: file_ids 只收已注册教材（自有或公用）；
   folder_ids 已废弃；GET detail 列出可读教材。
2. Retrieval boundary: a session inside the workspace reads ONLY the selected
   textbooks — unselected textbooks and plain library files are invisible。
3. Legacy migration: 旧格式工作区文件迁移进专属夹后属于 workspace-owned
   shared material，在同工作区继续可检索。
4. Workspace upload 是共享 RAG 来源，原件保留/下载与删除级联不变。

No LLM, no network. Data dirs are redirected to temp dirs.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core import library as library_mod  # noqa: E402
from app.core import session as session_mod  # noqa: E402
from app.core import textbook as tb_mod  # noqa: E402
from app.core import trash as trash_mod  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.core.library import load_library, save_library  # noqa: E402
from app.core.workspace import (  # noqa: E402
    load_workspace, merged_knowledge_files, merged_knowledge_store,
    readable_files, scoped_knowledge_stores,
)

GUEST = "student_default"


class _FakeSession:
    """Duck-typed stand-in for TutorSession (workspace_id + knowledge)."""

    def __init__(self, workspace_id: str, session_id: str = "s_fake"):
        from app.core.knowledge_store import KnowledgeStore
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.knowledge = KnowledgeStore()


class TestWorkspaceSources(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ws_src_")
        root = Path(self._tmp.name)
        self.root = root
        self._patches = [
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(ws_mod, "_WORKSPACES_DIR", root / "workspaces"),
            patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"),
            patch.object(tb_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
            patch.object(trash_mod, "_GLOBAL_POLICY", root / "trash" / "policy.json"),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    # --- helpers ---

    def _mk_file(self, name: str, text: str, folder_id: str = "") -> str:
        r = self.client.post(f"/api/v1/library/upload?folder_id={folder_id}",
                             files=[("files", (name, text.encode("utf-8"), "text/plain"))])
        self.assertEqual(r.status_code, 200)
        return r.json()["results"][0]["id"]

    def _mk_textbook(self, name: str, text: str, *, public: bool = False) -> tuple[str, str]:
        """直接落库 + 注册教材记录（绕过 HTTP 上传/后台构建，确定性）。
        返回 (textbook_id, file_id)。"""
        sid = tb_mod.PUBLIC_STUDENT_ID if public else GUEST
        lib = load_library(sid)
        meta = lib.add_file("", name, text)
        meta["kind"] = "textbook"
        save_library(lib)
        rec = tb_mod.create_textbook(sid, file_id=meta["id"], title=name)
        return rec["id"], meta["id"]

    def _mk_workspace(self, name: str, folder_ids=None, file_ids=None) -> str:
        r = self.client.post("/api/v1/workspaces",
                             json={"name": name, "folder_ids": folder_ids or [],
                                   "file_ids": file_ids or []})
        self.assertEqual(r.status_code, 200)
        return r.json()["workspace_id"]

    def _readable_texts(self, ws_id: str) -> str:
        session = _FakeSession(workspace_id=ws_id)
        store = merged_knowledge_store(session)
        return "\n".join(c.text for c in store.chunks)

    # --- 1. create with selection（只收教材） ---

    def test_create_selects_textbooks_only(self):
        _tb_id, fid = self._mk_textbook("浮力教材.txt", "浮力是向上的托力")
        wid = self._mk_workspace("物理学习区", file_ids=[fid])
        ws = load_workspace(wid)
        self.assertEqual(ws.selected_folder_ids, [])  # 文件夹选择已废弃
        self.assertEqual(ws.selected_file_ids, [fid])
        r = self.client.get(f"/api/v1/workspaces/{wid}")
        detail = r.json()
        names = [f["filename"] for f in detail["knowledge_files"]]
        self.assertIn("浮力教材.txt", names)
        r = self.client.get("/api/v1/workspaces")
        self.assertEqual(r.json()["workspaces"][0]["file_count"], 1)

    def test_unknown_and_plain_file_ids_dropped(self):
        plain = self._mk_file("散件.txt", "普通文件不是教材")
        wid = self._mk_workspace("校验区", folder_ids=["f_nope"],
                                 file_ids=["nope", plain])
        ws = load_workspace(wid)
        self.assertEqual(ws.selected_folder_ids, [])
        self.assertEqual(ws.selected_file_ids, [])  # 普通文件不可选入

    # --- 2. retrieval boundary ---

    def test_session_reads_only_selected_textbooks(self):
        _t1, sel_fid = self._mk_textbook("可见教材.txt", "可见内容 浮力原理")
        _t2, other_fid = self._mk_textbook("未选教材.txt", "秘密内容 不应出现")
        self._mk_file("普通文件.txt", "普通文件内容不可检索")
        wid = self._mk_workspace("边界区", file_ids=[sel_fid])

        texts = self._readable_texts(wid)
        self.assertIn("可见内容", texts)
        self.assertNotIn("秘密内容", texts)
        self.assertNotIn("普通文件内容", texts)

        scopes = [s for s, _st in scoped_knowledge_stores(_FakeSession(wid))]
        self.assertIn(f"file:{sel_fid}", scopes)
        self.assertNotIn(f"file:{other_fid}", scopes)

        _files, names = merged_knowledge_files(_FakeSession(wid))
        self.assertEqual(names, ["可见教材.txt"])

    def test_patch_selection_takes_effect_immediately(self):
        _t, fid = self._mk_textbook("临时教材.txt", "临时可见内容")
        wid = self._mk_workspace("调整区", file_ids=[fid])
        self.assertIn("临时可见内容", self._readable_texts(wid))

        r = self.client.patch(f"/api/v1/workspaces/{wid}", json={"file_ids": []})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("临时可见内容", self._readable_texts(wid))

        r = self.client.patch(f"/api/v1/workspaces/{wid}", json={"file_ids": [fid]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("临时可见内容", self._readable_texts(wid))

    def test_workspace_upload_is_shared_rag_source(self):
        """工作区直接上传文件在同区全部会话中可检索。"""
        wid = self._mk_workspace("专属可见性")
        r = self.client.post(f"/api/v1/workspaces/{wid}/upload",
                             files=[("files", ("专属.txt", "工作区上传的内容".encode("utf-8"),
                                               "text/plain"))])
        self.assertEqual(r.status_code, 200)
        self.assertIn("工作区上传的内容", self._readable_texts(wid))
        ws = load_workspace(wid)
        self.assertIn(r.json()["results"][0]["id"], ws.workspace_file_ids)

    def test_public_textbook_selectable(self):
        """公用教材可被任何工作区选入并检索。"""
        _t, pub_fid = self._mk_textbook("公用教材.txt", "公用教材内容 开普勒", public=True)
        wid = self._mk_workspace("公区", file_ids=[pub_fid])
        self.assertIn("公用教材内容", self._readable_texts(wid))

    # --- 3. legacy migration（迁移后是工作区拥有的共享来源） ---

    def test_legacy_workspace_migrates_on_load(self):
        ws_id = "ws_20200101_000000_legacy"
        ws_dir = self.root / "workspaces"
        (ws_dir / "uploads" / ws_id).mkdir(parents=True)
        (ws_dir / "uploads" / ws_id / "leg1.txt").write_text(
            "旧版共享资料 折射定律", encoding="utf-8")
        (ws_dir / f"{ws_id}.json").write_text(json.dumps({
            "workspace_id": ws_id, "name": "旧工作区", "session_ids": [],
            "knowledge_files": [{"id": "leg1", "filename": "旧资料.txt",
                                 "char_count": 12, "chunk_count": 1}],
        }, ensure_ascii=False), encoding="utf-8")

        ws = load_workspace(ws_id)
        self.assertIsNotNone(ws)
        self.assertEqual(ws.knowledge.file_list(), [])
        self.assertTrue(ws.library_folder_id)
        lib = load_library(GUEST)
        folder = lib.find_folder(ws.library_folder_id)
        self.assertEqual(folder["workspace_id"], ws_id)
        f = lib.find_file("leg1")  # file id preserved across the move
        self.assertIsNotNone(f)
        # 专属夹绑定本身是 workspace-owned 授权事实，旧共享资料继续可用。
        self.assertIn("折射定律", self._readable_texts(ws_id))

    # --- 4. workspace upload lands in the exclusive folder（存储语义不变） ---

    def test_workspace_upload_keeps_original(self):
        wid = self._mk_workspace("上传落点")
        raw = "工作区原始字节".encode("utf-8")
        r = self.client.post(f"/api/v1/workspaces/{wid}/upload",
                             files=[("files", ("原始.md", raw, "text/markdown"))])
        self.assertEqual(r.status_code, 200)
        fid = r.json()["results"][0]["id"]
        lib = load_library(GUEST)
        f = lib.find_file(fid)
        self.assertEqual(f["folder_id"], load_workspace(wid).library_folder_id)
        self.assertTrue(f.get("orig_ext"))
        r = self.client.get(f"/api/v1/library/files/{fid}/download")
        self.assertEqual(r.content, raw)

    # --- 5. delete cascades the exclusive folder ---

    def test_delete_workspace_removes_exclusive_folder(self):
        wid = self._mk_workspace("将被删除")
        self.client.post(f"/api/v1/workspaces/{wid}/upload",
                         files=[("files", ("随删.txt", "随工作区删除".encode("utf-8"),
                                           "text/plain"))])
        ws = load_workspace(wid)
        folder_id = ws.library_folder_id
        r = self.client.delete(f"/api/v1/workspaces/{wid}")
        self.assertEqual(r.status_code, 200)
        lib = load_library(GUEST)
        self.assertIsNone(lib.find_folder(folder_id))
        self.assertEqual(lib.files, [])

    def test_remove_file_semantics(self):
        wid = self._mk_workspace("移除语义")
        self.client.post(f"/api/v1/workspaces/{wid}/upload",
                         files=[("files", ("专属删.txt", "在专属夹中".encode("utf-8"),
                                           "text/plain"))])
        ws = load_workspace(wid)
        lib = load_library(GUEST)
        fid = next(f["id"] for f in lib.files
                   if f.get("folder_id") == ws.library_folder_id)
        # Workspace-owned file: archived and removed from the active library.
        r = self.client.delete(f"/api/v1/workspaces/{wid}/files/{fid}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "archived")
        self.assertEqual(body["workspace_id"], wid)
        self.assertEqual(body["file_id"], fid)
        self.assertEqual(body["trash_item"]["resource_type"], "library_file")
        self.assertEqual(body["trash_item"]["original_id"], fid)
        self.assertIsNone(load_library(GUEST).find_file(fid))
        self.assertIn(body["trash_item"]["id"],
                      {item["id"] for item in trash_mod.list_items(GUEST)})

        # Individually selected textbook: only unselected, file survives.
        _t, tb_fid = self._mk_textbook("散件教材.txt", "只取消选入")
        self.client.patch(f"/api/v1/workspaces/{wid}", json={"file_ids": [tb_fid]})
        r = self.client.delete(f"/api/v1/workspaces/{wid}/files/{tb_fid}")
        self.assertEqual(r.json()["status"], "unselected")
        self.assertIsNotNone(load_library(GUEST).find_file(tb_fid))
        self.assertNotIn(tb_fid, load_workspace(wid).selected_file_ids)


if __name__ == "__main__":
    unittest.main()
