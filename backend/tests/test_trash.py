from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.knowledge import KnowledgeService
from app.agents.knowledge import manager as knowledge_manager
from app.agents.knowledge import store as graph_store
from app.agents.memory import prompt_memory
from app.agents.memory import store as memory_store
from app.agents.memory.schema import EpisodicMemory
from app.core import context, learning_records, library, quiz_recent, session, textbook, trash, workspace
from app.core.config import settings


class TrashFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="trash_test_")
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(trash, "_TRASH_DIR", self.root / "trash"),
            patch.object(trash, "_GLOBAL_POLICY", self.root / "trash" / "policy.json"),
            patch.object(session, "_SESSIONS_DIR", self.root / "sessions"),
            patch.object(context, "_TRANSCRIPT_DIR", self.root / "sessions"),
            patch.object(settings, "trace_dir", str(self.root / "traces")),
            patch.object(library, "_LIBRARY_DIR", self.root / "library"),
            patch.object(textbook, "_LIBRARY_DIR", self.root / "library"),
            patch.object(workspace, "_WORKSPACES_DIR", self.root / "workspaces"),
            patch.object(graph_store, "_KG_DIR", self.root / "knowledge"),
            patch.object(graph_store, "_CUSTOM_DIR", self.root / "knowledge" / "custom"),
            patch.object(prompt_memory, "_STUDENTS_DIR", self.root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH", self.root / "students" / "prompt_policy.json"),
            patch.object(memory_store, "_STUDENTS_DIR", self.root / "students"),
            patch.object(learning_records, "_STUDENTS_DIR", self.root / "students"),
            patch.object(quiz_recent, "_STUDENTS_DIR", self.root / "students"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def make_session(self, sid="chat_one", ws_id=""):
        s = session.TutorSession(
            session_id=sid, student_id="stu1", workspace_id=ws_id,
            title="牛顿定律", messages=[{"role": "user", "content": "解释惯性"},
                                       {"role": "assistant", "content": "好的"}],
            trace_ids=["trace1"],
        )
        s.knowledge.add_file("attach1", "图.png", "OCR 文本", raw=b"img", orig_ext=".png")
        session.save_session(s)
        context.append_transcript(sid, 1, [{"role": "user", "content": "解释惯性"}])
        trace_dir = Path(settings.trace_dir)
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "trace_trace1.jsonl").write_text('{"kind":"finish"}\n', encoding="utf-8")
        return s


class TestSessionTrash(TrashFixture):
    def test_session_archive_restore_and_permanent_purge(self):
        self.make_session()
        memory_store.append_episode("stu1", EpisodicMemory(
            session_id="chat_one", summary="可追溯对话事件", event_type="concept_taught"))
        memory_store.append_episode("stu1", EpisodicMemory(
            session_id="", summary="独立学习记录", event_type="quiz_graded"))
        item = trash.archive_session("stu1", "chat_one")
        self.assertEqual(item["resource_type"], "session")
        self.assertEqual(item["archive_location"],
                         f"chat_history/trash/items/stu1/{item['id']}")
        self.assertIsNone(session.load_session("chat_one"))
        self.assertFalse(context.transcript_path("chat_one").exists())
        self.assertFalse((Path(settings.trace_dir) / "trace_trace1.jsonl").exists())
        self.assertEqual(len(trash.list_items("stu1")), 1)

        restored = trash.restore_item("stu1", item["id"])
        self.assertEqual(restored["status"], "restored")
        self.assertIsNotNone(session.load_session("chat_one"))
        self.assertTrue(context.transcript_path("chat_one").exists())
        self.assertTrue((Path(settings.trace_dir) / "trace_trace1.jsonl").exists())
        self.assertEqual(trash.list_items("stu1"), [])

        item2 = trash.archive_session("stu1", "chat_one")
        result = trash.purge_item("stu1", item2["id"])
        self.assertEqual(result["status"], "purged")
        self.assertEqual(trash.list_items("stu1"), [])
        self.assertIsNone(session.load_session("chat_one"))
        summaries = [x.summary for x in memory_store.read_episodes("stu1")]
        self.assertNotIn("可追溯对话事件", summaries)
        self.assertIn("独立学习记录", summaries)

    def test_permanent_purge_forgets_recent_prompt_influence_but_keeps_learning_ledger(self):
        self.make_session()
        prompt_memory.register_session("stu1", "chat_one")
        prompt_memory.record_contribution(
            "stu1", "chat_one", user_message="请一步一步讲",
            strategy_outcome="wrong")
        learning_records.record_question(
            "stu1", "chat_one", {"id": "q1", "stem": "一道题",
                                  "answer": "A", "knowledge_point": "矩阵"})
        learning_records.record_verdict(
            "stu1", "chat_one", stem="一道题", verdict="wrong",
            student_answer="B")
        quiz_recent.record_recent_quiz("chat_one", "stu1", {
            "topic": "矩阵", "questions": [{"id": "q1", "stem": "一道题",
            "answer": "A", "knowledge_point": "矩阵"}]})

        item = trash.archive_session("stu1", "chat_one")
        self.assertEqual(prompt_memory.session_forget_status("stu1", "chat_one"),
                         "recent")
        result = trash.purge_item("stu1", item["id"])
        self.assertEqual(result["memory_forget"].get("forgotten"), 1)
        self.assertEqual(prompt_memory.session_forget_status("stu1", "chat_one"),
                         "none")
        rows = learning_records.list_records("stu1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_status"], "deleted")
        self.assertEqual(rows[0]["session_id"], "")
        self.assertEqual(rows[0]["student_answer"], "B")
        recent = quiz_recent.list_recent_questions("stu1")
        self.assertEqual(recent[0]["session_id"], "")
        self.assertEqual(recent[0]["source_status"], "deleted")
        self.assertEqual(result["source_attribution_detached"],
                         {"learning_records": 1, "quiz_recent": 1})

    def test_archive_can_forget_immediately_and_restore_does_not_recreate_it(self):
        self.make_session()
        prompt_memory.register_session("stu1", "chat_one")
        prompt_memory.record_contribution(
            "stu1", "chat_one", user_message="请温柔一点")
        item = trash.archive_session(
            "stu1", "chat_one", forget_prompt_memory=True)
        self.assertEqual(item["metadata"]["memory_forget_result"], "forgotten")
        self.assertEqual(prompt_memory.session_forget_status("stu1", "chat_one"),
                         "none")
        trash.restore_item("stu1", item["id"])
        state = prompt_memory.load_state("stu1")
        restored = next((x for x in state["recent_sessions"]
                         if x.get("session_id") == "chat_one"), None)
        self.assertIsNone(restored)

    def test_session_restore_does_not_silently_reattach_workspace(self):
        ws = workspace.Workspace(workspace_id="ws1", student_id="stu1",
                                 name="原学习区", session_ids=["chat_one"])
        workspace.save_workspace(ws)
        self.make_session(ws_id="ws1")
        item = trash.archive_session("stu1", "chat_one")
        trash.restore_item("stu1", item["id"], workspace_ids=[])
        self.assertEqual(session.load_session("chat_one").workspace_id, "")
        self.assertNotIn("chat_one", workspace.load_workspace("ws1").session_ids)


