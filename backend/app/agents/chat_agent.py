"""Decision layer: conversational tutor agent with ReAct tool-calling loop.

Intent classification -> short-circuit branches -> ReAct loop with native
function-calling. Streams SSE events (thinking/answer/step/tool_*/done/error).
Each turn is traced. Duplicate tool calls within a turn are rejected.

Adapted from Paper_Agent chat_turn (D-056+) and agent-develop methodology:
  - Intent classifier skips the full loop for greetings (saves ~1000 tokens).
  - ReAct loop with hard max_steps (no infinite tool loops).
  - Unified tool protocol -> diagnosable errors.
  - Context: L1 system prompt (red lines) + L3 session messages (trimmed).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator, Callable

from ..core.llm_async import AsyncLLMClient, get_llm
from ..core.quiz_attempts import (merge_quiz_results_from_disk,
                                  quiz_digest_for_session,
                                  record_generated_quiz)
from ..core.quiz_recent import record_recent_quiz
from ..core.session import TutorSession, save_session
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, ToolResult, err, ok
from ..core.trace import Trace
from ..core.config import settings, trace_dir_path
from ..core.validator import validate_tool_args
from ..prompts.tutor import TUTOR_SYSTEM, grade_preamble
from ..prompts.tutor import error_recovery_hint
from ..prompts.registry import active_versions
from ..core.context import (build_context, compact_history, estimate_tokens, history_tokens,
                            SOFT_BUDGET_TOKENS, KEEP_RECENT_TURNS,
                            append_transcript, transcript_path)
from ..core.workspace_memory import update_workspace_memory

MAX_STEPS = 6
MAX_HISTORY = 12  # trim L3 to last N messages to manage context window


def _textbooks_for_session(session: Any, merged_files: list[dict]) -> list[dict]:
    """P3: reverse-lookup textbooks among this turn's visible files (legacy path).

    Mirrors supervisor._visible_textbooks so the legacy chat_turn path injects the
    same [当前教材] preamble block as the v2 supervisor path. Never raises.
    """
    if not merged_files:
        return []
    try:
        from ..core.textbook import textbook_for_file, PUBLIC_STUDENT_ID
        sid = getattr(session, "student_id", "") or ""
        if not sid:
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for f in merged_files:
            fid = f.get("id") or ""
            # 自有反查，公用命名空间兜底（P6-B：公用教材所有人可见）。
            tb = (textbook_for_file(sid, fid) if fid else None) or (
                textbook_for_file(PUBLIC_STUDENT_ID, fid)
                if fid and sid != PUBLIC_STUDENT_ID else None)
            if tb is not None and tb["id"] not in seen:
                seen.add(tb["id"])
                out.append(tb)
            if len(out) >= 3:
                break
        return out
    except Exception:
        return []


def _has_textbook_for_session(session: Any, merged_files: list[dict]) -> bool:
    return bool(_textbooks_for_session(session, merged_files))

# R4: Tool output truncation — cap size, persist full to disk
# R6: Token-aware context trimming — char budget instead of message count
_TOOL_MSG_MAX_CHARS = 2000
_CONTEXT_CHAR_BUDGET = 12000  # ~3000 tokens for CJK (1 char ≈ 1 token)
# R7: History compression — summarize old messages when conversation grows
_COMPRESS_THRESHOLD = 16  # messages; when exceeded, compress oldest half
# R9: Attachment context — inject a reminder + capped preview, not the body
_ATTACH_PREVIEW_CHARS = 300  # per file; full text stays on disk for knowledge_search

from .preresearch import (_FILE_REF_RE, build_query, consume_pending_materials,
                          decide_material_grounding)
from .pseudo_tool_guard import PseudoToolGuard


def _attachment_context(session: TutorSession) -> str:
    """R9: Inject a reminder that uploaded files exist + a tiny preview.
    Let the agent decide whether/what to knowledge_search — don't pre-stuff."""
    from ..core.workspace import merged_knowledge_files
    files, _names = merged_knowledge_files(session)
    if not files:
        return ""
    lines = [f"[已上传资料 {len(files)} 份]"]
    for f in files[:5]:
        lines.append(f"  - {f['filename']} ({f['char_count']}字/{f.get('chunk_count',0)}片段)")
    lines.append("如需引用教材原文，用 knowledge_search 检索相关片段。")
    return "\n".join(lines)


def _compress_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """R7: Compress old conversation into a read-only Summary message.
    The Summary replaces those messages; it never participates in further
    compression (no 'summary of summary' — avoids lossy degradation)."""
    if len(messages) < _COMPRESS_THRESHOLD + 2:
        return messages
    head = messages[:2]  # system + preamble
    convo = messages[2:]
    cut = len(convo) // 2
    old = convo[:cut]
    recent = convo[cut:]
    # build a compact summary (rule-based, no LLM call to save tokens)
    summary_parts = []
    for m in old:
        role = m.get("role", "?")
        content = m.get("content", "")[:150]
        if role == "user":
            summary_parts.append(f"学生问: {content}")
        elif role == "assistant":
            tools = m.get("toolCalls", [])
            tnames = ", ".join(t.get("name", "") for t in tools) if tools else ""
            summary_parts.append(f"老师答: {content}{' (调用:' + tnames + ')' if tnames else ''}")
    summary = f"[对话摘要（只读，{len(old)} 条已压缩）]\n" + "\n".join(summary_parts)
    return head + [{"role": "system", "content": summary}] + recent


