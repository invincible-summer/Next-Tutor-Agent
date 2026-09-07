"""Bloom taxonomy as the system's shared cognitive vocabulary (L1/L2 layer).

修订版布鲁姆教育目标分类学（Anderson & Krathwohl, 2001）的六个认知层级：
记忆 remember / 理解 understand / 应用 apply / 分析 analyze / 评价 evaluate /
创造 create。

DESIGN CONTRACT（反僵化原则）:
  - This module is PROMPT MATERIAL, not a rule gate. There is no value-domain
    validation, no ladder/step logic, no "answer N times then level up".
    Which level a question probes is decided by the LLM generator/teacher in
    context (it may jump, mix, or revisit levels freely); the level tag is
    just a shared vocabulary every module uses to TALK about cognition.
  - Levels attach to questions as metadata ("bloom_level") and aggregate into
    the student's cognitive profile (core/bloom_profile) — one source of truth
    shared by M4 generation/grading, chat quiz tools, M9 planning advice, and
    the profile page.
  - All helpers are pure functions; never raise.
"""
from __future__ import annotations

from typing import Any

# canonical level ids (persisted in questions/records; keep stable)
BLOOM_LEVELS: tuple[str, ...] = (
    "remember", "understand", "apply", "analyze", "evaluate", "create",
)

BLOOM_ZH: dict[str, str] = {
    "remember": "记忆", "understand": "理解", "apply": "应用",
    "analyze": "分析", "evaluate": "评价", "create": "创造",
}

# aliases -> canonical (中文与常见变体；normalize 永不抛错，未知返回 "")
_ALIASES: dict[str, str] = {}
for _lv in BLOOM_LEVELS:
    _ALIASES[_lv] = _lv
    _ALIASES[_lv.lower()] = _lv
    _ALIASES[BLOOM_ZH[_lv]] = _lv
_ALIASES.update({
    "记忆性": "remember", "再认": "remember", "复述": "remember",
    "理解性": "understand", "解释": "understand",
    "应用性": "apply", "运用": "apply",
    "分析性": "analyze", "辨析": "analyze",
    "评价性": "evaluate", "评判": "evaluate",
    "创造性": "create", "综合": "create", "设计": "create",
    "auto": "", "": "", "none": "",
})

# per-level question-style guidance (prompt fragments; the LLM stays free)
_LEVEL_STYLE: dict[str, str] = {
    "remember": "记忆：再认/复述事实、定义、公式——考“是什么”",
    "understand": "理解：解释、举例、分类归纳、说明为什么——考“懂没懂”",
    "apply": "应用：在新情境中选用方法/公式解题——考“会不会用”",
    "analyze": "分析：辨析关系、比较分解、找出错误或隐含条件——考“能不能拆开看”",
    "evaluate": "评价：依据标准判断优劣、论证取舍、批评方案——考“能不能下判断”",
    "create": "创造：设计、整合、提出新方案/新证明思路——考“能不能组装出新东西”",
}


def normalize_level(value: Any) -> str:
    """Tolerant alias -> canonical id. Unknown/empty/auto -> "" (unspecified)."""
    try:
        return _ALIASES.get(str(value or "").strip().lower(), "")
    except Exception:
        return ""


def level_label(level: Any, lang: str = "zh") -> str:
    """Display label ("应用"/"apply"); unknown -> ""."""
    lv = normalize_level(level)
    if not lv:
        return ""
    return BLOOM_ZH[lv] if lang == "zh" else lv


def guidance_block(*, focus: str = "", context_line: str = "") -> str:
    """The shared Bloom prompt block for question generators (M4 + chat tools).

    focus: an explicitly requested level ("" / "auto" = the LLM decides in
        context — the default and preferred mode).
    context_line: a one-line snapshot of the student's cognitive profile
        (see core.bloom_profile.context_line); "" = no profile data.

    The block ASKS the generator to (a) choose the level freely based on the
    student and purpose, (b) tag the question with "bloom_level" in its JSON
    output so the level flows into the shared ledger. No ladder rules.
    """
    target = normalize_level(focus)
    if target:
        head = (f"本题主要考查的认知层级指定为：{BLOOM_ZH[target]}（{target}）。"
                "题目风格参照该层级的考查方式。")
    else:
        head = ("本题考查的认知层级由你根据学生情况与考查目的综合判断，"
                "可在记忆/理解/应用/分析/评价/创造中自由选择（允许跳层、混层与回访，"
                "不要机械阶梯）。")
    lines = [f"认知层级（布鲁姆分类学）：{head}"]
    if context_line:
        lines.append(f"学生认知档案参考：{context_line}")
    lines.append("各层级风格参照——" + "；".join(_LEVEL_STYLE[lv] for lv in BLOOM_LEVELS))
    lines.append('在输出 JSON 的每道题对象中增加字段 "bloom_level"，'
                 "取值 remember|understand|apply|analyze|evaluate|create（英文小写）。")
    return "\n".join(lines)
