"""Round-count semantics (会话轮数口径):

One agent reply = one round. Pure upload messages (file attachments without
an assistant reply) never count. Pins TutorSession.round_count, the
context_summary wording, and the round_count field in list_sessions.
"""
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase
import app.core.session as session_mod  # noqa: E402
from app.core.session import TutorSession, list_sessions, save_session  # noqa: E402


class TestRoundCount(StorageSandboxTestCase):
    def _session(self, messages) -> TutorSession:
        s = TutorSession(session_id="rc_test")
        s.messages = messages
        return s

    def test_uploads_do_not_count_replies_do(self):
        s = self._session([
            {"role": "user", "content": "（上传了文件：笔记.pdf）"},
            {"role": "user", "content": "讲一下浮力"},
            {"role": "assistant", "content": "浮力是……"},
            {"role": "user", "content": "再举个例子"},
            {"role": "assistant", "content": "比如……"},
        ])
        self.assertEqual(s.round_count(), 2)
        self.assertIn("对话 2 轮", s.context_summary())

    def test_zero_rounds_before_first_reply(self):
        s = self._session([{"role": "user", "content": "（上传了文件：a.pdf）"}])
        self.assertEqual(s.round_count(), 0)

    def test_list_sessions_carries_round_count(self):
        tmp = tempfile.TemporaryDirectory(prefix="rc_")
        orig = session_mod._SESSIONS_DIR
        session_mod._SESSIONS_DIR = Path(tmp.name)
        try:
            s = TutorSession(session_id="rc_list", title="浮力")
            s.student_id = "student_default"
            s.messages = [
                {"role": "user", "content": "讲一下浮力"},
                {"role": "assistant", "content": "浮力是……"},
            ]
            save_session(s)
            items = list_sessions()
            item = next(i for i in items if i["session_id"] == "rc_list")
            self.assertEqual(item["round_count"], 1)
            self.assertEqual(item["message_count"], 2)
        finally:
            session_mod._SESSIONS_DIR = orig
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
