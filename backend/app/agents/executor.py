"""Supervisor executor: the ReAct tool-calling loop, plan-driven.

This is the V1 chat_agent ReAct loop lifted out as a reusable module, with two
plan-aware behaviors added:
  - shadow/off keeps the legacy-compatible union of plan tools. gated mode
    exposes only the current PlanStep's Skill tools and advances after a valid
    tool result; an empty CHITCHAT plan remains direct answer.
  - The plan's steps are rendered as a soft instruction appended to context
    (like V1's Todo Recap) so the LLM follows the intended workflow within the
    ReAct loop. The plan does NOT hard-sequence tool calls.

All V1 guardrails are preserved verbatim: duplicate-call guard, circuit
breaker, arg validation, reflector, result truncation+persist, error-recovery
hints, max-step cap, lit tool_calls, transcript append, session save.
"""
from __future__ import annotations

import inspect
import json
import re
import time
from typing import Any, AsyncGenerator, Callable

from ..core.config import settings
from ..core.llm_async import AsyncLLMClient
from ..core.quiz_attempts import record_generated_quiz
from ..core.quiz_recent import record_recent_quiz
from ..core.session import TutorSession
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, ToolResult, err
from ..core.trace import Trace, trace_dir_path
from ..core.validator import validate_tool_args
from ..prompts.tutor import error_recovery_hint
from .preresearch import build_query, consume_pending_materials, decide_material_grounding
from .pseudo_tool_guard import PseudoToolGuard
from ..core.multimodal_context import with_context_images
from .router import route, route_full_plan
from .state import TaskPlan

MAX_STEPS = settings.agent_max_steps  # V1 hard cap (default 6)
_TOOL_MSG_MAX_CHARS = 2000

# Circuit breaker (V1 R3) -- module-level state, keyed per tool. Reset per
# turn is NOT done because breakers are meant to persist across a process (a
# tool that keeps failing stays tripped). Half-open recovery: after the
# cooldown, one probe call is allowed through; success resets the breaker,
# failure re-trips it with a fresh cooldown.
_CIRCUIT_THRESHOLD = 3
_CIRCUIT_COOLDOWN_S = 60.0  # 熔断冷却期，过后放行一次半开试探
_circuit_failures: dict[str, int] = {}
_circuit_opened_at: dict[str, float] = {}  # tool -> 熔断时刻（在字典里即熔断中）
_circuit_half_open: set[str] = set()       # 冷却结束、已放行一次试探的工具


# Reflector (V1 R2) -- rule-based quality checks on tool results.
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


def _circuit_check(tool_name: str) -> ToolResult | None:
    """Return a CIRCUIT_OPEN error if the tool is tripped, else None.

    冷却期内一律拒绝；冷却结束后放行一次半开试探（其余并发调用仍拒绝，
    避免试探风暴）。试探结果由 _circuit_record 结算。"""
    opened_at = _circuit_opened_at.get(tool_name)
    if opened_at is None:
        return None
    if time.time() - opened_at < _CIRCUIT_COOLDOWN_S or tool_name in _circuit_half_open:
        return err(tool_name, ErrorCode.CIRCUIT_OPEN,
                   f"工具 '{tool_name}' 因连续失败已暂时禁用。请稍后再试或换一种方式。")
    _circuit_half_open.add(tool_name)  # 放行一次半开试探
    return None


def _circuit_record(tool_name: str, result: ToolResult) -> None:
    """Update failure counter; trip the circuit on threshold.

    半开试探成功则完全复位，失败则以新的冷却期重新熔断。熔断拒绝本身
    （CIRCUIT_OPEN）不计入失败次数。"""
    if result.is_error:
        if result.error_code == ErrorCode.CIRCUIT_OPEN:
            return
        _circuit_failures[tool_name] = _circuit_failures.get(tool_name, 0) + 1
        if (tool_name in _circuit_half_open
                or _circuit_failures[tool_name] >= _CIRCUIT_THRESHOLD):
            _circuit_opened_at[tool_name] = time.time()
            _circuit_half_open.discard(tool_name)
    else:
        _circuit_failures[tool_name] = 0
        _circuit_opened_at.pop(tool_name, None)
        _circuit_half_open.discard(tool_name)


def _reflect_tool_result(tool_name: str, result: ToolResult) -> str | None:
    if result.is_error:
        return None
    for check, message in _REFLECT_RULES.get(tool_name, []):
        try:
            if check(result):
                return message
        except Exception:
            continue
    return None


def _make_call_key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def _build_tool_result_message(result: ToolResult) -> str:
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
            parts.append(f"摘要: {result.text}")
    if not parts[1:] and result.data:
        parts.append(f"数据: {json.dumps(result.data, ensure_ascii=False)[:1500]}")
    return "\n".join(parts)


def _project_tool_message(result: ToolResult) -> tuple[str, dict[str, Any]]:
    """Return model-facing text plus shadow accounting without changing SSE."""
    from ..core.tool_context import (ToolResultRetention, project_tool_result)
    projection = project_tool_result(result, ToolResultRetention.CURRENT_FULL)
    mode = settings.tool_context_projection_mode
    # Error recovery contracts are intentionally not projected: keep the
    # machine error code and the existing actionable recovery hint verbatim.
    text = (_build_tool_result_message(result) if result.is_error
            else projection.text if mode == "on"
            else _build_tool_result_message(result))
    return text, projection.to_dict()


