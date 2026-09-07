"""最近习题库（quiz_recent）：快照写入 / 100 上限 / verdict 回填 / fail-open。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import learning_records, quiz_recent
from app.core.quiz_recent import (_resolve, list_recent_questions,
                                  record_recent_quiz, record_recent_verdict)


def _quiz(stems, topic="浮力"):
    return {"topic": topic, "grade": "高中",
            "questions": [{"stem": s, "type": "fill_blank",
                           "difficulty": "medium", "answer": "1",
                           "explanation": "...", "knowledge_point": topic}
                          for s in stems]}


class TestQuizRecent(unittest.TestCase):
    def setUp(self):
        self.sid = "student_quiz_recent_" + os.urandom(3).hex()
        self.tmp = tempfile.TemporaryDirectory(prefix="quiz_recent_")
        self.patches = [
            patch.object(quiz_recent, "_STUDENTS_DIR", Path(self.tmp.name)),
            patch.object(learning_records, "_STUDENTS_DIR", Path(self.tmp.name)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        try:
            _resolve(self.sid).unlink()
        except OSError:
            pass
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_record_and_list_newest_first(self):
        record_recent_quiz("sess_a", self.sid, _quiz(["题一", "题二"]))
        record_recent_quiz("sess_b", self.sid, _quiz(["题三"], topic="压强"))
        items = list_recent_questions(self.sid)
        # 全局新→旧：sess_b 的题三最新；同套题内后生成的题二排在题一前
        self.assertEqual([q["stem"] for q in items], ["题三", "题二", "题一"])
        self.assertEqual(items[0]["session_id"], "sess_b")
        self.assertEqual(items[0]["topic"], "压强")
        self.assertEqual(items[0]["verdict"], "")

    def test_cap_100_drops_oldest(self):
        record_recent_quiz("sess_x", self.sid,
                           _quiz([f"旧题{i}" for i in range(60)]))
        record_recent_quiz("sess_y", self.sid,
                           _quiz([f"新题{i}" for i in range(60)]))
        items = list_recent_questions(self.sid)
        self.assertEqual(len(items), 100)
        stems = [q["stem"] for q in items]
        self.assertIn("新题59", stems)      # 最新保留
        self.assertNotIn("旧题0", stems)    # 最旧被淘汰
        self.assertIn("旧题20", stems)      # 边界：旧题20..59 仍在

    def test_verdict_backfill_by_session_and_stem(self):
        record_recent_quiz("sess_a", self.sid, _quiz(["计算浮力大小"]))
        record_recent_verdict("sess_a", self.sid, stem="计算浮力大小",
                              verdict="correct", student_answer="10N")
        items = list_recent_questions(self.sid)
        self.assertEqual(items[0]["verdict"], "correct")
        self.assertEqual(items[0]["student_answer"], "10N")
        # 不同会话的同题干不受影响
        record_recent_quiz("sess_b", self.sid, _quiz(["计算浮力大小"]))
        record_recent_verdict("sess_b", self.sid, stem="计算浮力大小",
                              verdict="wrong", student_answer="5N")
        items = list_recent_questions(self.sid)
        self.assertEqual(items[0]["verdict"], "wrong")
        self.assertEqual(items[1]["verdict"], "correct")

    def test_fail_open_on_garbage(self):
        record_recent_quiz("", self.sid, _quiz(["题"]))          # 无 session 仍记录
        record_recent_quiz("s", "", _quiz(["题"]))                # 无 student 丢弃
        record_recent_verdict("s", self.sid, stem="", verdict="correct")
        path = _resolve(self.sid)
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(list_recent_questions(self.sid), [])


if __name__ == "__main__":
    unittest.main()
