"""课堂来源解析/冻结/复核回归（plan.md C01 退出门）。

覆盖 §7.1 六步：工作区归属、readable_files 授权面、BM25 证据（组卷顺序）、
章节筛选、全文 hash 冻结、摘录预算、显式会话附件（仅本人 session）、
发布前授权与 hash 复核（available/changed/revoked）、冻结 chunk 回读。
退出门对应：跨工作区/账号/会话附件越权必须失败；资料未解析不得编内容。
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import limits, sources  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.core import library as lib_mod  # noqa: E402
from app.core import session as sess_mod  # noqa: E402
from app.core import textbook as tb_mod  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

OWNER = "usr_srcowner01"
OTHER = "usr_srcother01"
WS = "ws_src_物理备考区"
PUBLIC = tb_mod.PUBLIC_STUDENT_ID

# 结构化（V2 分块）教材正文：三章、每章两段，确保章节筛选可判。
TEXTBOOK_TEXT = """第一章 运动的描述
位移与路程是描述物体位置变化的基本概念，位移是矢量而路程是标量。
速度描述位置变化的快慢，加速度描述速度变化的快慢。

第二章 动量
动量守恒定律的内容：系统不受外力或所受合外力为零时，系统总动量保持不变。
动量定理建立了冲量与动量变化之间的关系，是力对时间累积效果的量度。

