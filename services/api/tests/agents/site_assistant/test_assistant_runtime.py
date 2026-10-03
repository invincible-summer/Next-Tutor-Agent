"""助手轮运行时与 API 回归（A05）。

覆盖：受理幂等与冲突、容量与并发上限、SSE 事件流、取消语义、
重启恢复 interrupted、会话删除（If-Match）、草稿端点、访客 401、
限流。全部走 StorageSandboxTestCase 沙箱。
"""
from __future__ import annotations

import asyncio
import time
import unittest
import uuid
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import runtime as rt_mod
from app.core import assistant_store as store


def _page_context(route: str = "chat") -> dict:
    return {"schema_version": 1, "route_id": route, "route_epoch": 1}


def _turn_body(text: str, revision: int, *, key: str | None = None,
               route: str = "chat") -> dict:
    return {
        "schema_version": 1,
        "client_message_id": key or str(uuid.uuid4()),
        "expected_conversation_revision": revision,
        "text": text,
        "lang": "zh",
        "timezone": "Asia/Shanghai",
        "scope": {"mode": "follow_page"},
        "page_context": _page_context(route),
    }


class _ApiCase(StorageSandboxTestCase):
    """API 层测试基座：登录用户 + TestClient + 运行时/限流重置。"""

    def setUp(self) -> None:
        super().setUp()
        rt_mod.reset_runtime()
        from app.api.v1 import assistant as api_mod
        api_mod._turn_limiter.reset()
        api_mod._guide_limiter.reset()
        from fastapi.testclient import TestClient
        from app.main import create_app
        caps_patch = mock.patch(
            "app.agents.site_assistant.capabilities.settings")
        fake_caps = caps_patch.start()
        fake_caps.site_assistant_enabled = True
        fake_caps.classroom_enabled = False
        fake_caps.llm_api_key = "sk-test"
        self._caps_patch = caps_patch
        self.app = create_app()
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="rt@example.com", username="",
            password_hash=hash_password("secret123"))
        self.auth = {"Authorization": f"Bearer {create_token(self.user.id)}"}
        self.sid = self.user.id
        # 持有 client 上下文：同一事件循环承载请求与后台轮任务
        # （裸 TestClient 每请求一个临时 loop，会把 spawn 的任务取消）。
        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        rt_mod.reset_runtime()
        self._client_cm.__exit__(None, None, None)
        self._caps_patch.stop()
        super().tearDown()

    def _create_conversation(self) -> tuple[str, int]:
        resp = self.client.post(
            "/api/v1/assistant/conversations",
            json={"client_request_id": str(uuid.uuid4())},
            headers=self.auth)
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        return body["conversation_id"], body["revision"]

    def _post_turn(self, cid: str, text: str, revision: int, **kw):
        return self.client.post(
            f"/api/v1/assistant/conversations/{cid}/turns",
            json=_turn_body(text, revision, **kw), headers=self.auth)

    def _latest_revision(self, cid: str) -> int:
        detail = self.client.get(
            f"/api/v1/assistant/conversations/{cid}", headers=self.auth)
        return detail.json()["revision"]

    def _wait_turn_terminal(self, turn_id: str, timeout: float = 5.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            resp = self.client.get(f"/api/v1/assistant/turns/{turn_id}",
                                   headers=self.auth)
            self.assertEqual(resp.status_code, 200, resp.text)
            state = resp.json().get("state")
            if state in ("completed", "cancelled", "failed", "interrupted"):
                return resp.json()
            time.sleep(0.05)
        self.fail("turn did not reach terminal state in time")


class TurnAcceptanceApiTest(_ApiCase):
    def test_full_turn_happy_path_with_sse(self) -> None:
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "备课怎么做？", revision)
        self.assertEqual(resp.status_code, 202, resp.text)
        accepted = resp.json()
        self.assertFalse(accepted["duplicate"])
        self.assertTrue(accepted["events_path"].endswith("/events"))

        snap = self._wait_turn_terminal(accepted["turn_id"])
        self.assertEqual(snap["state"], "completed")
        self.assertEqual(snap["user_message"]["role"], "user")
        answer = snap["assistant_message"]["blocks"][0]["text"]
        self.assertIn("备课上课", answer)
        self.assertEqual(snap["assistant_message"]["status"], "complete")
        detail = self.client.get(
            f"/api/v1/assistant/conversations/{cid}", headers=self.auth)
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(len(body["messages"]), 2)
        self.assertGreater(body["revision"], revision)
        self.assertIsNone(body["active_turn"])

        # SSE：终态轮回放 snapshot + turn_done，不重新执行。
        events = []
        with self.client.stream(
                "GET",
                f"/api/v1/assistant/turns/{accepted['turn_id']}/events",
                headers=self.auth) as stream:
            self.assertEqual(stream.headers.get("content-type"),
                             "text/event-stream; charset=utf-8")
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    events.append(line[7:])
        self.assertEqual(events, ["snapshot", "turn_done"])

    def test_idempotent_resubmission(self) -> None:
        cid, revision = self._create_conversation()
        key = str(uuid.uuid4())
        first = self._post_turn(cid, "你好", revision, key=key)
        self.assertEqual(first.status_code, 202)
        turn_id = first.json()["turn_id"]
        self._wait_turn_terminal(turn_id)
        dup = self._post_turn(cid, "你好", self._latest_revision(cid), key=key)
        self.assertEqual(dup.status_code, 202, dup.text)
        body = dup.json()
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["turn_id"], turn_id)
        detail = self.client.get(
            f"/api/v1/assistant/conversations/{cid}", headers=self.auth)
        self.assertEqual(len(detail.json()["messages"]), 2)

    def test_idempotency_conflict_on_different_body(self) -> None:
        cid, revision = self._create_conversation()
        key = str(uuid.uuid4())
        first = self._post_turn(cid, "你好", revision, key=key)
        self._wait_turn_terminal(first.json()["turn_id"])
        clash = self._post_turn(cid, "不同的问题",
                                self._latest_revision(cid), key=key)
        self.assertEqual(clash.status_code, 409)
        self.assertEqual(clash.json()["error"]["code"],
                         "idempotency_conflict")

    def test_conversation_changed_on_stale_revision(self) -> None:
        cid, revision = self._create_conversation()
        first = self._post_turn(cid, "第一问", revision)
        self._wait_turn_terminal(first.json()["turn_id"])
        stale = self._post_turn(cid, "第二问", revision)  # 旧 revision
        self.assertEqual(stale.status_code, 409)
        err = stale.json()["error"]
        self.assertEqual(err["code"], "conversation_changed")
        self.assertIn("latest_revision", err.get("extra", {}))

    def test_guest_unauthorized(self) -> None:
        resp = self.client.get("/api/v1/assistant/conversations")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"]["code"],
                         "guest_disabled")

    def test_conversation_crud_and_delete_ifmatch(self) -> None:
        cid, revision = self._create_conversation()
        listing = self.client.get("/api/v1/assistant/conversations",
                                  headers=self.auth)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total"], 1)
        bad = self.client.delete(
            f"/api/v1/assistant/conversations/{cid}",
            headers={**self.auth, "If-Match": "999"})
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(bad.json()["error"]["code"], "revision_conflict")
        ok = self.client.delete(
            f"/api/v1/assistant/conversations/{cid}",
            headers={**self.auth, "If-Match": str(revision)})
        self.assertEqual(ok.status_code, 204)
        again = self.client.delete(
            f"/api/v1/assistant/conversations/{cid}", headers=self.auth)
        self.assertEqual(again.status_code, 204)  # 幂等
        missing = self.client.get(
            f"/api/v1/assistant/conversations/{cid}", headers=self.auth)
        self.assertEqual(missing.status_code, 404)

    def test_turn_rate_limited(self) -> None:
        cid, revision = self._create_conversation()
        with mock.patch(
            "app.api.v1.assistant._turn_limiter") as fake_limiter:
            fake_limiter.allow = mock.Mock(return_value=(False, 4.0))
            resp = self._post_turn(cid, "太快了", revision)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error"]["code"], "rate_limited")
        self.assertEqual(resp.headers.get("Retry-After"), "5")


