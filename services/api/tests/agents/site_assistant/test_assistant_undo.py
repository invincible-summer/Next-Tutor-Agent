"""§21.4/§21.5 撤销协议回归：真实补偿、只回滚自己的变更、窗口与幂等。

覆盖：每类可逆操作的成功撤销；并发修改/完成后的 409 指引回原模块；
expected_result_revision 不一致 409；窗口过期 410；不可逆操作 422；
同 client_request_id 幂等重放；重复撤销拒绝。
"""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.core import assistant_store as store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class _UndoCase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_undo_" + _hex()
        self.cid = "astc_undo"
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": self.cid,
            "turn_id": "astt_undo", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, **kw):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-undo", route_epoch=1, **kw)

    def _approve(self, action_id: str):
        """review_required 操作先走预览+批准（§21.4）。"""
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, action_id)
        approval = previews.approve(
            self.sid, action_id, preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        return approval["approval_id"]

    def _undo(self, action_id: str, result_revision: str = "",
              client_request_id: str = ""):
        from app.agents.site_assistant import undo as undo_svc
        from app.agents.site_assistant.actions import locate_action
        if not result_revision:
            _rec, _c, action = locate_action(self.sid, action_id)
            result_revision = str(
                (action.get("business_result") or {}).get(
                    "result_revision") or "")
        return undo_svc.undo_action(
            self.sid, action_id,
            client_request_id=client_request_id or "cur_" + _hex(),
            expected_result_revision=result_revision)

    def _make_session(self, title: str = "撤销用对话") -> str:
        from app.core.session import TutorSession, save_session
        s = TutorSession(title=title, student_id=self.sid)
        save_session(s)
        return s.session_id

    def _make_workspace(self, name: str = "撤销区") -> str:
        from app.core.workspace import Workspace, save_workspace
        return save_workspace(Workspace(name=name, student_id=self.sid))


class WorkspaceCreateUndoTest(_UndoCase):
    def test_undo_archives_untouched_workspace(self) -> None:
        from app.core.workspace import load_workspace
        from app.core.trash import list_items
        self._add_action("asta_uwc", {
            "kind": "domain_write", "operation": "workspace.create",
            "input": {"name": "撤销专区"}})
        result = self._execute("asta_uwc")
        self.assertEqual(result["action"]["state"], "succeeded")
        wid = result["business_result"]["entity_id"]
        out = self._undo("asta_uwc", client_request_id="cur_uwc")
        self.assertTrue(out["undone"])
        self.assertIsNone(load_workspace(wid))       # 已归档移出工作区
        # 归档条目真实存在（§21.5：真实补偿，不从缓存偷偷写回）。
        self.assertTrue(any(i.get("original_id") == wid
                            for i in list_items(self.sid, "workspace")))
        # 幂等：同 client_request_id 重放返回同一结果，不二次归档。
        again = self._undo("asta_uwc", client_request_id="cur_uwc")
        self.assertTrue(again["undone"])
        self.assertEqual(
            len([i for i in list_items(self.sid, "workspace")
                 if i.get("original_id") == wid]), 1)

    def test_undo_rejects_when_workspace_got_sessions(self) -> None:
        from app.core.workspace import load_workspace
        from app.agents.site_assistant.actions import ActionRejected
        from app.core.workspace import add_session_to_workspace
        sid = self._make_session()
        self._add_action("asta_uwcs", {
            "kind": "domain_write", "operation": "workspace.create",
            "input": {"name": "被占用区"}})
        result = self._execute("asta_uwcs")
        wid = result["business_result"]["entity_id"]
        add_session_to_workspace(wid, sid)           # 创建后出现依赖
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_uwcs")
        self.assertEqual(ctx.exception.code, "target_changed")
        self.assertIsNotNone(load_workspace(wid))    # 不删有依赖的辅导区


class ChatActionsUndoTest(_UndoCase):
    def test_rename_undo_restores_previous_title(self) -> None:
        from app.core.session import load_session
        from app.agents.site_assistant.actions import ActionRejected
        sid = self._make_session("旧标题")
        self._add_action("asta_urn", {
            "kind": "domain_write", "operation": "chat.rename",
            "input": {"session_id": sid, "title": "新标题"}})
        self._execute("asta_urn")
        self.assertEqual(load_session(sid).title, "新标题")
        out = self._undo("asta_urn")
        self.assertTrue(out["undone"])
        self.assertEqual(load_session(sid).title, "旧标题")

    def test_rename_undo_rejects_after_further_edit(self) -> None:
        from app.core.session import load_session, rename_session
        from app.agents.site_assistant.actions import ActionRejected
        sid = self._make_session("旧标题")
        self._add_action("asta_urn2", {
            "kind": "domain_write", "operation": "chat.rename",
            "input": {"session_id": sid, "title": "新标题"}})
        self._execute("asta_urn2")
        rename_session(sid, "用户又改了")            # 并发编辑
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_urn2")
        self.assertEqual(ctx.exception.code, "target_changed")
        self.assertEqual(load_session(sid).title, "用户又改了")

    def test_move_undo_moves_session_back(self) -> None:
        from app.core.session import load_session, save_session
        from app.core.workspace import add_session_to_workspace
        sid = self._make_session()
        before_ws = self._make_workspace("原辅导区")
        target = self._make_workspace("目标辅导区")
        add_session_to_workspace(before_ws, sid)
        s = load_session(sid)
        s.workspace_id = before_ws
        save_session(s)
        self._add_action("asta_umv", {
            "kind": "domain_write", "operation": "chat.move_workspace",
            "input": {"session_id": sid, "workspace_id": target}})
        self._execute("asta_umv", main_loop=None)
        self.assertEqual(load_session(sid).workspace_id, target)
        out = self._undo("asta_umv")
        self.assertTrue(out["undone"])
        self.assertEqual(load_session(sid).workspace_id, before_ws)

    def test_archive_undo_restores_session(self) -> None:
        from app.core.session import load_session
        sid = self._make_session("要归档再撤销")
        self._add_action("asta_uar", {
            "kind": "domain_write", "operation": "chat.archive",
            "input": {"session_id": sid}})
        self._execute("asta_uar")
        self.assertIsNone(load_session(sid))
        out = self._undo("asta_uar")
        self.assertTrue(out["undone"])
        self.assertIsNotNone(load_session(sid))      # 真实 restore


class LibraryUndoTest(_UndoCase):
    def test_rename_file_undo(self) -> None:
        from app.core.library import load_library, save_library
        lib = load_library(self.sid)
        meta = lib.add_file("", "原名.txt", "内容")
        save_library(lib)
        self._add_action("asta_urnf", {
            "kind": "domain_write", "operation": "library.rename_file",
            "input": {"file_id": meta["id"], "filename": "新名.txt"}})
        self._execute("asta_urnf")
        self.assertEqual(
            load_library(self.sid).find_file(meta["id"])["filename"],
            "新名.txt")
        out = self._undo("asta_urnf")
        self.assertTrue(out["undone"])
        self.assertEqual(
            load_library(self.sid).find_file(meta["id"])["filename"],
            "原名.txt")

    def test_move_file_undo(self) -> None:
        from app.core.library import load_library, save_library
        lib = load_library(self.sid)
        meta = lib.add_file("", "移动.txt", "内容")
        folder = lib.create_folder("中转夹")
        save_library(lib)
        self._add_action("asta_umvf", {
            "kind": "domain_write", "operation": "library.move_file",
            "input": {"file_id": meta["id"], "folder_id": folder["id"]}})
        self._execute("asta_umvf")
        self.assertEqual(
            load_library(self.sid).find_file(meta["id"])["folder_id"],
            folder["id"])
        out = self._undo("asta_umvf")
        self.assertTrue(out["undone"])
        self.assertEqual(
            load_library(self.sid).find_file(meta["id"])["folder_id"], "")

    def test_create_folder_undo_only_when_empty(self) -> None:
        from app.core.library import load_library, save_library
        self._add_action("asta_ucf", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "撤销夹"}})
        result = self._execute("asta_ucf")
        fid = result["business_result"]["entity_id"]
        out = self._undo("asta_ucf")
        self.assertTrue(out["undone"])
        self.assertIsNone(load_library(self.sid).find_folder(fid))

        # 非空文件夹（含文件）不可在此撤销。
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_ucf2", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "占用夹"}})
        result = self._execute("asta_ucf2")
        fid2 = result["business_result"]["entity_id"]
        lib = load_library(self.sid)
        lib.add_file(fid2, "文件.txt", "内容")
        save_library(lib)
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_ucf2")
        self.assertEqual(ctx.exception.code, "target_changed")
        self.assertIsNotNone(load_library(self.sid).find_folder(fid2))


