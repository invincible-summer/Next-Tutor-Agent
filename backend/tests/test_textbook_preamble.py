"""P3 对话/工作区联动测试：preamble [当前教材] 块 + TaskFrame.has_textbook trace。

验收（update_plan §6.5）：
- 选教材的会话 preamble 出现 [当前教材] 块且内容正确；无教材零变化。
- TaskFrame.has_textbook 信号写入 trace（诊断用，不改门控）。
- legacy chat_turn 路径与 v2 supervisor 路径行为一致（同一反查函数族）。
"""
import unittest
from pathlib import Path
from unittest.mock import patch
import os
import tempfile


class _TmpStore:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def setUp(self):
        import app.core.textbook as tb
        import app.core.library as lib
        self._tb = tb
        self._lib = lib
        self._orig = {
            "tb._LIBRARY_DIR": tb._LIBRARY_DIR,
            "lib._LIBRARY_DIR": lib._LIBRARY_DIR,
        }
        tb._LIBRARY_DIR = self.root / "library"
        lib._LIBRARY_DIR = self.root / "library"

    def tearDown(self):
        self._tb._LIBRARY_DIR = self._orig["tb._LIBRARY_DIR"]
        self._lib._LIBRARY_DIR = self._orig["lib._LIBRARY_DIR"]
        self.tmp.cleanup()


class TestPreambleTextbookBlock(unittest.TestCase):
    def test_textbook_block_content(self):
        from app.prompts.tutor import grade_preamble
        tbs = [{"title": "高等数学（第七版）", "subject": "数学", "level": "本科"},
               {"title": "大学物理", "subject": "物理", "level": ""}]
        p = grade_preamble("", False, textbooks=tbs)
        self.assertIn("[当前教材]", p)
        self.assertIn("《高等数学（第七版）》", p)
        self.assertIn("本科·数学", p)
        self.assertIn("未指定学段·物理", p)  # level 空时显示「未指定学段」
        self.assertIn("标注页码", p)

    def test_no_textbook_no_block(self):
        from app.prompts.tutor import grade_preamble
        # 无教材（textbooks=None 或空）→ preamble 无 [当前教材] 块（零变化）
        self.assertNotIn("[当前教材]", grade_preamble("高中", False))
        self.assertNotIn("[当前教材]", grade_preamble("高中", False, textbooks=[]))

    def test_textbook_block_with_auto_grade_coexists(self):
        # 教材块 + 自动学段轻约束共存（顺序：教材 → 学段）
        from app.prompts.tutor import grade_preamble
        p = grade_preamble("", False, textbooks=[{"title": "T", "subject": "S", "level": ""}])
        self.assertIn("[当前教材]", p)
        self.assertIn("[学段] 学生未指定学段", p)
        self.assertLess(p.index("[当前教材]"), p.index("[学段]"))

    def test_textbook_block_capped_at_three(self):
        from app.prompts.tutor import grade_preamble
        tbs = [{"title": f"T{i}", "subject": "S", "level": ""} for i in range(5)]
        p = grade_preamble("", False, textbooks=tbs)
        self.assertEqual(p.count("《T"), 3)  # 最多渲染 3 本


class TestVisibleTextbooksReverseLookup(unittest.TestCase):
    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def _session_with_files(self, student_id, file_ids):
        from types import SimpleNamespace
        from app.core.knowledge_store import KnowledgeStore
        ks = KnowledgeStore()
        for fid in file_ids:
            ks.files.append({"id": fid, "filename": f"f{fid}", "char_count": 10})
        return SimpleNamespace(student_id=student_id, workspace_id="", knowledge=ks)

    def test_supervisor_visible_textbooks(self):
        from app.core import textbook as tb
        from app.agents.supervisor import _visible_textbooks
        tb.create_textbook("stu1", file_id="f1", title="高数", level="本科")
        tb.create_textbook("stu1", file_id="f2", title="大物", level="")
        session = self._session_with_files("stu1", ["f1", "f2", "f3"])
        merged = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
        out = _visible_textbooks(session, merged)
        self.assertEqual(len(out), 2)
        titles = {t["title"] for t in out}
        self.assertEqual(titles, {"高数", "大物"})

    def test_supervisor_visible_textbooks_isolation(self):
        from app.core import textbook as tb
        from app.agents.supervisor import _visible_textbooks
        tb.create_textbook("stu1", file_id="f1", title="A")
        # stu2 的会话看不到 stu1 的教材
        session = self._session_with_files("stu2", ["f1"])
        self.assertEqual(_visible_textbooks(session, [{"id": "f1"}]), [])

    def test_chat_agent_textbooks_parity(self):
        from app.core import textbook as tb
        from app.agents.chat_agent import _textbooks_for_session, _has_textbook_for_session
        tb.create_textbook("stu1", file_id="f1", title="高数", level="本科")
        session = self._session_with_files("stu1", ["f1"])
        merged = [{"id": "f1"}]
        out = _textbooks_for_session(session, merged)
        self.assertEqual(len(out), 1)
        self.assertTrue(_has_textbook_for_session(session, merged))

    def test_empty_files_returns_empty(self):
        from app.agents.supervisor import _visible_textbooks
        from types import SimpleNamespace
        session = SimpleNamespace(student_id="stu1", workspace_id="", knowledge=None)
        self.assertEqual(_visible_textbooks(session, []), [])


