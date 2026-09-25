"""G3 回归：评价生命周期（plan §5.3 / §18.2 test_evaluation_lifecycle）。

- 会话移动历史不迁区（历史归属按 workspace_id_at_observation）。
- 删除/恢复/永久删除副本清理；assessment 档案独立保留语义。
- scope 变化：越界概念退出当前视图 + 未开始任务取消。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import dialogue as dlg
from app.agents.student_model.evaluation import lifecycle, schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core.session import TutorSession, save_session
from app.core.workspace import Workspace, save_workspace

SID = "usr_life_a"
WS = "ws_life"
WS2 = "ws_life_2"


class LifecycleFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core import learner_runtime
        learner_runtime.reset_learner_runtime()
        save_workspace(Workspace(workspace_id=WS, name="物理A", student_id=SID))
        save_workspace(Workspace(workspace_id=WS2, name="物理B", student_id=SID))
        self.session = TutorSession(session_id="sess_life", grade="高中",
                                    student_id=SID, workspace_id=WS)
        save_session(self.session)

    def add_dialogue_source(self, message_id: str, workspace: str = WS) -> str:
        session = self.session if workspace == WS else TutorSession(
            session_id="sess_life_b", grade="高中", student_id=SID,
            workspace_id=workspace)
        if workspace != WS:
            save_session(session)
        sid = dlg.register_dialogue_source(
            student_id=SID, session=session,
            message={"role": "user", "content": "我解释了端点判断并联的方法。",
                     "message_id": message_id})
        assert sid is not None
        return sid


class TestSessionLifecycle(LifecycleFixture):
    def test_history_stays_with_observation_workspace_after_move(self):
        """§5.3：会话 A→B 后，A 时期的历史评价仍归 A。"""
        src = self.add_dialogue_source("m_a")      # 在 WS 观察
        # 会话移到 WS2（改 workspace_id）
        self.session.workspace_id = WS2
        save_session(self.session)
        self.add_dialogue_source("m_b")            # 新表现归 WS2
        state = get_journal(SID).state()
        self.assertEqual(
            state.sources[src].receipt.workspace_id_at_observation, WS)
        ws2_sources = [s for s in state.sources.values()
                       if s.receipt.workspace_id_at_observation == WS2]
        self.assertEqual(len(ws2_sources), 1)

    def test_archive_and_restore(self):
        src = self.add_dialogue_source("m_arch")
        archived = lifecycle.archive_session_sources(SID, "sess_life")
        self.assertEqual(archived, [src])
        state = get_journal(SID).state()
        self.assertEqual(state.sources[src].availability, "archived")
        restored = lifecycle.restore_session_sources(SID, "sess_life")
        self.assertEqual(restored, [src])
        self.assertEqual(
            get_journal(SID).state().sources[src].availability, "available")

    def test_permanent_delete_removes_dialogue_copies(self):
        src = self.add_dialogue_source("m_del")
        out = lifecycle.delete_session_sources(
            SID, "sess_life", include_assessments=False)
        self.assertEqual(out["deleted_session"], "sess_life")
        state = get_journal(SID).state()
        self.assertNotIn(src, state.sources)      # 物理清除（非仅 tombstone）
        raw = get_journal(SID).path.read_text(encoding="utf-8")
        self.assertNotIn("端点判断并联", raw)      # 原文不残留

    def test_scope_change_drops_out_of_scope_and_cancels_jobs(self):
        from app.core import learner_runtime
        self.add_dialogue_source("m_scope")
        scheduler = learner_runtime.get_scheduler()
        queued = scheduler.enqueue(
            SID, kind=S.JobKind.DIALOGUE_EVALUATION, workspace_id=WS,
            scope_revision="old")
        # 工作区换到一个空 scope（无教材 → 全部概念越界）
        save_workspace(Workspace(workspace_id=WS, name="物理A", student_id=SID,
                                 selected_file_ids=[]))
        from app.agents.student_model.evaluation.scope import get_scope_resolver
        get_scope_resolver().invalidate()
        dropped, cancelled = lifecycle.on_scope_change(
            SID, WS, change="volumes_deselected")
        self.assertIn(queued.job_id, cancelled)
        state = get_journal(SID).state()
        for (_ws, _key) in state.concept_current:
            self.assertNotEqual(_ws, WS)


if __name__ == "__main__":
    unittest.main()