def _stream_with_supported_options(
    llm: AsyncLLMClient,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    disable_thinking: bool = False,
    reasoning_effort: str = "",
    reasoning_budget_tokens: int = 0,
):
    """Call pluggable LLM clients without breaking older adapters/test doubles.

    The production client accepts the stage-aware budget controls.  A few
    provider adapters in downstream deployments still implement the original
    ``stream(messages, tools, temperature)`` surface, so filter optional
    keywords by signature rather than turning a harmless capability gap into a
    failed student turn.
    """
    options: dict[str, Any] = {"tools": tools}
    if temperature is not None:
        options["temperature"] = temperature
    if max_tokens is not None:
        options["max_tokens"] = max_tokens
    if disable_thinking:
        options["disable_thinking"] = True
    if reasoning_effort:
        options["reasoning_effort"] = reasoning_effort
    if reasoning_budget_tokens:
        options["reasoning_budget_tokens"] = reasoning_budget_tokens
    try:
        signature = inspect.signature(llm.stream)
        accepts_any = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in signature.parameters.values()
        )
        if not accepts_any:
            options = {k: v for k, v in options.items()
                       if k in signature.parameters}
    except (TypeError, ValueError):
        # Some dynamically generated adapters do not expose a Python signature.
        # The stable baseline options remain safe for the original protocol.
        options = {k: v for k, v in options.items()
                   if k in {"tools", "temperature"}}
    return llm.stream(messages, **options)


def _append_tool_exchange(messages: list[dict[str, Any]], *, mode: str,
                          assistant_content: str, call_id: str,
                          tool_name: str, args: dict[str, Any],
                          result_text: str) -> None:
    if mode == "native":
        from ..core.message_protocol import build_openai_tool_messages
        messages.extend(build_openai_tool_messages(
            assistant_content, call_id=call_id, tool_name=tool_name,
            args=args, result_text=result_text))
    else:
        messages.append({"role": "assistant",
                         "content": assistant_content or f"(调用工具: {tool_name})"})
        messages.append({"role": "user", "content": result_text})


def _lite_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip large quiz payloads from the done SSE event (V1)."""
    out = []
    for c in calls:
        r = c.get("result")
        if isinstance(r, dict):
            r = {**r}
            data = r.get("data", {})
            if isinstance(data, dict) and "questions" in data:
                r["data"] = {**data, "questions": data["questions"][:2]}
        out.append({"name": c.get("name"), "result": r})
    return out


def _plan_recap(plan: TaskPlan | None) -> str:
    """Render the plan as a soft instruction pinned at the context tail,
    like V1's Todo Recap. Empty/no plan -> no instruction (plain ReAct)."""
    if plan is None or plan.is_empty:
        return ""
    header = ("[本次教学计划（按 Skill Gate 顺序执行）]"
              if settings.skill_runtime_mode == "gated"
              else "[本次教学计划（建议执行顺序，非强制）]")
    lines = [header]
    for i, s in enumerate(plan.steps, 1):
        tag = {"knowledge": "检索资料", "teaching": "讲解",
               "assessment": "出题练习", "memory": "回顾历史"}.get(s.agent_role, s.agent_role)
        mark = "" if not s.optional else "（可选）"
        skills = f" [Skill: {', '.join(s.skill_ids)}]" if s.skill_ids else ""
        lines.append(f"{i}. {tag}：{s.task}{mark}{skills}")
    return "\n".join(lines)


def _tools_for_execution(plan: TaskPlan | None, all_tools: list[Tool],
                         step_index: int, *, gated: bool) -> list[Tool]:
    if not gated:
        return route_full_plan(plan, all_tools) if plan is not None else list(all_tools)
    if plan is None:
        return list(all_tools)
    if plan.is_empty or not 0 <= step_index < len(plan.steps):
        return []
    return route(plan, all_tools, step_index=step_index)


def _next_executable_step(plan: TaskPlan | None, all_tools: list[Tool],
                          start_index: int) -> tuple[int, list[int]]:
    """Skip advisory-only plan steps and return the next tool-bearing step.

    Teaching Skills can be executable prompt behavior without owning a tool.
    In gated mode they must not block a later assessment/retrieval Skill from
    becoming visible. The advisory instruction remains in the plan recap, so
    the model still performs it before invoking the next visible tool.
    """
    if plan is None:
        return start_index, []
    index = start_index
    skipped: list[int] = []
    while index < len(plan.steps) and not route(plan, all_tools, step_index=index):
        skipped.append(index)
        index += 1
    return index, skipped


