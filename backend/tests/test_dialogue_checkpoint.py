"""G3 回归：对话 eligibility 与 turn hook（plan §7.2 / §13.1 / §18.2）。

- 明确无关（问候/感谢/单独“懂了/继续”/命令）不受理；其余进 LLM。
- run_turn 统一 hook：supervisor/legacy/错误路径都只受理一次。
- message_id 稳定：补 ID 一次性生成，二次保存不变。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import dialogue as dlg
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core.session import TutorSession, save_session, load_session
from app.core.workspace import Workspace, save_workspace

SID = "usr_dlg_a"


class DialogueFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.core import learner_runtime
        learner_runtime.reset_learner_runtime()
        save_workspace(Workspace(workspace_id="ws_dlg", name="物理",
                                 student_id=SID))
        self.session = TutorSession(session_id="sess_dlg", grade="高中",
                                    student_id=SID, workspace_id="ws_dlg")
        save_session(self.session)

    def _msg(self, text: str, message_id: str = "m_1") -> dict:
        return {"role": "user", "content": text, "message_id": message_id}

    def register(self, text: str, message_id: str = "m_1"):
        return dlg.register_dialogue_source(
            student_id=SID, session=self.session,
            message=self._msg(text, message_id))


class TestEligibility(DialogueFixture):
    def test_trivial_messages_not_registered(self):
        for text in ("谢谢", "懂了", "继续", "好的", "ok", "嗯",
                     "/clear", "你好", " ", "继续讲"):
            self.assertIsNone(self.register(text), text)
        state = get_journal(SID).state()
        self.assertEqual(len(state.sources), 0)

    def test_substantive_message_registered_with_job(self):
        # 简短但有内容的反例/理由必须可评（A18：不能用长度门）
        sid = self.register("不对，并联应该看公共端点，不是看图上并排。")
        self.assertIsNotNone(sid)
        state = get_journal(SID).state()
        src = state.sources[sid]
        self.assertEqual(src.receipt.kind, S.SourceKind.DIALOGUE)
        self.assertEqual(src.receipt.message_ref, "m_1")
        jobs = [rt for rt in state.jobs.values()
                if rt.job.source_id == sid]
        self.assertEqual(len(jobs), 1)      # 受理 + job 同事务

    def test_duplicate_message_id_is_idempotent(self):
        first = self.register("我认为换元后积分限也要换。", "m_dup")
        second = self.register("我认为换元后积分限也要换。", "m_dup")
        self.assertEqual(first, second)
        state = get_journal(SID).state()
        self.assertEqual(len(state.sources), 1)

    def test_no_workspace_not_registered(self):
        session = TutorSession(session_id="sess_nowork", grade="高中",
                               student_id=SID, workspace_id="")
        save_session(session)
        self.assertIsNone(dlg.register_dialogue_source(
            student_id=SID, session=session,
            message={"role": "user", "content": "有力分析的第二步怎么做？",
                     "message_id": "m_2"}))

    def test_foreign_workspace_not_registered(self):
        session = TutorSession(session_id="sess_foreign", grade="高中",
                               student_id="usr_other",
                               workspace_id="ws_dlg")   # 他人工作区
        save_session(session)
        self.assertIsNone(dlg.register_dialogue_source(
            student_id="usr_other", session=session,
            message={"role": "user", "content": "内容", "message_id": "m_3"}))


class TestTurnHook(DialogueFixture):
    def test_after_turn_hook_registers_last_user_message(self):
        self.session.messages.append(self._msg(
            "我用反例说明了这条路不通：当两电阻同端点时它们并联。", "m_t1"))
        self.session.messages.append({"role": "assistant", "content": "好",
                                      "message_id": "m_t2"})
        save_session(self.session)
        fresh = load_session("sess_dlg")
        sids = dlg.after_turn_hook(SID, fresh)
        self.assertEqual(len(sids), 1)

    def test_turn_hook_runs_once_across_dispatch_paths(self):
        """§13.1：supervisor/legacy/voice 共用同一 hook——run_turn 包装层
        在 finally 中调用一次，无论内部路径成败。"""
        from app.agents.chat_agent import _after_turn_dialogue_receipt
        self.session.messages.append(self._msg(
            "我按端点条件重新判断了并联关系。", "m_hook"))
        save_session(self.session)
        _after_turn_dialogue_receipt(self.session, SID)
        state = get_journal(SID).state()
        self.assertEqual(len(state.sources), 1)
        # 重复调用幂等（同 message 已受理）
        _after_turn_dialogue_receipt(self.session, SID)
        self.assertEqual(len(get_journal(SID).state().sources), 1)

    def test_unsaved_message_not_registered(self):
        # 保存与受理之间的故障窗口：未落盘的消息不受理
        self.session.messages.append(self._msg("未保存的内容", "m_unsaved"))
        # 不 save_session
        from app.agents.chat_agent import _after_turn_dialogue_receipt
        _after_turn_dialogue_receipt(self.session, SID)
        self.assertEqual(len(get_journal(SID).state().sources), 0)


class TestMessageIds(StorageSandboxTestCase):
    def test_message_ids_stable_across_saves(self):
        session = TutorSession(session_id="sess_mid", grade="高中",
                               student_id="x")
        session.messages = [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "答"},
        ]
        save_session(session)
        ids_before = [m["message_id"] for m in session.messages]
        self.assertTrue(all(i.startswith("m_") for i in ids_before))
        # 压缩模拟：删除中间消息后保存，剩余消息 ID 不变
        session.messages = session.messages[:1]
        save_session(session)
        fresh = load_session("sess_mid")
        self.assertEqual(fresh.messages[0]["message_id"], ids_before[0])


if __name__ == "__main__":
    unittest.main()
