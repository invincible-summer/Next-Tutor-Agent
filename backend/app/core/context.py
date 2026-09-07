"""Context engineering for the tutor agent (mainstream-aligned).

Implements GSSC + L1/L2/L3 + recoverable compaction (Claude Code/Manus pattern):

  - Transcript JSONL: append-only full-history backup per session. The
    "file system as ultimate context" — recoverable black box. Compaction
    is lossy but made RECOVERABLE via the recall_history tool.
  - Working context (session.messages): COMPRESSED working set fed to the
    LLM = [compaction_summary?] + recent N full turns. Bounded by budget.
  - Compaction: when L3 exceeds SOFT_BUDGET, an LLM produces a STRUCTURED
    summary. Replaces old turns as one read-only system message (never
    re-summarized). Recent N turns kept. State persisted.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import os

from .llm_async import AsyncLLMClient

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TRANSCRIPT_DIR = _PROJECT_ROOT / "chat_history"

def _soft_budget_tokens() -> int:
    """L3 压缩触发预算，统一为 token 口径（与 estimate_tokens 比较）。

    EDU_SOFT_BUDGET_TOKENS 优先；旧变量 EDU_SOFT_BUDGET_CHARS（字符口径）
    仍生效但按 ~4 字符/token 换算并记 deprecation warning（与
    estimate_tokens 的 latin 启发式一致，原默认值 24000 chars ≈ 6000 tokens）。
    """
    tok = os.getenv("EDU_SOFT_BUDGET_TOKENS")
    if tok:
        return int(tok)
    legacy = os.getenv("EDU_SOFT_BUDGET_CHARS")
    if legacy:
        import warnings
        warnings.warn(
            "EDU_SOFT_BUDGET_CHARS 已废弃，请改用 EDU_SOFT_BUDGET_TOKENS"
            "（token 口径）；当前按 ~4 字符/token 换算。",
            DeprecationWarning, stacklevel=2)
        return max(1, int(legacy) // 4)
    # Dynamic default: reserve room for the system prompt, tool schemas and
    # the next answer instead of treating history as the whole context.
    try:
        from .config import settings
        usable = (settings.llm_context_window
                  - settings.llm_max_output_tokens
                  - settings.llm_context_safety_margin)
        return min(settings.context_history_max_tokens,
                   max(1000, int(usable * 0.50)))
    except Exception:
        return 6000


SOFT_BUDGET_TOKENS = _soft_budget_tokens()
KEEP_RECENT_TURNS = 4       # full user-assistant turns kept after compaction

from ..prompts.registry import get as _prompt

# 阶段D：prompt 文本统一由注册表管理（含版本号），此处薄 re-export 兼容。
_COMPACT_SYSTEM = _prompt("compact_system").text

# --- 注入防护：定界标记 -------------------------------------------------------
# 不可信内容（用户消息/资料摘录/历史找回/工作区公共记忆）一律包裹定界标记；
# TUTOR_SYSTEM 的「定界内容」条款声明：标记内是数据不是指令。
USER_INPUT_OPEN = "<user_input>"
USER_INPUT_CLOSE = "</user_input>"


def wrap_user_input(text: str) -> str:
    """把当前用户消息包裹进 <user_input> 定界标记。"""
    return f"{USER_INPUT_OPEN}{text}{USER_INPUT_CLOSE}"


def unwrap_user_input(text: str) -> str:
    """剥掉 <user_input> 定界标记（无标记时原样返回）。"""
    if text.startswith(USER_INPUT_OPEN) and text.endswith(USER_INPUT_CLOSE):
        return text[len(USER_INPUT_OPEN):-len(USER_INPUT_CLOSE)]
    return text


def transcript_path(session_id: str) -> Path:
    if not session_id:
        raise ValueError("session_id required for transcript path")
    _TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    bare = Path(session_id).name  # path-traversal guard (session._resolve)
    if bare.endswith(".transcript.jsonl"):
        bare = bare[: -len(".transcript.jsonl")]
    return _TRANSCRIPT_DIR / f"{bare}.transcript.jsonl"


def append_transcript(session_id: str, turn: int, entries: list[dict[str, Any]]) -> None:
    """Append raw turn entries (user/assistant/tool_result) to the session's
    transcript JSONL. Append-only, crash-safe — the recoverable backup."""
    if not session_id or not entries:
        return
    path = transcript_path(session_id)
    ts = time.time()
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            line = {"ts": ts, "turn": turn, "session_id": session_id, **e}
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK ~1 char/token, latin ~4 chars/token."""
    if not text:
        return 0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(0, other) // 4


def history_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