第三章 能量
动能定理说明合外力做的功等于物体动能的变化。
机械能守恒条件是只有重力或弹力做功。
"""

WORKSPACE_TEXT = """惯性系与非惯性系
牛顿第一定律在惯性参考系中成立，非惯性系中需要引入惯性力。
系统内力成对出现，改变系统总动量的只有外力。
"""


def _selection(files=None, sessions=None) -> sc.SourceSelection:
    return sc.SourceSelection(
        files=[sc.SourceFileSelection(
            file_id=f["file_id"],
            chapters=[sc.ChapterSelection(**c) for c in f.get("chapters", [])])
            for f in (files or [])],
        extra_sessions=[sc.ExtraSessionSelection(
            session_id=s["session_id"],
            attachment_file_ids=s.get("attachment_file_ids", []))
            for s in (sessions or [])],
    )


class ClassroomSourceTests(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        # 工作区上传文件（owner 私有 library，workspace_file_ids 授权）
        lib = lib_mod.load_library(OWNER)
        self.ws_file = lib.add_file("", "力学讲义.txt", WORKSPACE_TEXT)
        lib_mod.save_library(lib)
        # 公用教材：public 命名空间 + 教材注册，V2 结构化分块（章节路径）
        pub = lib_mod.load_library(PUBLIC)
        self.tb_file = pub.add_file("", "公共物理教材.txt", TEXTBOOK_TEXT)
        pub.find_file(self.tb_file["id"])["chunk_schema"] = "structured-v2"
        lib_mod.save_library(pub)
        lib_mod._chunk_cache.pop((PUBLIC, self.tb_file["id"]), None)
        tb_mod.create_textbook(PUBLIC, file_id=self.tb_file["id"],
                               title="公共物理教材")
        ws = ws_mod.Workspace(workspace_id=WS, name="物理备考",
                              student_id=OWNER,
                              workspace_file_ids=[self.ws_file["id"]],
                              selected_file_ids=[self.tb_file["id"]])
        ws_mod.save_workspace(ws)

    # -- 冻结 ---------------------------------------------------------------

    def test_freeze_workspace_file_with_hash_and_fingerprint(self) -> None:
        got = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.ws_file["id"]}]),
            topic="系统内力与动量", goals=["会判断系统动量是否守恒"])
        self.assertTrue(got.ok)
        self.assertEqual(got.issues, [])
        rec = got.records[0]
        self.assertEqual(rec.kind, sc.SourceKind.workspace_file)
        self.assertEqual(rec.locator.namespace, OWNER)
        self.assertTrue(rec.locator.chunk_ids)
        self.assertTrue(rec.excerpt)
        expected = hashlib.sha256(
            (lib_mod.library_data_dir(OWNER)
             / f"{self.ws_file['id']}.txt").read_bytes()).hexdigest()
        self.assertEqual(rec.locator.content_hash, expected)
        self.assertEqual(rec.excerpt_hash,
                         hashlib.sha256(rec.excerpt.encode()).hexdigest())
        self.assertRegex(rec.source_id, r"^src_[0-9a-f]{24}$")
        # 同一授权内容的 scope fingerprint 稳定（与随机 src_ id 无关）
        again = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.ws_file["id"]}]),
            topic="系统内力与动量")
        self.assertEqual(got.scope_fingerprint, again.scope_fingerprint)
        self.assertNotEqual(rec.source_id, again.records[0].source_id)

    def test_public_textbook_uses_public_namespace(self) -> None:
        got = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.tb_file["id"]}]),
            topic="动量守恒")
        self.assertTrue(got.ok)
        rec = got.records[0]
        self.assertEqual(rec.kind, sc.SourceKind.textbook)
        self.assertEqual(rec.locator.namespace, PUBLIC)

    def test_chapter_selection_filters_and_keeps_book_order(self) -> None:
        chapter = {"section_path": ["第二章 动量"], "title": "第二章 动量"}
        got = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.tb_file["id"],
                                          "chapters": [chapter]}]),
            topic="动量守恒定律")
        self.assertTrue(got.ok, msg=str(got.issues))
        rec = got.records[0]
        chunks = sources.load_locator_chunks(OWNER, rec.locator)
        self.assertTrue(chunks)
        for c in chunks:
            self.assertIn("动量", c.text,
                          "章节筛选后不应混入其他章内容")
            self.assertNotIn("动能定理", c.text)
        indexes = [c.index for c in chunks]
        self.assertEqual(indexes, sorted(indexes), "证据须按组卷顺序冻结")

    def test_chapter_without_match_reports_not_ready(self) -> None:
        got = sources.resolve_classroom_sources(
            OWNER, WS,
            _selection(files=[{"file_id": self.tb_file["id"],
                               "chapters": [{"section_path": ["第九章 量子"],
                                             "title": "第九章 量子"}]}]),
            topic="动量守恒")
        self.assertFalse(got.ok)
        self.assertEqual(got.issues[0].code, "source_not_ready")

    # -- 授权边界（退出门） ---------------------------------------------------

    def test_unauthorized_file_rejected(self) -> None:
        other_lib = lib_mod.load_library(OTHER)
        foreign = other_lib.add_file("", "他人讲义.txt", "他人的私有内容。")
        lib_mod.save_library(other_lib)
        got = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": foreign["id"]},
                                         {"file_id": "不存在"}]),
            topic="动量")
        self.assertFalse(got.ok)
        codes = {i.code for i in got.issues}
        self.assertEqual(codes, {"source_unauthorized"})
        # 原子性：合法来源照常冻结
        got2 = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.ws_file["id"]},
                                         {"file_id": foreign["id"]}]),
            topic="动量")
        self.assertEqual(len(got2.records), 1)
        self.assertEqual(len(got2.issues), 1)

    def test_cross_account_workspace_denied(self) -> None:
        with self.assertRaises(ClassroomError) as ctx:
            sources.resolve_classroom_sources(
                OTHER, WS, _selection(files=[{"file_id": self.ws_file["id"]}]),
                topic="动量")
        self.assertEqual(ctx.exception.code, "source_not_found")

    def test_missing_extracted_text_is_not_ready(self) -> None:
        path = lib_mod.library_data_dir(OWNER) / f"{self.ws_file['id']}.txt"
        path.unlink()
        got = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.ws_file["id"]}]),
            topic="动量")
        self.assertFalse(got.ok)
        self.assertEqual(got.issues[0].code, "source_not_ready")

    # -- 会话附件 -----------------------------------------------------------

    def _make_session(self, student_id: str, sid: str) -> tuple:
        sess = sess_mod.TutorSession(session_id=sid, student_id=student_id,
                                     workspace_id=WS)
        meta = sess.knowledge.add_file(
            "attsess0001", "课堂补充笔记.txt",
            "本节课补充：碰撞分类与例子。\n弹性碰撞举例。")
        sess_mod.save_session(sess)
        return sess, meta

    def test_session_attachment_own_session_only(self) -> None:
        sess, meta = self._make_session(OWNER, "chat_20260926_补课")
        got = sources.resolve_classroom_sources(
            OWNER, WS,
            _selection(sessions=[{"session_id": sess.session_id}]),
            topic="碰撞")
        self.assertTrue(got.ok, msg=str(got.issues))
        rec = got.records[0]
        self.assertEqual(rec.kind, sc.SourceKind.session_file)
        self.assertEqual(rec.locator.namespace,
                         f"session:{sess.session_id}")
        self.assertIn("碰撞", rec.excerpt)
        # 他人会话 / 不存在的会话一律拒绝
        self._make_session(OTHER, "chat_20260926_他人")
        for sid in ("chat_20260926_他人", "chat_不存在的会话"):
            got2 = sources.resolve_classroom_sources(
                OWNER, WS, _selection(sessions=[{"session_id": sid}]),
                topic="碰撞")
            self.assertFalse(got2.ok)
            self.assertEqual(got2.issues[0].code, "source_unauthorized")
        # 指定不属于该会话的文件 id 同样拒绝
        got3 = sources.resolve_classroom_sources(
            OWNER, WS,
            _selection(sessions=[{"session_id": sess.session_id,
                                  "attachment_file_ids": ["outsider01"]}]),
            topic="碰撞")
        self.assertFalse(got3.ok)
        self.assertEqual(got3.issues[0].code, "source_unauthorized")

    def test_session_attachment_revoked_after_delete(self) -> None:
        sess, meta = self._make_session(OWNER, "chat_20260926_补课")
        got = sources.resolve_classroom_sources(
            OWNER, WS,
            _selection(sessions=[{"session_id": sess.session_id}]),
            topic="碰撞")
        rec = got.records[0]
        self.assertEqual(sources.verify_sources(OWNER, WS, [rec]),
                         {rec.source_id: sources.STATUS_AVAILABLE})
        (sess_mod.load_session(sess.session_id).knowledge.upload_dir
         / f"{meta['id']}.txt").unlink()
        self.assertEqual(sources.verify_sources(OWNER, WS, [rec]),
                         {rec.source_id: sources.STATUS_REVOKED})

    # -- 复核（§7.1 步骤 6） --------------------------------------------------

    def _resolve_ws_file(self) -> sc.SourceRecord:
        got = sources.resolve_classroom_sources(
            OWNER, WS, _selection(files=[{"file_id": self.ws_file["id"]}]),
            topic="动量")
        self.assertTrue(got.ok)
        return got.records[0]

    def test_verify_changed_when_text_mutated(self) -> None:
        rec = self._resolve_ws_file()
        path = lib_mod.library_data_dir(OWNER) / f"{self.ws_file['id']}.txt"
        path.write_text("被替换后的新内容。\n", encoding="utf-8")
        self.assertEqual(sources.verify_sources(OWNER, WS, [rec]),
                         {rec.source_id: sources.STATUS_CHANGED})
        with self.assertRaises(ClassroomError) as ctx:
            sources.assert_sources_authorized(OWNER, WS, [rec])
        self.assertEqual(ctx.exception.code, "source_changed")

    def test_verify_revoked_when_unselected(self) -> None:
        rec = self._resolve_ws_file()
        ws = ws_mod.load_workspace(WS)
        ws.workspace_file_ids = []
        ws_mod.save_workspace(ws)
        self.assertEqual(sources.verify_sources(OWNER, WS, [rec]),
                         {rec.source_id: sources.STATUS_REVOKED})
        with self.assertRaises(ClassroomError) as ctx:
            sources.assert_sources_authorized(OWNER, WS, [rec])
        self.assertEqual(ctx.exception.code, "scope_changed")

    def test_web_record_is_frozen_snapshot(self) -> None:
        stamp = datetime(2026, 9, 26, tzinfo=timezone.utc)
        web = sc.SourceRecord(
            source_id="src_" + "a" * 24, kind=sc.SourceKind.web,
            title="权威页面",
            locator=sc.WebLocator(
                url="https://example.com/physics",
                canonical_url="https://example.com/physics",
                domain="example.com", publisher="Example",
                retrieved_at=stamp),
            excerpt="快照摘录", excerpt_hash="0" * 64,
            retrieved_at=stamp)
        self.assertEqual(sources.verify_sources(OWNER, WS, [web]),
                         {web.source_id: sources.STATUS_AVAILABLE})

    def test_assert_allows_unchanged_sources(self) -> None:
        rec = self._resolve_ws_file()
        sources.assert_sources_authorized(OWNER, WS, [rec])  # 不抛

    def test_load_locator_chunks_rejects_tampered_text(self) -> None:
        rec = self._resolve_ws_file()
        chunks = sources.load_locator_chunks(OWNER, rec.locator)
        self.assertTrue(chunks)
        path = lib_mod.library_data_dir(OWNER) / f"{self.ws_file['id']}.txt"
        path.write_text("篡改后的正文。\n", encoding="utf-8")
        with self.assertRaises(ClassroomError) as ctx:
            sources.load_locator_chunks(OWNER, rec.locator)
        self.assertEqual(ctx.exception.code, "source_changed")

    # -- 预算 -----------------------------------------------------------------

    def test_evidence_total_budget_truncates(self) -> None:
        long_text = "动量守恒需要先选定系统。" * 300  # 远超单材料摘录上限
        lib = lib_mod.load_library(OWNER)
        fids = []
        for i in range(3):
            m = lib.add_file("", f"讲义{i}.txt", long_text)
            fids.append(m["id"])
        lib_mod.save_library(lib)
        ws = ws_mod.load_workspace(WS)
        ws.workspace_file_ids = ws.workspace_file_ids + fids
        ws_mod.save_workspace(ws)
        with mock.patch.object(limits, "EVIDENCE_CHARS_TOTAL", 5000):
            got = sources.resolve_classroom_sources(
                OWNER, WS,
                _selection(files=[{"file_id": f} for f in fids]),
                topic="动量守恒")
        self.assertEqual(len(got.records), 3)
        total = sum(len(r.excerpt) for r in got.records)
        self.assertLessEqual(total, 5000)
        self.assertTrue(any(i.code == "evidence_truncated"
                            for i in got.issues))
        for r in got.records:
            self.assertLessEqual(len(r.excerpt),
                                 limits.EXCERPT_CHARS_PER_MATERIAL)


if __name__ == "__main__":
    unittest.main()
