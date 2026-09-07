"""Tests for the M-Notes agent (generate pipeline + per-note chat agent):

1. Generate: no sources -> error event; with a session source -> streamed
   draft persisted as a draft note with wiki-links and source provenance;
   code-fence/opening-line stripping; three source modes + real RAG.
2. Three-mode system (2026-09): ask answers only; plan emits a JSON plan
   card and never writes; authorize writes ONLY the bound note (cross-note
   and notes_create rejected); legacy mode values map suggest/collab->plan,
   cowrite/auto->authorize.
3. Plan approval state machine: pending -> approved (mode auto-switches to
   authorize, executed once) -> repeated approval rejected; reject_plan
   stays in plan mode; approval without a plan errors.
4. Per-note history isolation + one-shot lazy migration from legacy threads.
5. Optimistic-concurrency write conflict returns a retry hint.
6. Live thinking stream, error-branch closeout (run_end/done), attachments.
7. NOTES_AGENT_MODE=off degradation: SSE streams a single error event.
8. API surface: /generate and /chat/stream SSE; per-note agent endpoints;
   mode/action enum validation (422).

Fake LLMs only, no network. Data dirs are redirected to temp dirs.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.agents import notes_agent  # noqa: E402
from app.core import notes as notes_mod  # noqa: E402
from app.core import session as session_mod  # noqa: E402
from app.core import trash as trash_mod  # noqa: E402


class _TmpDirs:
    def __init__(self, test: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="notes_agent_")
        root = Path(self._tmp.name)
        self.root = root
        from app.agents.learning_orchestration import store as orch_store
        from app.agents.memory import prompt_memory
        from app.core import library as library_mod
        from app.core import textbook as textbook_mod
        from app.core import workspace as ws_mod
        self._patches = [
            patch.object(notes_mod, "_NOTES_DIR", root / "notes"),
            # 漏 patch 的写路径曾把 student_default.prompt_memory.json 直写生产目录。
            patch.object(prompt_memory, "_STUDENTS_DIR", root / "students"),
            patch.object(prompt_memory, "_POLICY_PATH",
                         root / "students" / "prompt_memory_policy.json"),
            patch.object(session_mod, "_SESSIONS_DIR", root / "chat_history"),
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
            patch.object(orch_store, "_STUDENTS_DIR", root / "students"),
            patch.object(library_mod, "_LIBRARY_DIR",
                         root / "chat_history" / "library"),
            patch.object(textbook_mod, "_LIBRARY_DIR",
                         root / "chat_history" / "library"),
            patch.object(ws_mod, "_WORKSPACES_DIR", root / "workspaces"),
        ]
        for p in self._patches:
            p.start()
        test.addCleanup(self.cleanup)

    def cleanup(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()


class _GenFake:
    """单轮流式假模型：生成管线用。"""

    def __init__(self, draft: str):
        self.draft = draft

    async def stream(self, messages, tools=None, temperature=0.3, **kw):
        yield {"kind": "answer", "delta": self.draft}
        yield {"kind": "done", "finish_reason": "stop"}


class _ToolFake:
    """首轮发 answer+tool_calls、次轮收尾的假模型：对话循环用。"""

    def __init__(self, calls: list[dict]):
        self.calls = calls
        self.rounds = 0

    async def stream(self, messages, tools=None, **kw):
        self.rounds += 1
        if tools and self.rounds == 1 and self.calls:
            yield {"kind": "answer", "delta": "先处理一下。"}
            yield {"kind": "tool_calls", "calls": self.calls}
            yield {"kind": "done", "finish_reason": "tool_calls"}
        else:
            yield {"kind": "answer", "delta": "处理完毕。"}
            yield {"kind": "done", "finish_reason": "stop"}


class _AnswerFake:
    """单轮固定回复的假模型：plan 卡解析等场景用。"""

    def __init__(self, answer: str, thinking: str = ""):
        self.answer = answer
        self.thinking = thinking

    async def stream(self, messages, tools=None, **kw):
        if self.thinking:
            yield {"kind": "thinking", "delta": self.thinking}
        yield {"kind": "answer", "delta": self.answer}
        yield {"kind": "done", "finish_reason": "stop"}


class _RetrievalFake:
    """生成管线假模型：complete 出检索查询，stream 记录最终 user 内容。"""

    def __init__(self, draft: str, queries: list[str]):
        self.draft = draft
        self.queries = queries
        self.queries_seen = False
        self.seen_user = ""

    async def complete(self, messages, **kw):
        self.queries_seen = True
        return json.dumps(self.queries, ensure_ascii=False), None

    async def stream(self, messages, tools=None, temperature=0.3, **kw):
        self.seen_user = str(messages[-1].get("content") or "")
        yield {"kind": "answer", "delta": self.draft}
        yield {"kind": "done", "finish_reason": "stop"}


PLAN_ANSWER = (
    "我建议这样修改当前笔记：\n"
    "1. 补充「动能定理」小节；2. 修正符号错误。\n"
    "```json\n"
    '{"title": "动能定理补充计划", "steps": ['
    '{"title": "补充动能定理小节", "detail": "在正文第二段后新增推导与例题"},'
    '{"title": "修正符号", "detail": "把 E_k 改为 $E_k$"}]}'
    "\n```"
)


def _run_chat(fake, **kwargs):
    async def run():
        with patch.object(notes_agent, "get_llm", lambda: fake):
            return [e async for e in notes_agent.run_notes_chat(
                "student_default", **kwargs)]
    return asyncio.run(run())


class TestNotesAgent(unittest.TestCase):
    def setUp(self) -> None:
        _TmpDirs(self)
        self.client = TestClient(create_app())

    def _make_session(self, title: str, messages: list[dict]) -> str:
        sid = session_mod.new_session_id(title)
        session = session_mod.TutorSession(session_id=sid)
        session.student_id = "student_default"
        session.title = title
        session.messages = messages
        session_mod.save_session(session)
        return sid

    def _make_note(self, title: str, content: str) -> dict:
        vault = notes_mod.load_vault("student_default")
        meta = vault.create_note(title=title, content=content)
        notes_mod.save_vault(vault)
        return meta

    def _set_pending_plan(self, note_id: str, plan_text: str = PLAN_ANSWER) -> dict:
        card = notes_agent._parse_plan_card(plan_text)
        assert card is not None
        pending = {"status": "pending", **card, "plan_text": plan_text,
                   "created_at": 1.0}
        notes_mod.set_pending_plan("student_default", note_id, pending)
        return pending

    # -- 1. generate ---------------------------------------------------------

    def test_generate_without_sources_errors(self):
        async def run():
            with patch.object(notes_agent, "get_llm",
                              lambda: _GenFake("x")):
                return [e async for e in notes_agent.generate_note(
                    "student_default", template_id="knowledge_summary",
                    sources={})]
        events = asyncio.run(run())
        self.assertEqual(events[-1]["type"], "error")

    def test_generate_creates_draft_note_with_links_and_source(self):
        sid = self._make_session("力学讨论", [
            {"role": "user", "content": "讲讲牛顿第二定律"},
            {"role": "assistant", "content": "F=ma"},
        ])
        draft = ("好的，以下是笔记\n```markdown\n"
                 "## 牛顿第二定律\n\n$F=ma$，参见 [[牛顿第二定律]]。\n```")

        async def run():
            with patch.object(notes_agent, "get_llm",
                              lambda: _GenFake(draft)):
                return [e async for e in notes_agent.generate_note(
                    "student_default", template_id="knowledge_summary",
                    sources={"session_ids": [sid]})]
        events = asyncio.run(run())
        kinds = [e["type"] for e in events]
        self.assertIn("sources_summary", kinds)
        self.assertIn("note_created", kinds)
        created = next(e for e in events if e["type"] == "note_created")
        note = created["note"]
        # code fence + opening line stripped
        self.assertTrue(created["content"].startswith("## 牛顿第二定律"))
        self.assertNotIn("```", created["content"])
        self.assertEqual(note["status"], "draft")
        self.assertEqual(note["source"]["session_ids"], [sid])
        # title derived from first heading
        self.assertEqual(note["title"], "牛顿第二定律")

    def test_generate_review_template_registers_m9_card(self):
        sid = self._make_session("复习", [{"role": "user", "content": "x"}])
        self._make_note("已有", "y")

        async def run():
            with patch.object(notes_agent, "get_llm",
                              lambda: _GenFake("## 温故计划\n内容")):
                return [e async for e in notes_agent.generate_note(
                    "student_default", template_id="review_note",
                    sources={"session_ids": [sid]})]
        events = asyncio.run(run())
        created = next(e for e in events if e["type"] == "note_created")
        self.assertTrue(created["note"]["review"]["enabled"])
        self.assertGreater(created["note"]["review"]["next_review_at"], 0)

    # -- 2. ask mode ----------------------------------------------------------

    def test_chat_ask_mode_answers_and_persists_history(self):
        note = self._make_note("问答", "内容")
        events = _run_chat(
            _ToolFake([]), message="这篇笔记讲了什么",
            context={"note_id": note["id"], "scope": "note"}, mode="ask")
        self.assertEqual(events[-1]["type"], "done")
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertEqual([m["role"] for m in state["messages"]],
                         ["user", "assistant"])
        self.assertEqual(state["messages"][0]["context"]["note_id"],
                         note["id"])
        self.assertEqual(state["mode"], "ask")

    def test_write_tool_rejected_in_ask_and_plan_modes(self):
        for mode in ("ask", "plan"):
            with self.subTest(mode=mode):
                note = self._make_note(f"保护-{mode}", "locked")
                fake = _ToolFake([{
                    "id": "c1", "name": "notes_write",
                    "args": {"note_id": note["id"], "content": "hacked",
                             "summary": "x"}}])
                events = _run_chat(
                    fake, message="改",
                    context={"note_id": note["id"], "scope": "note"},
                    mode=mode)
                tool_results = [e for e in events
                                if e["type"] == "tool_result"]
                self.assertTrue(tool_results)
                self.assertEqual(tool_results[0]["result"]["status"], "error")
                self.assertEqual(
                    notes_mod.load_vault("student_default").read_note(
                        note["id"]),
                    "locked")

    # -- 3. authorize mode ------------------------------------------------------

    def test_authorize_mode_writes_bound_note(self):
        note = self._make_note("授权目标", "v1")
        fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": note["id"], "content": "v2-by-agent",
                     "summary": "直写"}}])
        events = _run_chat(
            fake, message="直接改",
            context={"note_id": note["id"], "scope": "note"},
            mode="authorize")
        kinds = [e["type"] for e in events]
        self.assertIn("note_updated", kinds)
        updated = next(e for e in events if e["type"] == "note_updated")
        self.assertEqual(updated["content"], "v2-by-agent")
        self.assertEqual(updated["summary"], "直写")
        vault = notes_mod.load_vault("student_default")
        self.assertEqual(vault.read_note(note["id"]), "v2-by-agent")
        self.assertTrue(any(r["author"] == "agent"
                            for r in vault.list_revisions(note["id"])))

    def test_authorize_mode_cannot_write_other_notes(self):
        bound = self._make_note("绑定笔记", "keep")
        other = self._make_note("其他笔记", "untouched")
        fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": other["id"], "content": "hacked",
                     "summary": "越权"}}])
        events = _run_chat(
            fake, message="改那篇",
            context={"note_id": bound["id"], "scope": "note"},
            mode="authorize")
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual(results[0]["result"]["status"], "error")
        self.assertIn("当前笔记", results[0]["result"]["text"])
        vault = notes_mod.load_vault("student_default")
        self.assertEqual(vault.read_note(other["id"]), "untouched")

    def test_authorize_mode_cannot_create_notes(self):
        note = self._make_note("绑定", "x")
        fake = _ToolFake([{
            "id": "c1", "name": "notes_create",
            "args": {"title": "新笔记", "content": "y"}}])
        events = _run_chat(
            fake, message="新建",
            context={"note_id": note["id"], "scope": "note"},
            mode="authorize")
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual(results[0]["result"]["status"], "error")
        vault = notes_mod.load_vault("student_default")
        titles = [str(n.get("title")) for n in vault.notes]
        self.assertNotIn("新笔记", titles)

    def test_authorize_mode_requires_bound_note(self):
        # 仓库级对话（无绑定笔记）：即便 authorize 也不装写入工具
        fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": "whatever", "content": "x",
                     "summary": "s"}}])
        events = _run_chat(fake, message="改", context={"scope": "vault"},
                           mode="authorize")
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertTrue(results)
        self.assertEqual(results[0]["result"]["status"], "error")

    # -- 4. plan card + approval state machine -----------------------------------

    def test_plan_mode_emits_plan_card_and_sets_pending(self):
        note = self._make_note("计划", "v0")
        events = _run_chat(
            _AnswerFake(PLAN_ANSWER), message="帮我规划修改",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        cards = [e for e in events if e["type"] == "plan_card"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["plan"]["status"], "pending")
        self.assertEqual(cards[0]["plan"]["title"], "动能定理补充计划")
        self.assertEqual(len(cards[0]["plan"]["steps"]), 2)
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertEqual(state["pending_plan"]["status"], "pending")
        # plan 模式绝不写入
        self.assertEqual(
            notes_mod.load_vault("student_default").read_note(note["id"]), "v0")

    def test_plan_mode_plain_answer_sets_no_pending(self):
        note = self._make_note("澄清", "v0")
        events = _run_chat(
            _AnswerFake("你想先补哪一节？我们讨论一下。"),
            message="讨论", context={"note_id": note["id"], "scope": "note"},
            mode="plan")
        self.assertNotIn("plan_card", [e["type"] for e in events])
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertIsNone(state["pending_plan"])

    def test_plan_approval_executes_once_and_switches_mode(self):
        note = self._make_note("计划目标", "v0")
        self._set_pending_plan(note["id"])
        fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": note["id"], "content": "v1-per-plan",
                     "summary": "按计划修订"}}])
        events = _run_chat(
            fake, message="", action="approve_plan",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        kinds = [e["type"] for e in events]
        self.assertIn("mode_changed", kinds)
        self.assertEqual(
            next(e for e in events if e["type"] == "mode_changed")["mode"],
            "authorize")
        self.assertIn("note_updated", kinds)
        self.assertEqual(
            notes_mod.load_vault("student_default").read_note(note["id"]),
            "v1-per-plan")
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertEqual(state["pending_plan"]["status"], "executed")
        self.assertEqual(state["mode"], "authorize")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["mode"], "authorize")

    def test_plan_approval_cannot_repeat(self):
        note = self._make_note("重复批复", "v0")
        self._set_pending_plan(note["id"])
        fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": note["id"], "content": "v1",
                     "summary": "执行"}}])
        first = _run_chat(
            fake, message="", action="approve_plan",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        self.assertIn("note_updated", [e["type"] for e in first])
        # 第二次批复：已执行过 → 明确拒绝，且不再写入
        second_fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": note["id"], "content": "v2-again",
                     "summary": "重复"}}])
        second = _run_chat(
            second_fake, message="", action="approve_plan",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        self.assertEqual(second[-1]["type"], "error")
        self.assertIn("重复批复", second[-1]["message"])
        self.assertEqual(
            notes_mod.load_vault("student_default").read_note(note["id"]),
            "v1")

    def test_plan_approval_without_plan_errors(self):
        events = _run_chat(_ToolFake([]), message="", mode="plan",
                           action="approve_plan",
                           context={"scope": "vault"})
        self.assertEqual(events[-1]["type"], "error")

    def test_plan_reject_stays_in_plan_mode(self):
        note = self._make_note("驳回", "v0")
        notes_mod.set_agent_mode("student_default", note["id"], "plan")
        self._set_pending_plan(note["id"])
        events = _run_chat(
            _ToolFake([]), message="", action="reject_plan",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        kinds = [e["type"] for e in events]
        self.assertIn("plan_card", kinds)
        self.assertEqual(
            next(e for e in events if e["type"] == "plan_card")["plan"]
            ["status"], "rejected")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["mode"], "plan")
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertEqual(state["pending_plan"]["status"], "rejected")
        self.assertEqual(state["mode"], "plan")
        # 驳回后不能再批复
        again = _run_chat(
            _ToolFake([]), message="", action="approve_plan",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        self.assertEqual(again[-1]["type"], "error")

    def test_new_plan_card_overwrites_pending(self):
        note = self._make_note("改计划", "v0")
        self._set_pending_plan(note["id"])
        new_plan = PLAN_ANSWER.replace("动能定理补充计划", "新方案二期")
        events = _run_chat(
            _AnswerFake(new_plan), message="换个方案",
            context={"note_id": note["id"], "scope": "note"}, mode="plan")
        cards = [e for e in events if e["type"] == "plan_card"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["plan"]["title"], "新方案二期")
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertEqual(state["pending_plan"]["status"], "pending")

    # -- 5. mode normalization ----------------------------------------------------

    def test_normalize_mode_maps_legacy_values(self):
        self.assertEqual(notes_agent.normalize_mode("suggest"), "plan")
        self.assertEqual(notes_agent.normalize_mode("collab"), "plan")
        self.assertEqual(notes_agent.normalize_mode("cowrite"), "authorize")
        self.assertEqual(notes_agent.normalize_mode("auto"), "authorize")
        self.assertEqual(notes_agent.normalize_mode("plan"), "plan")
        self.assertEqual(notes_agent.normalize_mode("ask"), "ask")
        self.assertEqual(notes_agent.normalize_mode("authorize"), "authorize")
        self.assertEqual(notes_agent.normalize_mode("junk"), "ask")
        self.assertEqual(notes_agent.normalize_mode(""), "ask")

    def test_legacy_cowrite_behaves_as_authorize(self):
        note = self._make_note("旧值", "x")
        fake = _ToolFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": note["id"], "content": "y",
                     "summary": "s"}}])
        events = _run_chat(
            fake, message="m", context={"note_id": note["id"]},
            mode="cowrite")
        self.assertIn("note_updated", [e["type"] for e in events])
        state = notes_mod.agent_history_view("student_default", note["id"])
        self.assertEqual(state["mode"], "authorize")

    def test_legacy_collab_behaves_as_plan(self):
        note = self._make_note("旧值2", "x")
        events = _run_chat(
            _AnswerFake(PLAN_ANSWER), message="m",
            context={"note_id": note["id"]}, mode="collab")
        self.assertIn("plan_card", [e["type"] for e in events])

    # -- 6. per-note isolation + migration -------------------------------------------

    def test_histories_are_isolated_per_note(self):
        one = self._make_note("笔记一", "a")
        two = self._make_note("笔记二", "b")
        _run_chat(_ToolFake([]), message="关于一的问题",
                  context={"note_id": one["id"]}, mode="ask")
        _run_chat(_ToolFake([]), message="关于二的问题",
                  context={"note_id": two["id"]}, mode="ask")
        s1 = notes_mod.agent_history_view("student_default", one["id"])
        s2 = notes_mod.agent_history_view("student_default", two["id"])
        self.assertEqual([m["content"] for m in s1["messages"]],
                         ["关于一的问题", "处理完毕。"])
        self.assertEqual([m["content"] for m in s2["messages"]],
                         ["关于二的问题", "处理完毕。"])

    def test_legacy_threads_migrate_once_per_note(self):
        note = self._make_note("迁移目标", "x")
        other = self._make_note("另一篇", "y")
        tdir = notes_mod._threads_dir("student_default")
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "default.json").write_text(json.dumps({
            "thread_id": "default", "title": "旧线程", "messages": [
                {"role": "user", "content": "vault 消息不迁移",
                 "context": {"scope": "vault"}, "ts": 1},
                {"role": "user", "content": "u1",
                 "context": {"note_id": note["id"], "mode": "collab"},
                 "ts": 2},
                {"role": "assistant", "content": "a1",
                 "context": {"note_id": note["id"], "mode": "collab"},
                 "ts": 3},
                {"role": "user", "content": "其他笔记的消息",
                 "context": {"note_id": other["id"]}, "ts": 4},
            ]}, ensure_ascii=False), encoding="utf-8")
        state = notes_mod.load_agent_history("student_default", note["id"])
        self.assertEqual([m["content"] for m in state["messages"]],
                         ["u1", "a1"])
        # 旧模式 collab → plan
        self.assertEqual(state["mode"], "plan")
        # 其他笔记的历史独立迁移，互不混入
        state_other = notes_mod.load_agent_history(
            "student_default", other["id"])
        self.assertEqual([m["content"] for m in state_other["messages"]],
                         ["其他笔记的消息"])

    def test_removing_note_deletes_its_agent_history(self):
        note = self._make_note("待删", "x")
        _run_chat(_ToolFake([]), message="hi",
                  context={"note_id": note["id"]}, mode="ask")
        agent_file = notes_mod._agent_state_path("student_default",
                                                 note["id"])
        self.assertTrue(agent_file.exists())
        self.client.delete(f"/api/v1/notes/notes/{note['id']}")  # 归档
        self.assertFalse(agent_file.exists())

    # -- 7. optimistic concurrency ------------------------------------------------

    def test_write_conflict_returns_retry_hint(self):
        note = self._make_note("并发", "v1")

        class _ConcurrentFake(_ToolFake):
            async def stream(self, messages, tools=None, **kw):
                if tools and self.rounds == 0:
                    # 学生在智能体拿到版本快照之后、写入之前保存了编辑
                    vault = notes_mod.load_vault("student_default")
                    vault.write_note(note["id"], "user-edited",
                                     author="user", summary="学生编辑")
                    notes_mod.save_vault(vault)
                async for ev in super().stream(messages, tools=tools, **kw):
                    yield ev

        fake = _ConcurrentFake([{
            "id": "c1", "name": "notes_write",
            "args": {"note_id": note["id"], "content": "agent-edited",
                     "summary": "s"}}])
        events = _run_chat(
            fake, message="改", context={"note_id": note["id"]},
            mode="authorize")
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual(results[0]["result"]["status"], "error")
        self.assertIn("重读", results[0]["result"]["text"])
        # 学生编辑未被覆盖
        self.assertEqual(
            notes_mod.load_vault("student_default").read_note(note["id"]),
            "user-edited")

    # -- 8. live thinking + error closeout ------------------------------------------

    def test_live_thinking_streams_but_never_persists(self):
        note = self._make_note("思考", "x")
        events = _run_chat(
            _AnswerFake("答案。", thinking="我先分析当前笔记结构。"),
            message="问", context={"note_id": note["id"]}, mode="ask")
        live = [e for e in events if e["type"] == "thinking"]
        self.assertTrue(live)
        self.assertTrue(all(e.get("is_delta") and not e.get("summary")
                            for e in live))
        self.assertIn("我先分析当前笔记结构",
                      "".join(e["content"] for e in live))
        state = notes_mod.agent_history_view("student_default", note["id"])
        for m in state["messages"]:
            self.assertNotIn("thinking", m)
            self.assertNotIn("我先分析当前笔记结构", m["content"])

    def test_error_branch_appends_history_and_closes_stream(self):
        note = self._make_note("异常", "x")

        class _Boom:
            async def stream(self, messages, tools=None, **kw):
                raise RuntimeError("provider down")
                yield  # pragma: no cover

        events = _run_chat(
            _Boom(), message="hi", context={"note_id": note["id"]},
            mode="ask")
        kinds = [e["type"] for e in events]
        self.assertIn("error", kinds)
        self.assertIn("run_end", kinds)
        self.assertEqual(events[-1]["type"], "done")
        state = notes_mod.agent_history_view("student_default", note["id"])
        roles = [m["role"] for m in state["messages"]]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertTrue(state["messages"][1]["context"].get("error"))

    # -- 9. knowledge_search + attachments --------------------------------------------

    def _add_textbook(self, fid: str, name: str, text: str,
                      *, status: str = "ready") -> dict:
        """落库一个文件并注册为教材（对齐 test_attach_library 的做法）。"""
        from app.core import library as library_mod
        from app.core import textbook as textbook_mod
        lib = library_mod.load_library("student_default")
        meta = lib.add_file("", name, text, file_id=fid)
        meta["kind"] = "textbook"
        library_mod.save_library(lib)
        rec = textbook_mod.create_textbook(
            "student_default", file_id=fid, title=name)
        if rec.get("status") != status:
            recs = textbook_mod.load_textbooks("student_default")
            for r in recs:
                if r["id"] == rec["id"]:
                    r["status"] = status
            textbook_mod._save("student_default", recs)
        return rec

    def test_resolve_source_mode_prefers_explicit_then_infers(self):
        self.assertEqual(
            notes_agent._resolve_source_mode({"source_mode": "workspace"}),
            "workspace")
        self.assertEqual(
            notes_agent._resolve_source_mode({"workspace_id": "ws_1"}),
            "workspace")
        self.assertEqual(
            notes_agent._resolve_source_mode({"textbook_ids": ["tb"]}),
            "textbooks")
        self.assertEqual(notes_agent._resolve_source_mode({}), "sessions")

    def test_generate_textbooks_mode_runs_real_retrieval(self):
        rec = self._add_textbook(
            "f_phys_01", "物理.pdf",
            "第二章 力。牛顿第二定律：物体的加速度与合外力成正比，F=ma。"
            "动能定理：合外力做的功等于动能的变化。")
        fake = _RetrievalFake(
            draft="## 牛顿第二定律笔记\n\n$F=ma$，参见 [[牛顿第二定律]]。",
            queries=["牛顿第二定律"])

        async def run():
            with patch.object(notes_agent, "get_llm", lambda: fake):
                return [e async for e in notes_agent.generate_note(
                    "student_default", template_id="knowledge_summary",
                    sources={"source_mode": "textbooks",
                             "textbook_ids": [rec["id"]]})]
        events = asyncio.run(run())
        created = next(e for e in events if e["type"] == "note_created")
        self.assertEqual(created["note"]["source"]["source_mode"], "textbooks")
        self.assertIn("f_phys_01",
                      created["note"]["source"]["material_file_ids"])
        self.assertTrue(fake.queries_seen)
        self.assertIn("检索片段", fake.seen_user)
        self.assertIn("F=ma", fake.seen_user)
        summary = next(e for e in events if e["type"] == "sources_summary")
        self.assertGreaterEqual(summary.get("retrieved", 0), 1)
        self.assertIn("retrieving", [e.get("stage") for e in events
                                     if e["type"] == "step"])

    def test_retrieval_queries_fall_back_when_complete_fails(self):
        rec = self._add_textbook("f_chem_01", "化学.pdf",
                                 "氧化还原反应的本质是电子转移。")

        class _Broken:
            async def complete(self, *a, **kw):
                raise RuntimeError("offline")

            async def stream(self, messages, tools=None, temperature=0.3, **kw):
                yield {"kind": "answer", "delta": "## 化学笔记\n内容"}
                yield {"kind": "done", "finish_reason": "stop"}

        async def run():
            with patch.object(notes_agent, "get_llm", lambda: _Broken()):
                return [e async for e in notes_agent.generate_note(
                    "student_default", template_id="knowledge_summary",
                    sources={"source_mode": "textbooks",
                             "textbook_ids": [rec["id"]]})]
        events = asyncio.run(run())
        self.assertIn("note_created", [e["type"] for e in events])
        self.assertNotEqual(events[-1]["type"], "error")

    def test_generate_workspace_mode_expands_all_sessions(self):
        from app.core import workspace as ws_mod
        s1 = self._make_session("力学", [{"role": "user", "content": "a"}])
        s2 = self._make_session("热学", [{"role": "user", "content": "b"}])
        ws = ws_mod.Workspace(name="总复习", student_id="student_default")
        ws_mod.save_workspace(ws)
        ws_mod.add_session_to_workspace(ws.workspace_id, s1)
        ws_mod.add_session_to_workspace(ws.workspace_id, s2)

        async def run():
            with patch.object(notes_agent, "get_llm",
                              lambda: _GenFake("## 总复习\n内容")):
                return [e async for e in notes_agent.generate_note(
                    "student_default", template_id="knowledge_summary",
                    sources={"source_mode": "workspace",
                             "workspace_id": ws.workspace_id})]
        events = asyncio.run(run())
        created = next(e for e in events if e["type"] == "note_created")
        self.assertEqual(created["note"]["source"]["source_mode"], "workspace")
        self.assertEqual(set(created["note"]["source"]["session_ids"]),
                         {s1, s2})

    def test_chat_knowledge_search_tool_available_in_ask_mode(self):
        # 无笔记上下文 + 无来源：fallback 教材语料仍让 knowledge_search 可用
        self._add_textbook(
            "f_bio_01", "生物.pdf",
            "光合作用是绿色植物利用光能将二氧化碳和水合成有机物并释放氧气的过程。")
        fake = _ToolFake([{
            "id": "c1", "name": "knowledge_search",
            "args": {"query": "光合作用"}}])
        events = _run_chat(fake, message="光合作用是什么", mode="ask")
        results = [e["result"] for e in events if e["type"] == "tool_result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "success")
        self.assertIn("光合作用", results[0]["text"])
        self.assertEqual(events[-1]["type"], "done")

    def test_chat_attachments_recorded_and_degrade_without_vision(self):
        note = self._make_note("附件", "x")
        events = _run_chat(
            _ToolFake([]), message="看图",
            context={"note_id": note["id"], "scope": "note"},
            mode="ask",
            attachments=[{"id": "nofile", "filename": "a.png"}])
        self.assertEqual(events[-1]["type"], "done")
        state = notes_mod.agent_history_view("student_default", note["id"])
        ctx = state["messages"][0]["context"]
        self.assertEqual(ctx.get("attachments"),
                         [{"id": "nofile", "filename": "a.png"}])

    def test_knowledge_cards_are_compact_and_deduplicated(self):
        result = {"data": {"results": [{
            "file_id": "physics", "filename": "普通物理学.pdf",
            "chapter": "第 5 章", "printed_page": 123, "index": 9,
            "context_hash": "stable",
            "evidence_excerpt": "自旋角动量在外力矩作用下发生方向变化，用于说明进动现象。" + "原文" * 500,
        }]}}
        cards = notes_agent._knowledge_cards(result)
        content = notes_agent._with_knowledge_cards("正文", cards)
        content = notes_agent._with_knowledge_cards(content, cards)
        self.assertEqual(content.count("[知识卡]"), 1)
        self.assertIn("第 123 页", content)
        self.assertLess(len(content), 700)
        self.assertNotIn("原文" * 100, content)

    # -- 10. degradation + API surface ---------------------------------------------------

    def test_disabled_agent_route_returns_error_sse(self):
        import os
        with patch.dict(os.environ, {"NOTES_AGENT_MODE": "off"}):
            r = self.client.post("/api/v1/notes/chat/stream",
                                 json={"message": "你好"})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.headers["content-type"].startswith(
                "text/event-stream"))
            self.assertIn("event: error", r.text)
            self.assertIn("NOTES_AGENT_MODE", r.text)

    def test_generate_route_streams_sse(self):
        sid = self._make_session("来源", [{"role": "user", "content": "q"}])

        async def fake_generate(*args, **kwargs):
            yield {"type": "step", "stage": "collecting"}
            yield {"type": "done", "note_id": "x", "revision": 1}

        with patch.object(notes_agent, "generate_note", fake_generate):
            r = self.client.post("/api/v1/notes/generate", json={
                "template_id": "knowledge_summary",
                "sources": {"session_ids": [sid]}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith(
            "text/event-stream"))
        self.assertIn("event: step", r.text)
        self.assertIn("event: done", r.text)

    def test_chat_route_streams_sse_and_validates_mode(self):
        async def fake_chat(*args, **kwargs):
            yield {"type": "answer", "content": "你好", "is_delta": True}
            yield {"type": "done", "answer": "你好", "mode": "ask"}

        with patch.object(notes_agent, "run_notes_chat", fake_chat):
            r = self.client.post("/api/v1/notes/chat/stream", json={
                "message": "你好", "mode": "collab"})  # 旧值 → 映射放行
            self.assertEqual(r.status_code, 200)
            self.assertIn("event: answer", r.text)
            r2 = self.client.post("/api/v1/notes/chat/stream", json={
                "message": "你好", "mode": "godmode"})
            self.assertEqual(r2.status_code, 422)
            r3 = self.client.post("/api/v1/notes/chat/stream", json={
                "message": "你好", "action": "nuke_all"})
            self.assertEqual(r3.status_code, 422)

    def test_chat_route_passes_action_through(self):
        captured = {}

        async def fake_chat(*args, **kwargs):
            captured.update(kwargs)
            yield {"type": "done", "answer": "ok", "mode": "plan"}

        with patch.object(notes_agent, "run_notes_chat", fake_chat):
            r = self.client.post("/api/v1/notes/chat/stream", json={
                "message": "", "mode": "plan", "action": "approve_plan"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured.get("action"), "approve_plan")

    def test_note_agent_history_endpoints(self):
        note = self._make_note("端点", "x")
        base = f"/api/v1/notes/notes/{note['id']}/agent"
        r = self.client.get(base)
        self.assertEqual(r.status_code, 200)
        view = r.json()
        self.assertEqual(view["mode"], "ask")
        self.assertEqual(view["messages"], [])
        self.assertIn("modes", view)

        r = self.client.patch(base, json={"mode": "plan"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "plan")
        r = self.client.patch(base, json={"mode": "collab"})  # 旧值映射
        self.assertEqual(r.json()["mode"], "plan")
        r = self.client.patch(base, json={"mode": "junk"})
        self.assertEqual(r.status_code, 422)

        _run_chat(_ToolFake([]), message="hi",
                  context={"note_id": note["id"]}, mode="ask")
        r = self.client.get(base)
        self.assertEqual(len(r.json()["messages"]), 2)
        r = self.client.delete(base)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(base).json()["messages"], [])

    def test_missing_note_agent_endpoint_404(self):
        r = self.client.get("/api/v1/notes/notes/note_missing_x/agent")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
