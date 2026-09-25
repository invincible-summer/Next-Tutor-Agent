"""Async streaming LLM client (OpenAI-compatible).

Streams content (answer channel) and reasoning_content (thinking channel) as
async generators.  Native OpenAI-compatible tool calls are normalized together
with a fail-closed DeepSeek V4/V4.1 DSML fallback for gateways that surface the
model's internal tool markup through ``delta.content`` instead of
``delta.tool_calls``.

Network transport is direct by default. Shell proxy environment variables are
not trusted, so deployments do not accidentally route through a local proxy.
"""
from __future__ import annotations

import contextvars
from typing import Any, AsyncGenerator

import asyncio
import json

import httpx
from openai import AsyncOpenAI
from openai import RateLimitError, APITimeoutError, APIConnectionError, APIStatusError

from .config import settings
from .tool_call_compat import (
    DeepSeekDSMLStreamParser,
    normalize_tool_calls,
    remember_tool_reasoning,
)


# ---------------------------------------------------------------------------
# 课堂 LLM 预算钩子（plan.md §15.4，D01）
# 只在课堂 worker 上下文 set；complete() 发 HTTP 前向钩子预留一次调用与
# token 上限，返回后按 usage 结算。未设置（普通聊天/quiz 既有路径）时
# 行为零变化。钩子协议见 app/classroom/llm_budget.py::LLMUsageBudget。
# ---------------------------------------------------------------------------
_llm_budget_hook: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "edu_llm_budget_hook", default=None)


def set_llm_budget_hook(hook: Any) -> contextvars.Token:
    return _llm_budget_hook.set(hook)


def reset_llm_budget_hook(token: contextvars.Token) -> None:
    _llm_budget_hook.reset(token)


