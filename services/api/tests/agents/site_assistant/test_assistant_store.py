"""站内助手存储回归（A04）。

覆盖：原子写与损坏区分、受理幂等、容量上限（200 条 / 2 MiB + 预留）、
草稿 TTL 与 consume 幂等、会话删除级联草稿与索引、来源反向索引与失效
标记、owner generation、账号清理/孤儿扫描/沙箱隔离四处登记。
全部测试继承 StorageSandboxTestCase，不触生产根。
"""
from __future__ import annotations

import json
import unittest
import uuid

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import store

SID = "usr_store_test"
OTHER = "usr_store_other"


def _mk_conversation(title: str = "测试会话") -> dict:
    return store.create_conversation(
        SID, client_request_id=str(uuid.uuid4()), title=title)


def _mk_message(record: dict, seq: int, text: str = "你好") -> dict:
    return {
        "message_id": store.mint_message_id(),
        "seq": seq,
        "role": "user",
        "created_at": store.utc_now_iso(),
        "turn_id": store.mint_turn_id(),
        "status": "complete",
        "scope": {"mode": "workspace", "workspace_ids": [],
                  "scope_revisions": {}},
        "blocks": [{"block_id": "b1", "type": "markdown", "text": text}],
        "sources": [],
    }


class ConversationStoreTest(StorageSandboxTestCase):
    def test_create_is_idempotent_by_client_request(self) -> None:
        key = str(uuid.uuid4())
        first = store.create_conversation(SID, client_request_id=key)
        second = store.create_conversation(SID, client_request_id=key)
        self.assertEqual(first["conversation_id"], second["conversation_id"])
        items, total = store.list_conversations(SID)
        self.assertEqual(total, 1)

    def test_load_missing_vs_corrupt(self) -> None:
        self.assertIsNone(store.load_conversation(SID, "astc_" + "0" * 32))
        record = _mk_conversation()
        path = store._conversation_path(SID, record["conversation_id"])
        path.write_text("{ broken json", encoding="utf-8")
        with self.assertRaises(store.AssistantStoreError):
            store.load_conversation(SID, record["conversation_id"])

    def test_save_updates_index_and_list_order(self) -> None:
        a = _mk_conversation("A")
        b = _mk_conversation("B")
        a["messages"].append(_mk_message(a, 1))
        store.save_conversation(SID, a)
        items, total = store.list_conversations(SID)
        self.assertEqual(total, 2)
        self.assertEqual(items[0]["conversation_id"], a["conversation_id"])
        by_id = {i["conversation_id"]: i for i in items}
        self.assertEqual(by_id[b["conversation_id"]]["title"], "B")
        self.assertEqual(by_id[a["conversation_id"]]["message_count"], 1)

    def test_capacity_limits(self) -> None:
        record = _mk_conversation()
        record["messages"] = [
            _mk_message(record, i) for i in range(
                store.MAX_MESSAGES_PER_CONVERSATION
                - store.TURN_RESERVE_MESSAGES)]
        cap = store.conversation_capacity(record)
        self.assertTrue(cap["can_accept_turn"])
        record["messages"].append(_mk_message(record, 999))
        cap = store.conversation_capacity(record)
        self.assertFalse(cap["can_accept_turn"])
        self.assertEqual(cap["reason"], "message_limit")
        # 体积上限：一条消息撑过 2 MiB - 256 KiB 预留
        big = _mk_conversation()
        big["messages"] = [_mk_message(big, 1, text="字" * (2 * 1024 * 1024))]
        cap = store.conversation_capacity(big)
        self.assertFalse(cap["can_accept_turn"])
        self.assertEqual(cap["reason"], "size_limit")

    def test_delete_cascades_drafts_and_index(self) -> None:
        record = _mk_conversation()
        draft = store.create_draft(SID, {
            "conversation_id": record["conversation_id"],
            "action_id": store.mint_action_id(),
            "prefill": {"kind": "note", "title": "t", "markdown": ""},
            "source_ids": []})
        store.register_source_refs(SID, [{
            "origin_kind": "learning_evidence", "origin_id": "src_x",
            "source_id": store.mint_source_ref_id(),
            "conversation_id": record["conversation_id"],
            "message_id": "astm_x", "revision": "r1"}])
        self.assertTrue(store.delete_conversation(
            SID, record["conversation_id"]))
        self.assertIsNone(store.load_conversation(
            SID, record["conversation_id"]))
        self.assertIsNone(store.load_draft(SID, draft["draft_id"]))
        items, _ = store.list_conversations(SID)
        self.assertEqual(items, [])
        self.assertEqual(store.lookup_origin_refs(
            SID, "learning_evidence", "src_x"), [])
        # 幂等重复删除
        self.assertTrue(store.delete_conversation(
            SID, record["conversation_id"]))

    def test_rebuild_index_from_files(self) -> None:
        a = _mk_conversation("A")
        a["messages"].append(_mk_message(a, 1))
        store.save_conversation(SID, a)
        _mk_conversation("B")
        # 索引丢失后可重建
        store._index_path(SID).unlink()
        items, total = store.list_conversations(SID)
        self.assertEqual(total, 2)
        titles = {i["title"] for i in items}
        self.assertEqual(titles, {"A", "B"})

    def test_isolation_between_students(self) -> None:
        mine = _mk_conversation()
        theirs = store.create_conversation(
            OTHER, client_request_id=str(uuid.uuid4()))
        self.assertNotEqual(mine["conversation_id"],
                            theirs["conversation_id"])
        items, _ = store.list_conversations(SID)
        self.assertEqual(len(items), 1)


