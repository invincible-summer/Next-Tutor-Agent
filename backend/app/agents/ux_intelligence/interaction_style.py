"""Interaction style resolution: turn UX style dims into LLM-facing phrasing.

These are pure label helpers used by explanation_adapter / context_builder.
They never call an LLM and never touch academic state -- they only translate
the UX-only InteractionStyle into concrete expression guidance ("keep it under
N lines", "use a friendly encouraging register", "lead with a diagram").
"""
from __future__ import annotations

from .schema import DetailLevel, InteractionStyle, Tone, UXProfile


def tone_guidance(tone: Tone) -> str:
    """How the LLM should set its register for this student."""
    if tone == Tone.ENCOURAGING:
        return ("语气亲切鼓励，肯定学生的努力与提问；先认可再讲解；"
                "遇到答错时强调「没关系，这正是要搞清楚的地方」。")
    if tone == Tone.FORMAL:
        return "语气严谨、学术、客观；术语准确；避免口语化与夸赞。"
    return "语气平静、克制、专业；陈述事实为主，不做刻意的鼓励。"


def detail_guidance(detail: DetailLevel) -> str:
    """Target length / density for the explanation."""
    if detail == DetailLevel.CONCISE:
        return ("回答精炼：先给结论或一句话核心，再补必要的最短解释；"
                "整体控制在约 150 字内，避免长段落。")
    if detail == DetailLevel.DETAILED:
        return ("回答充分展开：分步骤推导、给出完整过程与边界条件；可适当加长，确保逻辑闭环。")
    return "回答详略适中：先给要点再展开必要细节，避免过长或过短。"


def visual_guidance(style: InteractionStyle) -> str:
    if style.visual_preference:
        return ("该生偏好可视化：优先用表格、要点列表、示意流程或 ASCII/图示说明结构与对比；"
                "能用图/表就先于纯文字。")
    return ""


def pacing_guidance(style: InteractionStyle) -> str:
    p = style.pacing
    if p == "slow":
        return "节奏放慢：一次只推进一个要点，确认学生跟上再继续。"
    if p == "fast":
        return "节奏可加快：可合并步骤、直奔结论，避免逐字讲解。"
    return ""


def preferred_style_block(profile: UXProfile) -> list[str]:
    """Assemble the per-style guidance lines for this student. Returns only the
    non-empty ones; the caller joins them into the directive block."""
    style = profile.style
    lines = [
        tone_guidance(style.tone),
        detail_guidance(style.detail_level),
        visual_guidance(style),
        pacing_guidance(style),
    ]
    return [ln for ln in lines if ln]
