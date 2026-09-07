import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import session as session_mod
from app.core.config import settings
from app.core.session import TutorSession, delete_session, save_session


class TestSessionMaterialCleanup(unittest.TestCase):
    def test_delete_removes_text_and_original(self):
        with tempfile.TemporaryDirectory(prefix="session_cleanup_") as td:
            root = Path(td)
            with patch.object(session_mod, "_SESSIONS_DIR", root / "sessions"), \
                    patch.object(settings, "trace_dir", str(root / "traces")):
                session = TutorSession(session_id="s-clean")
                meta = session.knowledge.add_file(
                    "f-clean", "scan.png", "OCR content", raw=b"image", orig_ext=".png")
                save_session(session)
                text_path = session.knowledge.upload_dir / "f-clean.txt"
                orig_path = session.knowledge.upload_dir / "f-clean.orig.png"
                self.assertTrue(text_path.exists())
                self.assertTrue(orig_path.exists())
                self.assertTrue(delete_session("s-clean"))
                self.assertFalse(text_path.exists())
                self.assertFalse(orig_path.exists())


if __name__ == "__main__":
    unittest.main()