class TestLibraryTrash(TrashFixture):
    def test_folder_restores_only_to_selected_workspace(self):
        lib = library.Library("stu1")
        folder = lib.create_folder("物理资料")
        file_meta = lib.add_file(folder["id"], "运动学.txt", "速度和加速度", raw=b"raw", orig_ext=".txt")
        library.save_library(lib)
        ws = workspace.Workspace(workspace_id="ws1", student_id="stu1", name="物理",
                                 selected_folder_ids=[folder["id"]])
        workspace.save_workspace(ws)

        item = trash.archive_library_folder("stu1", folder["id"])
        self.assertIsNone(library.load_library("stu1").find_folder(folder["id"]))
        self.assertNotIn(folder["id"], workspace.load_workspace("ws1").selected_folder_ids)
        trash.restore_item("stu1", item["id"], workspace_ids=["ws1"])
        restored = library.load_library("stu1")
        self.assertIsNotNone(restored.find_folder(folder["id"]))
        self.assertIsNotNone(restored.find_file(file_meta["id"]))
        self.assertIn(folder["id"], workspace.load_workspace("ws1").selected_folder_ids)


class TestTextbookTrash(TrashFixture):
    def test_textbook_bundle_preserves_ids_graph_and_source(self):
        lib = library.Library("stu1")
        meta = lib.add_file("", "线性代数.pdf", "矩阵 向量", raw=b"pdf", orig_ext=".pdf")
        library.save_library(lib)
        tb = textbook.create_textbook("stu1", file_id=meta["id"], title="线性代数")
        graph_store.save_custom_graph("stu1", tb["topic_key"], {
            "topic_key": tb["topic_key"], "topic": "线性代数",
            "nodes": [{"id": "c1", "name": "矩阵", "kind": "concept"}], "edges": []})
        graph_store.save_concept_chunks("stu1", tb["topic_key"], {
            "file_id": meta["id"], "concepts": {"c1": {"chunk_ids": [meta["id"] + "#0"]}}})
        graph_store.save_volume_spec("stu1", tb["topic_key"], meta["id"], {
            "file_id": meta["id"], "text_sha256": "hash", "prompt_version": "p1",
            "schema_version": "2", "raw_spec": {"chapters": []},
            "normalized_spec": {"chapters": []}})

        item = trash.archive_textbook("stu1", tb["id"])
        self.assertIsNone(textbook.find_textbook("stu1", tb["id"]))
        self.assertIsNone(graph_store.load_custom_graph("stu1", tb["topic_key"]))
        self.assertIsNone(graph_store.load_volume_spec("stu1", tb["topic_key"], meta["id"]))
        self.assertIsNone(library.load_library("stu1").find_file(meta["id"]))
        trash.restore_item("stu1", item["id"])
        self.assertEqual(textbook.find_textbook("stu1", tb["id"])["file_id"], meta["id"])
        self.assertIsNotNone(graph_store.load_custom_graph("stu1", tb["topic_key"]))
        self.assertIsNotNone(graph_store.load_concept_chunks("stu1", tb["topic_key"]))
        self.assertIsNotNone(graph_store.load_volume_spec("stu1", tb["topic_key"], meta["id"]))
        self.assertIsNotNone(library.load_library("stu1").find_file(meta["id"]))

    def test_waiting_textbook_restore_reschedules_and_purge_removes_all_state(self):
        lib = library.Library("stu1")
        meta = lib.add_file("", "扫描教材.pdf", "", raw=b"pdf", orig_ext=".pdf")
        library.save_library(lib)
        tb = textbook.create_textbook("stu1", file_id=meta["id"], title="扫描教材")
        waiting_state = {
            "version": 1,
            "mode": "persistent_api",
            "volumes": {
                meta["id"]: {
                    "status": "waiting",
                    "target_pages": [1, 2],
                    "successful_pages": [1],
                    "pending_pages": [2],
                    "attempts": {"2": 3},
                    "next_retry_at": 9999999999.0,
                    "last_error": {"code": "provider_retryable", "summary": "429"},
                }
            },
        }
        textbook.update_textbook(
            "stu1", tb["id"], status="ocr_waiting", ocr_state=waiting_state)

        with patch("app.core.textbook_ocr.cancel_textbook_ocr") as cancel:
            item = trash.archive_textbook("stu1", tb["id"])
        cancel.assert_called_once_with("stu1", tb["id"])
        self.assertIsNone(textbook.find_textbook("stu1", tb["id"]))
        with patch("app.core.textbook_ocr.schedule_textbook_resume") as schedule:
            restored = trash.restore_item("stu1", item["id"])
        self.assertEqual(restored["status"], "restored")
        current = textbook.find_textbook("stu1", tb["id"])
        self.assertEqual(current["status"], "ocr_waiting")
        self.assertEqual(current["ocr_state"], waiting_state)
        schedule.assert_called_once_with("stu1", tb["id"], 9999999999.0)

        item2 = trash.archive_textbook("stu1", tb["id"])
        bundle = trash._item_dir("stu1", item2["id"])
        self.assertTrue(bundle.exists())
        purged = trash.purge_item("stu1", item2["id"])
        self.assertEqual(purged["status"], "purged")
        self.assertFalse(bundle.exists())
        self.assertIsNone(textbook.find_textbook("stu1", tb["id"]))
        self.assertEqual(trash.list_items("stu1"), [])

    def test_public_graph_lifecycle_invalidates_every_student_view(self):
        svc = KnowledgeService()
        key = "public-cache-regression"
        node_id = "custom.public-cache-regression.c1"
        graph_store.save_custom_graph(textbook.PUBLIC_STUDENT_ID, key, {
            "topic_key": key, "topic": "公共线性代数",
            "nodes": [{"id": node_id, "name": "公共矩阵概念",
                       "kind": "concept", "origin": "material"}],
            "edges": [], "contents": [],
        })
        with patch.object(knowledge_manager, "_INSTANCE", svc):
            self.assertIn(node_id, svc.graph_for("stu1").nodes)
            self.assertIn("stu1", svc._student_graphs)

            item = trash.archive_knowledge_graph(
                textbook.PUBLIC_STUDENT_ID, key)
            self.assertNotIn("stu1", svc._student_graphs)
            self.assertNotIn(node_id, svc.graph_for("stu1").nodes)

            trash.restore_item(textbook.PUBLIC_STUDENT_ID, item["id"])
            self.assertIn(node_id, svc.graph_for("stu1").nodes)

    def test_group_volume_archives_and_restores_to_original_position(self):
        lib = library.Library("stu1")
        first = lib.add_file("", "上册.pdf", "向量", raw=b"a", orig_ext=".pdf")
        second = lib.add_file("", "下册.pdf", "矩阵", raw=b"b", orig_ext=".pdf")
        library.save_library(lib)
        group = textbook.create_group(
            "stu1", file_ids=[first["id"], second["id"]], title="线性代数")
        graph_store.save_custom_graph("stu1", group["topic_key"], {
            "topic_key": group["topic_key"], "topic": "线性代数",
            "nodes": [{"id": "c1", "name": "矩阵", "kind": "concept"}], "edges": []})
        item = trash.archive_textbook_volume("stu1", group["id"], first["id"])
        self.assertEqual(textbook.find_textbook("stu1", group["id"])["file_ids"], [second["id"]])
        self.assertIsNone(library.load_library("stu1").find_file(first["id"]))
        trash.restore_item("stu1", item["id"])
        self.assertEqual(textbook.find_textbook("stu1", group["id"])["file_ids"],
                         [first["id"], second["id"]])
        self.assertIsNotNone(library.load_library("stu1").find_file(first["id"]))