def _todo_recap(session: TutorSession, step: int) -> str:
    """R8: One-line Todo Recap pinned at the tail of the context.
    Always tells the model 'what am I doing right now' — like a sticky note."""
    parts = [f"[当前状态] step {step}/{MAX_STEPS}"]
    from ..core.workspace import merged_knowledge_files
    _mf, _mn = merged_knowledge_files(session)
    if _mf:
        parts.append(f"已上传{len(_mf)}份资料")
    if session.quiz_history:
        parts.append(f"已出{len(session.quiz_history)}套题")
    parts.append(f"学段={session.grade}")
    from ..core.quiz_attempts import latest_quiz_digest
    digest = latest_quiz_digest(session)
    if digest:
        parts.append(digest)
    return " | ".join(parts)

# R4: Tool output truncation — cap size, persist full to disk
_TOOL_MSG_MAX_CHARS = 2000

# R3: Circuit breaker — track consecutive failures per tool, disable after threshold
_CIRCUIT_THRESHOLD = 3
_circuit_failures: dict[str, int] = {}
_circuit_open: set[str] = set()


def _circuit_check(tool_name: str) -> ToolResult | None:
    """Return an error result if the circuit is open, else None."""
    if tool_name in _circuit_open:
        return err(tool_name, ErrorCode.CIRCUIT_OPEN,
                   f"工具 '{tool_name}' 因连续失败已暂时禁用。请稍后再试或换一种方式。")
    return None


def _circuit_record(tool_name: str, result: ToolResult) -> None:
    """Update failure counter; trip the circuit on threshold."""
    if result.is_error:
        _circuit_failures[tool_name] = _circuit_failures.get(tool_name, 0) + 1
        if _circuit_failures[tool_name] >= _CIRCUIT_THRESHOLD:
            _circuit_open.add(tool_name)
    else:
        _circuit_failures[tool_name] = 0


# R2: Reflector — rule-based quality checks on tool results (always on)
_REFLECT_RULES: dict[str, list] = {
    "knowledge_search": [
        (lambda r: r.data.get("count", 0) == 0,
         "检索返回 0 个片段。建议换一个关键词或上传更多资料。"),
    ],
    "generate_quiz": [
        (lambda r: len(r.data.get("questions", [])) == 0,
         "未生成任何题目。建议换一个更明确的知识点。"),
        (lambda r: any(len(str(q.get("explanation", ""))) < 15
                       for q in r.data.get("questions", [])),
         "部分题目解析过短，可能讲解不够充分。"),
    ],
}


def _reflect_tool_result(tool_name: str, result: ToolResult) -> str | None:
    """Run rule-based reflection. Returns a warning string or None."""
    if result.is_error:
        return None
    for check, message in _REFLECT_RULES.get(tool_name, []):
        try:
            if check(result):
                return message
        except Exception:
            continue
    return None


# intent classification: pure rule-based, no extra LLM call
_GREETINGS = {"你好", "您好", "hi", "hello", "hey", "在吗", "在", "谢谢", "感谢",
              "好的", "ok", "嗯", "嗯嗯", "收到", "了解", "明白", "知道了",
              "行", "好", "666", "好的好的"}

_TOOL_TRIGGERS = ("出题", "练习", "测验", "测一测", "巩固", "考考",
                  "检索", "查资料", "查找", "搜索",
                  "上传", "文件", "资料", "课件", "教材",
                  "错题", "分析错", "讲解一下", "讲一下", "讲讲")


def _classify_intent(message: str, session: TutorSession) -> str:
    """Returns 'direct' (skip tools) or 'react' (enter ReAct loop).

    R14: Handles greetings, acks, and short conversational fragments without
    the full tool loop. Anything with tool-trigger keywords always goes ReAct.
    Multi-pattern matching, not just exact set membership."""
    msg = message.strip()
    msg_lower = msg.lower()

    # tool-trigger keywords — force ReAct even for short messages
    if any(kw in msg for kw in _TOOL_TRIGGERS):
        return "react"

    # 书名号里的篇目/课文名（共享触发信号）：「《荷塘月色讲》」类短句必须
    # 进 ReAct 环，模型才有机会调检索工具。
    from .material_signals import mentions_title
    if mentions_title(msg):
        return "react"

    # greetings / acks — exact match
    if msg_lower in _GREETINGS:
        return "direct"

    # very short (<5 chars) with no punctuation and no tool triggers —
    # likely a greeting or ack fragment, skip the loop
    if len(msg) < 5 and not any(c in msg for c in "？！，。、,.?"):
        return "direct"

    return "react"


def _make_call_key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def _build_tool_result_message(result: ToolResult) -> str:
    """Compact, field-aware summary. R4: truncate+persist on overflow."""
    if result.is_error:
        hint = error_recovery_hint(result.error_code or "")
        return (f"[工具 {result.tool} 失败] 错误码: {result.error_code} — {result.text}\n"
                f"[恢复建议] {hint}")
    parts = [f"[工具 {result.tool} 完成]"]
    if result.text:
        text = result.text
        if len(text) > _TOOL_MSG_MAX_CHARS:
            spill_path = trace_dir_path() / f"tool_spill_{result.tool}_{int(time.time())}.txt"
            spill_path.write_text(text, encoding="utf-8")
            parts.append(f"摘要: {text[:_TOOL_MSG_MAX_CHARS]}\n...[已截断，完整 {len(text)} 字存于 {spill_path}]")
        else:
            parts.append(f"摘要: {text}")
    if not parts[1:] and result.data:
        parts.append(f"数据: {json.dumps(result.data, ensure_ascii=False)[:1500]}")
    return "\n".join(parts)


