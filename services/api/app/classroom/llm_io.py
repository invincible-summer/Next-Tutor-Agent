"""课堂结构化 LLM 调用（D02）。

统一 complete(disable_thinking=True)；输出过 Pydantic 校验，失败最多一次
受预算约束的修复（SCHEMA_REPAIR_ATTEMPTS=1，修复也计入同一预算钩子）。
外部资料只以 <material_excerpt>/<web_excerpt> 数据边界进入 user 消息。
"""
from __future__ import annotations

import json
import re
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import limits
from .errors import ClassroomError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class IncompleteJSONError(ValueError):
    """模型已开始输出 JSON 对象，但在对象闭合前停止。"""


def extract_json(text: str) -> Any:
    """从模型输出提取 JSON：剥代码围栏；截取首个 { 到与之配对的 }。"""
    raw = (text or "").strip()
    raw = _FENCE_RE.sub("", raw).strip()
    if not raw:
        raise ValueError("空输出")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    if start < 0:
        raise ValueError("输出中没有 JSON 对象")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        ch = raw[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:index + 1])
    raise IncompleteJSONError("JSON 对象未闭合")


def _json_prefix(text: str) -> str:
    raw = _FENCE_RE.sub("", text).lstrip()
    start = raw.find("{")
    return raw[start:] if start >= 0 else raw


def _continuation_candidates(prefix: str, continuation: str) -> list[str]:
    """只拼接模型实际返回的字符；重叠回显不重复计入原文。"""
    head = _json_prefix(prefix)
    tail = _FENCE_RE.sub("", continuation).lstrip("\r\n")
    candidates = [head + tail]
    for size in range(min(len(head), len(tail), 1500), 7, -1):
        if head.endswith(tail[:size]):
            candidates.append(head + tail[size:])
            break
    # 兼容供应商忽略“只返回尾部”并重发完整对象；仍须通过完整 schema。
    if tail.lstrip().startswith("{"):
        candidates.append(tail)
    return list(dict.fromkeys(candidates))


