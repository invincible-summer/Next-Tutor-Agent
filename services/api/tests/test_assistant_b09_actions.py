"""B09 画像/记忆/助手偏好回归（plan.md §21.3/§22.4）。

覆盖：assistant.preferences（提案→执行→回答风格注入→撤销）、
memory.set_window（clamp+撤销）、profile.update（白名单+学段同步+撤销）、
set_local_preference 本地 UI 偏好 payload（客户端执行，不经服务端写）。
"""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.core import assistant_store as store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class _B09Case(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b09_" + _hex()
        from app.identity import store as id_store
        from app.identity.security import hash_password
        id_store.create_user("b09_" + _hex() + "@example.com", "B09",
                             hash_password("pw123456"), user_id=self.sid)
        self.cid = "astc_b09"
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": self.cid,
            "turn_id": "astt_b09", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, approval_id: str | None = None):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-b09", route_epoch=1,
            approval_id=approval_id)

    def _preview(self, action_id: str):
        from app.agents.site_assistant import previews
        return previews.build_preview(self.sid, action_id)

    def _approve(self, action_id: str) -> str:
        from app.agents.site_assistant import previews
        preview = self._preview(action_id)
        approval = previews.approve(
            self.sid, action_id, preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        return approval["approval_id"]

    def _undo(self, action_id: str):
        from app.agents.site_assistant import undo as undo_svc
        from app.agents.site_assistant.actions import locate_action
        _rec, _c, action = locate_action(self.sid, action_id)
        return undo_svc.undo_action(
            self.sid, action_id, client_request_id="cur_" + _hex(),
            expected_result_revision=str(
                (action.get("business_result") or {}).get(
                    "result_revision") or ""))


class AssistantPreferencesTest(_B09Case):
    def test_length_preference_flow(self) -> None:
        from app.core.assistant_store import load_preferences
        self._add_action("asta_pref", {
            "kind": "domain_write", "operation": "assistant.preferences",
            "input": {"response_length": "short"}})
        preview = self._preview("asta_pref")
        self.assertEqual(preview["approval"], "intent_sufficient")
        self.assertEqual(preview["changes"][0]["after"], "short")
        result = self._execute("asta_pref")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(load_preferences(self.sid)["response_length"],
                         "short")
        # 撤销恢复默认。
        out = self._undo("asta_pref")
        self.assertTrue(out["undone"])
        self.assertEqual(load_preferences(self.sid)["response_length"],
                         "standard")

    def test_style_directive_injected_into_answer_prompt(self) -> None:
        from app.core.assistant_store import save_preferences
        from app.agents.site_assistant import service
        save_preferences(self.sid, {"response_length": "short",
                                    "tone": "encouraging"})
        directive = service._style_directive(self.sid)
        self.assertIn("简短", directive)
        self.assertIn("鼓励", directive)

    def test_preferences_route_roundtrip(self) -> None:
        from app.core.assistant_store import load_preferences, save_preferences
        merged = save_preferences(self.sid, {"tone": "encouraging"})
        self.assertEqual(merged["tone"], "encouraging")
        self.assertEqual(load_preferences(self.sid)["tone"], "encouraging")
        # 白名单外的键不落盘。
        save_preferences(self.sid, {"theme": "dark"})  # type: ignore[dict-item]
        self.assertNotIn("theme", load_preferences(self.sid))


class MemoryWindowTest(_B09Case):
    def test_set_and_undo(self) -> None:
        from app.agents.memory.prompt_memory import get_user_window
        self._add_action("asta_mw", {
            "kind": "domain_write", "operation": "memory.set_window",
            "input": {"window": 30}})
        preview = self._preview("asta_mw")
        self.assertEqual(preview["changes"][0]["before"],
                         str(get_user_window(self.sid)))
        self.assertEqual(preview["changes"][0]["after"], "30")
        result = self._execute("asta_mw")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(get_user_window(self.sid), 30)
        out = self._undo("asta_mw")
        self.assertTrue(out["undone"])
        self.assertNotEqual(get_user_window(self.sid), 30)


class ProfileUpdateTest(_B09Case):
    def _make_user(self) -> None:
        from app.identity import store as id_store
        from app.identity.security import hash_password
        id_store.create_user("b09@example.com", "B09",
                             hash_password("pw123456"), user_id=self.sid)

    def test_grade_update_and_undo(self) -> None:
        self._make_user()
        from app.identity import store as id_store
        self._add_action("asta_pu", {
            "kind": "domain_write", "operation": "profile.update",
            "input": {"grade": "高中"}})
        preview = self._preview("asta_pu")
        self.assertEqual(preview["approval"], "review_required")
        result = self._execute("asta_pu", self._approve("asta_pu"))
        self.assertEqual(result["action"]["state"], "succeeded")
        user = id_store.get_by_id(self.sid)
        self.assertEqual(user.profile.grade, "高中")
        # 学段同步进 StudentModel（沿用原服务）。
        from app.agents.student_model import get_student_model
        self.assertEqual(get_student_model(self.sid).profile.grade, "高中")
        out = self._undo("asta_pu")
        self.assertTrue(out["undone"])
        self.assertEqual(id_store.get_by_id(self.sid).profile.grade, "本科")

    def test_unknown_user_404(self) -> None:
        # 画像操作针对不存在的账号：用独立 sid 建动作（无账号记录）。
        ghost = "usr_b09_ghost_" + _hex()
        store.save_conversation(ghost, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {},
            "actions": {
                "asta_pu2": {
                    "action_id": "asta_pu2", "conversation_id": self.cid,
                    "turn_id": "astt_b09", "label": "profile.update",
                    "payload": {
                        "kind": "domain_write",
                        "operation": "profile.update",
                        "input": {"name": "无名"}},
                    "execution": "user_click", "state": "proposed",
                    "created_at": store.utc_now_iso(),
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "business_result": {"kind": "none"}}},
            "accepted": {}})
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(ghost, "asta_pu2")
        self.assertEqual(ctx.exception.code, "entity_not_found")