async def _slow_execute(rt, turn):  # noqa: ANN001
    await asyncio.sleep(1.5)
    message = next(m for m in turn.record["messages"]
                   if m["turn_id"] == turn.turn_id
                   and m["role"] == "assistant")
    message["status"] = "complete"
    message["blocks"] = [{"block_id": "a1", "type": "markdown", "text": "ok"}]
    turn.record["turns"][turn.turn_id]["state"] = "completed"
    turn.record["revision"] = int(turn.record.get("revision", 1)) + 1


class TurnRuntimeUnitTest(_ApiCase):
    def test_busy_and_concurrency_limits(self) -> None:
        cid, revision = self._create_conversation()
        with mock.patch("app.agents.site_assistant.service.execute_turn",
                        _slow_execute):
            first = self._post_turn(cid, "慢问题", revision)
            self.assertEqual(first.status_code, 202)
            busy = self._post_turn(cid, "并发", self._latest_revision(cid))
            self.assertEqual(busy.status_code, 409)
            self.assertEqual(busy.json()["error"]["code"],
                             "conversation_busy")
            cid2, rev2 = self._create_conversation()
            second = self._post_turn(cid2, "另一会话", rev2)
            self.assertEqual(second.status_code, 202)
            cid3, rev3 = self._create_conversation()
            third = self._post_turn(cid3, "第三会话", rev3)
            self.assertEqual(third.status_code, 429)
            self.assertEqual(third.json()["error"]["code"],
                             "concurrency_limit")
            self._wait_turn_terminal(first.json()["turn_id"], timeout=6)
            self._wait_turn_terminal(second.json()["turn_id"], timeout=6)

    def test_cancel_semantics(self) -> None:
        cid, revision = self._create_conversation()
        with mock.patch("app.agents.site_assistant.service.execute_turn",
                        _slow_execute):
            accepted = self._post_turn(cid, "取消我", revision)
            self.assertEqual(accepted.status_code, 202)
            turn_id = accepted.json()["turn_id"]
            cancel = self.client.post(
                f"/api/v1/assistant/turns/{turn_id}/cancel",
                json={"client_request_id": str(uuid.uuid4())},
                headers=self.auth)
            self.assertEqual(cancel.status_code, 200, cancel.text)
            snap = self._wait_turn_terminal(turn_id)
            self.assertEqual(snap["state"], "cancelled")
            self.assertEqual(snap["user_message"]["blocks"][0]["text"],
                             "取消我")
            self.assertEqual(snap["assistant_message"]["status"],
                             "cancelled")

    def test_capacity_full(self) -> None:
        cid, revision = self._create_conversation()
        record = store.load_conversation(self.sid, cid)
        record["messages"] = [{
            "message_id": store.mint_message_id(), "seq": i + 1,
            "role": "user", "created_at": store.utc_now_iso(),
            "turn_id": store.mint_turn_id(), "status": "complete",
            "scope": {"mode": "workspace", "workspace_ids": [],
                      "scope_revisions": {}},
            "blocks": [{"block_id": "u", "type": "markdown", "text": "x"}],
            "sources": []} for i in range(
                store.MAX_MESSAGES_PER_CONVERSATION)]
        record["revision"] += 1
        store.save_conversation(self.sid, record)
        full = self._post_turn(cid, "再问一句", self._latest_revision(cid))
        self.assertEqual(full.status_code, 409)
        self.assertEqual(full.json()["error"]["code"], "conversation_full")


