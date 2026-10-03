"""全站实体搜索回归（B02）。

覆盖：权限过滤（他人会话不可见）、闭集 kinds 校验由调用方保证、排序
（精确 > 标题 > 摘要；同档 updated_at 降序）、分页与 total、空查询空集、
正文检索仅在 include_content 时命中笔记内容、course/lesson 归属过滤。
"""
from __future__ import annotations

import json
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class SiteSearchTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_search_1"
        self.other = "usr_search_2"

    def _add_session(self, sid: str, session_id: str, title: str,
                     updated_at: float = 100.0) -> None:
        from app.core import session as session_core
        path = session_core._SESSIONS_DIR / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "session_id": session_id, "student_id": sid, "title": title,
            "workspace_id": "", "updated_at": updated_at,
            "messages": []}), encoding="utf-8")

    def _add_note(self, title: str, content: str, note_id: str = "note_1",
                  updated_at: float = 50.0) -> None:
        from app.core import notes as notes_store
        vault = notes_store.load_vault(self.sid)
        vault.notes.append({
            "id": note_id, "title": title, "content": content,
            "tags": [], "summary": "", "updated_at": updated_at,
            "revision": 1, "created_at": updated_at,
        })
        notes_store.save_vault(vault)

    def test_search_finds_own_session_and_hides_others(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_session(self.sid, "sess_mine", "定积分复习对话")
        self._add_session(self.other, "sess_other", "定积分别人的对话")
        out = s.search_site_entities(self.sid, q="定积分", kinds=["chat"])
        ids = [i["entity_id"] for i in out["items"]]
        self.assertIn("sess_mine", ids)
        self.assertNotIn("sess_other", ids)
        self.assertEqual(out["total"], 1)
        self.assertTrue(out["complete"])
        self.assertEqual(out["items"][0]["target"]["kind"], "chat_session")

    def test_exact_id_match_ranks_first(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_session(self.sid, "sess_exact", "随手标题", updated_at=1.0)
        self._add_session(self.sid, "sess_title", "电磁感应专题", updated_at=2.0)
        out = s.search_site_entities(self.sid, q="电磁感应")
        self.assertEqual(out["items"][0]["entity_id"], "sess_title")
        out2 = s.search_site_entities(self.sid, q="sess_exact")
        self.assertEqual(out2["items"][0]["entity_id"], "sess_exact")
        self.assertEqual(out2["items"][0]["match_kind"], "exact")

    def test_pagination_and_limit_cap(self) -> None:
        from app.agents.site_assistant import search as s
        for i in range(7):
            self._add_session(self.sid, f"sess_p{i}", f"分页检索会话{i}",
                              updated_at=float(i))
        out = s.search_site_entities(self.sid, q="分页检索", limit=3)
        self.assertEqual(len(out["items"]), 3)
        self.assertEqual(out["total"], 7)
        page2 = s.search_site_entities(self.sid, q="分页检索", offset=3,
                                       limit=3)
        # updated_at 降序：第一页 p6/p5/p4，第二页 p3/p2/p1。
        self.assertEqual([i["entity_id"] for i in page2["items"]],
                         [f"sess_p{i}" for i in (3, 2, 1)])

    def test_empty_query_returns_nothing(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_session(self.sid, "sess_x", "标题")
        out = s.search_site_entities(self.sid, q="  ")
        self.assertEqual(out["items"], [])
        self.assertEqual(out["total"], 0)

    def test_note_content_only_with_flag(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_note("课堂记录", "本次课推导了伯努利方程的适用条件与限制")
        plain = s.search_site_entities(self.sid, q="伯努利", kinds=["note"])
        self.assertEqual(plain["total"], 0)
        with_content = s.search_site_entities(
            self.sid, q="伯努利", kinds=["note"], include_content=True)
        self.assertEqual(with_content["total"], 1)
        self.assertEqual(with_content["items"][0]["match_kind"], "content")
        self.assertIn("伯努利", with_content["items"][0]["snippet"])

    def test_workspace_filter_scopes_chat(self) -> None:
        from app.agents.site_assistant import search as s
        from app.core import session as session_core
        for sid_, ws, sess in ((self.sid, "wsp_a", "sess_a"),
                               (self.sid, "wsp_b", "sess_b")):
            path = session_core._SESSIONS_DIR / f"{sess}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "session_id": sess, "student_id": sid_, "title": "范围检索",
                "workspace_id": ws, "updated_at": 10.0, "messages": []}),
                encoding="utf-8")
        out = s.search_site_entities(self.sid, q="范围检索",
                                     kinds=["chat"], workspace_id="wsp_a")
        self.assertEqual([i["entity_id"] for i in out["items"]], ["sess_a"])

    def test_unknown_kinds_fall_back_to_full_set(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_session(self.sid, "sess_k", "闭集回退")
        out = s.search_site_entities(self.sid, q="闭集回退",
                                     kinds=["bogus_kind"])
        self.assertEqual(out["total"], 1)

    # -- 自然语言查询（P1-1 回归：整句不再 0 结果）------------------------

    def test_natural_sentence_finds_note_by_book_title(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_note("定积分与可积性", "内容", note_id="note_nl1")
        out = s.search_site_entities(
            self.sid, q=s.clean_query("找到我的笔记《定积分与可积性》"),
            kinds=["note"])
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["items"][0]["entity_id"], "note_nl1")
        self.assertEqual(out["items"][0]["target"]["kind"], "note")

    def test_sentence_containing_full_title_matches_reverse(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_note("定积分与可积性", "内容", note_id="note_rev")
        # 未经净化的整句也能靠反向包含（标题含于查询）命中完整标题。
        out = s.search_site_entities(
            self.sid, q="我想看看之前那篇定积分与可积性",
            kinds=["note"])
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["items"][0]["match_kind"], "title")

    def test_clean_query_strips_fillers_and_page(self) -> None:
        from app.agents.site_assistant import search as s
        self.assertEqual(s.clean_query("找到我的笔记《定积分与可积性》"),
                         "定积分与可积性")
        self.assertEqual(s.clean_query("打开微积分讲义第3页"), "微积分")
        self.assertEqual(s.clean_query("帮我找一下我的笔记"),
                         "帮我找一下我的笔记")  # 净空回原文
        self.assertEqual(s.clean_query(""), "")

    def test_short_title_guard_prevents_false_positive(self) -> None:
        from app.agents.site_assistant import search as s
        # 单字标题不参与反向包含（防任意句中一字命中）。
        self._add_session(self.sid, "sess_short", "题")
        out = s.search_site_entities(
            self.sid, q="帮我看一下有什么课", kinds=["chat"])
        self.assertEqual(out["total"], 0)

    def test_punctuation_does_not_block_title_match(self) -> None:
        from app.agents.site_assistant import search as s
        self._add_session(self.sid, "sess_punct", "定积分，复习！")
        out = s.search_site_entities(
            self.sid, q="《定积分,复习》", kinds=["chat"])
        self.assertEqual(out["total"], 1)

    def test_conservative_clean_keeps_entity_words_in_name(self) -> None:
        from app.agents.site_assistant import search as s
        # 类词是实体名一部分（文件「微积分讲义复验.pdf」）时，保守力度
        # 保留「讲义」，激进力度剥离 —— expand_query 两个都给。
        self.assertEqual(s.clean_query("打开微积分讲义复验第3页"),
                         "微积分复验")
        self.assertEqual(
            s.clean_query("打开微积分讲义复验第3页",
                          keep_entity_words=True),
            "微积分讲义复验")
        self.assertEqual(s.expand_query("找到我的笔记《定积分与可积性》"),
                         ["定积分与可积性"])

    def test_expanded_query_file_stem_exact_beats_weak_alias(self) -> None:
        from app.agents.site_assistant import search as s
        from app.core.library import Library, save_library
        lib = Library(student_id=self.sid)
        fid = lib.add_file("", "微积分讲义复验.pdf", "讲义",
                           raw=b"%PDF-1.4\n%%EOF", orig_ext=".pdf")["id"]
        save_library(lib)
        from app.core import notes as notes_store
        vault = notes_store.load_vault(self.sid)
        vault.notes.append({
            "id": "note_tag1", "title": "微积分学习路线图", "content": "",
            "tags": ["微积分"], "summary": "", "updated_at": 50.0,
            "revision": 1, "created_at": 50.0})
        notes_store.save_vault(vault)
        # 激进 needle「微积分复验」只会经标签反向弱命中笔记；保守
        # needle 凭去扩展名精确命中文件 → 合并后文件排最前且为 exact。
        out = s.search_with_expanded_query(
            self.sid, text="打开微积分讲义复验第3页")
        self.assertEqual(out["items"][0]["entity_id"], fid)
        self.assertEqual(out["items"][0]["match_kind"], "exact")
        kinds = {i["entity_kind"] for i in out["items"]}
        self.assertIn("note", kinds)


if __name__ == "__main__":
    unittest.main()