def _validation_errors(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        lines = []
        for err in exc.errors()[:12]:
            lines.append(f"- {'.'.join(str(p) for p in err['loc'])}: "
                         f"{err['msg']}")
        return "\n".join(lines)
    return str(exc)[:800]


async def generate_json(
    llm: Any,
    *,
    prompt_id: str,
    prompt_version: str | None = None,
    user_text: str,
    model_cls: Type[T],
    repair_prompt_id: str | None = None,
    context_note: str = "",
    pre_validate: Any = None,
    repair_attempts: int = limits.SCHEMA_REPAIR_ATTEMPTS,
    max_tokens: int | None = None,
) -> tuple[T, list[dict[str, Any] | None]]:
    """一次生成 + 至多一次修复。返回 (model, usages)。

    pre_validate：严格校验前对原始 dict 的确定性无损修复（如 slide 载荷
    span 超限合并）；修复失败的结构问题仍走下方一次修复重试。
    未闭合 JSON 优先续写缺失尾部，不把同一页的全部正文再次发给模型重写。
    最终失败抛 ClassroomError(content_invalid)——管线把它标为阶段失败，
    不把坏 JSON 写进 artifact。"""
    from ..prompts.registry import get as get_prompt
    from ..core.llm_async import LLMRequestError

    system = get_prompt(prompt_id, prompt_version).text
    usages: list[dict[str, Any] | None] = []
    last_error = ""
    payload = user_text
    repair_attempts = max(0, min(repair_attempts, limits.SCHEMA_REPAIR_ATTEMPTS))

    def validate_text(raw: str) -> T:
        data = extract_json(raw)
        if pre_validate is not None:
            data = pre_validate(data)
        return model_cls.model_validate(data)

    for attempt in range(1 + repair_attempts):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ]
        try:
            reply = await llm.complete(
                messages, disable_thinking=True,
                return_finish_reason=True,
                max_tokens=max_tokens or limits.LLM_STAGE_OUTPUT_TOKENS.get(
                    prompt_id, 3000))
        except LLMRequestError as exc:
            raise ClassroomError("generation_failed", exc.message,
                                 retryable=exc.retryable) from exc
        content, usage = reply[:2]
        finish_reason = reply[2] if len(reply) > 2 else None
        usages.append(usage)
        if not (content or "").strip():
            if finish_reason == "length":
                raise ClassroomError(
                    "content_invalid",
                    "模型已用完本次输出额度但没有返回正文；"
                    "请检查模型思考模式或缩小备课范围")
            # provider 重试已在 client 内完成；空响应不属于可修复的 JSON。
            raise ClassroomError("generation_failed", "模型暂未返回可用内容，已保存当前进度",
                                 retryable=True)
        try:
            return validate_text(content), usages
        except (ValueError, ValidationError) as exc:
            last_error = _validation_errors(exc)
            if attempt < repair_attempts:
                if isinstance(exc, IncompleteJSONError):
                    prefix = _json_prefix(content)
                    continuation_messages = [
                        {"role": "system", "content": get_prompt(
                            "classroom_json_continue").text},
                        {"role": "user", "content": user_text},
                        {"role": "assistant", "content": prefix},
                        {"role": "user", "content":
                         "上条 assistant 内容是被截断的 JSON 前缀。"
                         "请只输出紧接最后一个字符的缺失后缀，"
                         "不要重复前缀，不要输出解释或代码围栏。"},
                    ]
                    try:
                        continued = await llm.complete(
                            continuation_messages, disable_thinking=True,
                            return_finish_reason=True,
                            max_tokens=min(
                                max_tokens or limits.LLM_STAGE_OUTPUT_TOKENS.get(
                                    prompt_id, 3000),
                                limits.LLM_STAGE_OUTPUT_TOKENS["classroom_json_continue"]))
                    except LLMRequestError as request_error:
                        raise ClassroomError(
                            "generation_failed", request_error.message,
                            retryable=request_error.retryable) from request_error
                    suffix, suffix_usage = continued[:2]
                    suffix_finish = continued[2] if len(continued) > 2 else None
                    usages.append(suffix_usage)
                    if suffix:
                        for candidate in _continuation_candidates(prefix, suffix):
                            try:
                                return validate_text(candidate), usages
                            except (ValueError, ValidationError) as suffix_error:
                                last_error = _validation_errors(suffix_error)
                    cause = ("达到单次输出上限" if finish_reason == "length"
                             else "JSON 未闭合")
                    continuation_cause = ("，续写也达到输出上限"
                                          if suffix_finish == "length" else "")
                    last_error = (f"单页输出{cause}{continuation_cause}，"
                                  f"尾部仍未通过校验：{last_error}")
                    break
                repair_id = repair_prompt_id or prompt_id
                payload = (
                    f"{user_text}\n\n上一次输出未通过校验，错误列表：\n"
                    f"{last_error}\n\n请严格按 schema 重新输出完整 JSON。"
                    f"\n上一次输出：\n{content[:16000]}\n"
                    f"{('（修复规则：' + get_prompt(repair_id).text + '）') if repair_prompt_id else ''}"
                )
                if context_note:
                    payload += f"\n\n{context_note}"
                continue
    raise ClassroomError(
        "content_invalid",
        f"{prompt_id} 结构化输出校验失败：{last_error[:400]}")


def clamp_pages(count: int, duration: int, explicit: int | None) -> int:
    """页数裁剪：显式 page_plan 优先（仍夹在 schema 上限内），否则按
    §15.4 PAGE_PLAN_RANGES 的时长区间夹紧。"""
    if explicit and explicit >= 1:
        return min(explicit, limits.PAGE_PLAN_RANGES.get(duration, (4, 24))[1])
    low, high = limits.PAGE_PLAN_RANGES.get(duration, (8, 12))
    return max(low, min(count, high))