class LocalPreferenceProposalTest(_B09Case):
    def test_theme_and_pref_proposals(self) -> None:
        import asyncio
        from app.agents.site_assistant import policy
        from app.agents.site_assistant.intent import parse_intent

        # 本地 UI 偏好：payload kind=set_local_preference，不走服务端写。
        parsed = asyncio.run(parse_intent("切换深色主题", lang="zh"))
        self.assertEqual(parsed.kind, "prepare_action")
        actions = policy.decide_actions(
            parsed, [], {}, conversation_id=self.cid, turn_id="t1",
            lang="zh")
        self.assertEqual(len(actions), 1)
        payload = actions[0]["payload"]
        self.assertEqual(payload["kind"], "set_local_preference")
        self.assertEqual(payload["key"], "theme")
        self.assertEqual(payload["value"], "dark")

        # 服务端偏好：以后简短一点 → assistant.preferences 域写。
        parsed = asyncio.run(parse_intent("以后回答简短一点", lang="zh"))
        self.assertEqual(parsed.kind, "prepare_action")
        write = policy._propose_domain_write(parsed, [], zh=True,
                                             page_context=None,
                                             student_id=self.sid)
        self.assertEqual(write["operation"], "assistant.preferences")
        self.assertEqual(write["input"],
                         {"response_length": "short"})

        # 记忆窗口。
        parsed = asyncio.run(parse_intent("把记忆窗口改成20轮", lang="zh"))
        write = policy._propose_domain_write(parsed, [], zh=True,
                                             page_context=None,
                                             student_id=self.sid)
        self.assertEqual(write["operation"], "memory.set_window")
        self.assertEqual(write["input"], {"window": 20})

        # 疑问语气不触发。
        parsed = asyncio.run(parse_intent("怎么设置简短回答？", lang="zh"))
        self.assertNotEqual(parsed.kind, "prepare_action")


if __name__ == "__main__":
    unittest.main()