class TestWorkspaceTrash(TrashFixture):
    def test_workspace_bundle_includes_sessions_files_and_public_memory(self):
        lib = library.Library("stu1")
        folder = lib.create_folder("力学区", workspace_id="ws1")
        file_meta = lib.add_file(folder["id"], "实验.txt", "实验记录")
        library.save_library(lib)
        ws = workspace.Workspace(
            workspace_id="ws1", student_id="stu1", name="力学区",
            session_ids=["chat_one"], library_folder_id=folder["id"],
            selected_folder_ids=[folder["id"]], workspace_file_ids=[file_meta["id"]],
            public_memory="学生正在学习受力分析。",
        )
        workspace.save_workspace(ws)
        self.make_session(ws_id="ws1")

        item = trash.archive_workspace("stu1", "ws1")
        self.assertIsNone(workspace.load_workspace("ws1"))
        self.assertIsNone(session.load_session("chat_one"))
        self.assertIsNone(library.load_library("stu1").find_folder(folder["id"]))
        trash.restore_item("stu1", item["id"])
        restored = workspace.load_workspace("ws1")
        self.assertEqual(restored.public_memory, "学生正在学习受力分析。")
        self.assertIn("chat_one", restored.session_ids)
        self.assertEqual(session.load_session("chat_one").workspace_id, "ws1")
        self.assertIsNotNone(library.load_library("stu1").find_file(file_meta["id"]))

    def test_workspace_permanent_purge_forgets_member_recent_prompt_memory(self):
        ws = workspace.Workspace(workspace_id="ws1", student_id="stu1",
                                 name="力学区", session_ids=["chat_one"],
                                 public_memory="仅本工作区共同记忆")
        workspace.save_workspace(ws)
        self.make_session(ws_id="ws1")
        prompt_memory.register_session("stu1", "chat_one", "ws1")
        prompt_memory.record_contribution(
            "stu1", "chat_one", workspace_id="ws1",
            user_message="请一步一步讲")
        item = trash.archive_workspace("stu1", "ws1")
        self.assertEqual(prompt_memory.session_forget_status("stu1", "chat_one"),
                         "recent")
        result = trash.purge_item("stu1", item["id"])
        self.assertEqual(result["memory_forget"].get("forgotten"), 1)
        self.assertEqual(prompt_memory.session_forget_status("stu1", "chat_one"),
                         "none")
        self.assertIsNone(workspace.load_workspace("ws1"))
        self.assertIsNone(session.load_session("chat_one"))
        self.assertIsNone(trash.get_item("stu1", item["id"]))

    def test_workspace_restore_conflict_leaves_bundle_and_active_state_untouched(self):
        lib = library.Library("stu1")
        folder = lib.create_folder("区", workspace_id="ws1")
        library.save_library(lib)
        ws = workspace.Workspace(workspace_id="ws1", student_id="stu1", name="区",
                                 session_ids=["chat_one"], library_folder_id=folder["id"])
        workspace.save_workspace(ws)
        self.make_session(ws_id="ws1")
        item = trash.archive_workspace("stu1", "ws1")
        # Reuse the archived session id in a new active chat to force preflight conflict.
        session.save_session(session.TutorSession(session_id="chat_one", student_id="stu1"))
        with self.assertRaises(FileExistsError):
            trash.restore_item("stu1", item["id"])
        self.assertIsNone(workspace.load_workspace("ws1"))
        self.assertIsNotNone(trash.get_item("stu1", item["id"]))
        self.assertIsNone(library.load_library("stu1").find_folder(folder["id"]))