def _trim_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """R6: Keep system+preamble, then fit recent messages within a char budget.
    Drops oldest conversation messages first when over budget — closer to how
    LLM attention actually degrades (by token count, not message count)."""
    if len(messages) <= 3:
        return messages
    head = messages[:2]  # system + grade preamble (always kept)
    convo = messages[2:]
    total = sum(len(m.get("content", "")) for m in head)
    kept: list[dict[str, Any]] = []
    for m in reversed(convo):
        clen = len(m.get("content", ""))
        if total + clen > _CONTEXT_CHAR_BUDGET and kept:
            break
        kept.insert(0, m)
        total += clen
    return head + kept



async def _maybe_update_workspace_memory(
    session: TutorSession,
    user_message: str,
    assistant_message: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Fire-and-forget: update workspace public memory after a turn.
    Never raises -- errors are logged inside update_workspace_memory."""
    if not session.workspace_id:
        return
    async def _update() -> None:
        try:
            await update_workspace_memory(
                session.workspace_id,
                user_message,
                assistant_message,
                tool_calls,
                session_title=session.title or "",
            )
        except Exception:
            pass  # never block the response
    asyncio.create_task(_update()).add_done_callback(lambda _task: None)


def _persist_turn(session, user_msg, assistant_msg, tool_calls, trace):
    """Append this turn's raw entries to the session transcript JSONL and save.
    The transcript is the recoverable full-history backup for compaction."""
    from ..core.memory_safety import memory_safe_text
    safe_user = memory_safe_text(user_msg)
    safe_answer = memory_safe_text(assistant_msg)
    entries = [{"role": "user", "content": safe_user}]
    if tool_calls:
        entries.append({"role": "assistant", "content": safe_answer,
                         "tool_calls": [{"name": tc.get("name")} for tc in tool_calls]})
    else:
        entries.append({"role": "assistant", "content": safe_answer})
    try:
        if session.session_id:
            append_transcript(session.session_id, len(session.messages), entries)
    except Exception as e:
        trace.log("transcript_error", message=str(e))


def _legacy_prompt_memory_block(session: TutorSession) -> str:
    try:
        from .memory import get_memory_service, is_enabled
        if not is_enabled():
            return ""
        from .student_model.store import DEFAULT_STUDENT_ID
        sid = session.student_id or DEFAULT_STUDENT_ID
        return get_memory_service().build_directive(
            student_id=sid, concept="", subject="")
    except Exception:
        return ""


def _legacy_record_prompt_memory(session: TutorSession, user_msg: str,
                                 tool_calls: list[dict[str, Any]]) -> None:
    try:
        from .memory import get_memory_service, is_enabled
        if not is_enabled() or not session.session_id:
            return
        from .student_model.store import DEFAULT_STUDENT_ID
        sid = session.student_id or DEFAULT_STUDENT_ID
        outcome = ""
        for tc in tool_calls:
            result = tc.get("result") if isinstance(tc, dict) else None
            if isinstance(result, dict) and result.get("verdict"):
                outcome = str(result["verdict"])
                break
        get_memory_service().consume_turn(
            student_id=sid, session_id=session.session_id,
            workspace_id=session.workspace_id, user_message=user_msg,
            strategy_outcome=outcome)
    except Exception:
        pass


async def chat_turn(
    user_message: str,
    session: TutorSession,
    tools: list[Tool],
    llm: AsyncLLMClient | None = None,
    progress_cb: Callable[[str], Any] | None = None,
    lang: str = "zh",
    output_language: str | None = None,
    attachments: list[dict] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run one conversation turn, yielding SSE events.

    Events:
      {"type": "thinking", "content": "...", "is_delta": true}
      {"type": "answer", "content": "...", "is_delta": true}
      {"type": "step", "step": "thinking"|"tool_executing", "tool"?}
      {"type": "tool_start", "name": "...", "args": {...}}
      {"type": "tool_result", "result": {...}}
      {"type": "tool_warning", "warning": "...", "tool": "..."}
      {"type": "done", "thinking": "...", "answer": "...", "tool_calls": [...], "trace_id": "..."}
      {"type": "error", "message": "..."}
    """
    llm = llm or get_llm()
    trace = Trace()
    session._turn_material_cache_enabled = True
    session.__dict__.pop("_turn_merged_knowledge_cache", None)
    intent = _classify_intent(user_message, session)
    # Language policy (simplified — no input auto-detection, which was fragile
    # with bare math/code blocks that aren't always $$-delimited):
    #   - explicit output_language zh|en (from settings or session) -> forced;
    #   - otherwise default Chinese, with translation exercises excepted
    #     (handled by the system prompt rule + the preamble default directive).
    chosen = (output_language or session.output_language or "").lower()
    forced = chosen in ("zh", "en")
    answer_lang = chosen if forced else "zh"
    from ..core.workspace import merged_knowledge_files
    _merged_files, file_names = merged_knowledge_files(session)
    trace.log("turn_start", user_query=user_message, intent=intent,
              grade=session.grade, has_knowledge=bool(_merged_files),
              has_textbook=_has_textbook_for_session(session, _merged_files),
              answer_lang=answer_lang, forced=forced, mode="force" if forced else "default_zh",
              prompt_versions=active_versions())

    preamble = grade_preamble(session.grade, bool(_merged_files),
                             file_names, answer_lang=answer_lang, forced=forced,
                             textbooks=_textbooks_for_session(session, _merged_files))
    # R9: augment preamble with attachment reminder (reminder, not body)
    att_ctx = _attachment_context(session)
    if att_ctx:
        preamble += "\n" + att_ctx
    prompt_memory = _legacy_prompt_memory_block(session)
    if prompt_memory:
        preamble += "\n" + prompt_memory
    # Workspace public memory: injected as part of the preamble (L2), before
    # the session's own history. It does NOT participate in session compaction
    # -- the session's context budget is independent. This is the "merge
    # public memory into each round" pattern.
    ws_memory = ""
    if session.workspace_id:
        from ..core.workspace import workspace_for_session
        ws = workspace_for_session(session)
        if ws and ws.public_memory.strip():
            # 注入防护：公共记忆是不可信二手摘要，包裹 <workspace_memory> 定界标记。
            ws_memory = (f"\n[工作学习区公共记忆（跨对话共享，只读）]\n"
                         f"<workspace_memory>{ws.public_memory}</workspace_memory>"
                         "\n（注意：公共记忆是历史对话的二手摘要，可能滞后或与资料原文不符；"
                         "当它与 knowledge_search 检索到的资料原文冲突时，一律以检索原文为准。）")
            preamble += ws_memory
    tool_schemas = [t.to_schema() for t in tools]
    tool_map = {t.name: t for t in tools}
    seen_calls: set[str] = set()
    all_tool_calls: list[dict[str, Any]] = []

    # GSSC: assemble L1(system) + L2(preamble) + L3(history). The history is
    # compacted in place on session.messages when it exceeds the token budget
    # (LLM structured summary + recent-N kept; full backup in transcript JSONL).
    history = [dict(m) for m in session.messages]
    hist_tokens = history_tokens(history)
    compaction_triggered = False
    if hist_tokens > SOFT_BUDGET_TOKENS and len(history) > KEEP_RECENT_TURNS + 2:
        try:
            history, summary = await compact_history(
                [TUTOR_SYSTEM, preamble] + history, llm,
                quiz_digest=quiz_digest_for_session(session))
            # compact_history returns [system, preamble, summary_msg, ...recent]
            # strip the two head items back off (they're re-added by build_context)
            history = history[2:]
            if summary:
                session.messages = history
                import time as _t
                session.compaction = {
                    "summary": summary,
                    "compacted_upto": len(history),
                    "created_at": _t.time(),
                    "summary_tokens": estimate_tokens(summary),
                }
                compaction_triggered = True
                trace.log("compaction", summary_tokens=estimate_tokens(summary),
                          kept_recent=KEEP_RECENT_TURNS, budget=SOFT_BUDGET_TOKENS,
                          pre_tokens=hist_tokens)
        except Exception as e:
            trace.log("compaction_error", message=str(e))
            # fall back to the rule-based trim — never block the turn
            history = _trim_messages([TUTOR_SYSTEM, preamble] + history)[2:]
    trace.log("context", l3_tokens=history_tokens(history),
              history_msgs=len(history), compaction=compaction_triggered)
    messages = build_context(TUTOR_SYSTEM, preamble, history, user_message,
                             _todo_recap(session, 1))
    # 红线尾注（recency）：build_context 不再代压，由调用方在自身注入完成后
    # 压尾——legacy 路径的尾部就是这里。
    from ..prompts.registry import get as _get_prompt
    messages.append({"role": "system", "content": _get_prompt("redline_tail").text})
    _user_entry = {"role": "user", "content": user_message}
    if attachments:
        _user_entry["attachments"] = attachments
    session.messages.append(_user_entry)

    # --- Deterministic pre-retrieval (R10) ---
    # Weak models often NARRATE "let me search the file" and then answer from
    # the filename + 300-char preview without ever calling knowledge_search —
    # pure hallucination that looks file-grounded. When the question references
    # uploaded materials (or files were just attached) and knowledge exists,
    # run knowledge_search BEFORE the ReAct loop and inject the result as
    # context the answer must be based on. Retrieval is local BM25 — cheap.
    ks_tool = tool_map.get("knowledge_search")
    grounding = decide_material_grounding(session, user_message, attachments)
    pre_results_for_mm: list[dict[str, Any]] = []
    if ks_tool is not None and _merged_files and grounding.required:
        pre_args = {"query": build_query(user_message, grounding, _merged_files),
                    "top_k": 6}
        if grounding.file_ids:
            pre_args["file_ids"] = list(grounding.file_ids)
        yield {"type": "tool_start", "name": "knowledge_search", "args": pre_args, "auto": True}
        try:
            pre_result = await ks_tool.run(**pre_args)
        except Exception as e:
            pre_result = err("knowledge_search", ErrorCode.TOOL_ERROR, str(e))
        trace.log("tool_result", step=0, tool="knowledge_search",
                  status=pre_result.status, reason="auto_preresearch",
                  grounding_reason=grounding.trace_reason,
                  grounding_file_ids=list(grounding.file_ids),
                  error_code=pre_result.error_code or "",
                  error_message=(pre_result.text or "")[:160],
                  gate_drop_reasons=(((pre_result.data or {}).get("telemetry")
                                      or {}).get("drop_reasons")))
        all_tool_calls.append({"name": "knowledge_search", "result": pre_result.to_dict()})
        seen_calls.add(_make_call_key("knowledge_search", pre_args))
        yield {"type": "tool_result", "result": pre_result.to_dict()}
        consume_pending_materials(session, grounding)
        if not pre_result.is_error and pre_result.data.get("count", 0) > 0:
            pre_results_for_mm = list(pre_result.data.get("results") or [])
            from ..core.tool_context import (ToolResultRetention,
                                              project_tool_result)
            grounded_projection = project_tool_result(
                pre_result, ToolResultRetention.CURRENT_FULL).text
            messages.append({"role": "user", "content": (
                "[系统自动预检索] 已从学生上传的资料中检索到以下片段。"
                "你的回答必须严格基于这些资料原文组织内容；如需更多细节可继续调用 "
                "knowledge_search 换关键词检索。禁止脱离资料原文凭文件名或常识编造：\n\n"
                + grounded_projection)})
        else:
            messages.append({"role": "user", "content": (
                "[系统自动预检索] 已自动检索学生上传的资料，但未命中相关片段。"
                "你可以先用更精确的篇目名/课文名/概念名调用 knowledge_search 重试一次"
                "（不得重复同一查询）；重试仍无结果，才如实告诉学生"
                "「在已上传的资料中没有找到相关内容」，并建议补充上传或检查资料，"
                "严禁凭文件名猜测或编造资料内容。")})

    # --- B4 多模态路由：本轮含图（图片附件 + RAG 图表页快照）→ tutor 切
    # MULTIMODAL 通道并开启思考推理；未配置 MULTIMODAL 时降级纯文本不报错。
    mm_images: list[str] = []
    try:
        from ..core.multimodal_context import (attachment_context_images,
                                               evidence_snapshot_images,
                                               get_multimodal_llm)
        mm_images = attachment_context_images(session)
        if pre_results_for_mm:
            mm_images.extend(evidence_snapshot_images(
                pre_results_for_mm,
                getattr(session, "student_id", "") or "",
                used=len(mm_images)))
        mm_llm = get_multimodal_llm() if mm_images else None
        if mm_llm is not None:
            llm = mm_llm
            trace.log("multimodal_routing", images=len(mm_images),
                      model=str(getattr(llm, "model", "")), thinking="on")
    except Exception:
        mm_images = []

    # Live thinking stream (display-only), same gate contract as executor:
    # -1 streams reasoning_content deltas to the browser, 0 keeps the legacy
    # hidden-CoT behavior, >0 caps chars. Never persisted, never TTS'd.
    from .reasoning_live import LiveThinkingGate
    live_gate = LiveThinkingGate(settings.reasoning_live_max_chars)

    if intent == "direct":
        yield {"type": "step", "step": "thinking"}
        collected = ""
        mm_stream_messages = messages
        if mm_images:
            from ..core.multimodal_context import with_context_images
            mm_stream_messages = with_context_images(messages, mm_images)
        try:
            async for ev in llm.stream(mm_stream_messages, tools=None, temperature=0.4):
                if ev["kind"] == "answer":
                    collected += ev["delta"]
                    yield {"type": "answer", "content": ev["delta"], "is_delta": True}
                elif ev["kind"] == "done":
                    trace.llm_call(None, ev.get("usage"))
                elif ev["kind"] == "thinking":
                    live_delta = live_gate.take(ev["delta"])
                    if live_delta:
                        yield {"type": "thinking", "content": live_delta,
                               "is_delta": True, "summary": False}
                elif ev["kind"] == "retry":
                    trace.log("retry", branch="direct", attempt=ev.get("attempt"),
                               reason=ev.get("reason"))
                    yield {"type": "retry", "attempt": ev.get("attempt"),
                           "reason": ev.get("reason")}
        except Exception as e:
            trace.log("error", message=f"LLM error: {e}")
            yield {"type": "error", "message": f"LLM 错误: {e}"}
            return
        session.messages.append({"role": "assistant", "content": collected, "thinking": "",
                                  "toolCalls": []})
        trace.log("finish", branch="direct", answer_len=len(collected))
        merge_quiz_results_from_disk(session)
        save_session(session)
        _persist_turn(session, user_message, collected, [], trace)
        _legacy_record_prompt_memory(session, user_message, [])
        await _maybe_update_workspace_memory(session, user_message, collected, [])
        yield {"type": "done", "thinking": "", "answer": collected, "tool_calls": [],
               "trace_id": trace.run_id, "trace_summary": trace.summary()}
        return

    # --- REACT path: tool-calling loop ---
    pseudo_guard_used = False
    guard_parts: list[str] = []  # 伪标签截断后的各步可见前导（拼进最终答案）
    for step in range(1, MAX_STEPS + 1):
        yield {"type": "step", "step": "thinking"}
        thinking_buf = ""
        answer_buf = ""
        pseudo_guard: PseudoToolGuard | None = None
        tool_calls_raw: list[dict[str, Any]] = []
        finish_reason = "stop"
        try:
            # context already assembled+compacted once above; refresh the
            # tail todo_recap for the current step only (cheap).
            context = messages[:-1] + [{"role": "system", "content": _todo_recap(session, step)}
                                       ] if messages and messages[-1].get("role") == "system" else messages
            if mm_images:
                from ..core.multimodal_context import with_context_images
                context = with_context_images(context, mm_images)
            async for ev in llm.stream(context, tools=tool_schemas):
                if ev["kind"] == "thinking":
                    thinking_buf += ev["delta"]
                    live_delta = live_gate.take(ev["delta"])
                    if live_delta:
                        yield {"type": "thinking", "content": live_delta,
                               "is_delta": True, "summary": False}
                elif ev["kind"] == "answer":
                    answer_buf += ev["delta"]
                    # 伪工具标签护栏：标签形成即停流，改走真实检索（见下方分支）
                    if pseudo_guard is None:
                        pseudo_guard = PseudoToolGuard()
                    safe = pseudo_guard.feed(ev["delta"])
                    if safe:
                        yield {"type": "answer", "content": safe, "is_delta": True}
                elif ev["kind"] == "tool_calls":
                    tool_calls_raw = ev["calls"]
                elif ev["kind"] == "done":
                    finish_reason = ev.get("finish_reason", "stop")
                    trace.llm_call(step, ev.get("usage"))
                elif ev["kind"] == "retry":
                    trace.log("retry", step=step, attempt=ev.get("attempt"),
                               reason=ev.get("reason"))
                    yield {"type": "retry", "attempt": ev.get("attempt"),
                           "reason": ev.get("reason")}
        except Exception as e:
            trace.log("error", step=step, message=f"LLM error: {e}")
            yield {"type": "error", "message": f"LLM 错误: {e}"}
            return

        # 伪标签检出后 answer_buf 仍是含标记的原始累计（feed 只对放行文本
        # 转发）；此后所有落盘/续写一律基于实际放行的 emitted，杜绝标记
        # 进入最终答案与会话历史。
        if pseudo_guard is not None and pseudo_guard.detected:
            answer_buf = pseudo_guard.emitted

        trace.decision(step, thinking_buf,
                        tool_calls_raw[0]["name"] if tool_calls_raw else None,
                        bool(tool_calls_raw), finish_reason)

        # no tool call -> finished
        if not tool_calls_raw:
            if pseudo_guard is not None and not pseudo_guard.detected:
                tail = pseudo_guard.flush()
                if tail:
                    yield {"type": "answer", "content": tail, "is_delta": True}
            if (pseudo_guard is not None and pseudo_guard.detected
                    and not pseudo_guard_used and step < MAX_STEPS):
                ks_live = tool_map.get("knowledge_search")
                if ks_live is not None:
                    # 模型叙述了假 <knowledge_search> 标签：执行真实检索，
                    # 注入结果并继续环路让模型基于真结果续写。
                    pseudo_guard_used = True
                    guard_parts.append(pseudo_guard.emitted)
                    pseudo_query = pseudo_guard.extract_query(user_message)
                    trace.log("pseudo_tool_guard", step=step,
                              tool="knowledge_search", query=pseudo_query)
                    yield {"type": "tool_start", "name": "knowledge_search",
                           "args": {"query": pseudo_query}, "auto": True}
                    try:
                        pseudo_result = await ks_live.run(query=pseudo_query, top_k=6)
                    except Exception as e:
                        pseudo_result = err("knowledge_search",
                                            ErrorCode.TOOL_ERROR, str(e))
                    all_tool_calls.append(
                        {"name": "knowledge_search", "result": pseudo_result.to_dict()})
                    yield {"type": "tool_result", "result": pseudo_result.to_dict()}
                    messages.append({"role": "assistant",
                                     "content": pseudo_guard.emitted or "（先检索教材）"})
                    if not pseudo_result.is_error and pseudo_result.data.get("count", 0) > 0:
                        from ..core.tool_context import (ToolResultRetention,
                                                          project_tool_result)
                        projection = project_tool_result(
                            pseudo_result, ToolResultRetention.CURRENT_FULL).text
                        messages.append({"role": "user", "content": (
                            "[系统自动预检索] 已从学生上传的资料中检索到以下片段。"
                            "你的回答必须严格基于这些资料原文组织内容；"
                            "禁止脱离资料原文凭文件名或常识编造：\n\n" + projection)})
                    else:
                        messages.append({"role": "user", "content": (
                            "[系统自动预检索] 已自动检索学生上传的资料，但未命中相关片段。"
                            "你必须如实告诉学生「在已上传的资料中没有找到相关内容」，"
                            "并建议换关键词或补充上传，严禁编造资料内容。")})
                    continue
            final_answer = "".join(guard_parts) + answer_buf
            session.messages.append({"role": "assistant", "content": final_answer,
                                      "thinking": "",
                                      "toolCalls": [tc for tc in all_tool_calls]})
            trace.log("finish", step=step, branch="react_done",
                      tool_calls=len(all_tool_calls), answer_len=len(final_answer))
            merge_quiz_results_from_disk(session)
            save_session(session)
            _persist_turn(session, user_message, final_answer, all_tool_calls, trace)
            _legacy_record_prompt_memory(session, user_message, all_tool_calls)
            await _maybe_update_workspace_memory(session, user_message, final_answer, all_tool_calls)
            yield {"type": "done", "thinking": "", "answer": final_answer,
                   "tool_calls": _lite_tool_calls(all_tool_calls),
                   "trace_id": trace.run_id, "trace_summary": trace.summary()}
            return

        # execute the first tool call this step (single-dispatch per iteration)
        tc = tool_calls_raw[0]
        tool_name = tc.get("name", "unknown")
        tool_args = tc.get("args", {}) or {}
        yield {"type": "step", "step": "tool_executing", "tool": tool_name}
        yield {"type": "tool_start", "name": tool_name, "args": tool_args}
        if progress_cb:
            progress_cb(f"正在执行 {tool_name}…")

        # duplicate-call guard: reject identical (name, args) within one turn
        call_key = _make_call_key(tool_name, tool_args)
        if call_key in seen_calls:
            result = err(tool_name, ErrorCode.DUPLICATE_CALL,
                         "本回合已用相同参数调用过此工具，请换一种方式或调整参数。")
            trace.log("tool_result", step=step, tool=tool_name,
                      status="error", reason="duplicate_call")
            all_tool_calls.append({"name": tool_name, "result": result.to_dict()})
            yield {"type": "tool_result", "result": result.to_dict()}
            messages.append({"role": "assistant", "content": (
                pseudo_guard.emitted if pseudo_guard and pseudo_guard.detected
                else answer_buf) or f"(调用工具: {tool_name})"})
            messages.append({"role": "user", "content": _build_tool_result_message(result)})
            continue
        seen_calls.add(call_key)

        # execute
        tool = tool_map.get(tool_name)
        if tool is None:
            result = err(tool_name, ErrorCode.NO_TOOL, f"工具 '{tool_name}' 不存在。")
        elif (cb_err := _circuit_check(tool_name)) is not None:
            result = cb_err
        else:
            # R1: validate args against schema before execution
            val_err = validate_tool_args(tool_name, tool_args, tool.parameters)
            if val_err is not None:
                result = val_err
            else:
                try:
                    result = await tool.run(**tool_args)
                except TypeError as e:
                    result = err(tool_name, ErrorCode.BAD_ARGS, f"参数错误: {e}")
                except Exception as e:
                    result = err(tool_name, ErrorCode.TOOL_ERROR, str(e))
        # R3: record result for circuit breaker
        _circuit_record(tool_name, result)
        trace.log("tool_result", step=step, tool=tool_name, status=result.status,
                  error_code=result.error_code)
        result_dict = result.to_dict()
        all_tool_calls.append({"name": tool_name, "result": result_dict})
        yield {"type": "tool_result", "result": result_dict}

        # R2: Reflector — rule-based quality check (always on)
        warning = _reflect_tool_result(tool_name, result)
        if warning:
            trace.log("warning", step=step, tool=tool_name, message=warning, source="reflector")
            yield {"type": "tool_warning", "warning": warning, "tool": tool_name}

        # stash quiz results into session quiz_history
        if tool_name in ("generate_quiz", "fit_quiz") and not result.is_error:
            session.quiz_history.append(result.data)
            record_generated_quiz(session.session_id, result.data)
            # 跨会话「最近习题」库（测评中心列表，每学生上限 100 道）
            record_recent_quiz(session.session_id,
                               getattr(session, "student_id", "") or "",
                               result.data)

        # feed result back to LLM for the next iteration
        messages.append({"role": "assistant", "content": (
            pseudo_guard.emitted if pseudo_guard and pseudo_guard.detected
            else answer_buf) or f"(调用工具: {tool_name})"})
        messages.append({"role": "user", "content": _build_tool_result_message(result)})

    # max steps reached
    fallback = "我已经尽力处理，但暂时没能给出完整回答。可以换个说法再问一次吗？"
    session.messages.append({"role": "assistant", "content": fallback, "thinking": "",
                              "toolCalls": [tc for tc in all_tool_calls]})
    trace.log("finish", branch="max_steps", tool_calls=len(all_tool_calls))
    merge_quiz_results_from_disk(session)
    save_session(session)
    _persist_turn(session, user_message, fallback, all_tool_calls, trace)
    _legacy_record_prompt_memory(session, user_message, all_tool_calls)
    await _maybe_update_workspace_memory(session, user_message, fallback, all_tool_calls)
    yield {"type": "done", "thinking": "", "answer": fallback,
           "tool_calls": _lite_tool_calls(all_tool_calls),
           "trace_id": trace.run_id, "trace_summary": trace.summary()}


def _lite_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip large payloads from quiz results for the done SSE event.
    The frontend rebuilds toolCalls from tool_start/tool_result events;
    session.messages keeps the full payload for history reload."""
    out = []
    for c in calls:
        r = c.get("result")
        if isinstance(r, dict):
            r = {**r}
            # keep quiz data but trim very long explanation lists
            data = r.get("data", {})
            if isinstance(data, dict) and "questions" in data:
                r["data"] = {**data, "questions": data["questions"][:2]}
        out.append({"name": c.get("name"), "result": r})
    return out


# ---------------------------------------------------------------------------
# V2 dispatch: SUPERVISOR_MODE=v2 (default) -> Supervisor orchestrator; =legacy
# -> this V1 chat_turn. V2 failures are surfaced by default; operators may
# explicitly set SUPERVISOR_LEGACY_FALLBACK=1 as an emergency compatibility
# switch to fall back to V1 without breaking the SSE stream.
# P2-A（plan.md §29）：fallback 有显式开关（SUPERVISOR_LEGACY_FALLBACK，
# 默认 0）与结构化观测（异常分类/stage/会话/任务类型）。默认暴露 V2
# 回归；只有显式开启开关时才回落 V1。
# ---------------------------------------------------------------------------

def _classify_supervisor_error(exc: BaseException) -> str:
    """Coarse V2 failure category from the traceback path（plan.md §29）。

    只看代码路径（traceback 文本），不落入用户消息/教材正文/JWT——
    分类输入是异常类型与模块名，不是对话内容。
    """
    import traceback
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    path = "\n".join(tb).lower()
    if any(k in path for k in ("agents/planner", "make_plan", "taskplan")):
        return "planner_error"
    if any(k in path for k in ("agents/context", "compact", "preresearch",
                               "material_signals")):
        return "context_error"
    if any(k in path for k in ("agents/executor", "tool_base", "tools/")):
        return "executor_internal_error"
    if any(k in path for k in ("core/atomic", "core/session", "core/store",
                               "session_store", "_save", "persist")):
        return "persistence_error"
    return "unknown"


def _legacy_fallback_enabled() -> bool:
    import os
    return os.getenv("SUPERVISOR_LEGACY_FALLBACK", "0").strip().lower() \
        not in ("0", "false", "off")


def _after_turn_dialogue_receipt(session: TutorSession,
                                  student_id: str) -> None:
    """G3 统一 turn hook（plan §13.1/§7.2）：回合结束后对已可靠落盘的学生
    消息受理 dialogue 来源。用磁盘会话核对（保存与受理之间的故障窗口不
    受理未保存内容）；任何失败不影响对话流。"""
    try:
        from ..core.session import load_session
        sid = student_id or getattr(session, "student_id", "")             or "student_default"
        fresh = load_session(getattr(session, "session_id", ""))
        if fresh is None:
            return
        from ..agents.student_model.evaluation.dialogue import (
            after_turn_hook)
        after_turn_hook(sid, fresh)
    except Exception:
        pass


async def run_turn(
    user_message: str,
    session: TutorSession,
    tools: list[Tool],
    llm: AsyncLLMClient | None = None,
    progress_cb: Callable[[str], Any] | None = None,
    lang: str = "zh",
    output_language: str | None = None,
    attachments: list[dict] | None = None,
    student_id: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """Entry point chosen by chat.py. Dispatches to V1 chat_turn or V2
    supervisor.run based on SUPERVISOR_MODE (default v2).

    G3：无论 supervisor/legacy/回退/错误路径，本生成器收尾时统一执行
    dialogue 来源受理 hook（§13.1 单一受理点）。"""
    try:
        async for ev in _run_turn_dispatch(
                user_message, session, tools, llm, progress_cb, lang,
                output_language, attachments, student_id=student_id):
            yield ev
    finally:
        _after_turn_dialogue_receipt(session, student_id)


async def _run_turn_dispatch(
    user_message: str,
    session: TutorSession,
    tools: list[Tool],
    llm: AsyncLLMClient | None = None,
    progress_cb: Callable[[str], Any] | None = None,
    lang: str = "zh",
    output_language: str | None = None,
    attachments: list[dict] | None = None,
    student_id: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """Entry point chosen by chat.py. Dispatches to V1 chat_turn or V2
    supervisor.run based on SUPERVISOR_MODE (default v2).

    V2 failure semantics（plan.md §29）：
    - SUPERVISOR_LEGACY_FALLBACK 未设置或 =0：yield error 事件并结束本轮，
      V2 回归不会被 legacy 成功掩盖；
    - =1：结构化 trace 后显式回落 V1，仅作为紧急兼容开关。"""
    import os
    mode = os.getenv("SUPERVISOR_MODE", "v2").lower()
    if mode in ("v2", "supervisor"):
        try:
            from .supervisor import run as supervisor_run
            async for ev in supervisor_run(user_message, session, tools, llm,
                                            progress_cb, lang, output_language, attachments,
                                            student_id=student_id):
                yield ev
            return
        except Exception as e:  # never break the stream; observe, then decide
            fallback_enabled = _legacy_fallback_enabled()
            try:
                # 结构化观测：异常类型/分类/会话/任务类型/开关状态。
                # 只记异常与代码路径，不记 raw 用户消息/教材正文/JWT。
                Trace().log(
                    "supervisor_fallback_to_legacy",
                    exception_type=type(e).__name__,
                    category=_classify_supervisor_error(e),
                    stage="v2_run",
                    session_id=getattr(session, "session_id", ""),
                    task_kind=mode,
                    fallback_enabled=fallback_enabled,
                    message=str(e)[:200],
                )
            except Exception:
                pass
            if not fallback_enabled:
                yield {"type": "error",
                       "message": "对话编排服务暂时不可用，请稍后重试。"}
                return
    async for ev in chat_turn(user_message, session, tools, llm,
                              progress_cb=progress_cb, lang=lang,
                              output_language=output_language, attachments=attachments):
        yield ev
