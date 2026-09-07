"""P3/P4 测试：错题本聚合、记忆卫生（episodic 截断 / superseded 清理）、
难度弱化信号、recall_history 跨会话检索。"""
import asyncio
import os
import time
import unittest
from pathlib import Path

from tests.storage_sandbox import StorageSandboxTestCase


def _mk_session(sid: str, student: str, verdicts: list[str], title: str):
    """落一个带 quiz_history 的会话文件；verdicts 逐题对应。"""
    from app.core.session import TutorSession, save_session
    s = TutorSession(session_id=sid, grade="高中", title=title)
    s.student_id = student
    s.quiz_history = [{
        "topic": "浮力", "grade": "高中",
        "questions": [{
            "id": i + 1, "type": "fill_blank",
            "stem": f"{title}的第{i+1}题：求浮力大小",
            "answer": "10N", "explanation": "用阿基米德原理分步算。",
            "knowledge_point": "浮力", "difficulty": "easy",
            "result": ({"verdict": v, "student_answer": "5N"} if v else None),
        } for i, v in enumerate(verdicts)],
    }]
    # result=None 的题不该带 result 键
    for q in s.quiz_history[0]["questions"]:
        if q["result"] is None:
            del q["result"]
    save_session(s)


class TestErrorNotebook(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.student = "student_eb_" + os.urandom(3).hex()
        self.other = "student_eb_other_" + os.urandom(3).hex()
        self.s1 = "eb_sess_a_" + os.urandom(3).hex()
        self.s2 = "eb_sess_b_" + os.urandom(3).hex()
        self.s3 = "eb_sess_c_" + os.urandom(3).hex()
        _mk_session(self.s1, self.student, ["wrong", "correct"], "浮力练习")
        _mk_session(self.s2, self.student, ["partial"], "压强练习")
        _mk_session(self.s3, self.other, ["wrong"], "别人会话")

    def test_collects_wrong_and_partial_only(self):
        from app.core.error_notebook import collect_error_notebook
        items = collect_error_notebook(self.student)
        verdicts = [q["verdict"] for q in items]
        self.assertEqual(sorted(verdicts), ["partial", "wrong"])
        # correct 与未作答不收
        self.assertEqual(len(items), 2)
        # 字段完整（重练深链与作答对比需要）
        it = items[0]
        for k in ("stem", "correct_answer", "student_answer", "explanation",
                  "session_id", "knowledge_point"):
            self.assertTrue(it.get(k), f"缺字段 {k}")

    def test_isolation_other_student_invisible(self):
        from app.core.error_notebook import collect_error_notebook
        mine = collect_error_notebook(self.student)
        self.assertTrue(all(q["session_id"] != self.s3 for q in mine))
        other = collect_error_notebook(self.other)
        self.assertTrue(all(q["session_id"] == self.s3 for q in other))


class TestMemoryHygiene(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.sid = "student_hyg_" + os.urandom(3).hex()
        from app.agents.memory import store as _store
        self._store = _store

    def test_episodic_compaction_archives_overflow(self):
        from app.agents.memory.schema import EpisodicMemory
        # 把压缩阈值临时调小，避免真的写 512KB
        old = self._store._EPISODE_COMPACT_BYTES
        self._store._EPISODE_COMPACT_BYTES = 1024  # 1KB 即触发
        try:
            for i in range(520):
                self._store.append_episode(self.sid, EpisodicMemory(
                    summary=f"第{i}条学习事件记录", event_type="concept_taught",
                    concept="浮力"))
        finally:
            self._store._EPISODE_COMPACT_BYTES = old
        main = self._store._resolve(self.sid, ext=".episodes.jsonl")
        archive = self._store._resolve(self.sid, ext=".episodes_archive.jsonl")
        main_lines = main.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(main_lines), 500)
        self.assertTrue(archive.exists())
        self.assertGreater(len(archive.read_text(encoding="utf-8").splitlines()), 0)
        # 最新条目仍在主文件
        self.assertIn("第519条", main_lines[-1])

    def test_superseded_facts_trimmed_to_audit_cap(self):
        from app.agents.memory import store
        from app.agents.memory.schema import SemanticFact
        facts = []
        for i in range(30):
            facts.append(SemanticFact(fact=f"旧事实{i}", category="study_habit",
                                      confidence=0.5, evidence_count=1,
                                      superseded_by=f"new_{i}"))
        facts.append(SemanticFact(fact="当前有效事实", category="study_habit",
                                  confidence=0.9, evidence_count=3))
        store.save_semantic_facts(self.sid, facts)
        all_facts = store.load_all_semantic_facts(self.sid)
        superseded = [f for f in all_facts if f.superseded_by]
        active = [f for f in all_facts if not f.superseded_by]
        self.assertLessEqual(len(superseded), 20)
        self.assertEqual([f.fact for f in active], ["当前有效事实"])


class TestDifficultySoftening(unittest.TestCase):
    def test_downgrade_one_notch(self):
        from types import SimpleNamespace
        from app.agents.supervisor import _downgrade_strategy_difficulty

        class _Trace:
            def log(self, *a, **k): pass

        strat = SimpleNamespace(suggested_quiz_difficulty="hard",
                                exercise_level="hard",
                                next_check=SimpleNamespace(difficulty=4))
        _downgrade_strategy_difficulty(strat, _Trace())
        self.assertEqual(strat.suggested_quiz_difficulty, "medium")
        self.assertEqual(strat.exercise_level, "medium")
        self.assertEqual(strat.next_check.difficulty, 3)

        strat2 = SimpleNamespace(suggested_quiz_difficulty="easy",
                                 exercise_level="easy",
                                 next_check=SimpleNamespace(difficulty=1))
        _downgrade_strategy_difficulty(strat2, _Trace())
        self.assertEqual(strat2.suggested_quiz_difficulty, "easy")  # 触底不降
        self.assertEqual(strat2.next_check.difficulty, 1)


class TestCrossSessionRecall(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.student = "student_recall_" + os.urandom(3).hex()
        self.sa = "recall_a_" + os.urandom(3).hex()
        self.sb = "recall_b_" + os.urandom(3).hex()
        from app.core.session import TutorSession, save_session
        from app.core.context import append_transcript
        for sid, kw in ((self.sa, "牛顿第一定律"), (self.sb, "光的折射定律")):
            s = TutorSession(session_id=sid, grade="高中", title=f"会话{sid[-3:]}")
            s.student_id = self.student
            save_session(s)
        append_transcript(self.sa, 1, [
            {"role": "user", "content": "讲一下牛顿第一定律"},
            {"role": "assistant", "content": "牛顿第一定律又称惯性定律：一切物体总保持匀速直线运动状态或静止状态……"}])
        append_transcript(self.sb, 1, [
            {"role": "user", "content": "讲一下光的折射"},
            {"role": "assistant", "content": "光的折射定律：折射光线与入射光线分居法线两侧，折射率之比满足斯涅尔公式……"}])

    def test_cross_session_hit_labeled(self):
        from app.tools.recall_history import RecallHistoryTool
        # P6-D：跨会话召回默认仅同工作区——两会话需在同一 workspace。
        from app.core.session import load_session, save_session
        for sid in (self.sa, self.sb):
            s = load_session(sid)
            s.workspace_id = "ws_recall"
            save_session(s)
        tool = RecallHistoryTool(self.sa, self.student, workspace_id="ws_recall")
        result = asyncio.run(tool.run(query="斯涅尔公式 折射"))
        self.assertEqual(result.status, "success")
        text = result.text or ""
        self.assertIn("斯涅尔", text)
        self.assertIn("会话《", text)  # 跨会话来源标注

    def test_current_session_still_works_without_label(self):
        from app.tools.recall_history import RecallHistoryTool
        tool = RecallHistoryTool(self.sa, self.student)
        result = asyncio.run(tool.run(query="惯性定律"))
        self.assertEqual(result.status, "success")
        self.assertIn("惯性", result.text or "")


if __name__ == "__main__":
    unittest.main()
