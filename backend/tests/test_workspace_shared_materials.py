"""Workspace-owned uploads are shared only inside their workspace."""
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from unittest.mock import patch

from app.core import library as library_mod
from app.core import workspace as ws_mod
from app.core.library import load_library, save_library
from app.core.workspace import (Workspace, ensure_library_folder, material_sources,
                                merged_knowledge_store, readable_files,
                                save_workspace, workspace_owned_file_ids)
from app.core.knowledge_store import KnowledgeStore


class _Session:
    def __init__(self, ws_id: str, sid: str, student_id: str = "student_default"):
        self.workspace_id = ws_id
        self.session_id = sid
        self.student_id = student_id
        self.knowledge = KnowledgeStore()


class TestWorkspaceSharedMaterials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ws_shared_")
        root = Path(self.tmp.name)
        from app.core.config import settings
        self.patches = [
            patch.object(library_mod, "_LIBRARY_DIR", root / "library"),
            patch.object(ws_mod, "_WORKSPACES_DIR", root / "workspaces"),
            # 上传经默认 KnowledgeStore 落 uploads（trace_dir 派生），须隔离。
            patch.object(settings, "trace_dir", str(root / "traces")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def _workspace_with_upload(self, name="A"):
        ws = Workspace(name=name, student_id="student_default")
        save_workspace(ws)
        folder_id = ensure_library_folder(ws)
        lib = load_library("student_default")
        meta = lib.add_file(folder_id, "共享讲义.txt", "仅工作区可见的角动量内容")
        meta.update({"source_scope": "workspace",
                     "source_visibility": "workspace_shared"})
        save_library(lib)
        ws.workspace_file_ids.append(meta["id"])
        save_workspace(ws)
        return ws, meta

    def test_same_workspace_sessions_share_uploaded_material(self):
        ws, meta = self._workspace_with_upload()
        for sid in ("s1", "s2"):
            store = merged_knowledge_store(_Session(ws.workspace_id, sid))
            self.assertIn("角动量", "\n".join(c.text for c in store.chunks))
        self.assertIn(meta["id"], workspace_owned_file_ids(ws))
        listed = readable_files(ws)
        self.assertEqual(listed[0]["source_scope"], "workspace")

    def test_other_workspace_cannot_read_upload(self):
        _ws1, _meta = self._workspace_with_upload("A")
        ws2 = Workspace(name="B", student_id="student_default")
        save_workspace(ws2)
        ensure_library_folder(ws2)
        store = merged_knowledge_store(_Session(ws2.workspace_id, "s-other"))
        self.assertNotIn("角动量", "\n".join(c.text for c in store.chunks))

    def test_foreign_session_workspace_id_cannot_read_sources(self):
        ws, _meta = self._workspace_with_upload("owned")
        forged = _Session(ws.workspace_id, "foreign", student_id="another_student")
        store = merged_knowledge_store(forged)
        self.assertEqual(store.chunks, [])
        self.assertEqual(material_sources(forged), [])

    def test_manifest_separates_session_and_workspace_sources(self):
        ws, _meta = self._workspace_with_upload()
        session = _Session(ws.workspace_id, "s1")
        session.knowledge.add_file(
            "private", "本对话.txt", "私有内容",
            metadata={"source_scope": "session",
                      "source_visibility": "session_private"})
        sources = material_sources(session)
        scopes = {s["source_scope"] for s in sources}
        self.assertEqual(scopes, {"session", "workspace"})

    def test_workspace_upload_route_persists_authorized_source(self):
        class _Upload:
            filename = "shared.pdf"

            async def read(self):
                return b"pdf-bytes"

        ws = Workspace(name="route", student_id="student_default")
        save_workspace(ws)
        with patch("app.api.v1.workspace.extract_text_async",
                   new=AsyncMock(return_value=type("R", (), {
                       "text": "路由上传的共享内容", "warning": "",
                       "used_ocr": False, "ocr_pages": [], "media_count": 0,
                   })())), \
                patch("app.api.v1.chat._post_upload_ingest", new=AsyncMock()):
            memory_patch = patch("app.core.workspace_memory.update_workspace_memory",
                                 new=AsyncMock())
            memory_patch.start()
            from app.api.v1.workspace import upload_shared
            try:
                response = __import__("asyncio").run(upload_shared(
                    ws.workspace_id, [_Upload()], student_id="student_default"))
            finally:
                memory_patch.stop()
        fid = response["results"][0]["id"]
        loaded = ws_mod.load_workspace(ws.workspace_id)
        self.assertIn(fid, loaded.workspace_file_ids)
        self.assertIn("路由上传的共享内容",
                      "\n".join(c.text for c in merged_knowledge_store(
                          _Session(ws.workspace_id, "route-session")).chunks))


if __name__ == "__main__":
    unittest.main()
