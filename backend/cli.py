"""Interactive CLI for the V0 tutor agent.

Usage:
    python backend/cli.py                 # interactive REPL
    python backend/cli.py --once "讲一下浮力"   # single turn
    python backend/cli.py --grade 初中     # set student grade
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.llm_async import get_llm
from app.core.session import TutorSession, new_session_id, save_session
from app.tools.knowledge_search import KnowledgeSearchTool
from app.tools.quiz import GenerateQuizTool
from app.agents.chat_agent import chat_turn


def main() -> int:
    parser = argparse.ArgumentParser(description="Next Tutor Agent - CLI")
    parser.add_argument("--grade", default="", help="学段: 空(自动)/小学/初中/高中/本科")
    parser.add_argument("--once", metavar="MSG", help="run a single turn and exit")
    args = parser.parse_args()

    session = TutorSession(grade=args.grade)
    # 急切 id（与 API 路径同一契约，DESIGN §4.4）：首轮工具执行时
    # transcript 记录（出题/作答）就需要 session_id 已存在。
    session.session_id = new_session_id("cli")
    llm = get_llm()
    avoid = [str(q.get("stem", ""))[:40]
             for qh in (session.quiz_history or [])[-3:]
             for q in ((qh.get("questions") or []) if isinstance(qh, dict) else [])
             if isinstance(q, dict)]
    tools = [KnowledgeSearchTool(session.knowledge), GenerateQuizTool(llm, avoid_stems=avoid)]
    print(f"[Edu_Agent] 学段={args.grade or '自动'} 模型已就绪。输入 'exit' 退出。", flush=True)

    if args.once:
        _turn(args.once, session, tools)
        return 0
    while True:
        try:
            user = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user or user.lower() in {"exit", "quit", "q"}:
            break
        _turn(user, session, tools)
    return 0


def _turn(user: str, session: TutorSession, tools: list) -> None:
    async def _consume() -> None:
        answer = ""
        async for ev in _run(user, session, tools):
            if ev["type"] == "answer" and ev.get("is_delta"):
                print(ev["content"], end="", flush=True)
                answer += ev["content"]
            elif ev["type"] == "done":
                t = ev.get("trace_id", "?")
                n_tools = len(ev.get("tool_calls", []))
                print(f"\n[trace] tools={n_tools} id={t}", flush=True)
    try:
        asyncio.run(_consume())
    except Exception as e:
        print(f"\n[错误] {type(e).__name__}: {e}")


async def _run(user, session, tools):
    async for ev in chat_turn(user, session, tools):
        yield ev


if __name__ == "__main__":
    sys.exit(main())
