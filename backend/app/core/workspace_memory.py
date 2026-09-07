"""Public memory management for workspaces.

The public memory is a structured summary (same 7-field format as session
compaction) that accumulates across ALL sessions in a workspace. It is:

  - Injected as a separate system block BEFORE the session's own history.
    It does NOT participate in the session's compaction -- the session's
    own context budget is independent. This is the "merge public memory
    into each round" pattern from the user spec.
  - Updated after each chat turn (async, fire-and-forget -- never blocks
    the SSE response) and when a session is moved into a workspace.

The public memory window is bounded to SOFT_BUDGET_TOKENS * 4 chars (same as a single
conversation's context window). When it exceeds the budget, it is itself
compacted (summary of summary -- acceptable here because the raw transcripts
of all sessions are still on disk and recoverable via recall_history).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .llm_async import AsyncLLMClient, get_llm
from .context import estimate_tokens, SOFT_BUDGET_TOKENS, transcript_path
from .workspace import Workspace, load_workspace, save_workspace
from ..prompts.registry import get as _prompt

# Same structured format as session compaction, but scoped to the workspace.
# 阶段D：prompt 文本统一由注册表管理（含版本号），此处薄 re-export 兼容。
_WS_MEMORY_SYSTEM = _prompt("workspace_memory_system").text

# When public memory itself exceeds this, compress it (self-summarize).
# 这里的比较对象是 len()（字符），所以按 ~4 字符/token 从 token 预算换算，
# 保持与旧 SOFT_BUDGET_CHARS=24000 相同的有效阈值。
_WS_MEMORY_BUDGET = SOFT_BUDGET_TOKENS * 4  # same as single conversation window


def _render_turn_for_memory(
    user_message: str,
    assistant_message: str,
    tool_calls: list[dict[str, Any]] | None = None,
    session_title: str = "",
) -> str:
    """Render a single turn into a compact string for the memory update LLM."""
    from .memory_safety import memory_safe_text
    parts = []
    if session_title:
        parts.append(f"[来源对话: {session_title}]")
    user_trim = memory_safe_text(user_message, max_chars=800)
    parts.append(f"学生问: {user_trim}")
    asst_trim = memory_safe_text(assistant_message, max_chars=1200)
    parts.append(f"老师答: {asst_trim}")
    if tool_calls:
        names = ", ".join(tc.get("name", "") for tc in tool_calls)
        parts.append(f"(调用工具: {names})")
    return "\n".join(parts)


async def update_workspace_memory(
    ws_id: str,
    user_message: str,
    assistant_message: str,
    tool_calls: list[dict[str, Any]] | None = None,
    session_title: str = "",
    llm: AsyncLLMClient | None = None,
) -> None:
    """Merge a turn's information into the workspace public memory.

    Called after each chat turn in a workspace session. Fire-and-forget:
    errors are logged but never raised (does not block the SSE response).
    """
    try:
        ws = load_workspace(ws_id)
        if ws is None:
            return
        llm = llm or get_llm()
        turn_str = _render_turn_for_memory(
            user_message, assistant_message, tool_calls, session_title
        )
        existing = ws.public_memory or "(暂无公共记忆)"
        # Build the update prompt: existing memory + new turn info.
        prompt = (
            f"以下是该工作学习区当前的公共记忆：\n\n{existing}\n\n"
            f"---\n现在有一段新对话信息需要整合进公共记忆：\n\n{turn_str}\n\n"
            f"请输出更新后的完整公共记忆（保持7个字段格式）。"
        )
        # If the combined prompt is too large, truncate the existing memory.
        if len(prompt) > 20000:
            prompt = (
                f"以下是该工作学习区当前的公共记忆（已截断）：\n\n"
                f"{existing[:15000]}...\n\n---\n"
                f"新对话信息：\n\n{turn_str}\n\n"
                f"请输出更新后的完整公共记忆（保持7个字段格式，注意不要丢失旧记忆中的关键信息）。"
            )
        new_memory, _ = await llm.complete(
            [{"role": "system", "content": _WS_MEMORY_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
        )
        new_memory = (new_memory or "").strip()
        if new_memory:
            # Self-compaction: if memory exceeds budget, compress it once.
            if len(new_memory) > _WS_MEMORY_BUDGET:
                new_memory = await _compact_workspace_memory(new_memory, llm)
            # Reload BEFORE saving: the LLM call above takes seconds, and any
            # upload/rename/session-move landing in that window must not be
            # clobbered by the stale snapshot loaded at function entry
            # (read-modify-write race). save_workspace stamps updated_at.
            ws = load_workspace(ws_id)
            if ws is None:
                return
            ws.public_memory = new_memory
            ws.public_memory_updated_at = time.time()
            save_workspace(ws)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "workspace memory update failed for %s: %s", ws_id, e
        )


async def _compact_workspace_memory(memory: str, llm: AsyncLLMClient) -> str:
    """Self-summarize an oversized public memory (lossy, but raw transcripts
    are on disk for recovery via recall_history)."""
    compact_prompt = (
        "以下是该工作学习区的公共记忆，已超出容量上限。请压缩它，保留每个字段中"
        "最重要的信息（知识点、错题薄弱点、学生偏好、待办），去掉冗余细节。"
        "保持7个字段格式。\n\n"
    )
    result, _ = await llm.complete(
        [{"role": "system", "content": _WS_MEMORY_SYSTEM},
         {"role": "user", "content": compact_prompt + memory[:20000]}],
        temperature=0.2,
        max_tokens=1200,
    )
    return (result or "").strip() or memory[:_WS_MEMORY_BUDGET]


async def compact_workspace_memory_on_new_session(
    ws_id: str, session_id: str, llm: AsyncLLMClient | None = None,
) -> dict[str, Any]:
    """Whole-memory merge/compaction once per new workspace conversation.

    The public memory stays workspace-local. This hook does not inspect or
    modify user-level prompt memory and does not roll back when one chat is
    later deleted.
    """
    try:
        ws = load_workspace(ws_id)
        if ws is None or not session_id:
            return {"status": "missing"}
        if session_id in ws.memory_boundary_sessions:
            return {"status": "already_done"}
        if ws.public_memory.strip():
            llm = llm or get_llm()
            compacted = await _compact_workspace_memory(ws.public_memory, llm)
        else:
            compacted = ""
        latest = load_workspace(ws_id)
        if latest is None:
            return {"status": "missing"}
        if session_id not in latest.memory_boundary_sessions:
            latest.memory_boundary_sessions.append(session_id)
        latest.memory_boundary_sessions = latest.memory_boundary_sessions[-100:]
        if compacted:
            latest.public_memory = compacted[:_WS_MEMORY_BUDGET]
            latest.public_memory_updated_at = time.time()
        save_workspace(latest)
        return {"status": "compacted" if compacted else "empty"}
    except Exception:
        return {"status": "error"}


async def init_workspace_memory_from_session(
    ws_id: str,
    session_id: str,
    llm: AsyncLLMClient | None = None,
) -> None:
    """When a session is moved into a workspace, scan its transcript and
    generate/merge a public memory update from all its turns.

    This is the 'moving a project into a folder triggers a public memory
    update' requirement.
    """
    try:
        ws = load_workspace(ws_id)
        if ws is None:
            return
        path = transcript_path(session_id)
        if not path.exists():
            # No transcript yet -- nothing to extract.
            return
        llm = llm or get_llm()
        # Read all turns from the transcript JSONL.
        turns: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for ln in f:
                try:
                    turns.append(json.loads(ln))
                except Exception:
                    continue
        if not turns:
            return
        # Group by turn number, render each turn compactly.
        by_turn: dict[int, list[dict[str, Any]]] = {}
        for t in turns:
            tn = t.get("turn", 0)
            by_turn.setdefault(tn, []).append(t)
        rendered_turns = []
        for tn in sorted(by_turn.keys()):
            entries = by_turn[tn]
            user_msg = ""
            asst_msg = ""
            tc_names: list[str] = []
            for e in entries:
                role = e.get("role", "")
                if role == "user":
                    user_msg = e.get("content", "")
                elif role == "assistant":
                    asst_msg = e.get("content", "")
                    tcs = e.get("tool_calls") or []
                    for tc in tcs:
                        if isinstance(tc, dict):
                            tc_names.append(tc.get("name", ""))
            if user_msg or asst_msg:
                rendered_turns.append(_render_turn_for_memory(
                    user_msg[:600], asst_msg[:800],
                    [{"name": n} for n in tc_names] if tc_names else None,
                    session_title=f"session:{session_id[:20]}"
                ))
        if not rendered_turns:
            return
        # Batch: merge all turns at once (or in chunks if very long).
        all_turns = "\n\n".join(rendered_turns[:20])  # cap to first 20 turns
        existing = ws.public_memory or "(暂无公共记忆)"
        prompt = (
            f"以下是该工作学习区当前的公共记忆：\n\n{existing}\n\n---\n"
            f"以下是一个新移入的对话的完整历史，请整合进公共记忆：\n\n{all_turns}\n\n"
            f"请输出更新后的完整公共记忆（保持7个字段格式）。"
        )
        if len(prompt) > 20000:
            prompt = prompt[:19000] + "\n...(已截断)"
        new_memory, _ = await llm.complete(
            [{"role": "system", "content": _WS_MEMORY_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
        )
        new_memory = (new_memory or "").strip()
        if new_memory:
            if len(new_memory) > _WS_MEMORY_BUDGET:
                new_memory = await _compact_workspace_memory(new_memory, llm)
            ws.public_memory = new_memory
            ws.public_memory_updated_at = time.time()
            save_workspace(ws)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "workspace memory init from session %s failed: %s", session_id, e
        )
