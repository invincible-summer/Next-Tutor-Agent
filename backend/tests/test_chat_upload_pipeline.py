import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.api.v1 import chat as chat_api
from app.agents.memory import prompt_memory
from app.core import library as library_mod
from app.core import session as session_mod
from app.core import textbook as textbook_mod
from app.core.config import settings
from app.core.multimodal_parser import ExtractionResult
from app.core.session import load_session
from app.core.library import load_library, save_library


class TestChatUploadPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_image_upload_is_saved_as_session_material(self):
        class _Upload:
            filename = "question.png"

            async def read(self):
                return b"image-bytes"

        with tempfile.TemporaryDirectory(prefix="chat_upload_") as td:
            root = Path(td)
            upload = _Upload()
            with patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"), \
                    patch.object(settings, "trace_dir", str(root / "traces")), \
                    patch.object(prompt_memory, "_STUDENTS_DIR", root / "students"), \
                    patch.object(chat_api, "extract_text_async", new=AsyncMock(
                        return_value=ExtractionResult(text="OCR 题目内容", used_ocr=True))), \
                    patch.object(chat_api, "_post_upload_ingest", new=AsyncMock()):
                response = await chat_api.upload_files(
                    session_id=None, grade="", workspace_id=None,
                    files=[upload], student_id="student_default")
                session = load_session(response.session_id)
                self.assertIsNotNone(session)
                row = response.results[0]
                self.assertEqual(row.filename, "question.png")
                self.assertTrue(row.ocr_used)
                self.assertEqual(row.preview_text, "OCR 题目内容")
                self.assertEqual(session.knowledge.files[0]["source_scope"], "session")
                self.assertEqual(session.knowledge.chunks[0].text, "OCR 题目内容")
                self.assertEqual(session.pending_material_file_ids,
                                 [session.knowledge.files[0]["id"]])

    async def test_library_reference_sets_one_shot_grounding_signal(self):
        with tempfile.TemporaryDirectory(prefix="chat_attach_") as td:
            root = Path(td)
            with patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"), \
                    patch.object(library_mod, "_LIBRARY_DIR", root / "library"), \
                    patch.object(textbook_mod, "_LIBRARY_DIR", root / "library"), \
                    patch.object(settings, "trace_dir", str(root / "traces")), \
                    patch.object(prompt_memory, "_STUDENTS_DIR", root / "students"), \
                    patch.object(chat_api, "_post_upload_ingest", new=AsyncMock()):
                lib = load_library("student_default")
                meta = lib.add_file("", "教材.txt", "教材中的惯性知识", file_id="lib-tb")
                save_library(lib)
                textbook_mod.create_textbook(
                    "student_default", file_id=meta["id"], title="教材")
                response = await chat_api.attach_library_files(
                    "new", chat_api.AttachLibraryRequest(file_ids=["lib-tb"]),
                    student_id="student_default")
                session = load_session(response["session_id"])
                copied = response["results"][0]
                self.assertEqual(copied["source_scope"], "library")
                self.assertEqual(copied["library_file_id"], "lib-tb")
                self.assertEqual(session.pending_material_file_ids, [copied["id"]])


if __name__ == "__main__":
    unittest.main()
