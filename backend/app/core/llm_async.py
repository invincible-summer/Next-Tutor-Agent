"""Async streaming LLM client (OpenAI-compatible).

Streams content (answer channel) and reasoning_content (thinking channel,
for reasoning models like glm-5.2 / DeepSeek-R1) as async generators.
Network transport is direct by default. Shell proxy environment variables are
not trusted, so deployments do not accidentally route through a local proxy.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator

import asyncio

import httpx
from openai import AsyncOpenAI
from openai import RateLimitError, APITimeoutError, APIConnectionError, APIStatusError

from .config import settings


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
    ):
        self.model = model or settings.llm_model
        self.max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        effective_url = base_url or settings.llm_base_url
        # Direct network policy: never inherit HTTP(S)_PROXY/ALL_PROXY.
        trust_env = False
        self.client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=effective_url,
            timeout=timeout,
            max_retries=3,
            http_client=httpx.AsyncClient(trust_env=trust_env, timeout=timeout),
        )
        # R15: transient error retry config
        self._retry_max = 4
        self._retry_base_delay = 2.0
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
        {"kind": "thinking", "delta": "..."}  -- reasoning_content
        {"kind": "answer", "delta": "..."}    -- content
        {"kind": "tool_calls", "calls": [...]} -- complete tool_calls (once, at end)
        {"kind": "done", "finish_reason": "...", "usage": {...}}
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

        tc_by_index: dict[int, dict] = {}
        tc_order: list[int] = []
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        # R15: retry-with-backoff for transient errors (429/timeout/connection).
        # Retries wrap stream creation + all chunk iteration. On retry, the
        # partial token buffers are reset (no replay of already-yielded deltas).
        attempt = 0
        while True:
            attempt += 1
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
                            yield {"kind": "thinking", "delta": rc}
                        if delta.content:
                            yield {"kind": "answer", "delta": delta.content}
                        for piece in (delta.tool_calls or []):
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

        calls: list[dict[str, Any]] = []
        for idx in tc_order:
            cell = tc_by_index[idx]
            args: dict[str, Any] = {}
            if cell["arguments_json"]:
                import json
                try:
                    args = json.loads(cell["arguments_json"])
                except json.JSONDecodeError:
                    args = {"_raw": cell["arguments_json"]}
            calls.append({"id": cell["id"], "name": cell["name"], "args": args})
        if calls:
            yield {"kind": "tool_calls", "calls": calls}
        yield {"kind": "done", "finish_reason": finish_reason or "stop", "usage": usage}




    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        disable_thinking: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        """Non-streaming completion (for compaction/summarization, not the chat
        loop). Returns (content, usage). Shares the same retry/semaphore policy.

        disable_thinking asks reasoning models (deepseek-v4/R1 etc.) to skip
        the thinking chain via extra_body — short structured utility calls
        (JSON extraction, milestones, consolidation) otherwise starve: the
        whole max_tokens budget gets eaten by reasoning_content and content
        comes back empty. If the provider rejects the field (400), the call
        transparently retries once without it (provider-portable).
        """
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens if max_tokens is None else max_tokens,
        )
        if disable_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._semaphore:
                    resp = await self.client.chat.completions.create(**kwargs)
                content = (resp.choices[0].message.content or "") if resp.choices else ""
                usage = None
                if resp.usage:
                    usage = {"prompt_tokens": resp.usage.prompt_tokens,
                             "completion_tokens": resp.usage.completion_tokens,
                             "total_tokens": resp.usage.total_tokens}
                return content, usage
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt >= self._retry_max:
                    return "", None
                await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))
            except APIStatusError as e:
                if e.status_code == 400 and kwargs.get("extra_body"):
                    # provider doesn't support the thinking toggle: retry plain
                    kwargs.pop("extra_body")
                    continue
                if e.status_code == 429 and attempt < self._retry_max:
                    await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))
                    continue
                return "", None

def get_llm() -> AsyncLLMClient:
    return AsyncLLMClient()





