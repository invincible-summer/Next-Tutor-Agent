"""Legacy cleanup only removes attributable guest files, preserving shared refs."""
import json
import asyncio
from unittest.mock import patch
from tests.support.storage_sandbox import StorageSandboxTestCase
from app.core import guest_cleanup, guest_policy, guest_runtime
from app.identity import store


class GuestCleanupTests(StorageSandboxTestCase):
    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(content) if isinstance(content, (dict, list)) else content, encoding="utf-8")
        return target

    def test_all_roots_links_protection_and_idempotence(self):
        store.create_user("demo@test.local", "", "unused", user_id="usr_12e410b4e2")
        protected = [self.write("chat_history/demo.json", {"session_id": "demo", "student_id": "usr_12e410b4e2", "knowledge_files": [{"id": "shared"}], "trace_ids": ["shared_trace"]}),
                     self.write("chat_history/library/public.json", {"files": [{"id": "public_file"}]}),
                     self.write("chat_history/library/data/public/public_file.txt", "public textbook"),
                     self.write("uploads/public_file.orig.pdf", "public"),
                     self.write("uploads/shared.txt", "registered reference"),
                     self.write("traces/trace_shared_trace.jsonl", "registered reference"),
                     self.write("notes/usr_12e410b4e2/note.md", "demo note"),
                     self.write("students/usr_12e410b4e2.evaluation.json", "demo model"),
                     self.write("chat_history/library/usr_12e410b4e2.json", {"files": []}),
                     self.write("users/accounts.json.bak", "protected account backup")]
        legacy = [self.write("chat_history/legacy.json", {"session_id": "legacy", "knowledge_files": [{"id": "guestfile"}, {"id": "shared"}, {"id": "public_file"}], "trace_ids": ["guest_trace", "shared_trace"]}),
                  self.write("chat_history/legacy.transcript.jsonl", "legacy dialogue"),
                  self.write("traces/trace_guest_trace.jsonl", "legacy trace"),
                  self.write("uploads/guestfile.txt", "guest upload"),
                  self.write("uploads/guestfile.orig.pdf", "guest upload original"),
                  self.write("chat_history/workspaces/legacy_ws.json", {"workspace_id": "legacy_ws", "student_id": "student_default"}),
                  self.write("chat_history/workspaces/uploads/legacy_ws/old.txt", "old workspace upload"),
                  self.write("chat_history/library/student_default.json", {"files": [{"id": "oldlib"}], "folders": [{"id": "oldfolder"}]}),
                  self.write("chat_history/library/student_default.json.bak", {}),
                  self.write("chat_history/library/student_default.textbooks.json", []),
                  self.write("chat_history/library/data/student_default/oldlib.txt", "old text"),
                  self.write("notes/student_default/old.md", "old note"),
                  self.write("knowledge/custom/student_default/graph.json", {}),
                  self.write("students/student_default.learning_evidence.jsonl", {"source_session_ref": "deleted_chat"}),
                  self.write("chat_history/deleted_chat.transcript.jsonl", "linked by journal"),
                  self.write("chat_history/trash/items/student_default/item/payload/session.json", {"session_id": "archived"}),
                  self.write("chat_history/archived.transcript.jsonl", "archived guest"),
                  self.write("chat_history/trash/preferences/student_default.json", {}),
                  self.write("chat_history/classroom/student_default/old.json", {}),
                  self.write("chat_history/assistant/guest_old/old.json", {}),
                  self.write("users/avatars/student_default/avatar.png", "old avatar")]
        originals = {p: p.read_bytes() for p in protected}
        guest_policy.set_policy(True)
        ctx = guest_runtime.create_context()
        report = guest_cleanup.scan()
        self.assertEqual(report["active"]["visitors"], 1)
        self.assertGreater(report["total_items"], 15)
        with patch("app.core.vector_store.delete_file") as delete_file, patch("app.core.vector_store.delete_scope") as delete_scope:
            result = guest_cleanup.purge()
            self.assertEqual(result["status"], "purged")
            self.assertIn("guestfile", [c.args[0] for c in delete_file.call_args_list])
            self.assertNotIn("shared", [c.args[0] for c in delete_file.call_args_list])
            self.assertIn("session:legacy", [c.args[0] for c in delete_scope.call_args_list])
        self.assertTrue(ctx.revoked)
        self.assertTrue(all(not p.exists() for p in legacy))
        self.assertEqual({p: p.read_bytes() for p in protected}, originals)
        self.assertEqual(guest_cleanup.scan()["total_items"], 0)
        self.assertEqual(guest_cleanup.purge()["total_deleted"], 0)

    def test_failed_deletion_reports_actual_results(self):
        path = self.write("students/student_default.evaluation.json", {})
        with patch("pathlib.Path.unlink", side_effect=PermissionError("test")):
            result = guest_cleanup.purge()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["total_deleted"], 0)
        self.assertEqual(result["total_bytes"], 0)
        self.assertTrue(path.exists())

    def test_registered_guest_like_namespace_is_preserved(self):
        store.create_user("special@test.local", "", "unused", user_id="guest_registered")
        path = self.write("notes/guest_registered/preserved.md", "registered account")
        guest_cleanup.purge()
        self.assertTrue(path.exists())

    def test_old_guests_are_not_resumed_by_background_services(self):
        from app.core import textbook, textbook_ocr
        from app.classroom import storage as classroom_store
        from app.agents.site_assistant import store as assistant_store
        from app.classroom.worker import ClassroomWorker
        from app.agents.site_assistant import notifications
        from app.agents.site_assistant.runtime import AssistantRuntime
        for owner in ("student_default", "guest_old"):
            self.write(f"chat_history/library/{owner}.textbooks.json", {"textbooks": [
                {"id": "legacy_build", "kind": "single", "file_id": "old",
                 "status": "building", "build_job": {"state": "queued", "intent": {"use_llm": True}}},
                {"id": "legacy_ocr", "status": "ocr_waiting"},
            ]})
            self.write(f"chat_history/classroom/{owner}/workspaces/ws/old.json", {})
            self.write(f"chat_history/assistant/{owner}/conversations/old.json", {})
        baseline = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(textbook.migrate_legacy_single_to_groups(), 0)
        self.assertFalse(textbook.reconcile_stale_builds().recovered)
        self.assertEqual(textbook.interrupted_build_jobs(), [])
        with patch("app.agents.knowledge.textbook_builder.enqueue_textbook_build") as enqueue:
            self.assertEqual(textbook_ocr.resume_pending_textbook_ocr(), 0)
            enqueue.assert_not_called()
        with patch.object(classroom_store, "list_lesson_ids") as lessons:
            asyncio.run(ClassroomWorker()._startup_scan())
            lessons.assert_not_called()
        with patch.object(assistant_store, "load_conversation") as conversation:
            AssistantRuntime()._recover_interrupted()
            conversation.assert_not_called()
        with patch.object(notifications, "scheduler_enabled", return_value=True), \
                patch.object(notifications, "load_subscriptions") as subscriptions:
            self.assertEqual(notifications.scheduler_tick()["processed"], 0)
            subscriptions.assert_not_called()
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, baseline)
        store.create_user("legacy-registered@test.local", "", "unused", user_id="guest_registered")
        self.assertFalse(guest_runtime.is_legacy_guest_owner("guest_registered"))
