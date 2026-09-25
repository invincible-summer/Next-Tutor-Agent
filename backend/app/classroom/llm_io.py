"""课堂结构化 LLM 调用（plan.md §6.5，D02）。

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
    raise ValueError("JSON 对象未闭合")


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
    user_text: str,
    model_cls: Type[T],
    repair_prompt_id: str | None = None,
    context_note: str = "",
) -> tuple[T, list[dict[str, Any] | None]]:
    """一次生成 + 至多一次修复。返回 (model, usages)。

    最终失败抛 ClassroomError(content_invalid)——管线把它标为阶段失败，
    不把坏 JSON 写进 artifact。"""
    from ..prompts.registry import get as get_prompt

    system = get_prompt(prompt_id).text
    usages: list[dict[str, Any] | None] = []
    last_error = ""
    payload = user_text
    for attempt in range(1 + limits.SCHEMA_REPAIR_ATTEMPTS):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ]
        content, usage = await llm.complete(messages, disable_thinking=True)
        usages.append(usage)
        try:
            data = extract_json(content)
            return model_cls.model_validate(data), usages
        except (ValueError, ValidationError) as exc:
            last_error = _validation_errors(exc)
            if attempt < limits.SCHEMA_REPAIR_ATTEMPTS:
                repair_id = repair_prompt_id or prompt_id
                payload = (
                    f"{user_text}\n\n上一次输出未通过校验，错误列表：\n"
                    f"{last_error}\n\n请严格按 schema 重新输出完整 JSON。"
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