class AsyncLLMClient:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float = 180.0,
        concurrency: int = 1,
        sdk_max_retries: int | None = None,
        retry_max: int | None = None,
        retry_base_delay: float | None = None,
    ):
        self.model = model or settings.llm_model
        self.max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        effective_url = base_url or settings.llm_base_url
        self.base_url = effective_url
        # Direct network policy: never inherit HTTP(S)_PROXY/ALL_PROXY.
        trust_env = False
        self.client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=effective_url,
            timeout=timeout,
            max_retries=3 if sdk_max_retries is None else int(sdk_max_retries),
            http_client=httpx.AsyncClient(trust_env=trust_env, timeout=timeout),
        )
        # R15: transient error retry config.
        # sdk_max_retries / retry_max / retry_base_delay 是可选覆盖（评价
        # runner 专用：SDK 重试关闭、transport 重试由 runner 统一管理，
        # §10.2.4）；不传时保持全部既有调用方默认行为。
        self._retry_max = 4 if retry_max is None else int(retry_max)
        self._retry_base_delay = 2.0 if retry_base_delay is None \
            else float(retry_base_delay)
        # R16: concurrency limiter — prevents 429 by capping concurrent calls.
        # 默认 1 保持全部既有调用方行为不变；教材构建传入更高值并在调用点
        # 由 textbook_pipeline.llm_gate() 统一动态限流。
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        disable_thinking: bool = False,
        reasoning_effort: str = "",
        reasoning_budget_tokens: int = 0,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a completion. Yields deltas as dicts:

        ``{"kind": "thinking", "delta": "..."}`` -- reasoning_content
        ``{"kind": "answer", "delta": "..."}`` -- user-visible content only
        ``{"kind": "tool_calls", "calls": [...]}`` -- normalized complete calls
        ``{"kind": "done", "finish_reason": "...", "usage": {...}}``

        DeepSeek V4.1 normally returns OpenAI-compatible ``delta.tool_calls``.
        If a compatible gateway leaks its DSML control block into ``content``,
        that block is withheld from the answer stream and converted into the
        same internal tool-call event instead of reaching chat/history/TTS.
        """
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if disable_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif reasoning_budget_tokens:
            # Provider capability profiles opt into this non-standard
            # OpenAI-compatible extension explicitly; never send it by
            # default to gateways that do not document the field.
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled",
                             "budget_tokens": int(reasoning_budget_tokens)}
            }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        successful_tc_by_index: dict[int, dict[str, Any]] = {}
        successful_tc_order: list[int] = []
        successful_dsml_calls: list[dict[str, Any]] = []
        successful_dsml_protocol = ""
        successful_reasoning = ""

        # R15: retry-with-backoff for transient errors (429/timeout/connection).
        # Native/DSML tool buffers are attempt-local so a failed partial stream
        # cannot corrupt the structured call emitted by the successful retry.
        attempt = 0
        while True:
            attempt += 1
            tc_by_index: dict[int, dict[str, Any]] = {}
            tc_order: list[int] = []
            reasoning_parts: list[str] = []
            dsml_parser = DeepSeekDSMLStreamParser() if tools else None
            try:
                # R16: acquire semaphore to limit concurrent LLM calls
                async with self._semaphore:
                    stream = await self.client.chat.completions.create(**kwargs)
                    async for chunk in stream:
                        if chunk.usage:
                            usage = {"prompt_tokens": chunk.usage.prompt_tokens,
                                     "completion_tokens": chunk.usage.completion_tokens,
                                     "total_tokens": chunk.usage.total_tokens}
                            details = getattr(chunk.usage, "completion_tokens_details", None)
                            reasoning_tokens = getattr(details, "reasoning_tokens", None)
                            if reasoning_tokens is not None:
                                usage["reasoning_tokens"] = reasoning_tokens
                                usage["answer_tokens"] = max(
                                    0, chunk.usage.completion_tokens - reasoning_tokens)
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = choice.delta
                        if choice.finish_reason:
                            finish_reason = choice.finish_reason
                        rc = getattr(delta, "reasoning_content", None)
                        if rc:
                            reasoning_parts.append(str(rc))
                            yield {"kind": "thinking", "delta": rc}
                        content = getattr(delta, "content", None)
                        if content:
                            text = str(content)
                            if dsml_parser is not None:
                                text = dsml_parser.feed(text)
                            if text:
                                yield {"kind": "answer", "delta": text}
                        for piece in (getattr(delta, "tool_calls", None) or []):
                            idx = piece.index
                            if idx not in tc_by_index:
                                tc_by_index[idx] = {"id": piece.id or "", "name": "",
                                                    "arguments_json": ""}
                                tc_order.append(idx)
                            cell = tc_by_index[idx]
                            if piece.id:
                                cell["id"] = piece.id
                            if piece.function:
                                if piece.function.name:
                                    cell["name"] = piece.function.name
                                if piece.function.arguments:
                                    cell["arguments_json"] += piece.function.arguments
                if dsml_parser is not None:
                    safe_tail = dsml_parser.flush()
                    if safe_tail:
                        yield {"kind": "answer", "delta": safe_tail}
                    successful_dsml_calls = dsml_parser.calls
                    successful_dsml_protocol = dsml_parser.protocol
                successful_tc_by_index = tc_by_index
                successful_tc_order = tc_order
                successful_reasoning = "".join(reasoning_parts)
                break  # stream completed successfully
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt >= self._retry_max:
                    raise
                delay = self._retry_base_delay * (2 ** (attempt - 1))
                yield {"kind": "retry", "attempt": attempt, "delay": delay,
                       "reason": type(e).__name__}
                await asyncio.sleep(delay)
            except APIStatusError as e:
                # Provider-portable fallback: some OpenAI-compatible gateways
                # reject optional reasoning controls. Retry once without the
                # optional fields rather than failing the whole student turn.
                if (e.status_code == 400 and
                        ("extra_body" in kwargs or "reasoning_effort" in kwargs)):
                    kwargs.pop("extra_body", None)
                    kwargs.pop("reasoning_effort", None)
                    disable_thinking = False
                    reasoning_effort = ""
                    reasoning_budget_tokens = 0
                    yield {"kind": "capability_fallback",
                           "reason": "provider_rejected_reasoning_controls"}
                    attempt -= 1
                    continue
                if e.status_code == 429 and attempt < self._retry_max:
                    delay = self._retry_base_delay * (2 ** (attempt - 1))
                    yield {"kind": "retry", "attempt": attempt, "delay": delay,
                           "reason": "429"}
                    await asyncio.sleep(delay)
                else:
                    raise

        native_calls: list[dict[str, Any]] = []
        for idx in successful_tc_order:
            cell = successful_tc_by_index[idx]
            args: dict[str, Any] = {}
            if cell["arguments_json"]:
                try:
                    args = json.loads(cell["arguments_json"])
                except json.JSONDecodeError:
                    args = {"_raw": cell["arguments_json"]}
            native_calls.append({"id": cell["id"], "name": cell["name"], "args": args})

        # A compatibility gateway can emit both a native projection and the raw
        # DSML text. Normalize and deduplicate the two representations by
        # function+arguments so the Executor never executes the same call twice.
        calls = normalize_tool_calls(
            [*native_calls, *successful_dsml_calls], tools)
        if calls:
            remember_tool_reasoning(
                [str(call.get("id") or "") for call in calls],
                successful_reasoning,
                model=self.model,
                base_url=self.base_url,
            )
            protocol = (
                "native+" + successful_dsml_protocol
                if native_calls and successful_dsml_calls
                else successful_dsml_protocol or "native"
            )
            yield {"kind": "tool_calls", "calls": calls, "protocol": protocol}

        effective_finish = finish_reason or "stop"
        if calls and successful_dsml_calls and effective_finish == "stop":
            # Raw DSML gateways commonly report stop instead of tool_calls.
            # Internally expose the semantic finish reason after successful
            # normalization; Executor behavior already keys off the calls list.
            effective_finish = "tool_calls"
        yield {"kind": "done", "finish_reason": effective_finish, "usage": usage}

    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        disable_thinking: bool = False,
        return_finish_reason: bool = False,
    ) -> tuple[str, dict[str, Any] | None] | tuple[str, dict[str, Any] | None, str | None]:
        """Non-streaming completion (for compaction/summarization, not the chat
        loop). Returns (content, usage). Shares the same retry/semaphore policy.

        disable_thinking asks reasoning models (deepseek-v4/R1 etc.) to skip
        the thinking chain via extra_body — short structured utility calls
        (JSON extraction, milestones, consolidation) otherwise starve: the
        whole max_tokens budget gets eaten by reasoning_content and content
        comes back empty. If the provider rejects the field (400), the call
        transparently retries once without it (provider-portable).

        return_finish_reason=True（评价 runner 用，§10.2.6）追加第三个返回值
        finish_reason；其余调用方默认二元组不变。
        """
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens if max_tokens is None else max_tokens,
        )
        if disable_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        budget = _llm_budget_hook.get()
        reservation: dict[str, int] | None = None
        if budget is not None:
            # 真正发 HTTP 前预留；budget_exceeded 异常直接向上传播
            reservation = budget.reserve(
                messages=messages, max_tokens=kwargs["max_tokens"])
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._semaphore:
                    resp = await self.client.chat.completions.create(**kwargs)
                content = (resp.choices[0].message.content or "") if resp.choices else ""
                finish_reason = (resp.choices[0].finish_reason or "") if resp.choices else ""
                usage = None
                if resp.usage:
                    usage = {"prompt_tokens": resp.usage.prompt_tokens,
                             "completion_tokens": resp.usage.completion_tokens,
                             "total_tokens": resp.usage.total_tokens}
                if budget is not None and reservation is not None:
                    budget.settle(reservation, usage)
                if return_finish_reason:
                    return content, usage, finish_reason
                return content, usage
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if budget is not None and reservation is not None:
                    # 请求已可能到达 provider：按预留上限扣账（unknown outcome）
                    budget.settle(reservation, None)
                    reservation = budget.reserve(
                        messages=messages, max_tokens=kwargs["max_tokens"]) \
                        if attempt < self._retry_max else None
                if attempt >= self._retry_max:
                    if return_finish_reason:
                        return "", None, type(e).__name__
                    return "", None
                await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))
            except APIStatusError as e:
                if budget is not None and reservation is not None:
                    budget.settle(reservation, None)
                    reservation = None
                if e.status_code == 400 and kwargs.get("extra_body"):
                    # provider doesn't support the thinking toggle: retry plain
                    kwargs.pop("extra_body")
                    if budget is not None:
                        reservation = budget.reserve(
                            messages=messages, max_tokens=kwargs["max_tokens"])
                    continue
                if e.status_code == 429 and attempt < self._retry_max:
                    if budget is not None:
                        reservation = budget.reserve(
                            messages=messages, max_tokens=kwargs["max_tokens"])
                    await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))
                    continue
                if return_finish_reason:
                    return "", None, f"status_{e.status_code}"
                return "", None


def get_llm(purpose: str = "") -> AsyncLLMClient:
    """Return the shared client, with a bounded fast lane for quiz calls.

    The normal tutor client keeps its historical model and retry behavior.
    CAT/structured-question generation is short JSON work: using the light
    model and disabling SDK-level retries avoids multiplying a single failed
    request by both the SDK and the application's own bounded fallback.

    ``"classroom"``（plan §6.5/§15.4）：CLASSROOM_MODEL 为空回主模型，
    凭证/base URL 不另建；单次 90s、SDK 隐式重试关闭，应用层有界重试；
    token/调用预算经 contextvar 钩子由 worker 记账（见 set_llm_budget_hook）。
    """
    if purpose == "quiz":
        return AsyncLLMClient(
            model=settings.quiz_model,
            sdk_max_retries=settings.quiz_sdk_max_retries,
            retry_max=settings.quiz_retry_max,
            retry_base_delay=settings.quiz_retry_base_delay,
        )
    if purpose == "classroom":
        from ..classroom.limits import SINGLE_LLM_TIMEOUT_SECONDS

        return AsyncLLMClient(
            model=settings.classroom_model or None,
            sdk_max_retries=0,
            timeout=float(SINGLE_LLM_TIMEOUT_SECONDS),
            retry_max=2,
            retry_base_delay=1.0,
        )
    return AsyncLLMClient()