class TestRetention(TrashFixture):
    def test_policy_clamps_and_expired_cleanup_is_idempotent(self):
        global_policy = trash.set_global_policy(default_days=7, user_max_days=20,
                                                 forced_max_days=10, mode="auto",
                                                 cleanup_interval_seconds=3600)
        self.assertEqual(global_policy["forced_max_days"], 10)
        self.assertEqual(trash.set_user_policy("stu1", 30)["retention_days"], 10)
        self.make_session()
        item = trash.archive_session("stu1", "chat_one")
        result = trash.cleanup_expired(now=float(item["expires_at"]) + 1)
        self.assertEqual(result["purged"], 1)
        self.assertEqual(trash.cleanup_expired(now=float(item["expires_at"]) + 1)["purged"], 0)

    def test_lowered_forced_max_applies_to_existing_items(self):
        trash.set_global_policy(default_days=20, user_max_days=30,
                                forced_max_days=30, mode="auto",
                                cleanup_interval_seconds=3600)
        trash.set_user_policy("stu1", 20)
        self.make_session()
        item = trash.archive_session("stu1", "chat_one")
        trash.set_global_policy(default_days=7, user_max_days=30,
                                forced_max_days=5, mode="auto",
                                cleanup_interval_seconds=3600)
        result = trash.cleanup_expired(now=float(item["deleted_at"]) + 6 * 86400)
        self.assertEqual(result["purged"], 1)


