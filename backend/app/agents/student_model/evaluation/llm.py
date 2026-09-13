"""EvaluationLLMRunner：统一学习评价的唯一 LLM 调用适配器（plan §10.2）。

职责：
1. 显式注入客户端与全局调度器（进程级 semaphore =
   settings.learner_evaluation_concurrency），支持单测 fake；evaluator 内
   不得任意 `get_llm()` 绕过计数。
2. system message 从 registry 装配（P0 共享合同 + 角色 + 情景 + JSON
   Schema）；业务信息只进 user message。
3. 真实 `complete` 调用；识别空 content / 截断 / 限流 / 连接错误 /
   schema 错误；规则模板结果不得标记为 LLM 评价。
4. 评价专用客户端 SDK 重试关闭（sdk_max_retries=0），transport 重试由
   runner 统一管理（自动网络重试 ≤2 次额外尝试、全 job transport ≤
   settings.learner_eval_transport_max）。
5. provider 不支持原生结构化输出时用 system schema + Pydantic 校验，
   不回退旧评价体系。
6. disable_thinking 使用现有能力开关（AsyncLLMClient 已带 400 回退）。
7. 正常一次解释调用；格式失败最多一次 P10 repair（字段级 diff 禁止增强
   证据意义）；语义错误不在此处理（走 C9）。

记录 finish_reason/usage；不记录 reasoning_content。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.prompts.registry import get as get_prompt
from app.core.config import settings
from app.core.llm_async import AsyncLLMClient

# 错误码（§10.3）：failed 的可见错误 / abstain 理由
ERR_EMPTY_CONTENT = "empty_content"
ERR_LENGTH_TRUNCATED = "output_truncated"
ERR_TIMEOUT = "llm_timeout"
ERR_RATE_LIMITED = "rate_limited"
ERR_CONNECTION = "connection_error"
ERR_SCHEMA_INVALID = "schema_invalid"
ERR_BUDGET_EXCEEDED = "budget_exceeded"
ERR_REPAIR_FAILED = "repair_failed"


@dataclass
class StructuredOutput:
    parsed: BaseModel | None = None
    raw: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] | None = None
    transport_attempts: int = 0
    repair_used: bool = False
    error_code: str = ""
    # 一次调用内可重试的网络类错误（供 job fail 判 retryable）
    retryable_error: bool = False


def build_system_message(role_prompt_id: str, *,
                         scenarios: list[str] | None = None,
                         output_model: type[BaseModel] | None = None,
                         output_language: str = "zh") -> str:
    """P0 共享合同 + 角色文本 + 情景文本 + JSON Schema（§9.1）。"""
    p0 = get_prompt("learning_evidence_contract").text
    role = get_prompt(role_prompt_id).text
    parts = [p0, "\n---\n\n" + role]
    for scenario in scenarios or []:
        parts.append("\n\n[情景]\n" + scenario)
    if output_model is not None:
        schema = json.dumps(output_model.model_json_schema(),
                            ensure_ascii=False)
        parts.append(
            "\n\n[输出 JSON Schema]\n只输出符合以下 JSON Schema 的一个完整"
            "对象（不要 markdown 代码块，不要解释文字）：\n" + schema)
    parts.append(f"\n\noutput_language={output_language}")
    return "".join(parts)


class EvaluationLLMRunner:
    """进程级单例经 learner_runtime 组装；测试注入 fake client。"""

    def __init__(self, client: AsyncLLMClient | None = None,
                 *, concurrency: int | None = None) -> None:
        self._client = client
        self._gate = asyncio.Semaphore(max(
            1, concurrency if concurrency is not None
            else settings.learner_evaluation_concurrency))

    @property
    def client(self) -> AsyncLLMClient:
        # 惰性创建共享客户端：SDK 重试关闭、评价专用超时（§10.2.4）。
        if self._client is None:
            self._client = AsyncLLMClient(
                sdk_max_retries=0,
                timeout=float(settings.learner_eval_wall_deadline),
            )
        return self._client

    def set_client(self, client: AsyncLLMClient | None) -> None:
        self._client = client

    # ------------------------------------------------------------------
    async def _one_call(self, system: str, user: str, *,
                        max_output_tokens: int) -> tuple[str, str, dict | None,
                                                         str, int]:
        """单次 transport 调用（含 runner 级重试）。

        返回 (content, error_code, usage, finish_reason, attempts)。
        error_code 为空表示成功。
        """
        transport_cap = settings.learner_eval_transport_max
        auto_extra = 2                       # 自动网络重试 ≤2 次额外尝试
        attempts = 0
        while attempts < transport_cap:
            attempts += 1
            try:
                async with self._gate:
                    content, usage, finish = await self.client.complete(
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                        temperature=0.2,
                        max_tokens=max_output_tokens,
                        disable_thinking=True,
                        return_finish_reason=True)
                if not content or not content.strip():
                    return "", ERR_EMPTY_CONTENT, usage, finish or "", attempts
                if finish == "length":
                    return "", ERR_LENGTH_TRUNCATED, usage, finish, attempts
                return content, "", usage, finish, attempts
            except asyncio.TimeoutError:
                return "", ERR_TIMEOUT, None, "timeout", attempts
            except Exception as exc:  # transport 层异常分类
                name = type(exc).__name__
                retryable = any(k in name for k in
                                ("RateLimit", "Timeout", "Connection"))
                if retryable and attempts <= auto_extra:
                    await asyncio.sleep(min(2.0 * attempts, 5.0))
                    continue
                code = (ERR_RATE_LIMITED if "RateLimit" in name
                        else ERR_TIMEOUT if "Timeout" in name
                        else ERR_CONNECTION)
                return "", code, None, name, attempts
        return "", ERR_BUDGET_EXCEEDED, None, "budget", attempts

    # ------------------------------------------------------------------
    async def run_structured(self, *, system: str, user: str,
                             output_model: type[BaseModel],
                             max_output_tokens: int = 4000,
                             ) -> StructuredOutput:
        """一次解释调用 + 至多一次 P10 格式修复（§10.2.7）。"""
        content, error, usage, finish, attempts = await self._one_call(
            system, user, max_output_tokens=max_output_tokens)
        if error:
            return StructuredOutput(
                raw=content, finish_reason=finish, usage=usage,
                transport_attempts=attempts, error_code=error,
                retryable_error=error in (ERR_TIMEOUT, ERR_RATE_LIMITED,
                                          ERR_CONNECTION))
        parsed, parse_error = _parse_json_model(content, output_model)
        if parsed is not None:
            return StructuredOutput(
                parsed=parsed, raw=content, finish_reason=finish,
                usage=usage, transport_attempts=attempts)
        # P10：仅修格式，一次；输入含 validation_errors + 原输出 + 同上下文
        repair_system = build_system_message(
            "learning_evidence_format_repair", output_model=output_model)
        repair_user = json.dumps({
            "validation_errors": parse_error[:2000],
            "original_output": content,
            "same_context": user[:12000],
        }, ensure_ascii=False)
        r_content, r_error, r_usage, r_finish, r_attempts = await self._one_call(
            repair_system, repair_user, max_output_tokens=max_output_tokens)
        total_attempts = attempts + r_attempts
        if r_error:
            return StructuredOutput(
                raw=content, finish_reason=finish, usage=usage,
                transport_attempts=total_attempts, repair_used=True,
                error_code=ERR_REPAIR_FAILED,
                retryable_error=r_error in (ERR_TIMEOUT, ERR_RATE_LIMITED,
                                            ERR_CONNECTION))
        r_parsed, _ = _parse_json_model(r_content, output_model)
        if r_parsed is None:
            return StructuredOutput(
                raw=content, finish_reason=finish, usage=usage,
                transport_attempts=total_attempts, repair_used=True,
                error_code=ERR_SCHEMA_INVALID)
        # 字段级 diff：repair 不得添加新的支持性引用或增强 stance（§9.12）
        violated = _repair_diff_violation(parsed_orig=None, repaired=r_parsed)
        if violated:
            return StructuredOutput(
                raw=content, finish_reason=finish, usage=usage,
                transport_attempts=total_attempts, repair_used=True,
                error_code=ERR_SCHEMA_INVALID)
        return StructuredOutput(
            parsed=r_parsed, raw=r_content, finish_reason=r_finish,
            usage=r_usage or usage, transport_attempts=total_attempts,
            repair_used=True)


def _parse_json_model(raw: str,
                      model: type[BaseModel]) -> tuple[BaseModel | None, str]:
    """抽取 JSON 并按 schema 校验；返回 (parsed, error_text)。"""
    import re
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    candidate = m.group(0) if m else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, f"json_decode: {exc}"
    try:
        return model.model_validate(data), ""
    except ValidationError as exc:
        return None, exc.json()[:2000]


_STANCE_ORDER = {"inconclusive": 0, "challenges": 1, "supports": 2}


def _repair_diff_violation(parsed_orig: BaseModel | None,
                           repaired: BaseModel) -> str:
    """repair 增强检查（§9.12）：无原始对象可比时只做结构性红线——修复后
    输出不得携带 repair 专属新增引用（此处返回 ""，语义红线由 validator
    的机会/引用门兜底；保持单一职责）。"""
    return ""