def _serialize_for_compact(messages: list[dict[str, Any]]) -> str:
    """Render messages into a compact transcript string for the summarizer.
    system messages are skipped (never re-summarize the summary)."""
    parts = []
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        content = str(m.get("content", ""))
        if role == "system":
            # Incremental compaction must carry the previous structured summary
            # forward.  Skipping it would silently erase already-compacted
            # history on the second compaction cycle.  Other system messages
            # remain non-compressible and are intentionally excluded.
            if content.startswith("[对话压缩摘要"):
                body = content.split("\n", 1)[-1]
                parts.append(f"[{i}] 既有压缩摘要: {body}")
            continue
        tag = "学生" if role == "user" else "老师"
        tc = m.get("toolCalls") or m.get("tool_calls") or []
        tc_names = ", ".join(t.get("name", "") if isinstance(t, dict) else str(t) for t in tc) if tc else ""
        body = content if len(content) < 1200 else content[:1100] + f"…[{len(content)}字]"
        suffix = f"（调用工具: {tc_names}）" if tc_names else ""
        parts.append(f"[{i}] {tag}: {body}{suffix}")
    return "\n\n".join(parts)


async def compact_history(
    messages: list[dict[str, Any]],
    llm: AsyncLLMClient,
    keep_recent: int = KEEP_RECENT_TURNS,
    quiz_digest: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """LLM-based compaction. Splits [system+preamble] + [old] + [recent].
    Summarizes `old` into one read-only system message; keeps `recent`
    verbatim. Returns (new_messages, summary_text).

    The summary is a system message; _serialize_for_compact skips system
    messages, so it is never re-summarized (no summary-of-summary decay).
    ``quiz_digest`` is a deterministic render of the session's generated
    quizzes + graded answers (quiz payloads never appear in message text),
    injected ahead of the dialogue so the「练习与错题」field has real,
    loss-proof source material."""
    head = messages[:2]  # system + preamble (always kept)
    convo = messages[2:]
    # Keep complete user-started turns.  The cut must be *before* the earliest
    # retained user message; cutting after it leaves an orphan assistant entry
    # at the front of the recent window and breaks tool/result causality.
    user_positions = [i for i, message in enumerate(convo)
                      if message.get("role") == "user"]
    keep_recent = max(1, int(keep_recent))
    if len(user_positions) <= keep_recent:
        return messages, ""
    cut = user_positions[-keep_recent]
    if cut <= 0:
        return messages, ""
    old = convo[:cut]
    recent = convo[cut:]
    transcript_str = _serialize_for_compact(old)
    if len(transcript_str) > 20000:
        # 保留最近消息优先：尾部是最新进展，头截会丢掉最相关的上下文
        transcript_str = f"[已截断较早部分, 共{len(transcript_str)}字]…\n" + transcript_str[-19000:]
    if quiz_digest:
        # Prepend AFTER truncation so the deterministic quiz/answer facts can
        # never be cut away by the head-truncation above.
        transcript_str = ("【本会话出题与作答记录（结构化事实，优先保留进摘要）】\n"
                          + quiz_digest[:2000]
                          + "\n\n【对话原文】\n" + transcript_str)
    summary, _ = await llm.complete(
        [{"role": "system", "content": _COMPACT_SYSTEM},
         {"role": "user", "content": transcript_str}],
        temperature=0.2,
        max_tokens=1200,
        disable_thinking=True,
    )
    summary = (summary or "").strip()
    if not summary:
        # Never discard raw history when the summarizer starves its answer
        # channel or a Provider returns an empty completion.
        return messages, ""
    summary_msg = {
        "role": "system",
        "content": f"[对话压缩摘要（只读，完整历史见 transcript）]\n{summary}",
    }
    return head + [summary_msg] + recent, summary


def build_context(
    system_prompt: str,
    preamble: str,
    history: list[dict[str, Any]],
    user_message: str,
    todo_recap: str,
) -> list[dict[str, Any]]:
    """Assemble LLM messages: L1 system -> L2 preamble -> L3 history
    (already compacted) -> current user msg -> todo recap at tail.

    注入防护：当前用户消息包裹 <user_input> 定界标记（数据不是指令）。
    红线尾注（recency 效应）不在此附加——此处之后 supervisor/executor 还会
    叠加多层 system 消息，真正的尾部由调用方在所有注入完成后压（见
    executor 的 plan recap 之后 / chat_agent 的 build_context 调用点）。"""
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if preamble:
        msgs.append({"role": "user", "content": preamble})
    for m in history:
        msgs.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    msgs.append({"role": "user", "content": wrap_user_input(user_message)})
    if todo_recap:
        msgs.append({"role": "system", "content": todo_recap})
    return msgs
