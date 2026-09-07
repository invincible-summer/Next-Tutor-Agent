"""R10 deterministic pre-retrieval (chat_agent auto knowledge_search).

The bug this pins: weak models NARRATE "let me search the uploaded file" and
then answer from the filename + the 300-char preview without ever calling
knowledge_search — fluent hallucination that looks file-grounded. The fix:
when the question references uploaded materials and knowledge exists,
chat_turn runs knowledge_search BEFORE the ReAct loop and injects the result
as must-use context. Covered here with a stub LLM:

1. file-content question -> auto tool_result event emitted BEFORE any answer,
   and the LLM context contains the retrieved file text.
2. no-match retrieval -> context carries the "must admit not found" note.
3. unrelated question (no file reference) -> no auto retrieval.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.agents import chat_agent  # noqa: E402
from app.core.knowledge_store import KnowledgeStore  # noqa: E402
from app.core.session import TutorSession  # noqa: E402
from app.tools.knowledge_search import KnowledgeSearchTool  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class _StubLLM:
    """Captures contexts; always answers directly without calling tools."""

    def __init__(self):
        self.contexts: list[list[dict]] = []
        self.tools_seen: list[list[dict]] = []

    async def stream(self, messages, tools=None, temperature=None):
        self.contexts.append([dict(m) for m in messages])
        self.tools_seen.append(list(tools) if tools else [])
        yield {"kind": "answer", "delta": "基于资料的回答。"}
        yield {"kind": "done", "finish_reason": "stop", "usage": {}}


def _session_with_file(text: str) -> TutorSession:
    store = KnowledgeStore()
    store.add_file("fid1", "作业三（文件综述）.docx", text)
    return TutorSession(session_id="s_pre", knowledge=store)


def _run_turn(session, message, llm, attachments=None):
    async def _collect():
        return [ev async for ev in chat_agent.chat_turn(
            message, session, [KnowledgeSearchTool(session.knowledge)], llm=llm,
            attachments=attachments)]

    with patch.object(chat_agent, "save_session"), \
            patch.object(chat_agent, "_persist_turn"), \
            patch.object(chat_agent, "Trace") as trace_cls:
        trace_cls.return_value.run_id = "t0"
        trace_cls.return_value.summary.return_value = {}
        return asyncio.run(_collect())


FILE_TEXT = ("文献综述：《理想与现实的对比——〈堂吉诃德〉中的二元性探究》\n"
             "作者杨钧富，学号2025010180，电子系。综述认为二元性贯穿全书。")


class TestAutoPreresearch(StorageSandboxTestCase):
    def test_file_question_triggers_retrieval_and_grounds_context(self):
        llm = _StubLLM()
        events = _run_turn(_session_with_file(FILE_TEXT),
                           "这份 docx 文件具体讲了什么？", llm)
        # auto knowledge_search result is emitted before any answer delta
        kinds = [e["type"] for e in events]
        self.assertIn("tool_result", kinds)
        self.assertLess(kinds.index("tool_result"), kinds.index("answer"))
        auto = next(e for e in events if e["type"] == "tool_result")
        self.assertEqual(auto["result"]["tool"], "knowledge_search")
        self.assertEqual(auto["result"]["status"], "success")
        # the LLM's context carries the actual file text, not just the filename
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[-1])
        self.assertIn("杨钧富", ctx_text)
        self.assertIn("2025010180", ctx_text)
        self.assertIn("严格基于", ctx_text)

    def test_no_match_injects_honesty_note(self):
        llm = _StubLLM()
        store = KnowledgeStore()
        # >SMALL_STORE_MAX_CHUNKS so BM25 (not the small-store passthrough) runs
        for i in range(12):
            store.add_file(f"b{i}", f"生物笔记{i}.txt", f"细胞膜控制物质进出 线粒体供能 {i} " * 30)
        session = TutorSession(session_id="s_miss", knowledge=store)
        events = _run_turn(session, "这份资料里讲了量子引力吗？", llm)
        kinds = [e["type"] for e in events]
        self.assertIn("tool_result", kinds)
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[-1])
        self.assertIn("没有找到", ctx_text)
        self.assertIn("严禁凭文件名", ctx_text)

    def test_content_question_uses_visible_materials_without_filename_reference(self):
        """Workspace material availability is enough for substantive questions.

        The previous behavior let the model narrate a search while the gate
        removed knowledge_search because the user did not say "教材".
        """
        llm = _StubLLM()
        events = _run_turn(_session_with_file(FILE_TEXT),
                           "牛顿第三定律的内容是什么？", llm)
        self.assertIn("tool_result", [e["type"] for e in events])

    def test_current_turn_reference_forces_and_scopes_retrieval(self):
        llm = _StubLLM()
        store = KnowledgeStore()
        store.add_file("old", "旧教材.txt", "只属于旧教材的火星内容 " * 20)
        store.add_file("new", "刚引用教材.txt", "刚引用教材中的海王星知识 " * 20)
        session = TutorSession(session_id="s_ref", knowledge=store)
        events = _run_turn(session, "请讲解一下", llm,
                           attachments=[{"id": "new", "filename": "刚引用教材.txt"}])
        self.assertIn("tool_result", [e["type"] for e in events])
        result = next(e["result"] for e in events if e["type"] == "tool_result")
        ids = {r.get("file_id") for r in result["data"]["results"]}
        self.assertEqual(ids, {"new"})
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[-1])
        self.assertIn("海王星", ctx_text)
        self.assertNotIn("火星内容", ctx_text)

    def test_persisted_pending_reference_forces_next_turn_once(self):
        llm = _StubLLM()
        session = _session_with_file(FILE_TEXT)
        session.pending_material_file_ids = ["fid1"]
        events = _run_turn(session, "请继续", llm)
        self.assertIn("tool_result", [e["type"] for e in events])
        self.assertEqual(session.pending_material_file_ids, [])


class _StubTrace:
    run_id = "t0"

    def log(self, *a, **k):
        pass

    def llm_call(self, *a, **k):
        pass

    def decision(self, *a, **k):
        pass

    def summary(self):
        return {}


def _run_executor(session, message, llm, plan=None, attachments=None):
    """Drive the REAL serving path (supervisor executor) with a stub LLM."""
    from app.agents import executor
    messages = [{"role": "user", "content": message}]
    entry = {"role": "user", "content": message}
    if attachments:
        entry["attachments"] = attachments
    session.messages.append(entry)

    async def _collect():
        return [ev async for ev in executor.execute(
            messages, session, [KnowledgeSearchTool(session.knowledge)],
            plan, llm, _StubTrace())]

    with patch.object(executor, "save_session", create=True), \
            patch.object(executor, "_persist_turn", create=True):
        return asyncio.run(_collect())


class TestExecutorPreresearch(StorageSandboxTestCase):
    """The supervisor path (default SUPERVISOR_MODE=v2) must ground file
    questions exactly like the legacy chat_turn path."""

    def test_executor_auto_retrieval_grounds_context(self):
        llm = _StubLLM()
        events = _run_executor(_session_with_file(FILE_TEXT),
                               "这份 docx 文件具体讲了什么？", llm)
        kinds = [e["type"] for e in events]
        self.assertIn("tool_result", kinds)
        self.assertLess(kinds.index("tool_result"), kinds.index("answer"))
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[-1])
        self.assertIn("杨钧富", ctx_text)
        self.assertIn("严格基于", ctx_text)

    def test_executor_content_question_uses_visible_materials(self):
        llm = _StubLLM()
        events = _run_executor(_session_with_file(FILE_TEXT),
                               "牛顿第三定律的内容是什么？", llm)
        self.assertIn("tool_result", [e["type"] for e in events])

    def test_executor_current_turn_reference_without_filename_forces_search(self):
        llm = _StubLLM()
        session = _session_with_file(FILE_TEXT)
        events = _run_executor(
            session, "请给我讲清楚", llm,
            attachments=[{"id": "fid1", "filename": "作业三.docx"}])
        self.assertIn("tool_result", [e["type"] for e in events])
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[-1])
        self.assertIn("杨钧富", ctx_text)

    def test_executor_plan_without_knowledge_step_still_preresearches(self):
        """Regression for the router-narrowing bug: a plan whose steps don't
        reference the knowledge capability hides knowledge_search from the
        visible tool set — exactly the case that produced filename-based
        hallucinations. The pre-retrieval must look the tool up in the FULL
        tools list, fire anyway, and re-expose knowledge_search for the loop."""
        from app.agents.state import PlanStep, TaskPlan
        llm = _StubLLM()
        plan = TaskPlan(steps=[PlanStep(agent_role="teaching", task="讲解文件内容")])
        events = _run_executor(_session_with_file(FILE_TEXT),
                               "这份 docx 文件具体讲了什么？", llm, plan=plan)
        kinds = [e["type"] for e in events]
        self.assertIn("tool_result", kinds)
        self.assertLess(kinds.index("tool_result"), kinds.index("answer"))
        ctx_text = "\n".join(str(m.get("content", "")) for m in llm.contexts[-1])
        self.assertIn("杨钧富", ctx_text)
        # teaching-role plan exposes NO tools; after the auto retrieval fired,
        # knowledge_search must be visible for follow-up queries
        schema_names = [s.get("function", {}).get("name") for s in llm.tools_seen[-1]]
        self.assertIn("knowledge_search", schema_names)


if __name__ == "__main__":
    unittest.main()