class NoteTaskUndoTest(_UndoCase):
    def test_note_create_undo_archives_unedited(self) -> None:
        from app.core import notes as notes_store
        self._add_action("asta_unc", {
            "kind": "domain_write", "operation": "note.create",
            "input": {"title": "撤销笔记", "content": "原文"}})
        result = self._execute("asta_unc",
                               approval_id=self._approve("asta_unc"))
        note_id = result["business_result"]["entity_id"]
        self.assertIsNotNone(
            notes_store.load_vault(self.sid).find_note(note_id))
        out = self._undo("asta_unc")
        self.assertTrue(out["undone"])
        self.assertIsNone(
            notes_store.load_vault(self.sid).find_note(note_id))

    def test_note_undo_rejects_edited_note(self) -> None:
        from app.core import notes as notes_store
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_unc2", {
            "kind": "domain_write", "operation": "note.create",
            "input": {"title": "会被改的笔记", "content": "原文"}})
        result = self._execute("asta_unc2",
                               approval_id=self._approve("asta_unc2"))
        note_id = result["business_result"]["entity_id"]
        vault = notes_store.load_vault(self.sid)
        vault.write_note(note_id, "用户编辑后的内容")  # 创建后被编辑
        notes_store.save_vault(vault)
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_unc2")
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_task_create_undo_deletes_untouched(self) -> None:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        self._add_action("asta_utc", {
            "kind": "domain_write", "operation": "task.create",
            "input": {"title": "撤销任务", "day": ""}})
        result = self._execute("asta_utc")
        task_id = result["business_result"]["entity_id"]
        service = get_orchestration_service()
        self.assertTrue(any(t.id == task_id
                            for t in service._load(self.sid).daily_tasks))
        out = self._undo("asta_utc")
        self.assertTrue(out["undone"])
        self.assertFalse(any(t.id == task_id
                             for t in service._load(self.sid).daily_tasks))