def _enforced_plan_call(plan: TaskPlan | None, visible_tool_map: dict[str, Tool],
                        completed_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return an authorized deterministic call omitted by the model.

    Only steps explicitly marked ``auto_invoke`` with validated structured
    arguments qualify. This is deliberately narrower than "call every planned
    tool": ambiguous practice plans (generate vs fit) remain model-decided.
    """
    if plan is None:
        return None
    called = {str(item.get("name", "")) for item in completed_calls}
    for step_index, step in enumerate(plan.steps):
        if not step.auto_invoke or step.optional:
            continue
        for tool_name, args in step.tool_args.items():
            if tool_name in called or tool_name not in visible_tool_map:
                continue
            return {
                "id": f"plan_auto_{step_index}_{tool_name}",
                "name": tool_name,
                "args": dict(args),
                "_auto": True,
                "_plan_step_index": step_index,
            }
    return None


def _is_auto_fulfillment_tool(plan: TaskPlan | None, tool_name: str) -> bool:
    return bool(plan and any(step.auto_invoke and tool_name in step.tool_args
                             for step in plan.steps))


def _continuation_suffix(prefix: str, candidate: str) -> str:
    """Return only genuinely new text from an answer-recovery completion.

    Reasoning providers sometimes restart a truncated answer instead of obeying
    the continuation instruction. Exact-prefix/character overlap is not
    enough for Markdown answers: a restarted completion may prepend an apology
    and repeat a whole section heading. Prefer the first *new* heading after
    the already-visible answer, while retaining ordinary prose continuations.
    """
    if not prefix or not candidate:
        return candidate
    if candidate.startswith(prefix):
        return candidate[len(prefix):]
    limit = min(len(prefix), len(candidate), 1200)
    for overlap in range(limit, 0, -1):
        if prefix[-overlap:] == candidate[:overlap]:
            return candidate[overlap:]

    normalize = lambda text: re.sub(r"\s+", " ", text).strip()
    prefix_norm = normalize(prefix)
    headings = list(re.finditer(
        r"(?m)^(?:#{1,6}\s+[^\n]+|[一二三四五六七八九十百]+、[^\n]+)$",
        candidate))
    for match in headings:
        heading = normalize(match.group(0))
        if heading and heading not in prefix_norm:
            return candidate[match.start():]
    if headings and any(token in candidate[:headings[0].start()]
                        for token in ("抱歉", "上一轮", "刚才", "重新")):
        return ""
    return candidate


def _step_progress_note(plan: TaskPlan, completed_index: int, next_index: int,
                        *, skipped_optional: bool = False) -> str:
    completed = plan.steps[completed_index]
    action = ("可选步骤未得到有效结果，已安全跳过" if skipped_optional
              else "已完成")
    if next_index >= len(plan.steps):
        return (f"[Skill Gate] {action}步骤 {completed_index + 1}：{completed.task}。"
                "计划工具步骤已结束，请综合已有结果给出最终教学回复，不再调用其他工具。")
    upcoming = plan.steps[next_index]
    return (f"[Skill Gate] {action}步骤 {completed_index + 1}：{completed.task}。"
            f"现在只执行步骤 {next_index + 1}：{upcoming.task}。")


async def execute(
    messages: list[dict[str, Any]],
    session: TutorSession,
    tools: list[Tool],
    plan: TaskPlan | None,
    llm: AsyncLLMClient,
    trace: Trace,
    progress_cb: Callable[[str], Any] | None = None,
    *,
    search_queries: list[str] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the turn's ReAct loop (or direct answer), yielding SSE events.

    `messages` is the already-assembled context (L1+L2+L3+current user), ready
    to feed the LLM. `plan` narrows the visible tools and injects a recap.
    An empty plan -> direct, no-tool answer (chitchat path).

    `search_queries` carries the LLM-refined retrieval terms produced by task
    understanding (R11). When the deterministic pre-retrieval below fires, the
    first term becomes the primary query instead of the raw spoken sentence;
    empty/None keeps the raw-message fallback (build_query).
    """
    gated = settings.skill_runtime_mode == "gated" and plan is not None
    plan_step_index = 0
    advisory_steps: list[int] = []
    if gated:
        plan_step_index, advisory_steps = _next_executable_step(plan, tools, 0)
    visible_tools = _tools_for_execution(
        plan, tools, plan_step_index, gated=gated)
    tool_schemas = [t.to_schema() for t in visible_tools]
    tool_map = {t.name: t for t in visible_tools}
    from .skill_runtime.runtime import SkillRuntime
    skill_runtime = SkillRuntime(tools)
    if advisory_steps:
        trace.log("skill_plan_advisory", step_indexes=advisory_steps,
                  steps=[plan.steps[i].to_dict() for i in advisory_steps])
    if gated and plan and plan_step_index < len(plan.steps):
        trace.log("skill_plan_step", step_index=plan_step_index,
                  step=plan.steps[plan_step_index].to_dict(),
                  visible_tools=[t.name for t in visible_tools])

    # Inject the plan as a soft recap at the context tail (replaces the V1
    # todo recap slot). For an empty/chitchat plan we still append nothing.
    recap = _plan_recap(plan)
    if recap:
        messages = messages + [{"role": "system", "content": recap}]
    # 红线尾注压在真正的消息尾部（plan recap 之后）：build_context 名义上的
    # 尾部之后还有 supervisor 的 6 层软指令与本 recap，recency 设计意图曾被
    # 稀释——这里才是模型可见的最后一条。
    from ..prompts.registry import get as _get_prompt
    messages = messages + [{"role": "system",
                            "content": _get_prompt("redline_tail").text}]

    from ..core.context_budget import build_budget_snapshot
    from ..core.llm_runtime import current_capabilities, resolve_reasoning_policy
    capabilities = current_capabilities()
    complex_task = bool(plan and len(plan.steps) >= 3)
    initial_policy = resolve_reasoning_policy(
        "executor_tool" if tool_schemas else "executor_direct",
        has_tools=bool(tool_schemas), complex_task=complex_task,
        capabilities=capabilities)
    trace.log("provider_capabilities", **capabilities.to_dict())
    trace.log("reasoning_policy", **initial_policy.to_dict())
    if initial_policy.fallback_reason:
        trace.log("provider_capability_fallback",
                  requested=initial_policy.requested_mode.value,
                  applied=initial_policy.applied_mode,
                  reason=initial_policy.fallback_reason)
    initial_output_budget = initial_policy.max_output_tokens
    budget = build_budget_snapshot(
        messages, tool_schemas, stage="executor_tool" if tool_schemas else "executor_direct",
        max_output_tokens=initial_output_budget)
    trace.log("context_budget", **budget.to_dict())

    seen_calls: set[str] = set()
    all_tool_calls: list[dict[str, Any]] = []
    visible_answer_parts: list[str] = []
    # Raw reasoning accumulated across ReAct steps.  Each step resets its own
    # thinking_buf, so without this the done event only carries the FINAL
    # step's reasoning and the supervisor's real_summary digest loses the
    # main explanation step's material.
    thinking_parts: list[str] = []
    # Live thinking stream (display-only): REASONING_LIVE_MAX_CHARS=-1 streams
    # provider reasoning_content deltas to the browser as thinking events
    # (summary=False) so the student watches the deep-thinking process; 0
    # restores the hidden-CoT behavior; >0 caps total streamed chars. The
    # internal accumulation above (telemetry/real_summary material, done
    # tail-cap 6000) is unchanged, and raw CoT is still never persisted.
    from .reasoning_live import LiveThinkingGate
    live_gate = LiveThinkingGate(settings.reasoning_live_max_chars)
    empty_answer_retries = 0
    active_tool_message_mode = (settings.tool_message_mode
                                if settings.tool_message_mode in {"legacy", "shadow", "native"}
                                else "legacy")
    native_fallback_used = False
    # The planner has already performed the expensive Skill decision. When a
    # tool is visible, the executor should act rather than spend the whole
    # completion budget re-planning in reasoning_content. This is especially
    # important for reasoning models whose answer channel can otherwise be
    # truncated before the first tool call.
    force_disable_thinking = initial_policy.disable_thinking

    # --- Deterministic pre-retrieval (R10, mirrors chat_agent) ---
    # Weak models often NARRATE "let me search the file" and then answer from
    # the filename + 300-char preview without ever calling knowledge_search —
    # fluent hallucination that looks file-grounded. When the question
    # references uploaded materials (or files were just attached) and knowledge
    # exists, run knowledge_search BEFORE the loop and inject the result as
    # must-use context. Retrieval is local BM25 — cheap. Runs before the plan
    # branch so even chitchat/direct answers get grounded.
    # NOTE: look up the tool in the FULL tools list, not the plan-narrowed
    # tool_map — a plan without a knowledge step hides knowledge_search from
    # the router's visible set, which is exactly the case that needs this fix.
    ks_tool = next((t for t in tools if t.name == "knowledge_search"), None)
    user_msg = next((str(m.get("content", "")) for m in reversed(messages)
                     if m.get("role") == "user"), "")
    # build_context 给当前用户消息包了 <user_input> 定界标记；检索查询用原文。
    from ..core.context import unwrap_user_input
    user_msg = unwrap_user_input(user_msg)
    attachments = (session.messages[-1].get("attachments")
                   if session.messages and session.messages[-1].get("role") == "user"
                   else None)
    grounding = decide_material_grounding(session, user_msg, attachments)
    pre_results_for_mm: list[dict[str, Any]] = []
    if ks_tool is not None and grounding.required:
        try:
            from ..core.workspace import merged_knowledge_files
            merged_files, _names = merged_knowledge_files(session)
        except Exception:
            merged_files = []
        if merged_files:
            # 预检索查询精炼：task understanding 阶段 LLM 预分析的精炼检索词
            # （概念/篇目/课文名）优先，口语整句只作兜底——原句直查 BM25 的
            # 词面稀释是预检索失配的主因。
            focus = [str(q).strip() for q in (search_queries or [])
                     if str(q).strip()][:3]
            pre_args = {"query": (focus[0] if focus
                                  else build_query(user_msg, grounding, merged_files)),
                        "top_k": 6}
            if len(focus) > 1:
                pre_args["focus_queries"] = focus
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
                      query_source=("llm_focus" if focus else "raw_message"),
                      focus_variants=len(focus),
                      error_code=pre_result.error_code or "",
                      error_message=(pre_result.text or "")[:160],
                      gate_drop_reasons=(((pre_result.data or {}).get("telemetry")
                                           or {}).get("drop_reasons")))
            all_tool_calls.append({"name": "knowledge_search", "result": pre_result.to_dict()})
            seen_calls.add(_make_call_key("knowledge_search", pre_args))
            yield {"type": "tool_result", "result": pre_result.to_dict()}
            consume_pending_materials(session, grounding)
            pre_post = skill_runtime.validate_result("knowledge_search", pre_result)
            if pre_post is not None:
                trace.log("skill_postconditions", step=0, **pre_post.to_dict())
            if (gated and plan and plan_step_index < len(plan.steps)
                    and "knowledge_search" in {
                        t.name for t in route(plan, tools, step_index=plan_step_index)
                    } and ((not pre_result.is_error
                            and (pre_post is None or pre_post.valid))
                           or plan.steps[plan_step_index].optional)):
                completed_index = plan_step_index
                skipped_optional = bool(
                    plan.steps[plan_step_index].optional
                    and (pre_result.is_error or (pre_post is not None and not pre_post.valid)))
                plan_step_index += 1
                visible_tools = _tools_for_execution(
                    plan, tools, plan_step_index, gated=True)
                tool_schemas = [t.to_schema() for t in visible_tools]
                tool_map = {t.name: t for t in visible_tools}
                messages = messages + [{"role": "system", "content":
                    _step_progress_note(
                        plan, completed_index, plan_step_index,
                        skipped_optional=skipped_optional)}]
                trace.log("skill_plan_advance", completed_index=completed_index,
                          next_index=plan_step_index, trigger="auto_preresearch",
                          skipped_optional=skipped_optional,
                          visible_tools=[t.name for t in visible_tools])
            # Retrieval fired -> the model may legitimately need follow-up
            # searches; make knowledge_search visible even when the plan
            # didn't reference it.
            if not gated and ks_tool.name not in tool_map:
                visible_tools.append(ks_tool)
                tool_map[ks_tool.name] = ks_tool
                tool_schemas.append(ks_tool.to_schema())
            if not pre_result.is_error and pre_result.data.get("count", 0) > 0:
                pre_results_for_mm = list(pre_result.data.get("results") or [])
                # pre_result.text 内的资料原文已由 knowledge_search 逐段包裹
                # <material_excerpt> 定界标记（注入防护在工具输出源头完成）。
                from ..core.tool_context import (ToolResultRetention,
                                                  project_tool_result)
                grounded_projection = project_tool_result(
                    pre_result, ToolResultRetention.CURRENT_FULL).text
                messages = messages + [{"role": "user", "content": (
                    "[系统自动预检索] 已从学生上传的资料中检索到以下片段。"
                    "你的回答必须严格基于这些资料原文组织内容；如需更多细节可继续调用 "
                    "knowledge_search 换关键词检索。禁止脱离资料原文凭文件名或常识编造：\n\n"
                    + grounded_projection)}]
            else:
                messages = messages + [{"role": "user", "content": (
                    "[系统自动预检索] 已自动检索学生上传的资料，但未命中相关片段。"
                    "你可以先用更精确的篇目名/课文名/概念名调用 knowledge_search 重试一次"
                    "（不得重复同一查询）；重试仍无结果，才如实告诉学生"
                    "「在已上传的资料中没有找到相关内容」，并建议补充上传或检查资料，"
                    "严禁凭文件名猜测或编造资料内容。")}]

    # Once deterministic grounding has supplied the evidence, spending the
    # remaining tool-call budget on hidden provider reasoning is counterproductive:
    # it was the source of long "thinking" stalls and empty/partial answers.
    # That clamp stays in force only while live thinking streaming is OFF —
    # with REASONING_LIVE_MAX_CHARS>=0-off restored there is nothing to show
    # for the spent budget. When live streaming is ON the grounded answer runs
    # with thinking visible: budget_forces_direct and
    # incomplete_answer_recovery remain the guards against stalls.
    if grounding.required and not live_gate.enabled:
        force_disable_thinking = True
        trace.log("reasoning_policy_override", reason="mandatory_material_grounding",
                  disable_thinking=True)
    trace.log("reasoning_live_gate", mode=live_gate.remaining_hint,
              grounding_required=grounding.required)

    # --- B4 多模态路由：本轮含图（图片附件 + 预检索图表页快照）→ tutor 切
    # MULTIMODAL 通道；多模态轮开启思考推理（覆盖 grounding 的关思考覆盖），
    # 未配置 MULTIMODAL 时降级纯文本不报错。---
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
    except Exception:
        mm_images = []
        mm_llm = None
    if mm_llm is not None:
        llm = mm_llm
        if mm_images and grounding.required:
            # 看图讲题需要推理：多模态轮不做 grounding 关思考覆盖（预算护栏仍生效）
            force_disable_thinking = False
        trace.log("multimodal_routing", images=len(mm_images),
                  model=str(getattr(llm, "model", "")),
                  thinking=("on" if not force_disable_thinking else "budget_guard"))

    # --- DIRECT path: empty plan (chitchat) or no tools visible & no plan ---
    if plan is not None and plan.is_empty:
        yield {"type": "step", "step": "thinking"}
        collected = ""
        direct_messages = list(messages)
        if mm_images:
            direct_messages = with_context_images(direct_messages, mm_images)
        for direct_attempt in range(2):
            thinking_attempt = ""
            answer_attempt = ""
            finish_reason = "stop"
            direct_policy = resolve_reasoning_policy(
                "executor_direct", has_tools=False, complex_task=False,
                capabilities=capabilities)
            direct_budget = build_budget_snapshot(
                direct_messages, None, stage="executor_direct",
                max_output_tokens=direct_policy.max_output_tokens)
            trace.log("context_budget", step=0, recovery=bool(direct_attempt),
                      **direct_budget.to_dict())
            budget_forces_direct = (
                direct_budget.output_budget_reduced
                and direct_budget.max_output_tokens < direct_policy.answer_token_reserve
            )
            if budget_forces_direct:
                trace.log("context_budget_guard", step=0,
                          action="disable_thinking_for_answer_reserve",
                          available_output_tokens=direct_budget.max_output_tokens)
            trace.log("reasoning_policy_call", step=0,
                      recovery=bool(direct_attempt), **direct_policy.to_dict())
            try:
                async for ev in _stream_with_supported_options(
                        llm, direct_messages, tools=None, temperature=0.4,
                        max_tokens=direct_budget.max_output_tokens,
                        disable_thinking=(bool(direct_attempt)
                                          or direct_policy.disable_thinking
                                          or budget_forces_direct),
                        reasoning_effort=("" if direct_attempt or budget_forces_direct
                                          else direct_policy.reasoning_effort),
                        reasoning_budget_tokens=(
                            0 if direct_attempt or budget_forces_direct else
                            direct_policy.reasoning_budget_tokens)):
                    if ev["kind"] == "answer":
                        answer_attempt += ev["delta"]
                        if direct_attempt == 0:
                            collected += ev["delta"]
                            yield {"type": "answer", "content": ev["delta"], "is_delta": True}
                    elif ev["kind"] == "done":
                        finish_reason = ev.get("finish_reason", "stop")
                        trace.llm_call(None, ev.get("usage"))
                    elif ev["kind"] == "thinking":
                        thinking_attempt += ev["delta"]
                        live_delta = live_gate.take(ev["delta"])
                        if live_delta:
                            yield {"type": "thinking", "content": live_delta,
                                   "is_delta": True, "summary": False}
                    elif ev["kind"] == "capability_fallback":
                        trace.log("provider_capability_fallback", step=0,
                                  reason=ev.get("reason", "provider_rejected_optional_controls"))
                    elif ev["kind"] == "retry":
                        trace.log("retry", branch="direct", attempt=ev.get("attempt"),
                                  reason=ev.get("reason"))
                        yield {"type": "retry", "attempt": ev.get("attempt"),
                               "reason": ev.get("reason")}
            except Exception as e:
                trace.log("error", message=f"LLM error: {e}")
                yield {"type": "error", "message": f"LLM 错误: {e}"}
                return

            if direct_attempt and answer_attempt:
                suffix = _continuation_suffix(collected, answer_attempt)
                if suffix:
                    collected += suffix
                    yield {"type": "answer", "content": suffix, "is_delta": True}
                answer_attempt = suffix
            from ..core.context import estimate_tokens
            trace.log("llm_channels", step=0, recovery=bool(direct_attempt),
                      finish_reason=finish_reason,
                      reasoning_chars=len(thinking_attempt),
                      answer_chars=len(answer_attempt),
                      reasoning_estimated_tokens=estimate_tokens(thinking_attempt),
                      answer_estimated_tokens=estimate_tokens(answer_attempt))
            incomplete = finish_reason == "length" or not answer_attempt.strip()
            if direct_attempt == 0 and incomplete:
                trace.log("incomplete_answer_recovery", step=0,
                          finish_reason=finish_reason,
                          thought_len=len(thinking_attempt), retry=1,
                          branch="direct")
                if answer_attempt:
                    direct_messages.append({"role": "assistant",
                                            "content": answer_attempt})
                direct_messages.append({"role": "system", "content": (
                    "[输出恢复] 上一次生成未完成。停止继续展开内部分析；"
                    "若已有可见文字，从末尾继续且不要重复，否则直接给出完整答案。"
                    "紧扣学生最新一条消息的诉求作答；不得复述此前轮次已讲过的内容，"
                    "必须给出新的增量内容。"
                )})
                yield {"type": "retry", "attempt": 1,
                       "reason": "incomplete_answer_after_reasoning"}
                continue
            break

        if not collected.strip():
            collected = "刚才的回答生成被意外截断了，请重试一次，我会直接给出完整回答。"
            trace.log("empty_answer_fallback", step=0,
                      finish_reason=finish_reason, branch="direct")
            yield {"type": "answer", "content": collected, "is_delta": True}
        trace.log("finish", branch="direct", answer_len=len(collected))
        yield {"type": "done", "thinking": "", "answer": collected, "tool_calls": [],
               "trace_id": trace.run_id, "trace_summary": trace.summary()}
        return  # direct path done -- must not fall through into the ReAct loop

    # --- REACT path ---
    pseudo_guard_used = False
    for step in range(1, MAX_STEPS + 1):
        yield {"type": "step", "step": "thinking"}
        thinking_buf = ""
        answer_buf = ""
        pseudo_guard: PseudoToolGuard | None = None
        tool_calls_raw: list[dict[str, Any]] = []
        finish_reason = "stop"
        try:
            call_policy = resolve_reasoning_policy(
                "executor_tool" if tool_schemas else "executor_direct",
                has_tools=bool(tool_schemas), complex_task=complex_task,
                capabilities=capabilities)
            call_budget = build_budget_snapshot(
                messages, tool_schemas,
                stage="executor_tool" if tool_schemas else "executor_direct",
                max_output_tokens=call_policy.max_output_tokens)
            call_output_budget = call_budget.max_output_tokens
            trace.log("context_budget", step=step, **call_budget.to_dict())
            budget_forces_direct = (
                call_budget.output_budget_reduced
                and call_output_budget < call_policy.answer_token_reserve
            )
            if budget_forces_direct:
                trace.log("context_budget_guard", step=step,
                          action="disable_thinking_for_answer_reserve",
                          available_output_tokens=call_output_budget)
            trace.log("reasoning_policy_call", step=step, **call_policy.to_dict())
            stream = _stream_with_supported_options(
                llm,
                with_context_images(messages, mm_images) if mm_images else messages,
                tools=tool_schemas,
                max_tokens=call_output_budget,
                disable_thinking=(force_disable_thinking or budget_forces_direct),
                reasoning_effort=("" if budget_forces_direct else
                                  call_policy.reasoning_effort),
                reasoning_budget_tokens=(0 if budget_forces_direct else
                                         call_policy.reasoning_budget_tokens))
            async for ev in stream:
                if ev["kind"] == "thinking":
                    thinking_buf += ev["delta"]
                    # Live stream (display-only) when the gate allows it; the
                    # internal accumulation above feeds telemetry/real_summary.
                    live_delta = live_gate.take(ev["delta"])
                    if live_delta:
                        yield {"type": "thinking", "content": live_delta,
                               "is_delta": True, "summary": False}
                elif ev["kind"] == "answer":
                    answer_buf += ev["delta"]
                    # 伪工具标签护栏：假 <knowledge_search> 标签形成即停流转发，
                    # 交由下方分支执行真实检索并继续环路。
                    if pseudo_guard is None:
                        pseudo_guard = PseudoToolGuard()
                    safe = pseudo_guard.feed(ev["delta"])
                    if safe and empty_answer_retries == 0:
                        yield {"type": "answer", "content": safe, "is_delta": True}
                elif ev["kind"] == "tool_calls":
                    tool_calls_raw = ev["calls"]
                elif ev["kind"] == "done":
                    finish_reason = ev.get("finish_reason", "stop")
                    trace.llm_call(step, ev.get("usage"))
                elif ev["kind"] == "capability_fallback":
                    trace.log("provider_capability_fallback", step=step,
                              reason=ev.get("reason", "provider_rejected_optional_controls"))
                elif ev["kind"] == "retry":
                    trace.log("retry", step=step, attempt=ev.get("attempt"), reason=ev.get("reason"))
                    yield {"type": "retry", "attempt": ev.get("attempt"), "reason": ev.get("reason")}
        except Exception as e:
            if (active_tool_message_mode == "native" and not native_fallback_used
                    and getattr(e, "status_code", None) == 400):
                from ..core.message_protocol import native_to_legacy_messages
                messages = native_to_legacy_messages(messages)
                active_tool_message_mode = "legacy"
                native_fallback_used = True
                trace.log("tool_message_fallback", step=step,
                          from_mode="native", to_mode="legacy",
                          reason="provider_rejected_native_tool_messages")
                yield {"type": "retry", "attempt": 1,
                       "reason": "native_tool_message_fallback"}
                continue
            trace.log("error", step=step, message=f"LLM error: {e}")
            yield {"type": "error", "message": f"LLM 错误: {e}"}
            return

        # 伪标签检出后 answer_buf 仍是含标记的原始累计（feed 只对放行文本
        # 转发）；此后所有落盘/续写判定一律基于实际放行的 emitted，杜绝
        # 标记进入最终答案、续写前缀与会话历史。
        if pseudo_guard is not None and pseudo_guard.detected:
            answer_buf = pseudo_guard.emitted

        if empty_answer_retries and answer_buf:
            visible_prefix = "".join(visible_answer_parts)
            answer_buf = _continuation_suffix(visible_prefix, answer_buf)
            if answer_buf:
                yield {"type": "answer", "content": answer_buf, "is_delta": True}

        trace.decision(step, thinking_buf,
                       tool_calls_raw[0]["name"] if tool_calls_raw else None,
                       bool(tool_calls_raw), finish_reason)
        from ..core.context import estimate_tokens
        trace.log("llm_channels", step=step, finish_reason=finish_reason,
                  reasoning_chars=len(thinking_buf), answer_chars=len(answer_buf),
                  reasoning_estimated_tokens=estimate_tokens(thinking_buf),
                  answer_estimated_tokens=estimate_tokens(answer_buf))
        if thinking_buf.strip():
            thinking_parts.append(thinking_buf)

        if not tool_calls_raw:
            # Reasoning-capable models can consume the entire completion budget
            # in reasoning_content and finish with no student-visible answer.
            # Treat that as an incomplete generation, not a successful turn.
            incomplete = finish_reason == "length" or not answer_buf.strip()
            if (empty_answer_retries < 1 and incomplete
                    and (thinking_buf.strip() or finish_reason == "length")):
                empty_answer_retries += 1
                force_disable_thinking = True
                trace.log("incomplete_answer_recovery", step=step,
                          finish_reason=finish_reason,
                          thought_len=len(thinking_buf),
                          retry=empty_answer_retries)
                if answer_buf:
                    # Preserve a visible prefix and ask the second pass to
                    # continue instead of duplicating it. This also fixes the
                    # history path: answer text emitted before a tool call or
                    # a length stop must not disappear after the turn.
                    visible_answer_parts.append(answer_buf)
                    messages.append({"role": "assistant", "content": answer_buf})
                yield {"type": "retry", "attempt": empty_answer_retries,
                       "reason": "incomplete_answer_after_reasoning"}
                messages.append({"role": "system", "content": (
                    "[输出恢复] 上一次生成把预算耗在内部思考中，学生可见答案为空或未完成。"
                    "现在停止继续分析，直接完成当前教学计划：若上一次已经有可见文字，"
                    "从其末尾继续，不要重复；该调用的可用工具立即调用；否则直接输出完整、"
                    "可展示的回答。不要复述 Skill 决策过程。"
                    "紧扣学生最新一条消息的诉求作答；不得复述此前轮次已讲过的内容，"
                    "必须给出新的增量内容。"
                )})
                continue
            if pseudo_guard is not None and not pseudo_guard.detected:
                tail = pseudo_guard.flush()
                if tail and empty_answer_retries == 0:
                    yield {"type": "answer", "content": tail, "is_delta": True}
            if (pseudo_guard is not None and pseudo_guard.detected
                    and not pseudo_guard_used and ks_tool is not None
                    and step < MAX_STEPS):
                # 模型在正文里叙述了假 <knowledge_search> 标签而非发起调用：
                # 执行真实检索，注入结果，继续环路让模型基于真结果续写。
                pseudo_guard_used = True
                visible_answer_parts.append(pseudo_guard.emitted)
                pseudo_query = pseudo_guard.extract_query(user_msg)
                trace.log("pseudo_tool_guard", step=step,
                          tool="knowledge_search", query=pseudo_query)
                yield {"type": "tool_start", "name": "knowledge_search",
                       "args": {"query": pseudo_query}, "auto": True}
                try:
                    pseudo_result = await ks_tool.run(query=pseudo_query, top_k=6)
                except Exception as e:
                    pseudo_result = err("knowledge_search", ErrorCode.TOOL_ERROR, str(e))
                all_tool_calls.append(
                    {"name": "knowledge_search", "result": pseudo_result.to_dict()})
                yield {"type": "tool_result", "result": pseudo_result.to_dict()}
                if not pseudo_result.is_error and pseudo_result.data.get("count", 0) > 0:
                    from ..core.tool_context import (ToolResultRetention,
                                                      project_tool_result)
                    projection = project_tool_result(
                        pseudo_result, ToolResultRetention.CURRENT_FULL).text
                    followup = ("[系统自动预检索] 已从学生上传的资料中检索到以下片段。"
                                "你的回答必须严格基于这些资料原文组织内容；"
                                "禁止脱离资料原文凭文件名或常识编造：\n\n" + projection)
                else:
                    followup = ("[系统自动预检索] 已自动检索学生上传的资料，但未命中相关片段。"
                                "你必须如实告诉学生「在已上传的资料中没有找到相关内容」，"
                                "并建议换关键词或补充上传，严禁编造资料内容。")
                messages = messages + [
                    {"role": "assistant",
                     "content": pseudo_guard.emitted or "（先检索教材）"},
                    {"role": "user", "content": followup},
                ]
                continue
            forced_call = _enforced_plan_call(plan, tool_map, all_tool_calls)
            if forced_call is not None:
                tool_calls_raw = [forced_call]
                trace.log("skill_plan_auto_invoke", step=step,
                          plan_step_index=forced_call["_plan_step_index"],
                          tool=forced_call["name"],
                          reason="required_plan_step_omitted_by_model")
            else:
                if not answer_buf.strip() and not visible_answer_parts:
                    answer_buf = "刚才的回答生成被意外截断了，请重试一次，我会直接给出完整讲解。"
                    trace.log("empty_answer_fallback", step=step,
                              finish_reason=finish_reason)
                    yield {"type": "answer", "content": answer_buf,
                           "is_delta": True}
                final_answer = "".join(visible_answer_parts) + answer_buf
                # Tail-bounded join of every step's reasoning: enough material
                # for the real_summary digest without unbounded growth.
                all_thinking = "\n\n".join(thinking_parts)[-6000:]
                yield {"type": "done", "thinking": all_thinking, "answer": final_answer,
                       "tool_calls": _lite_tool_calls(all_tool_calls),
                       "trace_id": trace.run_id, "trace_summary": trace.summary()}
                return  # finished without a tool call

        tc = tool_calls_raw[0]
        tool_name = tc.get("name", "unknown")
        tool_args = tc.get("args", {}) or {}
        yield {"type": "step", "step": "tool_executing", "tool": tool_name}
        yield {"type": "tool_start", "name": tool_name, "args": tool_args,
               "auto": bool(tc.get("_auto", False))}
        if progress_cb:
            progress_cb(f"正在执行 {tool_name}…")

        call_key = _make_call_key(tool_name, tool_args)
        if call_key in seen_calls:
            result = err(tool_name, ErrorCode.DUPLICATE_CALL,
                         "本回合已用相同参数调用过此工具，请换一种方式或调整参数。")
            trace.log("tool_result", step=step, tool=tool_name, status="error", reason="duplicate_call")
            all_tool_calls.append({"name": tool_name, "result": result.to_dict()})
            yield {"type": "tool_result", "result": result.to_dict()}
            _dupe_text = (pseudo_guard.emitted if pseudo_guard and pseudo_guard.detected
                          else answer_buf)
            if _dupe_text:
                visible_answer_parts.append(_dupe_text)
            _append_tool_exchange(
                messages, mode=active_tool_message_mode,
                assistant_content=_dupe_text, call_id=str(tc.get("id", "")),
                tool_name=tool_name, args=tool_args,
                result_text=_build_tool_result_message(result))
            continue
        seen_calls.add(call_key)

        tool = tool_map.get(tool_name)
        if tool is None:
            result = err(tool_name, ErrorCode.NO_TOOL, f"工具 '{tool_name}' 不存在或本步不可见。")
        elif (cb_err := _circuit_check(tool_name)) is not None:
            result = cb_err
        else:
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
        _circuit_record(tool_name, result)
        trace.log("tool_result", step=step, tool=tool_name, status=result.status, error_code=result.error_code)
        postconditions = skill_runtime.validate_result(tool_name, result)
        if postconditions is not None:
            trace.log("skill_postconditions", step=step, **postconditions.to_dict())
        result_dict = result.to_dict()
        all_tool_calls.append({"name": tool_name, "result": result_dict})
        yield {"type": "tool_result", "result": result_dict}

        warning = _reflect_tool_result(tool_name, result)
        if warning:
            trace.log("warning", step=step, tool=tool_name, message=warning, source="reflector")
            yield {"type": "tool_warning", "warning": warning, "tool": tool_name}

        from .reasoning_narrator import tool_progress_summary
        progress_summary = tool_progress_summary(tool_name, succeeded=not result.is_error)
        trace.log("reasoning_summary", chars=len(progress_summary.content),
                  stage=progress_summary.stage, level=progress_summary.level,
                  tool=tool_name)
        yield {"type": "thinking", "content": progress_summary.content + "\n\n",
               "is_delta": True, "summary": True,
               "stage": progress_summary.stage, "level": progress_summary.level}

        # stash quiz results into session (V1 side-effect)
        if tool_name in ("generate_quiz", "fit_quiz") and not result.is_error:
            session.quiz_history.append(result.data)
            record_generated_quiz(session.session_id, result.data)
            # 跨会话「最近习题」库（测评中心列表，每学生上限 100 道）
            record_recent_quiz(session.session_id,
                               getattr(session, "student_id", "") or "",
                               result.data)

        _step_text = (pseudo_guard.emitted if pseudo_guard and pseudo_guard.detected
                      else answer_buf)
        if _step_text:
            visible_answer_parts.append(_step_text)
        projected_message, projection_meta = _project_tool_message(result)
        trace.log("tool_context_projection", mode=settings.tool_context_projection_mode,
                  **projection_meta)
        legacy_pair = [
            {"role": "assistant", "content": _step_text or f"(调用工具: {tool_name})"},
            {"role": "user", "content": projected_message},
        ]
        _append_tool_exchange(
            messages, mode=active_tool_message_mode,
            assistant_content=_step_text, call_id=str(tc.get("id", "")),
            tool_name=tool_name, args=tool_args, result_text=projected_message)

        if active_tool_message_mode in {"shadow", "native"}:
            from ..core.message_protocol import build_native_tool_shadow
            from ..core.context import estimate_tokens
            shadow = build_native_tool_shadow(
                _step_text, call_id=str(tc.get("id", "")), tool_name=tool_name,
                args=tool_args, result_text=projected_message)
            trace.log("tool_message_shadow", mode=active_tool_message_mode,
                      tool=tool_name,
                      legacy_estimated_tokens=estimate_tokens(
                          json.dumps(legacy_pair, ensure_ascii=False, default=str)),
                      **shadow)

        # Once a deterministic required step has succeeded, hide that tool for
        # the remainder of shadow/off execution too. Otherwise the model can
        # see the still-visible schema and immediately request the same quiz a
        # second time, producing a noisy duplicate tool card before the guard.
        if (not result.is_error and _is_auto_fulfillment_tool(plan, tool_name)
                and not gated):
            visible_tools = [t for t in visible_tools if t.name != tool_name]
            tool_map.pop(tool_name, None)
            tool_schemas = [t.to_schema() for t in visible_tools]
            trace.log("skill_tool_hidden_after_fulfillment", tool=tool_name,
                      visible_tools=[t.name for t in visible_tools])

        if (gated and plan and plan_step_index < len(plan.steps)):
            current_step = plan.steps[plan_step_index]
            result_valid = (not result.is_error
                            and (postconditions is None or postconditions.valid))
            may_advance = result_valid or current_step.optional
            current_names = {
                t.name for t in route(plan, tools, step_index=plan_step_index)
            }
            if tool_name in current_names and may_advance:
                completed_index = plan_step_index
                skipped_optional = bool(current_step.optional and not result_valid)
                plan_step_index += 1
                plan_step_index, skipped_advisory = _next_executable_step(
                    plan, tools, plan_step_index)
                if skipped_advisory:
                    trace.log("skill_plan_advisory",
                              step_indexes=skipped_advisory,
                              steps=[plan.steps[i].to_dict()
                                     for i in skipped_advisory])
                visible_tools = _tools_for_execution(
                    plan, tools, plan_step_index, gated=True)
                tool_schemas = [t.to_schema() for t in visible_tools]
                tool_map = {t.name: t for t in visible_tools}
                messages.append({"role": "system", "content":
                    _step_progress_note(
                        plan, completed_index, plan_step_index,
                        skipped_optional=skipped_optional)})
                trace.log("skill_plan_advance", completed_index=completed_index,
                          next_index=plan_step_index, trigger=tool_name,
                          skipped_optional=skipped_optional,
                          visible_tools=[t.name for t in visible_tools])
                if plan_step_index < len(plan.steps):
                    trace.log("skill_plan_step", step_index=plan_step_index,
                              step=plan.steps[plan_step_index].to_dict(),
                              visible_tools=[t.name for t in visible_tools])

    # max steps reached
    fallback = "我已经尽力处理，但暂时没能给出完整回答。可以换个说法再问一次吗？"
    trace.log("finish", branch="max_steps", tool_calls=len(all_tool_calls))
    final_answer = "".join(visible_answer_parts) + fallback
    yield {"type": "done", "thinking": "", "answer": final_answer,
           "tool_calls": _lite_tool_calls(all_tool_calls),
           "trace_id": trace.run_id, "trace_summary": trace.summary()}
    return  # max steps reached
