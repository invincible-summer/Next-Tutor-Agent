"""LLM second-pass summary of the model's real reasoning (real_summary level).

The narrator templates (reasoning_narrator.py) describe the PLAN, not the
model's actual reasoning — same input slots produce near-identical text turn
after turn.  When ``REASONING_SUMMARY_LEVEL=real_summary``, the Supervisor
additionally feeds the turn's raw provider reasoning (which the executor
already buffers for stats/recovery) through this one-shot summarizer and
emits the result as a ``reflection``-stage thinking event.

This keeps the hidden-CoT stance (raw chain-of-thought is never streamed or
persisted) while showing students a genuine digest of what the model actually
considered — the same "reasoning summary" pattern DeepSeek/OpenAI ship.

Fail-open: any error returns "" and the turn falls back to template-only
thinking.  The extra call is a small ``complete(disable_thinking=True)`` —
the same hardening pattern as every other structured call (DESIGN §21.1).
"""
from __future__ import annotations

from ..core.llm_async import AsyncLLMClient

_SUMMARY_PROMPT = """下面是辅导智能体在准备回答学生问题时的内部推理草稿。请把它提炼成 3-5 句给学生看的过程说明：
- 用给学生的口吻（"你"指学生），说人话，不要出现"系统提示 / 工具 / Skill / 提示词"等内部词汇；
- 只保留推理中的关键判断、思路转折和结论依据，不要复述全文；
- 100-200 字，直接输出提炼结果，不要任何前缀或标题。

内部推理草稿：
{draft}"""


async def summarize_reasoning(llm: AsyncLLMClient, thinking_text: str,
                              *, max_input_chars: int = 4000) -> str:
    """Compress raw reasoning into a student-readable digest. "" on failure."""
    text = (thinking_text or "").strip()
    if not text:
        return ""
    try:
        full, _usage = await llm.complete(
            messages=[{"role": "user", "content": _SUMMARY_PROMPT.format(
                draft=text[:max_input_chars])}],
            temperature=0.2, max_tokens=400, disable_thinking=True)
    except Exception:
        return ""
    return (full or "").strip()
