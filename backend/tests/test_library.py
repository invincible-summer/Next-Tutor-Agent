"""Tests for the per-student material library (M7 resource center backend):

1. Folder CRUD + workspace-exclusive folder guards (400).
2. Upload keeps the original binary (.orig<ext>) + extracted text, chunked.
3. Download: original bytes round-trip; legacy files (no original) get 404 —
   there is no text fallback (product decision: download serves originals only).
4. Move updates folder membership and the vector scope rule (file_scope).
5. Delete clears metadata + both disk artifacts.
6. M0 isolation: two authenticated users get fully separate libraries.

Fake clients only, no network. Data dirs are redirected to temp dirs.
"""
import os
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
from app.core import workspace as ws_mod  # noqa: E402
from app.core.library import file_scope, load_library  # noqa: E402
from app.core.workspace import Workspace, ensure_library_folder, save_workspace  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402


class _TmpDirs:
    """Redirect library/workspace persistence to a temp location."""

    def __init__(self, test: unittest.TestCase, with_users: bool = False):
        self._tmp = tempfile.TemporaryDirectory(prefix="library_")
        root = Path(self._tmp.name)
        self.root = root
        from app.agents.memory import prompt_memory
        from app.core import trash as trash_mod
        self._patches = [
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(ws_mod, "_WORKSPACES_DIR", root / "workspaces"),
            # 删除库文件走软删除归档（trash）、画像写 prompt_memory——历史
            # 上漏 patch 时直写生产目录。
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
            patch.object(trash_mod, "_GLOBAL_POLICY", root / "trash" / "policy.json"),
            patch.object(prompt_memory, "_STUDENTS_DIR", root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH",
                         root / "students" / "prompt_memory_policy.json"),
        ]
        if with_users:
            (root / "users").mkdir()
            self._patches.append(
                patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"))
        for p in self._patches:
            p.start()

    def cleanup(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()


class TestLibraryApi(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs = _TmpDirs(self)
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._dirs.cleanup()

    def _tree(self) -> dict:
        r = self.client.get("/api/v1/library")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _upload(self, name: str, content: bytes, folder_id: str = "") -> dict:
        r = self.client.post(f"/api/v1/library/upload?folder_id={folder_id}",
                             files=[("files", (name, content, "application/octet-stream"))])
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertNotIn("error", results[0])
        return results[0]

    # --- folders ---

    def test_folder_crud(self):
        r = self.client.post("/api/v1/library/folders", json={"name": "物理资料"})
        self.assertEqual(r.status_code, 200)
        fid = r.json()["folder"]["id"]
        tree = self._tree()
        self.assertEqual([f["name"] for f in tree["folders"]], ["物理资料"])

        r = self.client.patch(f"/api/v1/library/folders/{fid}", json={"name": "物理笔记"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._tree()["folders"][0]["name"], "物理笔记")

        r = self.client.delete(f"/api/v1/library/folders/{fid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._tree()["folders"], [])

    def test_workspace_exclusive_folder_guards(self):
        ws = Workspace(name="专属区")
        wid = save_workspace(ws)
        from app.core.workspace import load_workspace
        ws = load_workspace(wid)
        folder_id = ensure_library_folder(ws)
        r = self.client.patch(f"/api/v1/library/folders/{folder_id}", json={"name": "改名"})
        self.assertEqual(r.status_code, 400)
        r = self.client.delete(f"/api/v1/library/folders/{folder_id}")
        self.assertEqual(r.status_code, 400)

    # --- upload + original preservation ---

    def test_upload_stores_original_and_text(self):
        meta = self._upload("浮力笔记.txt", "浮力是向上的托力".encode("utf-8"))
        tree = self._tree()
        f = tree["files"][0]
        self.assertEqual(f["id"], meta["id"])
        self.assertTrue(f["has_original"])
        data = self._dirs.root / "library" / "data" / "student_default"
        self.assertTrue((data / f"{meta['id']}.txt").exists())
        self.assertTrue((data / f"{meta['id']}.orig.txt").exists())

    def test_rename_file_keeps_retrieval_metadata(self):
        meta = self._upload("原始名.txt", "洛伦兹变换文本".encode("utf-8"))
        r = self.client.patch(f"/api/v1/library/files/{meta['id']}", json={"filename": "大学物理学-相对论.txt"})
        self.assertEqual(r.status_code, 200)
        f = self._tree()["files"][0]
        self.assertEqual(f["filename"], "大学物理学-相对论.txt")
        self.assertEqual(f["original_filename"], "原始名.txt")
        lib = load_library("student_default")
        self.assertTrue(lib.chunks_for_files([meta["id"]]))

    def test_upload_into_folder(self):
        r = self.client.post("/api/v1/library/folders", json={"name": "夹A"})
        fid = r.json()["folder"]["id"]
        meta = self._upload("a.txt", b"content a", folder_id=fid)
        self.assertEqual(meta["folder_id"], fid)
        tree = self._tree()
        self.assertEqual(tree["folders"][0]["file_count"], 1)
        self.assertEqual(tree["files"][0]["folder_id"], fid)
        # file_scope: folder membership decides the vector scope
        lib = load_library("student_default")
        f = lib.find_file(meta["id"])
        self.assertEqual(file_scope(f), f"folder:{fid}")

    def test_upload_rejects_bad_ext(self):
        r = self.client.post("/api/v1/library/upload",
                             files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
        self.assertIn("error", r.json()["results"][0])

    # --- download ---

    def test_download_original_bytes_roundtrip(self):
        raw = "包含中文的原始字节 ✓".encode("utf-8")
        meta = self._upload("原稿.md", raw)
        r = self.client.get(f"/api/v1/library/files/{meta['id']}/download")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, raw)
        self.assertIn("attachment", r.headers.get("content-disposition", ""))

    def test_download_legacy_without_original_404(self):
        # Hand-craft a legacy file: extracted text on disk, no original.
        lib = load_library("student_default")
        meta = lib.add_file("", "旧笔记.pdf", "提取出的旧文本")
        from app.core.library import save_library
        save_library(lib)
        r = self.client.get(f"/api/v1/library/files/{meta['id']}/download")
        self.assertEqual(r.status_code, 404)

    def test_download_unknown_404(self):
        r = self.client.get("/api/v1/library/files/nope/download")
        self.assertEqual(r.status_code, 404)

    # --- move / delete ---

    def test_move_file_between_folders(self):
        r = self.client.post("/api/v1/library/folders", json={"name": "夹B"})
        fid = r.json()["folder"]["id"]
        meta = self._upload("m.txt", b"m")
        r = self.client.post(f"/api/v1/library/files/{meta['id']}/move",
                             json={"folder_id": fid})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._tree()["files"][0]["folder_id"], fid)
        r = self.client.post(f"/api/v1/library/files/{meta['id']}/move",
                             json={"folder_id": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._tree()["files"][0]["folder_id"], "")

    def test_delete_file_clears_disk(self):
        meta = self._upload("d.txt", b"to delete")
        r = self.client.delete(f"/api/v1/library/files/{meta['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._tree()["files"], [])
        data = self._dirs.root / "library" / "data" / "student_default"
        self.assertFalse((data / f"{meta['id']}.txt").exists())
        self.assertFalse((data / f"{meta['id']}.orig.txt").exists())

    def test_delete_folder_removes_its_files(self):
        r = self.client.post("/api/v1/library/folders", json={"name": "夹C"})
        fid = r.json()["folder"]["id"]
        meta = self._upload("inside.txt", b"inside", folder_id=fid)
        r = self.client.delete(f"/api/v1/library/folders/{fid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "archived")
        self.assertEqual(r.json()["trash_item"]["resource_type"], "library_folder")
        self.assertEqual(self._tree()["files"], [])


class TestLibraryFileIdCollision(unittest.TestCase):
    """注入的 file_id 与库内已有 id 冲突时必须换新 id——重复 id 会破坏
    find/remove 语义，并在前端资源页造成重复 key（observed: wf1 x2）。"""

    def setUp(self) -> None:
        self._dirs = _TmpDirs(self)

    def tearDown(self) -> None:
        self._dirs.cleanup()

    def test_duplicate_injected_id_falls_back_to_fresh_uuid(self):
        lib = load_library("student_default")
        first = lib.add_file("", "a.txt", "内容一", file_id="wf1")
        second = lib.add_file("", "b.txt", "内容二", file_id="wf1")
        self.assertEqual(first["id"], "wf1")
        self.assertNotEqual(second["id"], "wf1")
        # 两条元数据各自独立可删
        self.assertTrue(lib.remove_file(first["id"]))
        self.assertIsNotNone(lib.find_file(second["id"]))


class TestLibraryIsolation(unittest.TestCase):
    """M0: two authenticated users get fully separate libraries."""

    def setUp(self) -> None:
        self._dirs = _TmpDirs(self, with_users=True)
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        # AUTH_MODE=1 refuses to boot with the dev default JWT secret.
        self._secret_patch = patch.object(
            id_config, "AUTH_JWT_SECRET", "test-secret-not-default")
        self._secret_patch.start()
        self.client = TestClient(create_app())
        self._headers = {}
        for label in ("a", "b"):
            user = id_store.create_user(email=f"{label}@example.com", username="",
                                        password_hash=hash_password("secret123"))
            self._headers[label] = {"Authorization": f"Bearer {create_token(user.id)}"}

    def tearDown(self) -> None:
        self._secret_patch.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._dirs.cleanup()

    def test_users_are_fully_separate(self):
        r = self.client.post("/api/v1/library/upload",
                             files=[("files", ("a.txt", b"user A secret", "text/plain"))],
                             headers=self._headers["a"])
        fid = r.json()["results"][0]["id"]
        # B sees an empty library and cannot touch A's file.
        r = self.client.get("/api/v1/library", headers=self._headers["b"])
        self.assertEqual(r.json()["files"], [])
        r = self.client.get(f"/api/v1/library/files/{fid}/download",
                            headers=self._headers["b"])
        self.assertEqual(r.status_code, 404)
        r = self.client.delete(f"/api/v1/library/files/{fid}",
                               headers=self._headers["b"])
        self.assertEqual(r.status_code, 404)
        # A still sees their own file.
        r = self.client.get("/api/v1/library", headers=self._headers["a"])
        self.assertEqual(len(r.json()["files"]), 1)


class TestDownloadNaming(unittest.TestCase):
    """Downloaded files must keep a name that ends in the original extension —
    otherwise the OS can't open them. Pins the _download_response hardening:
    missing filename -> "file<orig_ext>"; filename missing the extension ->
    extension appended; non-ASCII names travel via RFC 5987 filename*."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dlname_")
        self.data = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _serve(self, meta: dict):
        from app.api.v1.library import _download_response
        (self.data / f"{meta['id']}.orig{meta['orig_ext']}").write_bytes(b"%PDF-1.4")
        return _download_response(self.data, meta)

    def test_missing_filename_falls_back_with_extension(self):
        resp = self._serve({"id": "f1", "orig_ext": ".pdf"})
        cd = resp.headers["content-disposition"]
        self.assertIn("file.pdf", cd)

    def test_filename_missing_extension_gets_it_appended(self):
        resp = self._serve({"id": "f2", "filename": "ohm", "orig_ext": ".docx"})
        self.assertIn("ohm.docx", resp.headers["content-disposition"])

    def test_chinese_filename_uses_rfc5987_star(self):
        from urllib.parse import unquote
        resp = self._serve({"id": "f3", "filename": "物理课件.pdf", "orig_ext": ".pdf"})
        cd = resp.headers["content-disposition"]
        self.assertIn("filename*=", cd.lower())
        star = cd.split("filename*=", 1)[1]
        encoded = star.split("''", 1)[1].rstrip('"')
        self.assertEqual(unquote(encoded), "物理课件.pdf")

    def test_no_original_still_404(self):
        from fastapi import HTTPException
        from app.api.v1.library import _download_response
        with self.assertRaises(HTTPException):
            _download_response(self.data, {"id": "f4", "filename": "旧.pdf", "orig_ext": ""})


if __name__ == "__main__":
    unittest.main()


class TestPageSnapshotApi(unittest.TestCase):
    """P7 页快照端点：PDF 原件按需渲染 PNG；越界/非 PDF/未知文件 404。"""

    def setUp(self):
        self._dirs = _TmpDirs(self)
        self.client = TestClient(create_app())

    def tearDown(self):
        self._dirs.cleanup()

    @staticmethod
    def _pdf_bytes(pages: int = 2) -> bytes:
        import fitz
        doc = fitz.open()
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 90), f"Page {i + 1} content")
        raw = doc.tobytes()
        doc.close()
        return raw

    def test_renders_png_for_pdf_page(self):
        res = self.client.post(
            "/api/v1/library/upload",
            files=[("files", ("notes.pdf", self._pdf_bytes(), "application/pdf"))])
        fid = res.json()["results"][0]["id"]
        r = self.client.get(f"/api/v1/library/files/{fid}/page/1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers.get("content-type"), "image/png")
        self.assertTrue(r.content[:8].startswith(b"\x89PNG"))

    def test_out_of_range_and_bad_input_404(self):
        res = self.client.post(
            "/api/v1/library/upload",
            files=[("files", ("notes.pdf", self._pdf_bytes(2), "application/pdf"))])
        fid = res.json()["results"][0]["id"]
        self.assertEqual(
            self.client.get(f"/api/v1/library/files/{fid}/page/3").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/library/files/{fid}/page/0").status_code, 404)

    def test_non_pdf_and_unknown_404(self):
        res = self.client.post(
            "/api/v1/library/upload",
            files=[("files", ("notes.txt", "文本".encode(), "text/plain"))])
        fid = res.json()["results"][0]["id"]
        self.assertEqual(
            self.client.get(f"/api/v1/library/files/{fid}/page/1").status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/library/files/unknown/page/1").status_code, 404)