class TestTaskFrameHasTextbook(unittest.TestCase):
    def test_has_textbook_default_false(self):
        from app.agents.skill_runtime.decision import build_task_frame
        from app.agents.state import StudentSnapshot, TaskType, TaskUnderstanding
        snap = StudentSnapshot(grade="")
        u = TaskUnderstanding(intent=TaskType.EXPLAIN, subject="数学", concept="导数",
                              requires_tools=False, confidence=0.8)
        frame = build_task_frame("讲一下导数", u, snap)
        self.assertFalse(frame.has_textbook)
        self.assertIn("has_textbook", frame.to_dict())

    def test_has_textbook_propagates(self):
        from app.agents.skill_runtime.decision import build_task_frame
        from app.agents.state import StudentSnapshot, TaskType, TaskUnderstanding
        snap = StudentSnapshot(grade="本科")
        u = TaskUnderstanding(intent=TaskType.EXPLAIN, subject="数学", concept="导数",
                              requires_tools=True, confidence=0.8)
        frame = build_task_frame("讲一下导数", u, snap, has_textbook=True)
        self.assertTrue(frame.has_textbook)
        d = frame.to_dict()
        self.assertTrue(d["has_textbook"])


class TestWorkspaceTextbookVisibleToChat(unittest.TestCase):
    """P6-C1 回归（用户报告的「工作区教材没被用到」）：工作区选中的教材必须
    进入对话管线——merged 文件清单、可见教材反查、检索 store 三处同时成立。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        import app.core.library as lib_mod
        import app.core.textbook as tb_mod
        import app.core.workspace as ws_mod
        self._mods = (lib_mod, tb_mod, ws_mod)
        self._orig = (lib_mod._LIBRARY_DIR, tb_mod._LIBRARY_DIR,
                      ws_mod._WORKSPACES_DIR)
        lib_mod._LIBRARY_DIR = root / "library"
        tb_mod._LIBRARY_DIR = root / "library"
        ws_mod._WORKSPACES_DIR = root / "workspaces"

    def tearDown(self):
        lib_mod, tb_mod, ws_mod = self._mods
        lib_mod._LIBRARY_DIR, tb_mod._LIBRARY_DIR, ws_mod._WORKSPACES_DIR = self._orig
        self._tmp.cleanup()

    def test_workspace_selected_textbook_reaches_pipeline(self):
        from types import SimpleNamespace
        from app.core.knowledge_store import KnowledgeStore
        from app.core.library import load_library, save_library
        from app.core import textbook as tb_mod
        from app.core.workspace import (Workspace, save_workspace, load_workspace,
                                        merged_knowledge_files, merged_knowledge_store)
        from app.agents.supervisor import _visible_textbooks
        sid = "student_default"
        # 落教材（含内容）+ 注册 + 工作区选入
        lib = load_library(sid)
        meta = lib.add_file("", "力学教材.txt", "角动量定理 教材原文内容 力矩 角动量")
        meta["kind"] = "textbook"
        save_library(lib)
        tb_mod.create_textbook(sid, file_id=meta["id"], title="力学教材", level="本科")
        ws = Workspace(name="物理区", student_id=sid)
        wid = save_workspace(ws)
        ws = load_workspace(wid)
        ws.selected_file_ids.append(meta["id"])
        save_workspace(ws)
        # 绑定该工作区的会话
        session = SimpleNamespace(student_id=sid, workspace_id=wid,
                                  knowledge=KnowledgeStore())
        files, names = merged_knowledge_files(session)
        self.assertIn("力学教材.txt", names)
        tbs = _visible_textbooks(session, files)
        self.assertEqual(len(tbs), 1)
        self.assertEqual(tbs[0]["title"], "力学教材")
        # 检索 store 含教材 chunks（R10 预检索/knowledge_search 的数据源）
        store = merged_knowledge_store(session)
        self.assertTrue(any("角动量" in c.text for c in store.chunks))

    def test_unselected_textbook_invisible(self):
        from types import SimpleNamespace
        from app.core.knowledge_store import KnowledgeStore
        from app.core.library import load_library, save_library
        from app.core import textbook as tb_mod
        from app.core.workspace import Workspace, save_workspace, merged_knowledge_files
        sid = "student_default"
        lib = load_library(sid)
        meta = lib.add_file("", "未选教材.txt", "不应出现的内容")
        meta["kind"] = "textbook"
        save_library(lib)
        tb_mod.create_textbook(sid, file_id=meta["id"], title="未选教材")
        ws = Workspace(name="空区", student_id=sid)
        wid = save_workspace(ws)
        session = SimpleNamespace(student_id=sid, workspace_id=wid,
                                  knowledge=KnowledgeStore())
        files, names = merged_knowledge_files(session)
        self.assertEqual(names, [])


if __name__ == "__main__":
    unittest.main()