class SourcesUndoTest(_UndoCase):
    def test_update_sources_undo_reverses_own_delta_only(self) -> None:
        from app.core.workspace import load_workspace, save_workspace
        from app.core.library import load_library as _ll, save_library as _sl
        from app.core import textbook as tb_store
        wid = self._make_workspace()
        # 两本教材进集合。
        fids = []
        for i, title in enumerate(("教材一", "教材二")):
            lib = _ll(self.sid)
            meta = lib.add_file("", f"{title}.pdf", "正文")
            _sl(lib)
            tb_id = f"tb_undo{i}_{_hex()}"
            rows = tb_store._load_raw(self.sid)
            rows.append({"id": tb_id, "title": title,
                         "file_id": meta["id"], "status": "ready",
                         "kind": "single", "created_at": 0, "updated_at": 0})
            tb_store._save(self.sid, rows)
            fids.append(str(meta["id"]))
        ws = load_workspace(wid)
        ws.selected_file_ids = [fids[0]]
        save_workspace(ws)
        self._add_action("asta_uus", {
            "kind": "domain_write", "operation": "workspace.update_sources",
            "input": {"workspace_id": wid, "add_file_ids": [fids[1]]}})
        self._execute("asta_uus")
        self.assertEqual(load_workspace(wid).selected_file_ids,
                         [fids[0], fids[1]])
        out = self._undo("asta_uus")
        self.assertTrue(out["undone"])
        self.assertEqual(load_workspace(wid).selected_file_ids, [fids[0]])

    def test_update_sources_undo_rejects_concurrent_write(self) -> None:
        from app.core.workspace import load_workspace, save_workspace
        from app.core.library import load_library as _ll, save_library as _sl
        from app.core import textbook as tb_store
        wid = self._make_workspace()
        lib = _ll(self.sid)
        meta = lib.add_file("", "并发教材.pdf", "正文")
        _sl(lib)
        rows = tb_store._load_raw(self.sid)
        rows.append({"id": "tb_cc_" + _hex(), "title": "并发教材",
                     "file_id": meta["id"], "status": "ready",
                     "kind": "single", "created_at": 0, "updated_at": 0})
        tb_store._save(self.sid, rows)
        fid = str(meta["id"])
        ws = load_workspace(wid)
        ws.selected_file_ids = []
        save_workspace(ws)
        self._add_action("asta_uus2", {
            "kind": "domain_write", "operation": "workspace.update_sources",
            "input": {"workspace_id": wid, "add_file_ids": [fid]}})
        self._execute("asta_uus2")
        # 执行后页面又改了来源（updated_at 变化）。
        ws = load_workspace(wid)
        ws.selected_file_ids = [fid, "file_other"]
        save_workspace(ws)
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_uus2")
        self.assertEqual(ctx.exception.code, "target_changed")
        # 并发结果保留，不被旧撤销覆盖。
        self.assertEqual(load_workspace(wid).selected_file_ids,
                         [fid, "file_other"])


