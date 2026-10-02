"""Prompt 回归评测 runner（阶段D）。

用法：
  python backend/scripts/run_prompt_eval.py            # 默认 mock 模式
  python backend/scripts/run_prompt_eval.py --mock     # 规则/结构断言，零成本，CI 可跑
  python backend/scripts/run_prompt_eval.py --llm      # 真实 LLM 模式（读 .env，需网络，默认不跑）

mock 模式对每条 golden 做确定性断言（不调 LLM）：
  - system_prompt_contains：注册表 prompt 文本必须含指定关键词
    （能抓到「删了红线段」「改了定界声明」这类回归）；
  - intent / plan_tools：规则理解 + 规则计划的工具选择正确性；
  - delimiter：消息构造必须含定界标记（能抓到「定界标记丢失」）；
  - preamble_contains：学段适配注入；
  - retrieval_faithfulness：检索未命中时必须如实告知且禁止编造。

真实 LLM 模式逐条调模型生成回答，按 expect.must_include_any /
must_not_include 做关键词断言（软指标，供人工趋势对比，不作 CI 门禁）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

GOLDEN_PATH = _BACKEND / "tests" / "prompt_eval" / "golden.jsonl"


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for i, ln in enumerate(f, 1):
            ln = ln.strip()
            if not ln:
                continue
            e = json.loads(ln)
            for key in ("id", "category", "input"):
                if key not in e:
                    raise ValueError(f"golden 第{i}行缺少字段 {key}")
            entries.append(e)
    return entries


# --- mock 检查 -----------------------------------------------------------------

def _check_system_prompt_contains(check: dict) -> list[str]:
    from app.prompts.registry import get
    text = get(check.get("prompt", "tutor_system")).text
    return [f"prompt {check.get('prompt', 'tutor_system')} 缺少关键词「{kw}」"
            for kw in check["keywords"] if kw not in text]


def _rule_understanding(entry: dict):
    from app.agents.task_understanding import rule_understand
    return rule_understand(entry["input"])


def _check_intent(entry: dict, check: dict) -> list[str]:
    u = _rule_understanding(entry)
    if u.intent.value != check["intent"]:
        return [f"intent 期望 {check['intent']}，实际 {u.intent.value}"]
    return []


def _check_plan_tools(entry: dict, check: dict) -> list[str]:
    from app.agents.planner import _rule_plan
    from app.agents.state import StudentSnapshot
    has_materials = bool(check.get("has_materials", False))
    snap = StudentSnapshot(
        grade=check.get("grade", "初中"),
        has_materials=has_materials,
        material_count=1 if has_materials else 0,
        recent_quiz_count=int(check.get("recent_quiz_count", 0)),
    )
    plan = _rule_plan(_rule_understanding(entry), snap)
    tools = {t for s in plan.steps for t in (s.suggested_tools or [])}
    fails = [f"计划缺少工具 {t}（实际 {sorted(tools)}）"
             for t in check.get("tools", []) if t not in tools]
    fails += [f"计划不应包含工具 {t}" for t in check.get("forbidden_tools", [])
              if t in tools]
    return fails


def _check_delimiter(entry: dict, check: dict) -> list[str]:
    target = check["target"]
    if target == "user_input":
        from app.core.context import build_context
        msgs = build_context("SYS", "", [], entry["input"], "")
        cur = [m for m in msgs if m["role"] == "user"][-1]["content"]
        if not (cur.startswith("<user_input>") and cur.endswith("</user_input>")):
            return ["build_context 未给当前用户消息包 <user_input> 定界标记"]
        return []
    if target == "redline_tail":
        from app.core.context import build_context
        from app.prompts.registry import get
        msgs = build_context("SYS", "", [], entry["input"], "recap")
        if msgs[-1]["content"] != get("redline_tail").text:
            return ["消息列表尾部缺少红线重述 system 消息"]
        return []
    if target == "knowledge_search":
        from app.core.knowledge_store import KnowledgeStore
        from app.tools.knowledge_search import KnowledgeSearchTool
        store = KnowledgeStore()
        store.add_file("eval_f", "评测笔记.txt",
                       "浮力是流体对物体向上的托力，阿基米德原理给出大小。")
        res = asyncio.run(KnowledgeSearchTool(store).run(query="浮力"))
        if res.is_error or "<material_excerpt>" not in res.text:
            return ["knowledge_search 结果缺少 <material_excerpt> 定界标记"]
        return []
    if target == "recall_history":
        import tempfile
        from unittest.mock import patch
        from app.core import context as ctx_mod
        from app.tools.recall_history import RecallHistoryTool
        with tempfile.TemporaryDirectory() as td, \
                patch.object(ctx_mod, "_TRANSCRIPT_DIR", Path(td)):
            ctx_mod.append_transcript("eval_sess", 1, [
                {"role": "assistant", "content": "浮力是向上的托力。"}])
            res = asyncio.run(RecallHistoryTool("eval_sess").run(query="浮力"))
        if res.is_error or "<history_excerpt>" not in res.text:
            return ["recall_history 结果缺少 <history_excerpt> 定界标记"]
        return []
    if target == "workspace_memory":
        from unittest.mock import patch
        from app.agents import supervisor
        from app.core.session import TutorSession

        class _WS:
            public_memory = "学生最近在学浮力。"

        session = TutorSession(grade="初中")
        session.workspace_id = "ws_eval"
        with patch("app.core.workspace.load_workspace", return_value=_WS()):
            block = supervisor._workspace_memory_block(session)
        if "<workspace_memory>" not in block:
            return ["工作区公共记忆块缺少 <workspace_memory> 定界标记"]
        return []
    return [f"未知 delimiter 目标 {target}"]


def _check_preamble_contains(check: dict) -> list[str]:
    from app.prompts.tutor import grade_preamble
    p = grade_preamble(check["grade"], False)
    return [f"grade_preamble({check['grade']}) 缺少「{kw}」"
            for kw in check["keywords"] if kw not in p]


def _check_retrieval_faithfulness(check: dict) -> list[str]:
    from app.core.knowledge_store import KnowledgeStore
    from app.tools.knowledge_search import KnowledgeSearchTool
    store = KnowledgeStore()
    # 小库存（<=8 chunks）会绕过 BM25 直接返回全部片段（既有行为），所以
    # 未命中路径必须用 >8 个与查询无关的片段来触发。
    for i, text in enumerate(check.get("seed_texts") or []):
        store.add_file(f"eval_f{i}", f"评测笔记{i}.txt", text)
    if check.get("seed_text"):
        store.add_file("eval_f", "评测笔记.txt", check["seed_text"])
    res = asyncio.run(KnowledgeSearchTool(store).run(query=check["query"]))
    if not res.is_error:
        return [f"检索「{check['query']}」本应未命中却返回了结果"]
    return [f"NOT_FOUND 文案缺少「{kw}」（检索忠实度/防臆造声明丢失）"
            for kw in check["keywords"] if kw not in res.text]


def check_mock(entry: dict) -> list[str]:
    """对一条 golden 执行全部 mock 检查，返回失败信息列表（空=通过）。"""
    fails: list[str] = []
    for check in entry.get("mock", []):
        t = check.get("type")
        if t == "system_prompt_contains":
            fails += _check_system_prompt_contains(check)
        elif t == "intent":
            fails += _check_intent(entry, check)
        elif t == "plan_tools":
            fails += _check_plan_tools(entry, check)
        elif t == "delimiter":
            fails += _check_delimiter(entry, check)
        elif t == "preamble_contains":
            fails += _check_preamble_contains(check)
        elif t == "retrieval_faithfulness":
            fails += _check_retrieval_faithfulness(check)
        else:
            fails.append(f"未知 mock 检查类型 {t}")
    return fails


# --- 真实 LLM 模式（需网络，默认不跑） -------------------------------------------

async def _check_llm(entry: dict, llm) -> list[str]:
    from app.core.context import wrap_user_input
    from app.prompts.registry import get
    expect = entry.get("expect", {})
    if not expect:
        return []
    answer, _ = await llm.complete(
        [{"role": "system", "content": get("tutor_system").text},
         {"role": "user", "content": wrap_user_input(entry["input"])}],
        temperature=0.2, max_tokens=800)
    answer = answer or ""
    fails: list[str] = []
    any_of = expect.get("must_include_any")
    if any_of and not any(kw in answer for kw in any_of):
        fails.append(f"回答未命中任一期望关键词 {any_of}")
    for kw in expect.get("must_not_include", []):
        if kw in answer:
            fails.append(f"回答含有禁用关键词「{kw}」")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="Prompt 回归评测 runner")
    ap.add_argument("--mock", action="store_true",
                    help="规则/结构断言（默认模式，零成本，CI 可跑）")
    ap.add_argument("--llm", action="store_true",
                    help="真实 LLM 模式（读 .env，需网络，默认不跑）")
    ap.add_argument("--golden", default=str(GOLDEN_PATH), help="golden JSONL 路径")
    args = ap.parse_args()

    entries = load_golden(Path(args.golden))
    print(f"加载 golden {len(entries)} 条（{args.golden}）")

    # mock 模式：默认始终执行（CI 门禁）
    n_fail = 0
    for e in entries:
        fails = check_mock(e)
        if fails:
            n_fail += 1
            for f in fails:
                print(f"[MOCK FAIL] {e['id']} ({e['category']}): {f}")
    print(f"mock 模式：{len(entries) - n_fail}/{len(entries)} 通过")

    # 真实 LLM 模式：显式 --llm 才跑
    if args.llm:
        from app.core.llm_async import get_llm
        llm = get_llm()
        llm_fail = 0
        for e in entries:
            try:
                fails = asyncio.run(_check_llm(e, llm))
            except Exception as exc:
                fails = [f"LLM 调用异常: {exc}"]
            if fails:
                llm_fail += 1
                for f in fails:
                    print(f"[LLM FAIL] {e['id']} ({e['category']}): {f}")
        print(f"llm 模式：{len(entries) - llm_fail}/{len(entries)} 通过")
        return 1 if (n_fail or llm_fail) else 0

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
