"""课堂 LLM 用量预算（plan.md §15.4，D01）。

worker 在任务上下文里 set_llm_budget_hook(LLMUsageBudget)；core/llm_async
在真正发 HTTP 前预留一次调用与 token 上限、完成后按 usage 结算——这样
quiz/critic 等内部嵌套调用也计入同一预算，工具内部重试绕不开限制。
普通聊天不设置该 contextvar，行为完全不变。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import limits
from .errors import ClassroomError


def _estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """粗估输入 tokens：CJK ~1 token/字、拉丁 ~1 token/2 字符；宁可高估。"""
    total = 0
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        text = content if isinstance(content, str) else str(content or "")
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        other = len(text) - cjk
        total += cjk + other // 2 + 8
    return total + 16


class LLMUsageBudget:
    """§15.4：全课 min(40, 2N+8) 次逻辑调用、输入 180k、输出 40k tokens。

    deadline 不在这里累计等待时间：worker 把剩余秒数写进 remaining_seconds，
    预留时一并检查（含每个子调用，防止嵌套重试绕过时间预算）。"""

    def __init__(self, *, call_budget: int,
                 input_tokens: int = limits.LLM_INPUT_TOKEN_BUDGET,
                 output_tokens: int = limits.LLM_OUTPUT_TOKEN_BUDGET) -> None:
        self.call_budget = call_budget
        self.input_budget = input_tokens
        self.output_budget = output_tokens
        self.calls_used = 0
        self.input_used = 0
        self.output_used = 0
        self.remaining_seconds: float = float(limits.JOB_DEADLINE_SECONDS)
        self.on_change: Callable[[], None] | None = None

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    @remaining_seconds.setter
    def remaining_seconds(self, value: float) -> None:
        self._deadline = time.monotonic() + max(0.0, value)

    # ---- hook 协议（core/llm_async 只依赖这三个方法名） ------------------

    def reserve(self, *, messages: list[dict[str, Any]],
                max_tokens: int | None) -> dict[str, int]:
        if self.calls_used >= self.call_budget:
            raise ClassroomError("budget_exceeded",
                                 f"全课 LLM 调用预算耗尽（{self.call_budget} 次）")
        est_in = _estimate_prompt_tokens(messages)
        est_out = int(max_tokens or 4096)
        if self.input_used + est_in > self.input_budget:
            raise ClassroomError("budget_exceeded",
                                 "全课 LLM 输入 token 预算耗尽")
        if self.output_used + est_out > self.output_budget:
            raise ClassroomError("budget_exceeded",
                                 "全课 LLM 输出 token 预算耗尽")
        if self.remaining_seconds <= 0:
            raise ClassroomError("budget_exceeded", "任务 deadline 已到")
        # 发请求前扣账并落盘：并发、进程中断和 provider 重试都不能绕过预算。
        self.calls_used += 1
        self.input_used += est_in
        self.output_used += est_out
        if self.on_change is not None:
            self.on_change()
        return {"est_in": est_in, "est_out": est_out}

    def settle(self, reservation: dict[str, int],
               usage: dict[str, Any] | None) -> None:
        if usage:
            self.input_used += int(usage.get("prompt_tokens")
                                   if usage.get("prompt_tokens") is not None
                                   else reservation["est_in"]) - reservation["est_in"]
            self.output_used += int(usage.get("completion_tokens")
                                    if usage.get("completion_tokens") is not None
                                    else reservation["est_out"]) - reservation["est_out"]
        # 无 usage 时保留预留上限；deadline 始终按墙钟计算。
        if self.on_change is not None:
            self.on_change()

    # ---- 快照 ----------------------------------------------------------

    @property
    def output_remaining(self) -> int:
        return max(0, self.output_budget - self.output_used)

    def page_output_limit(self, remaining_pages: int) -> int:
        """为未完成页均分剩余额度，并预留一页的结构修复空间。"""
        share = self.output_remaining // (max(1, remaining_pages) + 1)
        if share < limits.MIN_SLIDE_OUTPUT_TOKENS:
            raise ClassroomError(
                "budget_exceeded", "剩余输出额度不足以完成未生成页面；"
                "已完成页面已保存，请缩小课程范围后重新备课")
        return min(limits.LLM_STAGE_OUTPUT_TOKENS["classroom_slide"], share)

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_budget": self.call_budget, "calls_used": self.calls_used,
            "input_used": self.input_used, "output_used": self.output_used,
            "remaining_seconds": round(self.remaining_seconds, 1),
        }

    @property
    def exhausted(self) -> bool:
        return (self.calls_used >= self.call_budget
                or self.input_used >= self.input_budget
                or self.output_used >= self.output_budget
                or self.remaining_seconds <= 0)