class DraftStoreTest(StorageSandboxTestCase):
    def test_draft_ttl_and_consume(self) -> None:
        draft = store.create_draft(SID, {
            "conversation_id": "astc_" + "1" * 32,
            "action_id": store.mint_action_id(),
            "prefill": {"kind": "chat", "text": "hi"},
            "source_ids": []})
        loaded = store.load_draft(SID, draft["draft_id"])
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.get("expired"))
        consumed = store.consume_draft(
            SID, draft["draft_id"],
            result_entity={"kind": "chat", "id": "chat_1"})
        self.assertTrue(consumed["consumed"])
        self.assertEqual(consumed["result_entity"]["id"], "chat_1")
        # 重复消费幂等
        again = store.consume_draft(SID, draft["draft_id"])
        self.assertTrue(again["consumed"])
        self.assertEqual(again["result_entity"]["id"], "chat_1")

    def test_purge_expired_drafts(self) -> None:
        fresh = store.create_draft(SID, {
            "conversation_id": "astc_" + "2" * 32,
            "action_id": store.mint_action_id(),
            "prefill": {"kind": "chat", "text": "x"},
            "source_ids": []})
        old = store.create_draft(SID, {
            "conversation_id": "astc_" + "3" * 32,
            "action_id": store.mint_action_id(),
            "prefill": {"kind": "chat", "text": "y"},
            "source_ids": []})
        path = store._draft_path(SID, old["draft_id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["expires_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        removed = store.purge_expired_drafts(SID)
        self.assertEqual(removed, 1)
        self.assertIsNone(store.load_draft(SID, old["draft_id"]))
        self.assertIsNotNone(store.load_draft(SID, fresh["draft_id"]))
        # 无目录时不创建、不报错
        self.assertEqual(store.purge_expired_drafts(OTHER), 0)


class ReferencesAndGenerationTest(StorageSandboxTestCase):
    def test_source_refs_and_invalidation(self) -> None:
        store.register_source_refs(SID, [{
            "origin_kind": "learning_evidence", "origin_id": "src_1",
            "source_id": store.mint_source_ref_id(),
            "conversation_id": "astc_" + "4" * 32,
            "message_id": "astm_1", "revision": "r1"}])
        refs = store.lookup_origin_refs(SID, "learning_evidence", "src_1")
        self.assertEqual(len(refs), 1)
        self.assertFalse(store.is_origin_invalidated(
            SID, "learning_evidence", "src_1"))
        store.mark_origin_invalidated(SID, "learning_evidence", "src_1")
        self.assertTrue(store.is_origin_invalidated(
            SID, "learning_evidence", "src_1"))
        # 重复登记幂等
        store.mark_origin_invalidated(SID, "learning_evidence", "src_1")

    def test_owner_generation(self) -> None:
        self.assertEqual(store.current_owner_generation(SID), 1)
        self.assertEqual(store.bump_owner_generation(SID), 2)
        self.assertEqual(store.current_owner_generation(SID), 2)


class LifecycleIntegrationTest(StorageSandboxTestCase):
    def test_scan_and_clear_cover_assistant_root(self) -> None:
        from app.core import account_data
        _mk_conversation("hello")
        buckets = account_data.scan_storage([SID]).get(SID, {})
        self.assertGreater(buckets.get("assistant_bytes", 0), 0)
        self.assertGreater(buckets.get("total_bytes", 0), 0)
        report = account_data.clear_chat_data(SID, scope="all")
        self.assertIn("assistant_freed_bytes", report)
        self.assertGreater(report["assistant_freed_bytes"], 0)
        self.assertFalse(store._student_root(SID).exists())

    def test_orphan_scan_assistant_category(self) -> None:
        from app.core import orphan_cleanup
        _mk_conversation()
        # 不含该用户 → assistant 目录成为孤儿
        scan = orphan_cleanup.scan_orphans([OTHER])
        self.assertGreaterEqual(scan["categories"]["assistant"]["items"], 1)
        # 含该用户 → 不出现
        scan_ok = orphan_cleanup.scan_orphans([SID])
        self.assertEqual(scan_ok["categories"]["assistant"]["items"], 0)

    def test_sandbox_redirects_assistant_root(self) -> None:
        from app.agents.site_assistant import store as asst
        self.assertTrue(str(asst._ASSISTANT_DIR).startswith(
            str(self._tmp.name)))
        _mk_conversation()
        self.assertTrue(asst._ASSISTANT_DIR.joinpath(SID).exists())


if __name__ == "__main__":
    unittest.main()