class RecoveryTest(_ApiCase):
    def test_startup_marks_interrupted(self) -> None:
        # 直接在存储中构造“进程退出时在途”的轮（无孤儿任务，确定）。
        record = store.create_conversation(
            self.sid, client_request_id=str(uuid.uuid4()))
        cid = record["conversation_id"]
        turn_id = store.mint_turn_id()
        now = store.utc_now_iso()
        scope = {"mode": "workspace", "workspace_ids": [],
                 "scope_revisions": {}}
        record["messages"] = [
            {"message_id": store.mint_message_id(), "seq": 1, "role": "user",
             "created_at": now, "turn_id": turn_id, "status": "complete",
             "scope": scope,
             "blocks": [{"block_id": "u1", "type": "markdown",
                         "text": "中断前的问题"}], "sources": []},
            {"message_id": store.mint_message_id(), "seq": 2,
             "role": "assistant", "created_at": now, "turn_id": turn_id,
             "status": "streaming", "scope": scope,
             "blocks": [{"block_id": "a1", "type": "markdown",
                         "text": "中断前的部分回答"}], "sources": []},
        ]
        record["turns"][turn_id] = {
            "state": "running", "client_message_id": str(uuid.uuid4()),
            "cancel_requested": False, "created_at": now,
            "updated_at": now, "error": None}
        record["revision"] += 1
        store.save_conversation(self.sid, record)

        fresh = rt_mod.AssistantRuntime()
        asyncio.run(fresh.start())

        snap = self._wait_turn_terminal(turn_id, timeout=2.0)
        self.assertEqual(snap["state"], "interrupted")
        self.assertEqual(snap["assistant_message"]["status"], "interrupted")


class DraftApiTest(_ApiCase):
    def test_draft_flow(self) -> None:
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_" + "5" * 32,
            "action_id": store.mint_action_id(),
            "prefill": {"kind": "chat", "text": "hi"},
            "source_ids": []})
        got = self.client.get(
            f"/api/v1/assistant/drafts/{draft['draft_id']}",
            headers=self.auth)
        self.assertEqual(got.status_code, 200)
        consumed = self.client.post(
            f"/api/v1/assistant/drafts/{draft['draft_id']}/consume",
            json={"result_entity": {"kind": "chat", "id": "chat_9"}},
            headers=self.auth)
        self.assertEqual(consumed.status_code, 200, consumed.text)
        again = self.client.post(
            f"/api/v1/assistant/drafts/{draft['draft_id']}/consume",
            json={}, headers=self.auth)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["code"],
                         "draft_already_consumed")
        deleted = self.client.delete(
            f"/api/v1/assistant/drafts/{draft['draft_id']}",
            headers=self.auth)
        self.assertEqual(deleted.status_code, 204)
        missing = self.client.get(
            f"/api/v1/assistant/drafts/{draft['draft_id']}",
            headers=self.auth)
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