class ProtocolTest(_UndoCase):
    def test_wrong_result_revision_rejected(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        from app.agents.site_assistant import undo as undo_svc
        self._add_action("asta_up1", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "版本夹"}})
        self._execute("asta_up1")
        with self.assertRaises(ActionRejected) as ctx:
            undo_svc.undo_action(
                self.sid, "asta_up1",
                client_request_id="cur_" + _hex(),
                expected_result_revision="not-the-receipt")
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_window_expired(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_up2", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "过期夹"}})
        self._execute("asta_up2")
        # 把完成时间回拨 11 分钟 → 超出撤销窗口。
        rec = store.load_conversation(self.sid, self.cid)
        action = rec["actions"]["asta_up2"]
        action["invocation"]["completed_at"] = (
            datetime.now(tz=timezone.utc) - timedelta(minutes=11)
        ).isoformat()
        store.save_conversation(self.sid, rec)
        from app.agents.site_assistant import undo as undo_svc
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_up2")
        self.assertEqual(ctx.exception.code, "action_expired")

    def test_non_reversible_operation_rejected(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        from app.core.library import load_library as _ll, save_library as _sl
        from app.core import textbook as tb_store
        lib = _ll(self.sid)
        meta = lib.add_file("", "不可撤销教材.pdf", "正文")
        _sl(lib)
        tb_id = "tb_nr_" + _hex()
        tb_store._save(self.sid, [{
            "id": tb_id, "title": "不可撤销教材", "file_id": meta["id"],
            "status": "ready", "kind": "single", "created_at": 0,
            "updated_at": 0}])
        self._add_action("asta_up3", {
            "kind": "domain_write", "operation": "textbook.cancel",
            "input": {"textbook_id": tb_id}})
        self._execute("asta_up3")
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_up3")
        self.assertEqual(ctx.exception.code, "invalid_target")

    def test_double_undo_with_new_request_id_rejected(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_up4", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "只撤一次夹"}})
        self._execute("asta_up4")
        first = self._undo("asta_up4")
        self.assertTrue(first["undone"])
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_up4")                    # 新 request_id
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_not_succeeded_action_rejected(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_up5", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "未执行夹"}})
        with self.assertRaises(ActionRejected) as ctx:
            self._undo("asta_up5")
        self.assertEqual(ctx.exception.code, "action_state_invalid")

    def test_schedule_update_undo_restores_minutes(self) -> None:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        service = get_orchestration_service()
        service.update_schedule(self.sid, daily_minutes=45)
        self._add_action("asta_up6", {
            "kind": "domain_write", "operation": "schedule.update",
            "input": {"daily_minutes": 90}})
        self._execute("asta_up6")
        self.assertEqual(
            service._load(self.sid).schedule.daily_minutes, 90)
        out = self._undo("asta_up6")
        self.assertTrue(out["undone"])
        self.assertEqual(
            service._load(self.sid).schedule.daily_minutes, 45)

    def test_archive_restore_undo_rearchives(self) -> None:
        from app.core.session import load_session
        from app.core.trash import archive_session, get_item
        sid = self._make_session("恢复再撤销")
        item = archive_session(self.sid, sid)
        self._add_action("asta_up7", {
            "kind": "domain_write", "operation": "archive.restore",
            "input": {"item_id": item["id"]}})
        self._execute("asta_up7")
        self.assertIsNotNone(load_session(sid))
        out = self._undo("asta_up7")
        self.assertTrue(out["undone"])
        self.assertIsNone(load_session(sid))          # 重新归档


if __name__ == "__main__":
    unittest.main()