class TestOwnerDirHygiene(TrashFixture):
    """条目清空后不留空的 items/<owner>/ 目录（purge / restore / GC 三条路径）。"""

    def _owner_dir(self) -> Path:
        return trash._TRASH_DIR / "items" / "stu1"

    def test_purge_last_item_removes_owner_dir(self):
        self.make_session()
        item = trash.archive_session("stu1", "chat_one")
        self.assertTrue(self._owner_dir().is_dir())
        trash.purge_item("stu1", item["id"])
        self.assertFalse(self._owner_dir().exists())

    def test_restore_last_item_removes_owner_dir(self):
        self.make_session()
        item = trash.archive_session("stu1", "chat_one")
        trash.restore_item("stu1", item["id"])
        self.assertFalse(self._owner_dir().exists())

    def test_owner_dir_kept_while_other_items_remain(self):
        self.make_session("chat_one")
        self.make_session("chat_two")
        item = trash.archive_session("stu1", "chat_one")
        trash.archive_session("stu1", "chat_two")
        trash.purge_item("stu1", item["id"])
        self.assertTrue(self._owner_dir().is_dir())
        self.assertEqual(len(trash.list_items("stu1")), 1)

    def test_cleanup_expired_sweeps_empty_owner_dirs(self):
        self.make_session()
        item = trash.archive_session("stu1", "chat_one")
        trash.cleanup_expired(now=float(item["expires_at"]) + 1)
        self.assertFalse(self._owner_dir().exists())


if __name__ == "__main__":
    unittest.main()
