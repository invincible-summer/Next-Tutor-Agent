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


class TestR03ShortAnswersAndContext(DialogueFixture):
    """R03（update_plan §4）：对话来源、帮助条件与漏评恢复。"""

    def test_short_single_token_answers_eligible(self):
        # A18/R03：单字数字答案有意义，不能用长度门排除
        for text in ("3", "是", "②"):
            sid = self.register(text, "m_short_" + text)
            self.assertIsNotNone(sid, text)
        # 明确无关清单仍然排除
        self.assertIsNone(self.register("嗯", "m_hmm"))

    def test_short_answer_resolves_concept_from_prior_question(self):
        # 上一追问含概念名 → 短答仍可评价（candidates 并集前文）
        from app.agents.student_model.evaluation.dialogue import (
            concept_candidates)
        from app.agents.student_model.evaluation import scope as scope_mod
        from app.agents.student_model.evaluation import schema as CS
        concept = CS.ConceptRef(
            graph_owner_namespace="public", textbook_id="tb_dlg",
            file_ids=["f1"], concept_id="phys.parallel",
            concept_revision="cr_1", display_name="并联")

        class _R:
            def resolve(self, student_id, workspace_id):
                return CS.EvaluationScope(
                    workspace_id=workspace_id, scope_revision="sr_1",
                    selected_volumes=[], allowed_concepts=[concept],
                    graph_revisions=[], unresolved_graph_count=0)

            def invalidate(self, *a, **kw):
                pass

        scope_mod.set_scope_resolver(_R())
        try:
            hits = concept_candidates(
                SID, "ws_dlg", "3",
                prior_texts=["这道并联电路题里总电阻是多少？"])
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].concept_id, "phys.parallel")
            # 无前文时短答零候选（记录 concept_unresolved，不静默）
            self.assertEqual(concept_candidates(SID, "ws_dlg", "3"), [])
        finally:
            scope_mod.set_scope_resolver(None)

    def test_observed_at_frozen_at_message_save_time(self):
        # 消息 created_at（epoch）决定 observed_at，而非 hook 时间
        msg = {"role": "user", "content": "我用端点法重新判断了并联。",
               "message_id": "m_ts",
               "created_at": 1760000000.0}   # 2025-10-09T08:53:20Z
        sid = dlg.register_dialogue_source(
            student_id=SID, session=self.session, message=msg)
        self.assertIsNotNone(sid)
        receipt = get_journal(SID).state().sources[sid].receipt
        self.assertEqual(receipt.observed_at, "2025-10-09T08:53:20Z")

    def test_assessment_owned_spans_not_double_counted(self):
        # 题卡作答已被 assessment 拥有 → 整条不再受理 dialogue
        from app.agents.student_model.evaluation.store import get_journal \
            as gj
        text = "我选 B，因为它们共用同一对节点。"
        mid = "m_quiz_owned"
        owned_receipt = S.SourceReceipt(
            source_id="src_own_1", source_revision=1,
            kind=S.SourceKind.ASSESSMENT,
            observed_at=S.utc_now_iso(),
            workspace_id_at_observation="ws_dlg",
            canonical_text=text, assistance_events=[],
            spans=[S.EvidenceSpanOwnership(
                start=0, end=len(text), owner="assessment")],
            source_session_ref="sess_dlg", message_ref=mid)
        gj(SID).register_source(owned_receipt)
        self.assertIsNone(self.register(text, mid))
        # 部分拥有：dialogue 只拿剩余片段
        part_receipt = S.SourceReceipt(
            source_id="src_own_2", source_revision=1,
            kind=S.SourceKind.ASSESSMENT,
            observed_at=S.utc_now_iso(),
            workspace_id_at_observation="ws_dlg",
            canonical_text=text, assistance_events=[],
            spans=[S.EvidenceSpanOwnership(
                start=0, end=5, owner="assessment")],
            source_session_ref="sess_dlg", message_ref="m_quiz_part")
        gj(SID).register_source(part_receipt)
        sid = self.register(text, "m_quiz_part")
        self.assertIsNotNone(sid)
        spans = get_journal(SID).state().sources[sid].receipt.spans
        self.assertEqual([(s.start, s.end) for s in spans], [(5, len(text))])

    def test_hook_failure_backfill_registers_missed_messages(self):
        # 上一轮 hook 崩溃（消息已保存未受理）→ 下一轮 hook 回补
        self.session.messages.append(self._msg(
            "我先按电流方向分析了一遍。", "m_missed_1"))
        self.session.messages.append({"role": "assistant",
                                      "content": "好", "message_id": "m_a1"})
        self.session.messages.append(self._msg(
            "然后用电位差核对了一遍。", "m_missed_2"))
        save_session(self.session)
        fresh = load_session("sess_dlg")
        sids = dlg.after_turn_hook(SID, fresh)
        # 两条漏评消息都被回补（旧实现只处理最后一条）
        self.assertEqual(len(sids), 2)
        # 幂等：再次 hook 不重复
        self.assertEqual(len(dlg.after_turn_hook(SID, fresh)), 0)
